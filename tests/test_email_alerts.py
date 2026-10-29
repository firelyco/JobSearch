import copy,json,os,unittest
from datetime import datetime,timezone
from email.message import EmailMessage
from unittest.mock import Mock,patch
from src import email_alerts as e
from history_service.api import history

NOW=datetime(2026,10,29,10,tzinfo=timezone.utc)
HTML='''<html><body><table><tr><td><a href="https://www.linkedin.com/comm/jobs/view/4481477507/?trackingId=private">Head of Technical Program Management</a><p>LILT · San Francisco, CA (Remote)</p><a href="https://jobs.ashbyhq.com/lilt/1234?email=private%40example.com&amp;utm_source=email">Apply now</a></td></tr><tr><td><a href="https://www.linkedin.com/jobs/view/4474627516?trk=email">Vice President, Enterprise PMO</a><p>Workiva · United States (Remote)</p></td></tr></table><p>This was sent to private@example.com</p></body></html>'''

def message(body=HTML,sender='LinkedIn <jobalerts-noreply@linkedin.com>'):
    msg=EmailMessage();msg['From']=sender;msg['To']='private@example.com';msg['Subject']='Your daily job alert';msg.set_content('Your jobs are below');msg.add_alternative(body,subtype='html');return msg.as_bytes()

class FakeIMAP:
    def __init__(self,messages=None,fail_uid=None):self.messages=messages or {b'1':message()};self.calls=[];self.fail_uid=fail_uid
    def login(self,*args):self.calls.append(('login',));return 'OK',[]
    def select(self,folder,readonly):self.calls.append(('select',folder,readonly));return 'OK',[]
    def response(self,name):return name,[b'1234']
    def logout(self):self.calls.append(('logout',));return 'BYE',[]
    def uid(self,command,*args):
        self.calls.append((command,*args))
        if command=='search':return 'OK',[b' '.join(self.messages)]
        uid,query=args
        if uid==self.fail_uid:return 'NO',[]
        raw=self.messages[uid]
        if 'HEADER.FIELDS' in query:
            return 'OK',[(b'1 (RFC822.SIZE '+str(len(raw)).encode()+b')',b'From: jobalerts-noreply@linkedin.com\r\nSubject: Your job alert\r\n\r\n')]
        return 'OK',[(b'1 (BODY[]',raw)]

class EmailParsing(unittest.TestCase):
    def test_extracts_multiple_jobs_and_discards_tracking_and_private_mail(self):
        rows=e.parse_message(message());self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['title'],'Head of Technical Program Management')
        self.assertEqual(rows[0]['company'],'LILT');self.assertIn('Remote',rows[0]['location'])
        self.assertEqual(rows[1]['company'],'Workiva')
        self.assertEqual(rows[0]['employer_urls'],['https://jobs.ashbyhq.com/lilt/1234'])
        serialized=json.dumps(rows);self.assertNotIn('private',serialized);self.assertNotIn('trackingId',serialized)
    def test_alternative_parts_and_duplicate_buttons_dedupe_by_job_id(self):
        msg=EmailMessage();msg['From']='jobs-noreply@linkedin.com';msg.set_content('Head of TPM\nhttps://www.linkedin.com/jobs/view/4481477507/?trk=email')
        msg.add_alternative(HTML,subtype='html')
        rows=e.parse_message(msg.as_bytes());self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['company'],'LILT')
    def test_whole_clickable_card_uses_title_line_and_company(self):
        body='<a href="https://www.linkedin.com/jobs/view/4481477507"><div>Director, Program Management</div><div>Expedia Group · San Jose, CA (On-site)</div><div>$231K-$324K / year</div></a>'
        row=e.parse_html(body)[0]
        self.assertEqual(row['title'],'Director, Program Management')
        self.assertEqual(row['company'],'Expedia Group')
        self.assertNotIn('$',row['title'])
    def test_generic_hiring_cta_is_not_a_job_title(self):
        row=e.parse_html('<a href="https://www.linkedin.com/jobs/view/4481477507">This company is actively hiring</a>')[0]
        self.assertEqual(row['title'],'')

    def test_plaintext_generic_cta_cannot_override_empty_html_title(self):
        msg=EmailMessage();msg['From']='jobs-noreply@linkedin.com'
        msg.set_content('This company is actively hiring\nhttps://www.linkedin.com/jobs/view/4481477507')
        self.assertEqual(e.parse_message(msg.as_bytes())[0]['title'],'')

    def test_wrong_sender_and_lookalike_links_are_not_imported(self):
        self.assertEqual(e.parse_message(message(sender='LinkedIn <jobs@linkedin.com.evil.com>')),[])
        for url in ('javascript:alert(1)','https://linkedin.com.evil.com/jobs/view/4481477507','https://linkedin.com@evil.com/jobs/view/4481477507','https://127.0.0.1/jobs/view/4481477507'):
            self.assertIsNone(e.linkedin_id(url))
    def test_attachments_and_scripts_are_not_read_as_alert_content(self):
        msg=EmailMessage();msg['From']='jobs-noreply@linkedin.com';msg.set_content('No jobs here')
        msg.add_attachment(HTML.encode(),maintype='text',subtype='html',filename='private.html')
        self.assertEqual(e.parse_message(msg.as_bytes()),[])
        self.assertEqual(e.parse_html('<script>'+HTML+'</script>'),[])
    def test_plaintext_and_current_job_id_links_are_supported(self):
        self.assertEqual(e.linkedin_id('https://www.linkedin.com/jobs/search/?currentJobId=4481477507'),'4481477507')
        msg=EmailMessage();msg['From']='jobs-noreply@linkedin.com';msg.set_content('Director, Technical Program Management\nhttps://www.linkedin.com/jobs/view/4481477507/')
        self.assertEqual(e.parse_message(msg.as_bytes())[0]['title'],'Director, Technical Program Management')

