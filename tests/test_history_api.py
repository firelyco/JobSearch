import copy
import os
import time
import unittest
from unittest.mock import patch
from history_service.api import history

class HistorySecurity(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'HISTORY_SESSION_SECRET':'test-only-signing-secret-longer-than-32-characters'})
        self.env.start();self.addCleanup(self.env.stop)

    def test_session_expiration_and_tampering(self):
        token=history.issue_session(now=1000)
        self.assertTrue(history.verify_session(token,now=1001))
        self.assertFalse(history.verify_session(token,now=1000+history.SESSION_SECONDS))
        self.assertFalse(history.verify_session(token+'tampered',now=1001))
        self.assertFalse(history.verify_session('invalid',now=1001))

    def test_invalid_records_fail_before_database_writes(self):
        entry={'status':'applied','application':{'title':'Example','url':'https://example.com'},'updated_at':0,'mutation_id':'example','deleted':False}
        self.assertEqual(history.validate_records({'example':entry}),{'example':entry})
        for field,value in [('status','invalid'),('updated_at',True),('updated_at',int(time.time()*1000)+600000),('mutation_id',''),('deleted','true')]:
            bad=copy.deepcopy(entry);bad[field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):history.validate_records({'example':bad})
        bad=copy.deepcopy(entry);bad['application']['url']='javascript:alert(1)'
        with self.assertRaises(ValueError):history.validate_records({'example':bad})

    def test_old_application_dates_are_permanent_records(self):
        entry={'status':'applied','application':{'applied_at':'2020-01-01T12:00:00Z'},'updated_at':0,'mutation_id':'legacy-import','deleted':False}
        self.assertEqual(history.validate_records({'example':entry})['example']['application']['applied_at'],'2020-01-01T12:00:00Z')

    def test_empty_and_oversized_payloads(self):
        self.assertEqual(history.validate_records({}),{})
        for invalid in [None,[],{'x':{'status':'applied'}},dict.fromkeys(range(3001),{})]:
            with self.assertRaises(ValueError):history.validate_records(invalid)
