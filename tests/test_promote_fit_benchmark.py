import copy,hashlib,unittest
from unittest.mock import patch
from src import promote_fit_benchmark as promote
from src.evidence_fit import input_hash,scorer_revision,EvidenceVerdict,build_inventory

class CalibrationPublication(unittest.TestCase):
    def setUp(self):
        self.rules={'evidence_required':True,'fit_policy_version':'evidence-v1','comp_floor_usd':250000}
        self.profile={'experience':[{'achievements':[{'details':'Delivered shared software platform programs.'}]}]}
        self.job={'source':'test','company':'Example','id':'1','title':'Principal TPM'}
        self.key='test:Example:1';self.jd='Annual USD base salary $300,000. Program leadership.'
        self.fingerprint=input_hash(self.profile,self.job,self.jd,self.rules,promote.fit._fit_model())
        self.report={'scorer_revision':scorer_revision(),'cases':[{'job_key':self.key,'passed':True,'input_hash':self.fingerprint,'career_fit':'strong','recommendation':'strong'}]}
        self.config={'cases':[{'job_key':self.key,'jd_hash':hashlib.sha256(self.jd.encode()).hexdigest()}]}
        self.audit={'input_hash':self.fingerprint,'inventory':build_inventory(self.profile,self.rules),'extraction':{},'independent_review':{}}
    def execute(self,report=None,jd=None,audit=None):
        return promote.prepare(report or self.report,self.config,self.profile,self.rules,[self.job],{},fetch=lambda job:self.jd if jd is None else jd,load=lambda key:self.audit if audit is None else audit)
    def test_unresolved_benchmark_cannot_activate_policy(self):
        report=copy.deepcopy(self.report);report['cases'][0]['passed']=False
        with self.assertRaisesRegex(ValueError,'must pass'):self.execute(report=report)
    def test_changed_job_cannot_publish_prior_assessment(self):
        with self.assertRaisesRegex(ValueError,'description changed'):self.execute(jd=self.jd+' New requirement.')
    def test_wrong_private_assessment_cannot_publish(self):
        audit=copy.deepcopy(self.audit);audit['input_hash']='outdated'
        with self.assertRaisesRegex(ValueError,'current inputs'):self.execute(audit=audit)
    def test_current_reproducible_audit_can_be_published(self):
        verdict=EvidenceVerdict('strong','Reviewed','none',{'career_fit':'strong','engine_version':'evidence-v1'}, {})
        with patch('src.promote_fit_benchmark.review_result',return_value=verdict):
            scores,policy=self.execute()
        self.assertEqual(scores[self.key]['recommendation'],'strong');self.assertTrue(policy['evidence_required'])
        self.assertNotIn('inventory',scores[self.key]);self.assertNotIn('extraction',scores[self.key])