class YahooReadOnly(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'YAHOO_EMAIL':'private@example.com','YAHOO_APP_PASSWORD':'test-only-app-password'})
        self.env.start();self.addCleanup(self.env.stop)
    def test_readonly_peek_and_repeated_scans_never_refetch_processed_messages(self):
        state={'schema':1,'messages':{},'leads':{}};client=FakeIMAP()
        first=e.fetch_yahoo(state,now=NOW,factory=lambda *a,**k:client)
        self.assertEqual(first['status'],'ok');self.assertEqual(len(state['leads']),2)
        self.assertIn(('select','"INBOX"',True),client.calls)
        self.assertTrue(all('PEEK' in c[-1] for c in client.calls if c[0]=='fetch'))
        second=e.fetch_yahoo(state,now=NOW,factory=lambda *a,**k:FakeIMAP())
        self.assertEqual(second['messages_checked'],0)
        self.assertEqual(len(state['leads']),2)
        self.assertFalse(any(c[0] in {'store','copy','move','expunge','append'} for c in client.calls))
    def test_new_parser_reimports_once_and_retires_old_uid_warning(self):
        import hashlib
        old='v1:'+hashlib.sha256(b'INBOX:1234').hexdigest()[:16]+':1'
        state={'schema':1,'messages':{old:NOW.isoformat()},'unparsed':{old:NOW.isoformat()},'leads':{}}
        first=e.fetch_yahoo(state,now=NOW,factory=lambda *a,**k:FakeIMAP())
        self.assertEqual(first['messages_checked'],1)
        self.assertEqual(first['unresolved_alert_messages'],0)
        second=e.fetch_yahoo(state,now=NOW,factory=lambda *a,**k:FakeIMAP())
        self.assertEqual(second['messages_checked'],0)

    def test_caps_defer_without_marking_unread_messages_processed(self):
        state={'schema':1,'messages':{},'leads':{}}
        result=e.fetch_yahoo(state,now=NOW,factory=lambda *a,**k:FakeIMAP({b'1':message(),b'2':message()}),max_messages=1)
        self.assertEqual(result['status'],'partial');self.assertEqual(result['messages_deferred'],1)
        self.assertEqual(len(state['messages']),1)
    def test_failures_remain_unprocessed_and_retry_next_scan(self):
        state={'schema':1,'messages':{},'leads':{}}
        result=e.fetch_yahoo(state,now=NOW,factory=lambda *a,**k:FakeIMAP(fail_uid=b'1'))
        self.assertEqual(result['failed_messages'],1);self.assertFalse(state['messages'])
        self.assertNotIn('last_success_at',state)
    def test_unrecognized_alert_format_is_persistently_visible(self):
        state={'schema':1,'messages':{},'leads':{}}
        factory=lambda *a,**k:FakeIMAP({b'1':message('<p>View your job recommendations</p>')})
        first=e.fetch_yahoo(state,now=NOW,factory=factory)
        second=e.fetch_yahoo(state,now=NOW,factory=factory)
        self.assertEqual(first['unresolved_alert_messages'],1)
        self.assertEqual(second['status'],'partial');self.assertEqual(second['unresolved_alert_messages'],1)
    def test_sign_in_failure_exposes_stage_without_provider_text_or_secret(self):
        client=FakeIMAP();client.login=Mock(side_effect=e.imaplib.IMAP4.error('sensitive provider text'))
        with self.assertRaises(e.MailAccessError) as failure:
            e.fetch_yahoo({'schema':1,'messages':{},'leads':{}},now=NOW,factory=lambda *a,**k:client)
        self.assertEqual(failure.exception.phase,'sign_in')
        self.assertNotIn('sensitive',str(failure.exception))
        self.assertIn(('logout',),client.calls)

    def test_password_is_passed_exactly_as_saved(self):
        password='leading and internal spaces '
        client=FakeIMAP();client.login=Mock(return_value=('OK',[]))
        with patch.dict(os.environ,{'YAHOO_APP_PASSWORD':password}):
            e.fetch_yahoo({'schema':1,'messages':{},'leads':{}},now=NOW,factory=lambda *a,**k:client,max_messages=0)
        client.login.assert_called_once_with('private@example.com',password)
    def test_only_allowlisted_yahoo_diagnostics_are_exposed(self):
        failure=e.MailAccessError('sign_in',e.imaplib.IMAP4.error('[AUTHENTICATIONFAILED] user private@example.com secret text'))
        self.assertEqual(failure.response_code,'AUTHENTICATIONFAILED')
        self.assertEqual(failure.category,'authentication_rejected')
        self.assertNotIn('private',str(failure.__dict__))
        self.assertEqual(e.MailAccessError('sign_in',Exception('Please use an app password')).category,'app_password_required')
        self.assertEqual(e.MailAccessError('sign_in',Exception('Too many login attempts')).category,'temporarily_blocked')

    def test_connection_only_never_fetches_headers_or_bodies_or_saves_state(self):
        client=FakeIMAP()
        state={'schema':1,'messages':{},'leads':{}}
        result=e.fetch_yahoo(state,now=NOW,factory=lambda *a,**k:client,max_messages=0)
        self.assertEqual(result['messages_deferred'],1)
        self.assertFalse(any(call[0]=='fetch' for call in client.calls))
        self.assertEqual(state['messages'],{})

    def test_no_credentials_or_private_state_failure_never_opens_mail(self):
        with patch.dict(os.environ,{'YAHOO_EMAIL':'','YAHOO_APP_PASSWORD':''}),patch.object(e,'load_state') as load:
            _,health,_,_=e.prepare([],{})
            self.assertEqual(health['status'],'not_configured');load.assert_not_called()
        with patch.object(e,'load_state',side_effect=RuntimeError('private backend unavailable')),patch.object(e,'fetch_yahoo') as fetch:
            _,health,_,_=e.prepare([],{})
            self.assertEqual(health['status'],'failed');fetch.assert_not_called()

