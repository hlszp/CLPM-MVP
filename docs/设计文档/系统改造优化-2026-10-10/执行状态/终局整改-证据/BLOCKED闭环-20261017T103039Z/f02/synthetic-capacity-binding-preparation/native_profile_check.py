from pathlib import Path
import hashlib,importlib.util,json
from dotenv import dotenv_values
p=Path(__file__).parent
s=importlib.util.spec_from_file_location("capacity_binding_check",p/"prepare_bindings.py")
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
native=Path("/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/.env")
cfg=dotenv_values(native);m.private_config(cfg)
print(json.dumps({"result":"PASS","nativeEnvSha256":hashlib.sha256(native.read_bytes()).hexdigest(),"postgresDatabase":cfg["POSTGRES_DB"],"postgresHost":cfg["POSTGRES_HOST"],"postgresPort":cfg["POSTGRES_PORT"],"redisUsesSeparateFields":not bool(cfg.get("REDIS_URL")),"redisHost":cfg.get("REDIS_HOST"),"redisPort":cfg.get("REDIS_PORT"),"redisDb":cfg.get("REDIS_DB"),"actualConnections":0,"passwordsExported":False}))
