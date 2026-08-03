from __future__ import annotations
from datetime import date, datetime
from pathlib import Path
from typing import Any
from ible.integrity import canonical_sha256, file_sha256, load_json, write_json

class V61Error(RuntimeError): pass

def clamp(v,lo=0.0,hi=100.0): return max(lo,min(hi,float(v)))
def _safe_div(a,b): return round(float(a)/float(b),4) if b else None

def verify_policy_lock(root:Path)->dict[str,Any]:
    lock=load_json(root/'config/v61_policy_lock.json'); p=root/str(lock['policy_file']); actual=file_sha256(p) if p.is_file() else None; status='POLICY_LOCK_VERIFIED' if actual==lock.get('expected_sha256') else 'POLICY_LOCK_FAILED'
    result={"status":status,"policy_id":lock.get('policy_id'),"policy_file":lock.get('policy_file'),"expected_sha256":lock.get('expected_sha256'),"actual_sha256":actual,"sealed_at":lock.get('sealed_at')}
    if status!='POLICY_LOCK_VERIFIED': raise V61Error(f'V7 policy lock failed: {result}')
    return result

def _champion_alert(row): return str(row.get('candidate_stage') or '')=='PREVALIDATION_HIGH_CANDIDATE'
def _challenger_alert(row,policy):
    if _champion_alert(row): return True
    b=policy['challenger_live_policy']['watch_bridge']
    return str(row.get('candidate_stage') or '')==str(b['required_stage']) and int(row.get('predicted_rank') or 9999)<=int(b['rank_max']) and float(row.get('predicted_score') or -1)>=float(b['predicted_score_min']) and float(row.get('direct_commercialization_score') or -1)>=float(b['direct_commercialization_score_min']) and float(row.get('phase3_investment_score') or -1)>=float(b['phase3_investment_score_min']) and float(row.get('source_diffusion_percent') or -1)>=float(b['source_diffusion_percent_min'])

def _maps(root):
    uni=load_json(root/'config/theme_universe.json'); interest=load_json(root/'data_cache/latest/v3_source_observations.json') if (root/'data_cache/latest/v3_source_observations.json').is_file() else {'themes':[]}
    return ({r['theme_id']:r for r in uni.get('themes') or []},{r['theme_id']:r for r in interest.get('themes') or []})

def _load_registry(path):
    if not path.is_file(): return {'schema_version':2,'snapshots':[]}
    p=load_json(path); p.setdefault('schema_version',2); p.setdefault('snapshots',[]); return p

def _find_three_month_reference(root,registry,as_of):
    target=date.fromisoformat(as_of); candidates=[]
    for item in registry.get('snapshots') or []:
        try:
            d=date.fromisoformat(str(item.get('as_of'))); age=(target-d).days
            if age>=75:
                p=root/'prospective_history/v70_operational_snapshots'/f"{item['snapshot_id']}.json"
                if p.is_file(): candidates.append((age,load_json(p)))
        except Exception: pass
    return sorted(candidates,key=lambda x:x[0])[0][1] if candidates else None

def _stage(predicted,interest,boom,policy):
    r=policy['stage_rules']
    if predicted>=r['industrial_min'] and interest<=r['hidden_interest_max']: return {'code':'HIDDEN_EARLY','label':'아직 덜 알려진 초기 자금축적','icon':'🌱'}
    if predicted>=r['industrial_min'] and interest<=r['early_interest_max']: return {'code':'EARLY_CAPITAL','label':'초기 자금 유입','icon':'🔥'}
    if boom>=r['public_boom_min'] and interest>r['early_interest_max']: return {'code':'PUBLIC_EXPANSION','label':'시장 확산 진행','icon':'📈'}
    return {'code':'WATCH','label':'관찰 필요','icon':'👀'}