class Reconciliation(unittest.TestCase):
    def test_exact_match_is_distinct_from_title_similarity_and_missing(self):
        lead=e.parse_message(message())[0]
        job={'source':'ashby','company':'LILT','id':'1234','title':lead['title'],'location':'Remote, US','url':lead['employer_urls'][0],'posted_at':'2026-10-20'}
        from src import fit_backfill as b
        score={'recommendation':'strong','validated':True,'context_hash':'ctx','scorer_revision':b.scorer_revision(),'job_snapshot_hash':b.snapshot_hash(job)}
        state={'leads':{'4481477507':lead}}
        report=e.report(state,{'status':'ok'},[job],[job],{e.job_key(job):score},{'context_hash':'ctx'},{},now=NOW)
        self.assertEqual(report['rows'][0]['collection'],'tracked');self.assertEqual(report['rows'][0]['verdicts'],['Strong'])
        state['leads']['4481477507']['employer_urls']=[]
        report=e.report(state,{'status':'ok'},[job],[job],{}, {},{},now=NOW)
        self.assertEqual(report['rows'][0]['collection'],'possible_match')
        report=e.report(state,{'status':'ok'},[],[],{}, {},{},now=NOW)
        self.assertEqual(report['rows'][0]['collection'],'missing');self.assertFalse(report['rows'][0]['matches'])
    def test_scan_failure_retains_previous_public_job_references_without_raw_mail(self):
        old={'rows':[e.parse_message(message())[0]],'last_success_at':'yesterday'}
        report=e.report(None,{'status':'failed'},[],[],{}, {},{},previous=old,now=NOW)
        self.assertEqual(len(report['rows']),1);self.assertEqual(report['last_success_at'],'yesterday')
        self.assertNotIn('private@example.com',json.dumps(report))
    def test_completed_scores_are_not_rescored_during_alert_reconciliation(self):
        with patch('src.llm_client.build_client',side_effect=AssertionError('No AI call')):
            e.report({'leads':{}},{'status':'ok'},[],[],{}, {},{},now=NOW)

