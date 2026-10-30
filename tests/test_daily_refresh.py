import os,json,unittest
from datetime import datetime,timezone
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch,Mock
from src import daily_refresh as d

class DailyRefreshTests(unittest.TestCase):
    def now(self,value='2026-10-30T14:35:00+00:00'):return datetime.fromisoformat(value)
    def test_missed_refresh_after_one_is_due(self):
        self.assertTrue(d.needs_daily_refresh({'polled_at':'2026-10-29T18:30:43Z'},[],self.now())[0])
    def test_not_due_before_one_including_dst_change(self):
        for at in ('2026-10-30T04:59:00+00:00','2026-11-02T05:59:00+00:00'):
            self.assertFalse(d.needs_daily_refresh({},[],self.now(at))[0])
        self.assertTrue(d.needs_daily_refresh({},[],self.now('2026-11-02T06:00:00+00:00'))[0])
    def test_successful_today_or_active_worker_never_dispatches_duplicate(self):
        self.assertFalse(d.needs_daily_refresh({'polled_at':'2026-10-30T10:15:00Z'},[],self.now())[0])
        for status in d.ACTIVE:self.assertFalse(d.needs_daily_refresh({},[{'status':status}],self.now())[0])
    def test_failed_worker_has_bounded_cooldown(self):
        recent={'status':'completed','conclusion':'failure','updatedAt':'2026-10-30T14:30:00Z'}
        self.assertFalse(d.needs_daily_refresh({},[recent],self.now())[0])
        self.assertTrue(d.needs_daily_refresh({},[{**recent,'updatedAt':'2026-10-30T14:00:00Z'}],self.now())[0])
    def test_local_day_not_utc_day_controls_daily_gate(self):
        self.assertFalse(d.needs_daily_refresh({'polled_at':'2026-10-31T01:00:00Z'},[],self.now('2026-10-31T02:00:00+00:00'))[0])
    def test_gate_does_not_block_explicit_manual_runs(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);(root/'docs').mkdir();(root/'docs/meta.json').write_text(json.dumps({'polled_at':datetime.now(timezone.utc).isoformat()}));out=root/'output'
            with patch.object(d,'ROOT',root),patch.dict(os.environ,{'GITHUB_OUTPUT':str(out),'GITHUB_EVENT_NAME':'schedule','DAILY_ONLY':'false'}),patch('sys.argv',['daily_refresh','--gate']):d.main()
            self.assertEqual(out.read_text().strip(),'run=false')
            out.write_text('')
            with patch.object(d,'ROOT',root),patch.dict(os.environ,{'GITHUB_OUTPUT':str(out),'GITHUB_EVENT_NAME':'workflow_dispatch','DAILY_ONLY':'false'}),patch('sys.argv',['daily_refresh','--gate']):d.main()
            self.assertEqual(out.read_text().strip(),'run=true')