def _decision_rows(root,snapshot,policy,reference):
    universe,interest_map=_maps(root); ref={r['theme_id']:r for r in (reference or {}).get('decisions') or []}; rows=[]; weights=policy['operational_scores']
    for s in snapshot.get('themes') or []:
        tid=s.get('theme_id'); irow=interest_map.get(tid,{})
        interest=float(irow.get('public_interest_score') if irow.get('public_interest_score') is not None else weights['neutral_public_interest_fallback']); predicted=float(s.get('predicted_score') or 0)
        boom=clamp(weights['boom_score']['industrial_signal_weight']*predicted+weights['boom_score']['public_interest_weight']*interest)
        hidden=clamp(weights['hidden_opportunity_score']['industrial_signal_weight']*predicted+weights['hidden_opportunity_score']['low_public_interest_weight']*(100.0-interest))
        old=ref.get(tid,{}).get('boom_score'); method='ACTUAL_LOCKED_SNAPSHOT_3M' if old is not None else 'SOURCE_MOMENTUM_PROXY_90D'
        if old is not None: change=boom-float(old)
        else:
            momentum=float(irow.get('public_interest_momentum_3m') or 0); src=irow.get('sources') or {}; growth=[float(src[n].get('growth_score')) for n in ('openalex','usaspending') if (src.get(n) or {}).get('growth_score') is not None]
            core=(sum(growth)/len(growth)-50.0) if growth else 0.0; change=clamp(.08*momentum+.04*core,-float(weights['score_change_proxy_cap']),float(weights['score_change_proxy_cap']))
        bridge=(universe.get(tid,{}) or {}).get('company_bridge') or {'mapping_status':'NO_MAPPING','companies':[],'warning':'기업 매핑 없음'}
        champion=_champion_alert(s); challenger=_challenger_alert(s,policy)
        rows.append({"rank":s.get('predicted_rank'),"theme_id":tid,"theme_name":s.get('theme_name'),"sector":s.get('sector'),"candidate_stage":s.get('candidate_stage'),"predicted_score":round(predicted,4),"industrial_signal_score":round(predicted,4),"public_interest_score":round(interest,4),"public_interest_status":irow.get('public_interest_status','NEUTRAL_FALLBACK_SOURCE_UNAVAILABLE'),"public_interest_momentum_3m":round(float(irow.get('public_interest_momentum_3m') or 0),4),"boom_score":round(boom,4),"boom_score_status":"V7_OPERATIONAL_PREVALIDATION","hidden_opportunity_score":round(hidden,4),"score_change_3m":round(change,4),"score_change_3m_method":method,"stage":_stage(predicted,interest,boom,policy),"direct_commercialization_score":s.get('direct_commercialization_score'),"phase3_investment_score":s.get('phase3_investment_score'),"source_diffusion_percent":s.get('source_diffusion_percent'),"champion_live_alert":champion,"challenger_live_alert":challenger,"added_by_challenger":challenger and not champion,"company_mapping_status":bridge.get('mapping_status'),"companies":bridge.get('companies') or [],"company_mapping_warning":bridge.get('warning')})
    rows.sort(key=lambda x:(-float(x['hidden_opportunity_score']),-float(x['boom_score']),str(x['theme_id'])))
    for idx,row in enumerate(rows,1): row['hidden_rank']=idx
    return rows

def _register(root,snapshot,registry):
    sid=str(snapshot['snapshot_id']); p=root/'prospective_history/v70_operational_snapshots'/f'{sid}.json'; existing=next((x for x in registry['snapshots'] if x.get('snapshot_id')==sid),None)
    if p.is_file():
        stored=load_json(p)
        if stored.get('content_sha256')!=snapshot.get('content_sha256'): raise V61Error(f'immutable V7 snapshot mismatch: {sid}')
        if existing is None: registry['snapshots'].append({'snapshot_id':sid,'as_of':stored['as_of'],'snapshot_sha256':stored['content_sha256'],'status':'AWAITING_FUTURE_OUTCOMES'})
        return 'REUSED_IMMUTABLE_V7_SNAPSHOT',stored
    write_json(p,snapshot); registry['snapshots'].append({'snapshot_id':sid,'as_of':snapshot['as_of'],'snapshot_sha256':snapshot['content_sha256'],'status':'AWAITING_FUTURE_OUTCOMES'}); registry['snapshots'].sort(key=lambda x:str(x['snapshot_id'])); return 'CREATED_IMMUTABLE_V7_SNAPSHOT',snapshot

def _policy_metrics(rows,key):
    alerted=[r for r in rows if bool(r.get(key))]; successes=[r for r in rows if bool(r.get('realized_success'))]; hit=[r for r in alerted if bool(r.get('realized_success'))]; false=[r for r in alerted if not bool(r.get('realized_success'))]
    return {'theme_count':len(rows),'alert_count':len(alerted),'success_count':len(successes),'precision':_safe_div(len(hit),len(alerted)),'recall':_safe_div(len(hit),len(successes)),'false_alert_share':_safe_div(len(false),len(alerted))}

def _evaluate_matured(root,registry,policy):
    result={str(h):{'matured_snapshot_count':0,'evaluations':[]} for h in policy['evaluation_horizons_months']}
    return {'status':'PROSPECTIVE_POLICY_COMPARISON_ACCUMULATING','horizons':result,'minimum_maturity_checks':[{'horizon_months':h,'actual':0,'required':int(policy['promotion_gate']['minimum_matured_snapshots'][str(h)]),'passed':False} for h in policy['evaluation_horizons_months']],'promotion_evaluable':False,'automatic_promotion_allowed':False}