class PrivateEmailState(unittest.TestCase):
    def test_state_rejects_raw_mail_recipient_fields_and_credentials(self):
        good={'schema':1,'messages':{'v1:hash:1':NOW.isoformat()},'leads':{'4481477507':e.parse_message(message())[0]}}
        self.assertEqual(history.validate_email_state(good),good)
        for field in ('body','recipient','password','subject','headers'):
            bad=copy.deepcopy(good);bad[field]='private'
            with self.assertRaises(ValueError):history.validate_email_state(bad)
            bad=copy.deepcopy(good);bad['leads']['4481477507'][field]='private'
            with self.assertRaises(ValueError):history.validate_email_state(bad)

class EmailDiscoveryPreparation(unittest.TestCase):
    def test_existing_posting_is_not_queried_and_missing_leads_are_bounded(self):
        leads=e.parse_message(message());state={'schema':1,'messages':{},'leads':{r['linkedin_id']:r for r in leads}}
        previous=[{'url':leads[0]['employer_urls'][0]}]
        with patch.dict(os.environ,{'YAHOO_EMAIL':'private@example.com','YAHOO_APP_PASSWORD':'test-only','BRAVE_SEARCH_API_KEY':'test-only'}), \
             patch.object(e,'load_state',return_value=state),patch.object(e,'fetch_yahoo',return_value={'status':'ok'}),patch.object(e,'save_state'):
            saved,health,urls,queries=e.prepare(previous,{'max_resolution_queries':1},now=NOW)
            self.assertEqual(len(queries),1);self.assertIn('Workiva',queries[0]);self.assertNotIn('private',queries[0])
            self.assertEqual(saved['leads'][leads[1]['linkedin_id']]['resolution_attempts'],1)
            _,_,_,again=e.prepare(previous,{'max_resolution_queries':1},now=NOW)
            self.assertEqual(again,[])  # no repeated resolver calls for unchanged leads today
    def test_tenant_company_alias_is_only_a_possible_match(self):
        lead=e.parse_message(message())[1]
        job={'source':'greenhouse','company':'workivacareers','id':'1234','title':lead['title'],'url':'https://job-boards.greenhouse.io/workivacareers/jobs/1234'}
        report=e.report({'leads':{'4474627516':lead}},{'status':'ok'},[job],[job],{}, {},{},now=NOW)
        self.assertEqual(report['rows'][0]['collection'],'possible_match')

class EmailEndpointSecurity(unittest.TestCase):
    def handler(self,method='GET',body=None,authorization=''):
        import io
        instance=history.handler.__new__(history.handler)
        raw=json.dumps(body or {}).encode()
        instance.command=method;instance.path='/api/history?action=email_alert_read'
        instance.headers={'Content-Length':str(len(raw)),'Authorization':authorization}
        instance.rfile=io.BytesIO(raw);instance.respond=Mock();return instance
    def test_email_state_requires_machine_auth_before_database_access(self):
        with patch.dict(os.environ,{'HISTORY_SCORER_TOKEN':'machine-only','HISTORY_SESSION_SECRET':'test-session-signing-secret-longer-than-32'}),patch.object(history,'connect') as connect:
            for token in ('','Bearer incorrect','Bearer '+history.issue_session()):
                handler=self.handler(authorization=token);handler.process_history_request()
                self.assertEqual(handler.respond.call_args.args[0],401)
            connect.assert_not_called()
    def test_authenticated_read_and_method_restrictions(self):
        from unittest.mock import MagicMock
        connection=MagicMock();connection.execute.return_value.fetchone.return_value=({'schema':1,'messages':{},'leads':{}},)
        with patch.dict(os.environ,{'HISTORY_SCORER_TOKEN':'machine-only'}),patch.object(history,'connect') as connect:
            connect.return_value.__enter__.return_value=connection
            handler=self.handler(authorization='Bearer machine-only');handler.process_history_request()
            self.assertEqual(handler.respond.call_args.args[0],200)
            handler=self.handler(method='POST',body={'action':'email_alert_read'},authorization='Bearer machine-only')
            handler.process_history_request();self.assertEqual(handler.respond.call_args.args[0],405)
    def test_invalid_raw_mail_is_rejected_before_database_write(self):
        with patch.dict(os.environ,{'HISTORY_SCORER_TOKEN':'machine-only'}),patch.object(history,'connect') as connect:
            handler=self.handler(method='POST',body={'action':'email_alert_write','state':{'schema':1,'body':'private'}},authorization='Bearer machine-only')
            handler.process_history_request();self.assertEqual(handler.respond.call_args.args[0],400);connect.assert_not_called()

if __name__=='__main__':unittest.main()
