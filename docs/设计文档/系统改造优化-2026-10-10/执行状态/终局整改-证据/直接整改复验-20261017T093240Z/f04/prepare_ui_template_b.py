from pathlib import Path
import datetime,json,urllib.request
ROOT='http://127.0.0.1:17261/api/v1';rows=[]
def req(path,method='GET',data=None,token=None):
 headers={'Content-Type':'application/json'}
 if token:headers['Authorization']='Bearer '+token
 r=urllib.request.urlopen(urllib.request.Request(ROOT+path,data=json.dumps(data).encode() if data is not None else None,headers=headers,method=method),timeout=20)
 body=json.loads(r.read());rows.append({'method':method,'path':path,'request':({'username':'admin','password':'REDACTED'} if path=='/auth/login' else data),'status':r.status,'body':({'tokens':'REDACTED'} if path=='/auth/login' else body)})
 assert body['code']=='0'
 return body['data']
token=req('/auth/login','POST',{'username':'admin','password':'Audit-test-20261017!'})['accessToken']
template=req('/engineering-templates','POST',{'templateKey':'AUDIT-F04-UI-B-1017','name':'独立浏览器换绑目标B','category':'RESPONSE'},token)
revision=req('/configs/publish/revision',token=token)['revision']
version=req('/engineering-templates/'+template['id']+'/versions','POST',{'profile':{},'reason':'owned isolated UI test fixture B','expectedRevision':revision},token)
f=Path(__file__).with_name('ui-template-b-fixture.json');assert not f.exists();f.write_text(json.dumps({'timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),'template_id':template['id'],'records':rows,'boundary':'only own scratch DB test input; not UI creation acceptance'},ensure_ascii=False,indent=2)+'\n');print(json.dumps({'template_id':template['id'],'version':version['version']}))
