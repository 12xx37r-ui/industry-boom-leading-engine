from __future__ import annotations

import json, math, os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ible.integrity import canonical_sha256, load_json, write_json
from ible.model_lock import load_and_verify_model_lock
from ible.v3_collectors import ArxivCollector, OpenAlexCollector, UsaSpendingCollector, GdeltCollector, WikimediaCollector, NaverSearchTrendCollector, comparison_periods, three_attention_periods
from ible.v3_http import HttpError, HttpSettings, JsonHttpClient
from ible.v3_dynamic_terms import build_dynamic_discovery_report, collect_dynamic_documents, discover_candidates, write_dynamic_discovery_report
from ible.v3_lag_bridge import build_lag_bridge, write_lag_bridge
from ible.v3_sec_nowcast import build_sec_nowcast, ingest_sec_companyfacts, write_sec_nowcast
from ible.v3_frontier_signals import build_frontier_signals, write_frontier_signals
from ible.v3_hiring_nowcast import build_hiring_nowcast, write_hiring_nowcast

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


def _shrink_proxy_score(score: float, reliability: float = 0.55) -> float:
    """Shrink broad proxy evidence toward neutral so it cannot dominate a theme-specific signal."""
    return clamp(50.0 + (float(score) - 50.0) * float(reliability))

def _collect_usaspending(row, usaspending, recent_period, prior_period, captured_at):
    keywords = [str(x).strip() for x in (row.get('usaspending_keywords') or []) if str(x).strip()]
    naics_codes = [str(x).strip() for x in (row.get('usaspending_naics') or []) if str(x).strip()]

    strict_recent = usaspending.count(keywords, recent_period)
    strict_prior = usaspending.count(keywords, prior_period)
    if strict_recent > 0 or strict_prior > 0:
        metric = source_signal(strict_recent, strict_prior)
        return {
            "status": "LIVE_COLLECTED",
            "query": keywords,
            "query_mode": "THEME_KEYWORD",
            "specificity": "HIGH",
            "captured_at": captured_at,
            "recent_period": recent_period.as_dict(),
            "prior_period": prior_period.as_dict(),
            **metric,
        }

    # A 0/0 text result is not evidence that government support is truly zero.
    # Award descriptions often use procurement terminology instead of the dashboard's
    # theme label. Fall back to the already-curated QCEW NAICS industry basket.
    if naics_codes:
        proxy_recent = usaspending.count([], recent_period, naics_codes=naics_codes)
        proxy_prior = usaspending.count([], prior_period, naics_codes=naics_codes)
        if proxy_recent > 0 or proxy_prior > 0:
            metric = source_signal(proxy_recent, proxy_prior)
            raw_signal = float(metric["source_signal_score"])
            metric["raw_source_signal_score"] = round(raw_signal, 4)
            metric["source_signal_score"] = round(_shrink_proxy_score(raw_signal), 4)
            metric["proxy_reliability"] = 0.55
            return {
                "status": "LIVE_COLLECTED",
                "query": keywords,
                "query_mode": "NAICS_PROXY_FALLBACK",
                "specificity": "PROXY",
                "proxy_naics_codes": naics_codes,
                "strict_recent_count": strict_recent,
                "strict_prior_count": strict_prior,
                "captured_at": captured_at,
                "recent_period": recent_period.as_dict(),
                "prior_period": prior_period.as_dict(),
                **metric,
            }

    return {
        "status": "LIVE_COLLECTED",
        "query": keywords,
        "query_mode": "NO_MATCH",
        "specificity": "NONE",
        "proxy_naics_codes": naics_codes,
        "captured_at": captured_at,
        "recent_period": recent_period.as_dict(),
        "prior_period": prior_period.as_dict(),
        "recent_count": 0.0,
        "prior_count": 0.0,
        "growth_percent": None,
        "growth_score": None,
        "scale_score": None,
        "source_signal_score": None,
    }

def _load_cache(root:Path)->dict[str,Any]|None:
    p=root/'data_cache/latest/v3_source_observations.json'
    try: return load_json(p) if p.is_file() else None
    except Exception: return None

