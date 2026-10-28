import json
import tempfile
import unittest
from pathlib import Path
from soracom_tracks.auth import ApiSession, ApiError, POLICY, provision, NoRedirect
from soracom_tracks.soracom_io import DemoError, SoracomClient

class AuthTests(unittest.TestCase):
    def test_https_handler_keeps_certificate_verification(self):
        from soracom_tracks.auth import ApiHTTPSHandler
        import ssl
        handler=ApiHTTPSHandler()
        context=handler._context or ssl.create_default_context()
        self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED);self.assertTrue(context.check_hostname)

    def test_auth_refresh_per_coverage_and_no_post_retry(self):
        calls=[];fail_get=[True]
        def request(c,m,p,b,h=None):
            calls.append((c,m,p,b,h))
            if p=='/auth':return dict(apiKey='FAKE-API',token='FAKE-TOKEN',operatorId='FAKE-OP'),{}
            if p=='/subscribers?limit=100' and fail_get[0]:fail_get[0]=False;raise ApiError(401)
            if m=='POST':raise ApiError(401)
            return [],{}
        api=ApiSession(dict(kind='key',authKeyId='FAKE-ID',authKey='FAKE-SECRET'),request)
        api.runner('jp')(['subscribers','list','--limit','100','--fetch-all'])
        api.runner('g')(['subscribers','list','--limit','100','--fetch-all'])
        self.assertEqual([c for c,m,p,b,h in calls if p=='/auth'],['jp','jp','g'])
        with self.assertRaises(ApiError):api.runner('jp')(['sora-cam','devices','images','export','--device-id','CAM','--time','1000'])
        self.assertEqual(sum(p.endswith('/exports') for c,m,p,b,h in calls),1)
        self.assertTrue(all(h is None for c,m,p,b,h in calls if p=='/auth'))

    def test_pagination_short_pages_and_external_link_block(self):
        calls=[]
        def request(c,m,p,b,h):
            calls.append(p)
            if len(calls)==1:return [{'time':1}],{'link':'<https://api.soracom.io/v1/data/Subscriber/A?last_evaluated_key=opaque>; rel="next"'}
            return [{'time':2}],{}
        api=ApiSession(dict(kind='token',apiKey='FAKE',token='FAKE'),request)
        args=['data','get-entries','--resource-type','Subscriber','--resource-id','A','--limit','1000','--fetch-all']
        self.assertEqual(len(api.runner('jp')(args)),2)
        api.request=lambda *args:([{}],{'link':'<https://other.example/steal>; rel="next"'})
        with self.assertRaisesRegex(DemoError,'ページ送り先'):api.runner('jp')(args)
        api.request=lambda *args:([{}],{'x-soracom-next-key':'same'})
        with self.assertRaisesRegex(DemoError,'循環'):api.runner('jp')(args)
        with self.assertRaises(DemoError):NoRedirect().redirect_request(None,None,None,None,None,None)

    def test_video_mapping_and_token_no_refresh(self):
        calls=[]
        def request(*args):calls.append(args);return {},{}
        api=ApiSession(dict(kind='token',apiKey='FAKE',token='FAKE'),request)
        api.runner('g')(['sora-cam','devices','videos','export','--device-id','CAM/1','--from','1','--to','11','--archive=false'])
        self.assertEqual(calls[0][2],'/sora_cam/devices/CAM%2F1/videos/exports')
        self.assertEqual(calls[0][3],{'from':1,'to':11,'archive':False})
        def fail(*args):calls.append(args);raise ApiError(401)
        api.request=fail
        with self.assertRaises(ApiError):api.runner('jp')(['subscribers','list'])
        self.assertEqual(len(calls),2)

    def test_provision_dedicated_policy_readback_and_journal(self):
        calls=[]
        def request(c,m,p,b,h=None):
            calls.append((m,p,b))
            if p=='/auth':return dict(operatorId='OP',apiKey='FAKE-API',token='FAKE-TOKEN'),{}
            if p.endswith('/permission') and m=='GET':return {'permission':json.dumps(POLICY)},{}
            if m=='GET':return {'roleList':[]},{}
            if p.endswith('/auth_keys'):return dict(authKeyId='NEW-ID',authKey='NEW-SECRET'),{}
            return None,{}
        with tempfile.TemporaryDirectory() as root:
            journal=Path(root)/'provision.json'
            key,state=provision(dict(email='fake@example.org',password='FAKE-PASSWORD'),journal,request=request)
            self.assertEqual(key['kind'],'key');self.assertEqual(state['stage'],'key_issued')
            self.assertTrue(state['user'].startswith('qgis-tracks-'))
            text=journal.read_text()
            for secret in ('NEW-SECRET','FAKE-PASSWORD','FAKE-TOKEN'):self.assertNotIn(secret,text)
            n=len(calls)
            with self.assertRaisesRegex(DemoError,'前回の発行状態'):provision({},journal,request=request)
            self.assertEqual(len(calls),n+2)
            self.assertEqual(calls[-1][0],'GET')
            self.assertFalse(any('*' in op for op in POLICY['statements'][0]['api']))

    def test_permission_mismatch_stops_key_creation(self):
        calls=[]
        def request(c,m,p,b,h=None):
            calls.append(p)
            if p=='/auth':return dict(operatorId='OP',apiKey='FAKE',token='FAKE'),{}
            if p.endswith('/permission') and m=='GET':return {'permission':'{}'},{}
            return None,{}
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(DemoError,'権限の確認'):provision({},Path(root)/'p.json',request=request)
        self.assertFalse(any(p.endswith('/auth_keys') for p in calls))

    def test_mixed_coverage_isolation(self):
        with tempfile.TemporaryDirectory() as root:
            calls=[]
            def run(c):
                def call(args,cancel):
                    calls.append(c)
                    return [dict(time=1790000000000,content=json.dumps(dict(measured_at='2026-09-21T00:00:00Z',lat=35,lon=139,receiver='RTK',run='run',fix_status='FIX')))]
                return call
            jp=SoracomClient('unused','fake',root,runner=run('jp'),coverage='jp')
            g=SoracomClient('unused','fake',root,runner=run('g'),coverage='g');jp.coverage_clients={'g':g}
            resources=[dict(resource_type='Subscriber',resource_id='SAME',coverage=c) for c in ('jp','g')]
            points,bad,counts=jp.harvest_many(resources,'2026-09-21T00:00:00Z','2026-09-21T00:01:00Z')
            self.assertEqual(calls,['jp','g']);self.assertEqual(len(points),2)
            self.assertEqual({p['coverage'] for p in points},{'jp','g'})
            self.assertEqual(len({p['source_key'] for p in points}),2)

