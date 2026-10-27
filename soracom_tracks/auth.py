"""SORACOM direct API authentication. Secrets remain in memory or QGIS's vault."""
import json
import socket
import http.client
import re
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from .soracom_io import DemoError, atomic_json

BASES = {'jp': 'https://api.soracom.io/v1', 'g': 'https://g.api.soracom.io/v1'}
PERMISSIONS = [
    'Subscriber:listSubscribers', 'DataEntry:getDataEntries',
    'SoraCam:listSoraCamDevices', 'SoraCam:exportSoraCamDeviceRecordedImage',
    'SoraCam:getSoraCamDeviceExportedImage', 'SoraCam:exportSoraCamDeviceRecordedVideo',
    'SoraCam:getSoraCamDeviceExportedVideo', 'OAuth2:authorize',
]
POLICY = {'statements': [{'effect': 'allow', 'api': PERMISSIONS}]}

class ApiError(DemoError):
    def __init__(self, status):
        self.status = status
        hint = ('認証情報・有効期限・MFAを確認してください' if status == 401 else
                'この操作の権限がありません。SAMユーザーの権限を確認してください' if status == 403 else
                '通信状態と対象を確認してください')
        super().__init__(f'SORACOM API: HTTP {status}。{hint}')

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise DemoError('APIの転送先には認証情報を送信しません')

def connect_api(address, timeout=30, source_address=None):
    # Some local networks advertise IPv6 without a working route. Prefer IPv4,
    # retain IPv6 fallback, and bound all address attempts together.
    deadline=time.monotonic()+min(float(timeout),10)
    addresses=socket.getaddrinfo(*address,type=socket.SOCK_STREAM)
    addresses.sort(key=lambda a:a[0]!=socket.AF_INET)
    for family,socktype,proto,_,target in addresses:
        remaining=deadline-time.monotonic()
        if remaining<=0:break
        connection=socket.socket(family,socktype,proto)
        try:
            connection.settimeout(min(3,remaining))
            if source_address:connection.bind(source_address)
            connection.connect(target);connection.settimeout(timeout)
            return connection
        except OSError:connection.close()
    raise OSError('API connection failed')

class ApiHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self._create_connection=connect_api

class ApiHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self,request):
        return self.do_open(ApiHTTPSConnection,request,context=self._context)