def _cached_source(cache,theme_id,source,as_of=None):
    for row in (cache or {}).get('themes') or []:
        if row.get('theme_id')==theme_id:
            c=(row.get('sources') or {}).get(source)
            if not isinstance(c,dict): return None
            cached_as_of=str(c.get('as_of') or (cache or {}).get('as_of') or '')[:10]
            if as_of and cached_as_of and cached_as_of > str(as_of)[:10]:
                return None
            return c
    return None

def _cache_age_days(cached, as_of):
    cached_as_of=str((cached or {}).get('as_of') or '')[:10]
    if not cached_as_of or not as_of: return None
    try:
        return max(0, (date.fromisoformat(str(as_of)[:10]) - date.fromisoformat(cached_as_of)).days)
    except ValueError:
        return None

def _unavailable_source(query, captured_at, as_of, reason, status='SOURCE_UNAVAILABLE'):
    return {
        'status': status,
        'query': query,
        'captured_at': captured_at,
        'as_of': str(as_of),
        'availability_reason': reason,
        'source_signal_score': None,
        'growth_score': None,
        'growth_percent': None,
        'recent_count': None,
        'prior_count': None,
    }

def _collect_one(row, openalex, usaspending, gdelt, wikimedia, recent_period, prior_period, interest_period, cache, captured_at, as_of, enabled_sources):
    tid=str(row['theme_id']); sources={}; errors=[]
    tasks=[]
    if enabled_sources.get('openalex') and openalex:
        tasks.append(('openalex',lambda:(openalex.count(str(row['openalex_search']),recent_period),openalex.count(str(row['openalex_search']),prior_period)),str(row['openalex_search']),'count'))
    if enabled_sources.get('gdelt') and gdelt:
        # Truncate to 4 words: GDELT matches broader global news better with shorter queries
        _gdelt_q = " ".join(str(row['gdelt_query']).split()[:4]) or str(row['gdelt_query'])
        tasks.append(('gdelt', lambda q=_gdelt_q: gdelt.timeline(q, interest_period), _gdelt_q, 'series'))
    if enabled_sources.get('wikimedia') and wikimedia:
        tasks.append(('wikimedia',lambda:wikimedia.timeline(list(row['wikipedia_titles']),interest_period),list(row['wikipedia_titles']),'series'))
    task_names={item[0] for item in tasks}
    for name in ('openalex','gdelt','wikimedia'):
        if name not in task_names:
            sources[name]=_unavailable_source(
                str(row.get(name + '_search') or row.get(name + '_query') or row.get('wikipedia_titles') or []),
                captured_at, as_of, 'SOURCE_DISABLED_BY_CONFIG', 'SOURCE_DISABLED'
            )
    for name,call,query,kind in tasks:
        try:
            result=call(); metric=source_signal(*result) if kind=='count' else attention_signal(result)
            sources[name]={"status":"LIVE_COLLECTED","query":query,"captured_at":captured_at,"as_of":str(as_of),**metric}
        except (HttpError,ValueError,TypeError) as exc:
            cached=_cached_source(cache,tid,name,as_of)
            cached_usable = bool(cached) and (
                cached.get('attention_score') is not None or
                cached.get('source_signal_score') is not None or
                cached.get('recent_30d_level') is not None
            )
            if cached_usable:
                sources[name]={**cached,"status":"CACHE_FALLBACK","fallback_reason":str(exc)[:500],"cache_age_days":_cache_age_days(cached,as_of)}
            else:
                sources[name]=_unavailable_source(query,captured_at,as_of,str(exc)[:500])
                sources[name]['three_month_change_percent']=None
            errors.append({"source":name,"error":str(exc)[:500]})

    if not enabled_sources.get('usaspending') or not usaspending:
        sources['usaspending'] = _unavailable_source(list(row.get('usaspending_keywords') or []),captured_at,as_of,'SOURCE_DISABLED_BY_CONFIG','SOURCE_DISABLED')
    else:
      try:
        sources['usaspending'] = _collect_usaspending(row, usaspending, recent_period, prior_period, captured_at)
        sources['usaspending']['as_of']=str(as_of)
      except (HttpError,ValueError,TypeError) as exc:
        cached=_cached_source(cache,tid,'usaspending',as_of)
        cached_usable = bool(cached) and cached.get('source_signal_score') is not None
        if cached_usable:
            sources['usaspending']={**cached,"status":"CACHE_FALLBACK","fallback_reason":str(exc)[:500],"cache_age_days":_cache_age_days(cached,as_of)}
        else:
            sources['usaspending'] = _unavailable_source(list(row.get('usaspending_keywords') or []),captured_at,as_of,str(exc)[:500])
            sources['usaspending'].update({'query_mode':'SOURCE_UNAVAILABLE','proxy_naics_codes':list(row.get('usaspending_naics') or [])})
        errors.append({"source":'usaspending',"error":str(exc)[:500]})
    core=[sources[n] for n in ('openalex','usaspending') if sources[n].get('source_signal_score') is not None]
    return {"theme_id":tid,"theme_name":row['theme_name'],"sector":row['sector'],"data_build_priority":row['data_build_priority'],"status":"PHASE1_OBSERVED" if len(core)>=2 else ('PARTIAL_SOURCE' if core else 'NO_SOURCE_DATA'),"source_family_count":len(core),"phase1_data_signal_score":round(sum(float(x['source_signal_score']) for x in core)/len(core),4) if core else None,"public_interest_score":None,"public_interest_status":"PENDING_CROSS_SECTIONAL_NORMALIZATION","boom_score":None,"frozen_model_score_eligible":False,"sources":sources,"errors":errors,"limitations":["대중 관심도는 NAVER 검색어 트렌드를 주력으로 계산하고, NAVER 장애 시 GDELT 글로벌 뉴스 관심도를 보조 fallback으로 사용합니다.","V0.9.1 동결 점수와 별도인 운영용 데이터 레이어입니다."]}

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
    naics_cfg=load_json(root/'config/v31_theme_naics.json')
    naics_by_theme={str(x.get('theme_id')):[str(v) for v in (x.get('qcew_naics') or [])] for x in (naics_cfg.get('themes') or []) if x.get('theme_id')}
    enriched_themes=[]
    for item in qcfg['themes']:
        row=dict(item)
        row['usaspending_naics']=naics_by_theme.get(str(row.get('theme_id')),[])
        enriched_themes.append(row)
    tz=ZoneInfo(str(cfg.get('timezone') or 'Asia/Seoul')); now=datetime.now(tz); today=date.fromisoformat(run_date) if run_date else now.date(); as_of=today-timedelta(days=1); captured_at=now.isoformat(timespec='seconds')
    recent,prior=comparison_periods(as_of,int(cfg['lookback_days'])); interest=three_attention_periods(as_of,int(cfg.get('public_interest_window_days',30))); interest_period=type(recent)(interest[2].start,interest[0].end)
    net=cfg['network']
    cache_cfg=cfg.get('cache') or {}
    client=JsonHttpClient(
        HttpSettings(
            timeout_seconds=int(net['timeout_seconds']),
            max_attempts=int(net['max_attempts']),
            base_backoff_seconds=float(net['base_backoff_seconds']),
            user_agent=str(net['user_agent']),
            min_interval_seconds=float(net.get('min_interval_seconds',0.20)),
            cache_ttl_seconds=int(cache_cfg.get('ttl_seconds',21600)),
            stale_if_error_seconds=int(cache_cfg.get('stale_if_error_seconds',604800)),
        ),
        cache_dir=root / str(cache_cfg.get('directory','data_cache/http/v3')),
    )
    enabled_sources={name:bool((item or {}).get('enabled',True)) for name,item in (cfg.get('sources') or {}).items()}
    def collector(name, factory):
        source=cfg['sources'][name]
        return factory(source['base_url']) if enabled_sources.get(name) else None
    openalex=collector('openalex',lambda url:OpenAlexCollector(client,url))
    usaspending=collector('usaspending',lambda url:UsaSpendingCollector(client,url))
    # GDELT gets a dedicated single-attempt HTTP client. Retries are serialized
    # inside GdeltCollector so 429 retries cannot interleave across worker threads.
    gdelt = None
    if enabled_sources.get('gdelt'):
        gd_cfg = cfg['sources']['gdelt']
        gdelt_client = JsonHttpClient(
            HttpSettings(
                timeout_seconds=int(net['timeout_seconds']), max_attempts=1,
                base_backoff_seconds=float(net['base_backoff_seconds']),
                user_agent=str(net['user_agent']), min_interval_seconds=0.0,
                cache_ttl_seconds=int(cache_cfg.get('ttl_seconds',21600)),
                stale_if_error_seconds=int(cache_cfg.get('stale_if_error_seconds',604800)),
            ),
            cache_dir=root / str(cache_cfg.get('directory','data_cache/http/v3')),
        )
        gdelt = GdeltCollector(
            gdelt_client, gd_cfg['base_url'],
            min_interval_seconds=float(gd_cfg.get('min_request_interval_seconds',6.5)),
            max_attempts=int(gd_cfg.get('max_attempts',2)),
            retry_backoff_seconds=float(gd_cfg.get('retry_backoff_seconds',8.0)),
        )
    wikimedia=collector('wikimedia',lambda url:WikimediaCollector(client,url))

    naver_cfg = load_json(root / 'config/v3_naver_interest.json')
    naver = None
    naver_credentials_error = None
    if enabled_sources.get('naver_search_trend') and bool(naver_cfg.get('enabled', True)):
        client_id = os.environ.get(str(naver_cfg.get('client_id_env') or 'NAVER_API_HUB_CLIENT_ID'), '').strip()
        client_secret = os.environ.get(str(naver_cfg.get('client_secret_env') or 'NAVER_API_HUB_CLIENT_SECRET'), '').strip()
        if client_id and client_secret:
            naver = NaverSearchTrendCollector(client, str(naver_cfg.get('base_url') or cfg['sources']['naver_search_trend']['base_url']), client_id, client_secret)
        else:
            naver_credentials_error = 'NAVER_API_HUB_CLIENT_ID / NAVER_API_HUB_CLIENT_SECRET are not configured'
    dynamic_cfg = load_json(root / 'config/v3_dynamic_discovery.json')
    arxiv=ArxivCollector(client) if bool(dynamic_cfg.get('enabled', True)) else None

    # ── EARLY WRITES ──────────────────────────────────────────────────────────
    # Write mandatory output files BEFORE the slow 50-theme collection so they
    # survive a runner timeout. Each of these is cache-only / local-file-only
    # and completes in seconds regardless of network conditions.
    output_dir.mkdir(parents=True, exist_ok=True)

    frontier_cfg = load_json(root / 'config/v3_frontier_signals.json')
    if bool(frontier_cfg.get('enabled', True)):
        frontier_signals = build_frontier_signals(root, enriched_themes, as_of.isoformat(), client, frontier_cfg)
    else:
        frontier_signals = {
            'schema_version': 1, 'as_of': as_of.isoformat(), 'status': 'DISABLED_BY_CONFIG',
            'investment_use_allowed': False, 'official_statistics_replaced': False,
            'selected_theme_count': 0, 'github_query_count': 0, 'patent_query_count': 0,
            'github': [], 'patentsview': [],
            'history': {'schema_version': 1, 'as_of': as_of.isoformat(), 'observations': {}, 'patent_counts': {}},
            'lookahead_guard': 'FUTURE_DATA_REJECTED',
        }
    write_frontier_signals(root, output_dir, frontier_signals)

    hiring_nowcast = build_hiring_nowcast(root, enriched_themes, as_of.isoformat())
    write_hiring_nowcast(root, output_dir, hiring_nowcast)

    sec_ingest = ingest_sec_companyfacts(root, as_of.isoformat(), max_tickers=20)
    sec_nowcast = build_sec_nowcast(root, enriched_themes, as_of.isoformat())
    sec_nowcast["ingest"] = sec_ingest
    sec_nowcast["content_sha256"] = canonical_sha256({key: value for key, value in sec_nowcast.items() if key != "content_sha256"})
    write_sec_nowcast(root, output_dir, sec_nowcast)

    # Dynamic discovery: write a cache-based stub so the required file exists.
    # The live collection below will overwrite it with fresh results if it completes.
    early_dynamic_report = build_dynamic_discovery_report(root, enriched_themes, as_of.isoformat())
    early_dynamic_report['collection'] = {'status': 'PENDING_LIVE_COLLECTION', 'document_count': 0, 'selected_theme_count': 0, 'errors': []}
    write_dynamic_discovery_report(root, output_dir, early_dynamic_report)
    # ── END EARLY WRITES ──────────────────────────────────────────────────────

    cache=_load_cache(root); rows=[]
    with ThreadPoolExecutor(max_workers=int(net['max_workers'])) as ex:
        futs=[ex.submit(_collect_one,row,openalex,usaspending,gdelt,wikimedia,recent,prior,interest_period,cache,captured_at,as_of,enabled_sources) for row in enriched_themes]
        for f in as_completed(futs): rows.append(f.result())
    rows.sort(key=lambda x:(int(x['data_build_priority']),str(x['theme_id'])))

    # NAVER Search Trend is collected in batches because one request can compare
    # up to five keyword groups. Each batch includes the same anchor group, so
    # theme levels remain cross-batch comparable even though NAVER returns ratios.
    naver_by_theme = {str(x['theme_id']): x for x in (naver_cfg.get('themes') or [])}
    row_by_theme = {str(x['theme_id']): x for x in rows}
    anchor_cfg = naver_cfg.get('anchor') or {'group_name':'기준_반도체','keywords':['반도체']}
    anchor_name = str(anchor_cfg.get('group_name') or '기준_반도체')
    max_groups = max(2, min(5, int(naver_cfg.get('max_groups_per_request',5))))
    batch_size = max_groups - 1
    theme_ids = [str(x['theme_id']) for x in enriched_themes]
    for offset in range(0, len(theme_ids), batch_size):
        ids = theme_ids[offset:offset+batch_size]
        groups = [{'groupName':anchor_name,'keywords':list(anchor_cfg.get('keywords') or ['반도체'])}]
        for tid in ids:
            item = naver_by_theme.get(tid) or {}
            groups.append({'groupName':str(item.get('group_name') or row_by_theme[tid]['theme_name']),'keywords':list(item.get('keywords') or [row_by_theme[tid]['theme_name']])})
        try:
            if naver is None:
                raise ValueError(naver_credentials_error or 'NAVER Search Trend disabled')
            series_map = naver.search(groups, interest_period, str(naver_cfg.get('time_unit') or 'date'))
            anchor_series = series_map.get(anchor_name) or []
            anchor_metric = attention_signal(anchor_series) if anchor_series else None
            if not anchor_metric or float(anchor_metric.get('recent_30d_level') or 0) <= 0:
                raise ValueError('NAVER anchor series unavailable')
            for tid in ids:
                row = row_by_theme[tid]
                item = naver_by_theme.get(tid) or {}
                title = str(item.get('group_name') or row['theme_name'])
                values = series_map.get(title) or []
                if not values:
                    raise ValueError('NAVER series unavailable for ' + tid)
                metric = attention_signal(values)
                # Convert level fields to common-anchor units for cross-theme percentile.
                for field in ('recent_30d_level','prior_30d_level','oldest_30d_level'):
                    raw_value = float(metric.get(field) or 0.0)
                    anchor_value = float(anchor_metric.get(field) or 0.0)
                    metric['raw_' + field] = round(raw_value,6)
                    metric[field] = round(100.0 * raw_value / max(anchor_value,1e-9),6)
                row['sources']['naver_search_trend'] = {
                    'status':'LIVE_COLLECTED','query':list(item.get('keywords') or []),
                    'group_name':title,'anchor_group':anchor_name,'captured_at':captured_at,'as_of':str(as_of),
                    'series_basis':'NAVER_RATIO_WITH_COMMON_ANCHOR', **metric,
                }
        except (HttpError, ValueError, TypeError) as exc:
            for tid in ids:
                row = row_by_theme[tid]
                cached = _cached_source(cache,tid,'naver_search_trend',as_of)
                if cached and cached.get('recent_30d_level') is not None:
                    row['sources']['naver_search_trend'] = {**cached,'status':'CACHE_FALLBACK','fallback_reason':str(exc)[:500],'cache_age_days':_cache_age_days(cached,as_of)}
                else:
                    item=naver_by_theme.get(tid) or {}
                    row['sources']['naver_search_trend'] = _unavailable_source(list(item.get('keywords') or []),captured_at,as_of,str(exc)[:500])
                    row['sources']['naver_search_trend']['three_month_change_percent']=None
                row['errors'].append({'source':'naver_search_trend','error':str(exc)[:500]})

    for source in ('naver_search_trend','gdelt'):
        pct=_percentiles(rows,source)
        for row in rows:
            s=row['sources'][source]; raw=s.get('recent_30d_level',s.get('recent_count')); p=pct(raw)
            if p is not None and s.get('growth_score') is not None:
                s['attention_percentile']=round(p,4); s['attention_score']=round(clamp(.75*p+.25*float(s['growth_score'])),4)
    for row in rows:
        naver_src = row['sources'].get('naver_search_trend') or {}
        gdelt_src = row['sources'].get('gdelt') or {}
        if naver_src.get('attention_score') is not None:
            selected = naver_src
            row['public_interest_primary_source']='NAVER_SEARCH_TREND'
            row['public_interest_score']=round(float(selected['attention_score']),4)
            row['public_interest_momentum_3m']=round(float(selected.get('three_month_change_percent') or 0),4)
            row['public_interest_status']='LIVE_OR_CACHED_OBSERVED'
            row['public_interest_confidence']=1.0 if selected.get('status')=='LIVE_COLLECTED' else 0.85
        elif gdelt_src.get('attention_score') is not None:
            selected = gdelt_src
            row['public_interest_primary_source']='GDELT_FALLBACK'
            row['public_interest_score']=round(float(selected['attention_score']),4)
            row['public_interest_momentum_3m']=round(float(selected.get('three_month_change_percent') or 0),4)
            row['public_interest_status']='LIVE_OR_CACHED_OBSERVED'
            row['public_interest_confidence']=0.70 if selected.get('status')=='LIVE_COLLECTED' else 0.55
        else:
            row['public_interest_primary_source']='NEUTRAL_FALLBACK'
            row['public_interest_score']=float((cfg.get('public_interest_policy') or {}).get('missing_all_sources_fallback',50.0))
            row['public_interest_momentum_3m']=0.0
            row['public_interest_status']='NEUTRAL_FALLBACK_SOURCE_UNAVAILABLE'
            row['public_interest_confidence']=0.0
    collection_metrics=client.stats()
    collection_metrics['enabled_sources']=[name for name,enabled in enabled_sources.items() if enabled]
    collection_metrics['disabled_sources']=[name for name,enabled in enabled_sources.items() if not enabled]
    obs={"schema_version":3,"engine_release":cfg['engine_release'],"as_of":as_of.isoformat(),"captured_at":captured_at,"theme_count":len(rows),"source_provenance":cfg['sources'],"collection_metrics":collection_metrics,"investment_use_allowed":False,"themes":rows}; obs['content_sha256']=canonical_sha256(obs)
    dynamic_documents, dynamic_collection = ([], {'status': 'DISABLED', 'document_count': 0, 'selected_theme_count': 0, 'errors': []})
    if bool(dynamic_cfg.get('enabled', True)):
        dynamic_documents, dynamic_collection = collect_dynamic_documents(
            openalex, gdelt, enriched_themes, as_of.isoformat(), arxiv,
            max_theme_queries=int(dynamic_cfg.get('max_theme_queries_per_run', 5)),
            documents_per_source=int(dynamic_cfg.get('documents_per_source', 5)),
            lookback_days=int(dynamic_cfg.get('lookback_days', 90)),
        )
    if dynamic_documents:
        dynamic_report = discover_candidates(
            dynamic_documents, enriched_themes, as_of.isoformat(),
            **(dynamic_cfg.get('promotion_rule') or {}),
        )
        dynamic_report['input_path'] = 'generated_from_openalex_arxiv_or_gdelt_cached_documents'
        dynamic_report['input_document_count'] = len(dynamic_documents)
    else:
        dynamic_report = build_dynamic_discovery_report(root, enriched_themes, as_of.isoformat())
    dynamic_report['collection'] = dynamic_collection
    write_dynamic_discovery_report(root, output_dir, dynamic_report)
    obs["dynamic_discovery"] = {"status": dynamic_report["status"], "candidate_count": dynamic_report["candidate_count"], "auto_add_allowed": False}
    obs['content_sha256']=canonical_sha256(obs)
    lag_bridge = build_lag_bridge(obs, as_of.isoformat())
    write_lag_bridge(root, output_dir, lag_bridge)
    obs["lag_bridge"] = {
        "status": lag_bridge["status"],
        "nowcast_active_theme_count": lag_bridge["nowcast_active_theme_count"],
        "future_data_rejected_count": lag_bridge["future_data_rejected_count"],
        "official_statistics_replaced": False,
    }
    obs["sec_mdna_capex_nowcast"] = {
        "status": sec_nowcast["status"],
        "observed_theme_count": sec_nowcast["observed_theme_count"],
        "future_filing_rejected_count": sec_nowcast["future_filing_rejected_count"],
        "external_api_calls": 0,
        "ingest_status": sec_ingest["status"],
    }
    obs["frontier_signals"] = {
        "status": frontier_signals["status"],
        "selected_theme_count": frontier_signals["selected_theme_count"],
        "github_query_count": frontier_signals["github_query_count"],
        "patent_query_count": frontier_signals["patent_query_count"],
        "investment_use_allowed": False,
    }
    obs["hiring_nowcast"] = {
        "status": hiring_nowcast["status"],
        "observed_theme_count": hiring_nowcast["observed_theme_count"],
        "future_observation_rejected_count": hiring_nowcast["future_observation_rejected_count"],
        "investment_use_allowed": False,
    }
    obs['content_sha256']=canonical_sha256(obs)
    public_count=sum(1 for r in rows if r['public_interest_status']=='LIVE_OR_CACHED_OBSERVED')
    collection_metrics = client.stats()
    collection_metrics['enabled_sources']=[name for name,enabled in enabled_sources.items() if enabled]
    collection_metrics['disabled_sources']=[name for name,enabled in enabled_sources.items() if not enabled]
    summary={"status":"V7_PUBLIC_INTEREST_AND_CORE_DATA_COLLECTED","engine_release":cfg['engine_release'],"as_of":as_of.isoformat(),"theme_count":len(rows),"public_interest_observed_theme_count":public_count,"public_interest_coverage_percent":round(100*public_count/max(1,len(rows)),2),"collection_metrics":collection_metrics,"dynamic_discovery":{"status":dynamic_report["status"],"candidate_count":dynamic_report["candidate_count"],"auto_add_allowed":False},"lag_bridge":{"status":lag_bridge["status"],"nowcast_active_theme_count":lag_bridge["nowcast_active_theme_count"],"future_data_rejected_count":lag_bridge["future_data_rejected_count"],"official_statistics_replaced":False},"sec_mdna_capex_nowcast":{"status":sec_nowcast["status"],"observed_theme_count":sec_nowcast["observed_theme_count"],"future_filing_rejected_count":sec_nowcast["future_filing_rejected_count"],"external_api_calls":0},"frontier_signals":{"status":frontier_signals["status"],"selected_theme_count":frontier_signals["selected_theme_count"],"github_query_count":frontier_signals["github_query_count"],"patent_query_count":frontier_signals["patent_query_count"],"investment_use_allowed":False},"hiring_nowcast":{"status":hiring_nowcast["status"],"observed_theme_count":hiring_nowcast["observed_theme_count"],"future_observation_rejected_count":hiring_nowcast["future_observation_rejected_count"],"investment_use_allowed":False},"model_lock":model_lock,"investment_use_allowed":False}
    source_health={"status":"SOURCE_HEALTH_RECORDED","as_of":as_of.isoformat(),"public_interest_observed":public_count,"collection_metrics":collection_metrics,"sources":{name:{"enabled":enabled_sources.get(name,False),"live_count":sum(1 for row in rows if (row.get('sources') or {}).get(name,{}).get('status')=='LIVE_COLLECTED'),"cache_fallback_count":sum(1 for row in rows if (row.get('sources') or {}).get(name,{}).get('status')=='CACHE_FALLBACK'),"unavailable_count":sum(1 for row in rows if (row.get('sources') or {}).get(name,{}).get('status') in {'SOURCE_UNAVAILABLE','SOURCE_DISABLED'})} for name in ('openalex','usaspending','naver_search_trend','gdelt','wikimedia')}}
    write_json(output_dir/'v3_run_summary.json',summary); write_json(output_dir/'v3_source_observations.json',obs); write_json(output_dir/'v3_data_source_health.json',source_health); write_json(output_dir/'v3_model_lock_verification.json',model_lock); write_json(output_dir/'v3_next_gate.json',{"status":"V7_CORE_DATA_READY","investment_use_allowed":False})
    write_json(root/'data_cache/latest/v3_source_observations.json',obs); write_json(root/'data_cache'/f'{as_of.year:04d}'/f'{as_of.month:02d}'/as_of.isoformat()/'v3_source_observations.json',obs)
    return summary
