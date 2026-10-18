import argparse,hashlib,json,runpy
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument("--source",type=Path,required=True);p.add_argument("--out",type=Path,required=True);a=p.parse_args()
f=runpy.run_path(str(a.source))["objects"]
x=f('{"value":0.12345678901234567890}')[0];y=f('{"value":0.12345678901234567891}')[0]
r={"sourceSha256":hashlib.sha256(a.source.read_bytes()).hexdigest(),"leftRaw":"0.12345678901234567890","rightRaw":"0.12345678901234567891","parsedType":type(x["value"]).__name__,"comparedEqual":x==y,"actualServiceIO":0}
a.out.write_text(json.dumps(r,indent=2)+"\n");print(json.dumps(r));assert x!=y,"actual distinct PG numeric policy must remain distinct"
