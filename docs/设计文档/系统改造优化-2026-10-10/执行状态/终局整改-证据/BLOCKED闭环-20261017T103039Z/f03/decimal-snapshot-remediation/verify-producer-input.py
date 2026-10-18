from pathlib import Path
import asyncio,hashlib,json,sys
from decimal import Decimal
from unittest.mock import AsyncMock,MagicMock
sys.path.insert(0,'/Users/zhangping/DEV/clpm-remediation-20261017/backend')
from app.models.loop_config import LoopTypeWeight
from app.services.loop_config import get_loop_type_weights_map
from app.services.result_ledger import canonical_value
from tests.test_calc_snapshot_decimal import _producer_weights
p=Path(__file__).parent/'baseline/input-policy-before.stdout.log'
raw=[json.loads(x,parse_float=Decimal) for x in p.read_text().splitlines() if x.startswith('{')]
rows=next(x['rows'] for x in raw if x.get('section')=='loop-type-weights-input')
async def main():
 db=MagicMock();result=MagicMock();result.scalars.return_value.all.return_value=[LoopTypeWeight(**{k:r[k] for k in ('loop_type','weight_a','weight_f','weight_s')}) for r in rows];db.execute=AsyncMock(return_value=result)
 actual=await get_loop_type_weights_map(db);test=await _producer_weights();assert actual==test
 for w in actual.values():
  for v in w.values():assert type(v) is Decimal and v.is_finite()
 print(json.dumps({'result':'PASS','originalInputSha256':hashlib.sha256(p.read_bytes()).hexdigest(),'rows':len(rows),'scalarType':'decimal.Decimal','columnPrecision':LoopTypeWeight.weight_a.type.precision,'columnScale':LoopTypeWeight.weight_a.type.scale,'producer':str(db.execute.call_args.args[0]),'valuesExactDecimal':{k:{n:str(v) for n,v in w.items()} for k,w in actual.items()},'numericJSON':canonical_value(actual),'externalIO':False},ensure_ascii=False,indent=2))
asyncio.run(main())