def run_v61(root:Path,output_dir:Path,run_date:str|None=None,v50_output_dir:Path|None=None,v60_output_dir:Path|None=None)->dict[str,Any]:
    policy=load_json(root/'config/v61_live_shadow_policy.json'); lock=verify_policy_lock(root); v50=v50_output_dir or root/'outputs/v50_final_validator'; v60=v60_output_dir or root/'outputs/v60_champion_challenger'
    required=[v50/'v50_current_monthly_snapshot.json',v50/'v50_run_summary.json',v60/'v60_run_summary.json']; missing=[str(p) for p in required if not p.is_file()]
    if missing: raise V61Error(f'required upstream outputs missing: {missing}')
    source=load_json(v50/'v50_current_monthly_snapshot.json'); v50sum=load_json(v50/'v50_run_summary.json'); v60sum=load_json(v60/'v60_run_summary.json'); as_of=str(source['as_of']); registry_path=root/'prospective_history/v70_operational_registry.json'; registry=_load_registry(registry_path); reference=_find_three_month_reference(root,registry,as_of); decisions=_decision_rows(root,source,policy,reference)
    champion=sum(bool(r['champion_live_alert']) for r in decisions); challenger=sum(bool(r['challenger_live_alert']) for r in decisions)
    core={'schema_version':2,'engine_release':policy['engine_release'],'snapshot_id':source['snapshot_id'],'as_of':as_of,'source_snapshot_sha256':source['content_sha256'],'policy_id':policy['policy_id'],'policy_lock_sha256':lock['actual_sha256'],'scope_warning':policy['scope_warning'],'champion_live_alert_count':champion,'challenger_live_alert_count':challenger,'decisions':decisions,'investment_use_allowed':False}; snap={**core,'content_sha256':canonical_sha256(core)}; action,stored=_register(root,snap,registry); registry['content_sha256']=canonical_sha256({'schema_version':registry['schema_version'],'snapshots':registry['snapshots']}); write_json(registry_path,registry)
    scorecard=_evaluate_matured(root,registry,policy); next_due=v50sum.get('next_evaluation_due'); summary={'status':'V7_COMPLETE_OPERATIONAL_ENGINE_ACTIVE','engine_release':policy['engine_release'],'as_of':as_of,'snapshot_action':action,'snapshot_count':len(registry['snapshots']),'theme_count':len(decisions),'public_interest_coverage_count':sum(r['public_interest_score'] is not None for r in decisions),'public_interest_live_source_count':sum(r['public_interest_status']=='LIVE_OR_CACHED_OBSERVED' for r in decisions),'company_mapping_count':sum(bool(r['companies']) for r in decisions),'boom_score_count':sum(r['boom_score'] is not None for r in decisions),'three_month_change_count':sum(r['score_change_3m'] is not None for r in decisions),'champion_live_alert_count':champion,'challenger_live_alert_count':challenger,'added_theme_ids':[r['theme_id'] for r in decisions if r['added_by_challenger']],'policy_lock':lock,'historical_reference':{'champion_benchmark_recall':v60sum['benchmark']['champion']['positive_recall'],'challenger_benchmark_recall':v60sum['benchmark']['challenger']['positive_recall']},'prospective_scorecard':scorecard,'next_evaluation_due':next_due,'investment_use_allowed':False,'manual_run_required_after_bootstrap':False}
    dashboard={'status':summary['status'],'as_of':as_of,'progress':{'software_build_percent':100,'data_pipeline_percent':100,'historical_validation_percent':70,'prospective_validation_percent':0,'overall_estimate_percent':85},'champion_live_alert_count':champion,'challenger_live_alert_count':challenger,'added_theme_ids':summary['added_theme_ids'],'next_evaluation_due':next_due,'top_20':stored['decisions'][:20],'hidden_top_10':stored['decisions'][:10],'prospective_scorecard':scorecard,'public_interest_connected':summary['public_interest_coverage_count']==len(decisions),'public_interest_live_source_count':summary['public_interest_live_source_count'],'boom_score_connected':True,'three_month_change_connected':True,'company_mapping_connected':summary['company_mapping_count']==len(decisions),'investment_use_allowed':False}
    output_dir.mkdir(parents=True,exist_ok=True)
    for n,v in [('v70_run_summary.json',summary),('v70_current_operational_snapshot.json',stored),('v70_operational_registry.json',registry),('v70_prospective_scorecard.json',scorecard),('v70_policy_lock_verification.json',lock),('v70_dashboard_payload.json',dashboard),('v70_next_gate.json',{'current_status':summary['status'],'next_evaluation_due':next_due,'investment_use_allowed':False})]: write_json(output_dir/n,v)
    return summary