class SessionLoginTests(unittest.TestCase):
    def test_existing_sam_uses_only_auth_and_revokes_own_tokens(self):
        from soracom_tracks.auth import session_login
        calls=[]
        def request(c,m,p,b=None,h=None):
            calls.append((c,m,p))
            if p=='/auth':return dict(apiKey='API-'+c,token='TOKEN-'+c,operatorId='OP'),{}
            return [],{}
        credentials=session_login(dict(operatorId='OP',userName='sam',password='NEVER-SAVE'),request=request)
        self.assertNotIn('NEVER-SAVE',json.dumps(credentials))
        api=ApiSession(credentials,request);runner=api.runner('g')
        runner(['subscribers','list'])
        self.assertEqual(api.close(),[])
        with self.assertRaisesRegex(DemoError,'ログアウト'):runner(['subscribers','list'])
        self.assertEqual(api.credentials,{})
        self.assertEqual([p for c,m,p in calls if m=='POST'],['/auth','/auth','/auth/logout','/auth/logout'])

    def test_expiry_does_not_refresh_with_password(self):
        import time
        calls=[]
        api=ApiSession(dict(kind='session',sessions={'jp':dict(apiKey='A',token='T')},expires_at=time.monotonic()-1),lambda *a:calls.append(a))
        with self.assertRaisesRegex(DemoError,'有効期限'):api.call('jp','GET','/subscribers')
        self.assertEqual(calls,[])

    def test_partial_coverage_and_cancel_cleanup(self):
        from soracom_tracks.auth import session_login
        calls=[]
        def request(c,m,p,b=None,h=None):
            calls.append((c,m,p))
            if p=='/auth':
                if c=='g':raise ApiError(401)
                return dict(apiKey='A',token='T',operatorId='OP'),{}
            return None,{}
        creds=session_login(dict(operatorId='OP',userName='sam',password='PASS'),request=request)
        self.assertEqual(list(creds['sessions']),['jp'])
        with self.assertRaisesRegex(DemoError,'g のログイン'):ApiSession(creds,request).token('g')
        calls.clear()
        with self.assertRaisesRegex(DemoError,'中止'):
            session_login(dict(operatorId='OP',userName='sam',password='PASS'),lambda:len(calls)>0,request)
        self.assertEqual(calls[-1],('jp','POST','/auth/logout'))

    def test_logout_failure_still_clears_credentials_and_is_idempotent(self):
        calls=[]
        def request(c,m,p,b=None,h=None):
            calls.append((c,p))
            raise ApiError(503 if c=='jp' else 401)
        api=ApiSession(dict(kind='session',sessions={c:dict(apiKey='FAKE',token='FAKE') for c in ('jp','g')}),request)
        self.assertEqual(api.close(),['jp'])
        self.assertEqual(api.credentials,{})
        self.assertEqual(api.close(),[])
        self.assertEqual(len(calls),2)
