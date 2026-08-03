from __future__ import annotations

import json, math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ible.integrity import canonical_sha256, load_json, write_json
from ible.model_lock import load_and_verify_model_lock
from ible.v3_collectors import OpenAlexCollector, UsaSpendingCollector, GdeltCollector, WikimediaCollector, comparison_periods, three_attention_periods
from ible.v3_http import HttpError, HttpSettings, JsonHttpClient

class V3DataError(RuntimeError): pass

def clamp(value: float, low: float=0.0, high: float=100.0)->float: return max(low,min(high,float(value)))

def source_signal(recent: float, prior: float) -> dict[str,float]:
    ratio=(recent+1.0)/(prior+1.0); growth_pct=100.0*((recent-prior)/max(1.0,float(prior)))
    growth_score=50.0+35.0*math.tanh(math.log(ratio)/1.25)
    scale_score=min(100.0,18.0*math.log10(recent+1.0)); signal=clamp(.60*growth_score+.40*scale_score)
    return {"recent_count":round(float(recent),4),"prior_count":round(float(prior),4),"growth_percent":round(growth_pct,4),"growth_score":round(clamp(growth_score),4),"scale_score":round(scale_score,4),"source_signal_score":round(signal,4)}

def attention_signal(values: list[float], window_days:int=30)->dict[str,Any]:
    vals=[max(0.0,float(x)) for x in values]
    if not vals: raise ValueError("empty attention series")
    third=max(1,len(vals)//3); oldest=vals[:third]; prior=vals[third:2*third]; recent=vals[2*third:] or vals[-third:]
    def avg(xs): return sum(xs)/max(1,len(xs))
    r,p,o=avg(recent),avg(prior),avg(oldest)
    sig=source_signal(r,p); change=100.0*((r-o)/max(1e-9,o))
    sig.update({"oldest_30d_level":round(o,6),"recent_30d_level":round(r,6),"prior_30d_level":round(p,6),"three_month_change_percent":round(change,4),"series_points":len(vals)})
    return sig

def _load_cache(root:Path)->dict[str,Any]|None:
    p=root/'data_cache/latest/v3_source_observations.json'
    try: return load_json(p) if p.is_file() else None
    except Exception: return None

def _cached_source(cache,theme_id,source):
    for row in (cache or {}).get('themes') or []:
        if row.get('theme_id')==theme_id:
            c=(row.get('sources') or {}).get(source); return c if isinstance(c,dict) else None
    return None

def _collect_one(row, openalex, usaspending, gdelt, wikimedia, recent_period, prior_period, interest_period, cache, captured_at):
    tid=str(row['theme_id']); sources={}; errors=[]
    tasks=[
      ('openalex',lambda:(openalex.count(str(row['openalex_search']),recent_period),openalex.count(str(row['openalex_search']),prior_period)),str(row['openalex_search']),'count'),
      ('usaspending',lambda:(usaspending.count(list(row['usaspending_keywords']),recent_period),usaspending.count(list(row['usaspending_keywords']),prior_period)),list(row['usaspending_keywords']),'count'),
      ('gdelt',lambda:gdelt.timeline(str(row['gdelt_query']),interest_period),str(row['gdelt_query']),'series'),
      ('wikimedia',lambda:wikimedia.timeline(list(row['wikipedia_titles']),interest_period),list(row['wikipedia_titles']),'series'),
    ]
    for name,call,query,kind in tasks:
        try:
            result=call(); metric=source_signal(*result) if kind=='count' else attention_signal(result)
            sources[name]={"status":"LIVE_COLLECTED","query":query,"captured_at":captured_at,**metric}
        except (HttpError,ValueError,TypeError) as exc:
            cached=_cached_source(cache,tid,name)
            if cached:
                sources[name]={**cached,"status":"CACHE_FALLBACK","fallback_reason":str(exc)[:500]}
            else:
                sources[name]={"status":"SOURCE_UNAVAILABLE","query":query,"captured_at":captured_at,"source_signal_score":None,"growth_score":None,"growth_percent":None,"recent_count":None,"prior_count":None,"three_month_change_percent":None}
            errors.append({"source":name,"error":str(exc)[:500]})
    core=[sources[n] for n in ('openalex','usaspending') if sources[n].get('source_signal_score') is not None]
    return {"theme_id":tid,"theme_name":row['theme_name'],"sector":row['sector'],"data_build_priority":row['data_build_priority'],"status":"PHASE1_OBSERVED" if len(core)>=2 else ('PARTIAL_SOURCE' if core else 'NO_SOURCE_DATA'),"source_family_count":len(core),"phase1_data_signal_score":round(sum(float(x['source_signal_score']) for x in core)/len(core),4) if core else None,"public_interest_score":None,"public_interest_status":"PENDING_CROSS_SECTIONAL_NORMALIZATION","boom_score":None,"frozen_model_score_eligible":False,"sources":sources,"errors":errors,"limitations":["대중 관심도는 GDELT 글로벌 뉴스와 영문 위키피디아 열람량의 상대순위로 계산합니다.","V0.9.1 동결 점수와 별도인 운영용 데이터 레이어입니다."]}

def _percentiles(rows, source):
    vals=[]
    for row in rows:
        v=(row.get('sources') or {}).get(source,{}).get('recent_30d_level')
        if v is None: v=(row.get('sources') or {}).get(source,{}).get('recent_count')
        if v is not None: vals.append(float(v))
    ordered=sorted(vals)
    def pct(v):
        if v is None or not ordered: return None
        lower=sum(1 for x in ordered if x<float(v)); equal=sum(1 for x in ordered if x==float(v))
        return 100.0*(lower+0.5*equal)/len(ordered)
    return pct

def run_v3_data(root:Path, output_dir:Path, run_date:str|None=None)->dict[str,Any]:
    cfg=load_json(root/'config/v3_data_sources.json'); qcfg=load_json(root/'config/v3_theme_queries.json'); model_lock=load_and_verify_model_lock(root)
    if len(qcfg.get('themes') or [])!=int(cfg['minimum_theme_count']): raise V3DataError('theme query count mismatch')
    tz=ZoneInfo(str(cfg.get('timezone') or 'Asia/Seoul')); now=datetime.now(tz); today=date.fromisoformat(run_date) if run_date else now.date(); as_of=today-timedelta(days=1); captured_at=now.isoformat(timespec='seconds')
    recent,prior=comparison_periods(as_of,int(cfg['lookback_days'])); interest=three_attention_periods(as_of,int(cfg.get('public_interest_window_days',30))); interest_period=type(recent)(interest[2].start,interest[0].end)
    net=cfg['network']; client=JsonHttpClient(HttpSettings(timeout_seconds=int(net['timeout_seconds']),max_attempts=int(net['max_attempts']),base_backoff_seconds=float(net['base_backoff_seconds']),user_agent=str(net['user_agent'])))
    collectors=(OpenAlexCollector(client,cfg['sources']['openalex']['base_url']),UsaSpendingCollector(client,cfg['sources']['usaspending']['base_url']),GdeltCollector(client,cfg['sources']['gdelt']['base_url']),WikimediaCollector(client,cfg['sources']['wikimedia']['base_url']))
    cache=_load_cache(root); rows=[]
    with ThreadPoolExecutor(max_workers=int(net['max_workers'])) as ex:
        futs=[ex.submit(_collect_one,row,*collectors,recent,prior,interest_period,cache,captured_at) for row in qcfg['themes']]
        for f in as_completed(futs): rows.append(f.result())
    rows.sort(key=lambda x:(int(x['data_build_priority']),str(x['theme_id'])))
    for source in ('gdelt','wikimedia'):
        pct=_percentiles(rows,source)
        for row in rows:
            s=row['sources'][source]; raw=s.get('recent_30d_level',s.get('recent_count')); p=pct(raw)
            if p is not None and s.get('growth_score') is not None:
                s['attention_percentile']=round(p,4); s['attention_score']=round(clamp(.75*p+.25*float(s['growth_score'])),4)
    for row in rows:
        available=[row['sources'][n] for n in ('gdelt','wikimedia') if row['sources'][n].get('attention_score') is not None]
        if available:
            row['public_interest_score']=round(sum(float(x['attention_score']) for x in available)/len(available),4)
            row['public_interest_momentum_3m']=round(sum(float(x.get('three_month_change_percent') or 0) for x in available)/len(available),4)
            row['public_interest_status']='LIVE_OR_CACHED_OBSERVED'
        else:
            row['public_interest_score']=50.0; row['public_interest_momentum_3m']=0.0; row['public_interest_status']='NEUTRAL_FALLBACK_SOURCE_UNAVAILABLE'
    obs={"schema_version":2,"engine_release":cfg['engine_release'],"as_of":as_of.isoformat(),"captured_at":captured_at,"theme_count":len(rows),"source_provenance":cfg['sources'],"investment_use_allowed":False,"themes":rows}; obs['content_sha256']=canonical_sha256(obs)
    public_count=sum(1 for r in rows if r['public_interest_status']=='LIVE_OR_CACHED_OBSERVED')
    summary={"status":"V7_PUBLIC_INTEREST_AND_CORE_DATA_COLLECTED","engine_release":cfg['engine_release'],"as_of":as_of.isoformat(),"theme_count":len(rows),"public_interest_observed_theme_count":public_count,"public_interest_coverage_percent":round(100*public_count/max(1,len(rows)),2),"model_lock":model_lock,"investment_use_allowed":False}
    output_dir.mkdir(parents=True,exist_ok=True); write_json(output_dir/'v3_run_summary.json',summary); write_json(output_dir/'v3_source_observations.json',obs); write_json(output_dir/'v3_data_source_health.json',{"status":"SOURCE_HEALTH_RECORDED","as_of":as_of.isoformat(),"public_interest_observed":public_count}); write_json(output_dir/'v3_model_lock_verification.json',model_lock); write_json(output_dir/'v3_next_gate.json',{"status":"V7_CORE_DATA_READY","investment_use_allowed":False})
    write_json(root/'data_cache/latest/v3_source_observations.json',obs); write_json(root/'data_cache'/f'{as_of.year:04d}'/f'{as_of.month:02d}'/as_of.isoformat()/'v3_source_observations.json',obs)
    return summary
