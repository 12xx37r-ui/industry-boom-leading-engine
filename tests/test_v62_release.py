from __future__ import annotations
import json, shutil, tempfile, unittest
from pathlib import Path
from ible.v61_shadow import run_v61, verify_policy_lock
ROOT=Path(__file__).resolve().parents[1]

class V7ReleaseTests(unittest.TestCase):
    def test_release_manifest_below_90(self):
        m=json.loads((ROOT/'config/release_manifest.json').read_text(encoding='utf-8'))
        files=m['files']
        self.assertLess(len(files),90)
        self.assertEqual(len(files),len(set(files)))
        self.assertEqual([], [f for f in files if not (ROOT/f).is_file()])
        self.assertEqual(m['file_count'],len(files))

    def test_policy_lock(self):
        r=verify_policy_lock(ROOT)
        self.assertEqual(r['status'],'POLICY_LOCK_VERIFIED')
        self.assertEqual(r['policy_id'],'V7_COMPLETE_OPERATIONAL_POLICY_V1')

    def test_all_four_missing_layers_are_filled(self):
        with tempfile.TemporaryDirectory() as tmp:
            work=Path(tmp)/'repo'
            shutil.copytree(ROOT,work,ignore=shutil.ignore_patterns('outputs','__pycache__','*.pyc'))
            fixture=json.loads((work/'fixture/v61_upstream_fixture.json').read_text(encoding='utf-8'))
            v50=work/'fixture/runtime_v50'; v60=work/'fixture/runtime_v60'
            v50.mkdir(parents=True); v60.mkdir(parents=True)
            (v50/'v50_current_monthly_snapshot.json').write_text(json.dumps(fixture['v50_current_monthly_snapshot'],ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            (v50/'v50_run_summary.json').write_text(json.dumps(fixture['v50_run_summary'],ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            (v60/'v60_run_summary.json').write_text(json.dumps(fixture['v60_run_summary'],ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            s=run_v61(work,work/'outputs/v70','2026-08-04',v50,v60)
            snap=json.loads((work/'outputs/v70/v70_current_operational_snapshot.json').read_text(encoding='utf-8'))
            self.assertEqual(s['theme_count'],50)
            self.assertEqual(s['boom_score_count'],50)
            self.assertEqual(s['three_month_change_count'],50)
            self.assertEqual(s['company_mapping_count'],50)
            self.assertTrue(all(r['public_interest_score'] is not None and r['boom_score'] is not None and r['score_change_3m'] is not None and len(r['companies'])>0 for r in snap['decisions']))

    def test_queries_cover_interest_sources(self):
        q=json.loads((ROOT/'config/v3_theme_queries.json').read_text(encoding='utf-8'))
        self.assertEqual(len(q['themes']),50)
        self.assertTrue(all(r.get('gdelt_query') and r.get('wikipedia_titles') for r in q['themes']))

    def test_workflow_and_gas(self):
        w=(ROOT/'.github/workflows/run_v50_final_validator.yml').read_text(encoding='utf-8')
        self.assertIn('Industry Boom V7.0 Complete Engine',w)
        self.assertIn('outputs/v70_final_engine',w)
        self.assertNotIn('sec.gov',w.lower())
        self.assertNotIn('fmp',w.lower())
        c=(ROOT/'google_apps_script/V7Code.gs').read_text(encoding='utf-8')
        h=(ROOT/'google_apps_script/Index.html').read_text(encoding='utf-8')
        self.assertIn('v70_dashboard_payload.json',c)
        self.assertIn('public_interest_score',c)
        self.assertIn('Hidden Opportunity',h)
        self.assertNotIn('대중 관심도</span><strong>미연결',h)

if __name__=='__main__': unittest.main()
