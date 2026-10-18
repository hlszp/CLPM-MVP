import datetime,json,urllib.request,uuid,sys
from pathlib import Path
ROOT="http://127.0.0.1:17261/api/v1"
OUT=Path(__file__).parent
LOOP="a0d17000-0000-4000-8000-000000000001"
records=[]
def request(path,method="GET",data=None,token=None):
 headers={"Content-Type":"application/json"}
 if token:headers["Authorization"]="Bearer "+token
 r=urllib.request.urlopen(urllib.request.Request(ROOT+path,data=json.dumps(data).encode() if data is not None else None,headers=headers,method=method),timeout=20)
 result=json.loads(r.read());records.append({"path":path,"method":method,"status":r.status,"request":data if path!="/auth/login" else {"username":data["username"],"password":"REDACTED"},"response":result if path!="/auth/login" else {"login":"OK","tokens":"REDACTED"}})
 return result["data"]
peer=request("/auth/login","POST",{"username":"audit_peer","password":"Audit-test-20261017!"})["accessToken"]
for state,rev in [(True,0),(False,1)]:
 result=request("/me/loop-follows/"+LOOP,"PUT",{"isFollowed":state,"expectedRevision":rev,"requestId":str(uuid.uuid4())},peer)
 assert result["revision"]==rev+1 and result["isFollowed"]==state
(OUT/"real-409-precondition.json").write_text(json.dumps({"timestamp":datetime.datetime.now(datetime.timezone.utc).isoformat(),"boundary":"only own scratch DB, peer user, F07 loop","records":records},ensure_ascii=False,indent=2)+"\n")
print("peer tombstone revision=2; UI has not refreshed")
