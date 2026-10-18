from pathlib import Path
import datetime,json,urllib.request,urllib.error,uuid,sys
OUT=Path(__file__).parent
ROOT='http://127.0.0.1:17261/api/v1'
LOOP='a0d17000-0000-4000-8000-000000000001'
records=[]
def req(path,method='GET',data=None,token=None):
 headers={'Content-Type':'application/json'}
 if token:headers['Authorization']='Bearer '+token
 request=urllib.request.Request(ROOT+path,data=json.dumps(data).encode() if data is not None else None,headers=headers,method=method)
 try:r=urllib.request.urlopen(request,timeout=20)
 except urllib.error.HTTPError as e:r=e
 body=json.loads(r.read());records.append({'path':path,'method':method,'status':r.code,'request':({'username':data['username'],'password':'REDACTED'} if path=='/auth/login' else data),'body':({'tokens':'REDACTED','login':'OK'} if path=='/auth/login' and r.code==200 else body)})
 return r.code,body
status,body=req('/me/loop-follows');assert status==401
counts={}
for user in ['ic_engineer','audit_peer','audit_restricted']:
 status,login=req('/auth/login','POST',{'username':user,'password':'Audit-test-20261017!'});assert status==200
 token=login['data']['accessToken']
 status,data=req('/me/loop-follows',token=token);assert status==200
 counts[user]=data['data']['total']
 if user=='audit_restricted':
  status,data=req('/me/loop-follows/'+LOOP,'PUT',{'isFollowed':True,'expectedRevision':0,'requestId':str(uuid.uuid4())},token)
  assert status==403
f=OUT/('real-account-security-'+sys.argv[1]+'.json');assert not f.exists()
f.write_text(json.dumps({'timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),'count_by_user':counts,'records':records,'pass':True},ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'counts':counts,'anonymous':401,'out_of_scope_write':403}))