def transport(coverage, method, path, body=None, headers=None):
    if coverage not in BASES or not path.startswith('/') or path.startswith('//'):
        raise DemoError('API接続先が不正です')
    request = urllib.request.Request(BASES[coverage]+path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Accept': 'application/json', 'Content-Type': 'application/json', **(headers or {})}, method=method)
    try:
        with urllib.request.build_opener(NoRedirect(),ApiHTTPSHandler()).open(request, timeout=30) as response:
            raw=response.read(32*1024*1024+1)
            if len(raw)>32*1024*1024:raise DemoError('API応答が上限を超えました')
            return (json.loads(raw) if raw else None), {k.lower():v for k,v in response.headers.items()}
    except urllib.error.HTTPError as e:raise ApiError(e.code) from None
    except DemoError:raise
    except Exception:raise DemoError('API通信に失敗しました。作成・発行要求は自動再送しません') from None

def login(body, coverage='jp', request=transport):
    result,_=request(coverage,'POST','/auth',{**body,'tokenTimeoutSeconds':3600})
    if not isinstance(result,dict) or not all(result.get(k) for k in ('apiKey','token','operatorId')):
        raise DemoError('認証APIの応答が不正です')
    return result

def headers(session):
    return {'x-soracom-api-key':session['apiKey'], 'x-soracom-token':session['token']}

class ApiSession:
    def __init__(self, credentials, request=transport):
        self.credentials=dict(credentials);self.request=request;self.tokens={}
    def token(self, coverage):
        if self.credentials['kind']=='token':return self.credentials
        cached=self.tokens.get(coverage)
        if cached and cached[0]>time.monotonic():return cached[1]
        result=login({k:self.credentials[k] for k in ('authKeyId','authKey')},coverage,self.request)
        self.tokens[coverage]=(time.monotonic()+3300,result)
        return result
    def call(self, coverage, method, path, body=None, cancelled=lambda:False):
        if cancelled():raise DemoError('中止しました')
        session=self.token(coverage)
        if cancelled():raise DemoError('中止しました')
        try:return self.request(coverage,method,path,body,headers(session))
        except ApiError as e:
            # Only repeat a read after a token refresh, never an export/create request.
            if e.status!=401 or method!='GET' or self.credentials['kind']=='token':raise
            self.tokens.pop(coverage,None)
            session=self.token(coverage)
            if cancelled():raise DemoError('中止しました')
            return self.request(coverage,method,path,body,headers(session))
    def runner(self, coverage):
        def run(args, cancelled=lambda:False):
            opts={};i=0
            while i<len(args):
                if args[i].startswith('--'):
                    key=args[i][2:]
                    if '=' in key:key,value=key.split('=',1)
                    elif i+1<len(args) and not args[i+1].startswith('--'):i+=1;value=args[i]
                    else:value=True
                    opts[key]=value
                i+=1
            q=lambda name:urllib.parse.quote(str(opts[name]),safe='')
            method='GET';body=None
            if args[:2]==['subscribers','list']:path='/subscribers';params={'limit':opts.get('limit',100)}
            elif args[:2]==['data','get-entries']:
                path='/data/'+q('resource-type')+'/'+q('resource-id')
                params={k:opts[k] for k in ('from','to','sort','limit') if k in opts}
            elif args[:3]==['sora-cam','devices','list']:path='/sora_cam/devices';params={}
            elif args[:2]==['sora-cam','devices'] and args[2] in ('images','videos'):
                path='/sora_cam/devices/'+q('device-id')+'/'+args[2]+'/exports';params={}
                if args[3]=='export':
                    method='POST';body=({'time':int(opts['time'])} if args[2]=='images' else {'from':int(opts['from']),'to':int(opts['to']),'archive':False})
                elif args[3]=='get-exported':path+='/'+q('export-id')
                else:raise DemoError('未対応のAPI操作です')
            else:raise DemoError('未対応のAPI操作です')
            url=path+('?' + urllib.parse.urlencode(params) if params else '')
            seen=set();rows=[]
            for _ in range(1000):
                if url in seen:raise DemoError('APIページ送りが循環しています')
                seen.add(url)
                value,h=self.call(coverage,method,url,body,cancelled)
                if not opts.get('fetch-all'):return value
                if not isinstance(value,list):raise DemoError('API一覧の応答が不正です')
                rows.extend(value)
                if len(rows)>100000:raise DemoError('取得件数が上限を超えました')
                next_url=None
                for link in h.get('link','').split(','):
                    match=re.search(r'<([^>]+)>\s*;\s*rel=[\"\']?next',link)
                    if match:next_url=match.group(1);break
                if next_url:
                    parsed=urllib.parse.urlsplit(urllib.parse.urljoin(BASES[coverage]+url,next_url))
                    base=urllib.parse.urlsplit(BASES[coverage])
                    if parsed.scheme!=base.scheme or parsed.netloc!=base.netloc or parsed.path!=base.path+path or parsed.fragment:
                        raise DemoError('APIのページ送り先が不正です')
                    url=parsed.path[len(base.path):]+('?' + parsed.query if parsed.query else '')
                elif h.get('x-soracom-next-key'):
                    url=path+'?'+urllib.parse.urlencode({**params,'last_evaluated_key':h['x-soracom-next-key']})
                else:return rows
            raise DemoError('APIページ数が上限を超えました')
        return run

def provision(body, journal, cancelled=lambda:False, request=transport):
    """New dedicated SAM only; never change the login user's existing privileges."""
    previous=json.loads(journal.read_text()) if journal.exists() else None
    session=login(body,request=request)
    op=session['operatorId'];state=None
    if previous and previous.get('stage')!='saved':
        if previous.get('operator')!=op:raise DemoError('前回の発行元アカウントでログインして状態を確認してください')
        user=previous['user'];path='/operators/'+urllib.parse.quote(op,safe='')+'/users/'+urllib.parse.quote(user,safe='')
        try:account,_=request('jp','GET',path,None,headers(session))
        except ApiError as e:
            if e.status!=404:raise
        else:
            if previous.get('stage') in ('key_requested','key_issued'):
                raise DemoError('前回の発行状態を確認してください。SAM名: '+user+'。管理画面でこの専用SAMを削除してから再実行できます。認証キーは自動再発行しません')
            if account.get('description')!='QGIS SORACOM Tracks: Harvest and SoraCam' or account.get('roleList') or account.get('authKeyList'):
                raise DemoError('既存SAMの状態が異なります。管理画面で確認してください: '+user)
            state=previous
    if state is None:
        user='qgis-tracks-'+uuid.uuid4().hex[:12]
        path='/operators/'+urllib.parse.quote(op,safe='')+'/users/'+user
        state={'user':user,'operator':op,'stage':'create_requested'}
        if cancelled():raise DemoError('中止しました')
        atomic_json(journal,state)
        request('jp','POST',path,{'description':'QGIS SORACOM Tracks: Harvest and SoraCam'},headers(session))
    state['stage']='permission_pending';atomic_json(journal,state)
    if cancelled():raise DemoError('中止しました。作成済みSAM: '+user)
    request('jp','PUT',path+'/permission',{'permission':json.dumps(POLICY)},headers(session))
    value,_=request('jp','GET',path+'/permission',None,headers(session))
    if json.loads(value.get('permission','{}'))!=POLICY:raise DemoError('権限の確認に失敗しました。認証キーは発行していません')
    account,_=request('jp','GET',path,None,headers(session))
    if account.get('roleList'):raise DemoError('追加ロールがあるため認証キーは発行していません')
    if cancelled():raise DemoError('中止しました。作成済みSAM: '+user)
    state['stage']='key_requested';atomic_json(journal,state)
    value,_=request('jp','POST',path+'/auth_keys',None,headers(session))
    if not all(value.get(k) for k in ('authKeyId','authKey')):raise DemoError('認証キーの応答が不正です。再発行せず管理画面で確認してください')
    state['stage']='key_issued';atomic_json(journal,state)
    return {'kind':'key','authKeyId':value['authKeyId'],'authKey':value['authKey']},state
