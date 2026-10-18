from pathlib import Path
import asyncio,datetime,hashlib,json,sys
from unittest.mock import AsyncMock,patch
sys.path.insert(0,'/Users/zhangping/DEV/clpm-remediation-20261017/backend')
from app.services import calc_snapshot as cs
from app.services.result_ledger import canonical_value
from tests.test_calc_snapshot import _SaveDB,_LoadDB,_TS0,_TS1,_make_kpi_bundles,_record_stub,_kpi_manifest_extra
from tests.test_calc_snapshot_decimal import _producer_weights
from app.tasks.kpi_calc import _build_weights_map,_build_config_bundle,_compute_kpis_three_layer,_extract_metrics_detail
from app.contracts.data_types import ControlType
from app.models.calc_dataset_snapshot import CalculationDatasetSnapshot,KIND_KPI_GRID
root=Path(sys.argv[2]);meta=root.parent/'legacy-package-metadata.json'
cs.settings.CLPM_CALC_SNAPSHOT_DIR=str(root)
cs.executable_build_digest=lambda:'testbuild012345678'
async def main():
 if sys.argv[1]=='create':
  types=canonical_value(await _producer_weights());weights=_build_weights_map(types,'STABLE');bundles=_make_kpi_bundles()
  detail=_extract_metrics_detail(_compute_kpis_three_layer(bundles,_build_config_bundle('loop-1',ControlType.TEMPERATURE,None),weights)[0])
  arrays,blocks=cs.serialize_data_blocks(bundles)
  row=await _SaveDB().save(kind=KIND_KPI_GRID,ts_start=_TS0,ts_end=_TS1,dataset_ref='test:legacy:native',arrays=arrays,manifest_extra=_kpi_manifest_extra(weights=weights,effective_params={'typeWeights':types},blocks=blocks))
  raw={k:getattr(row,k) for k in ('id','dataset_ref','input_hash','schema_version','kind','retention_class','frozen_by_record_ids','size_bytes')}
  for k in ('ts_start','ts_end','expires_at','created_at'):raw[k]=getattr(row,k).isoformat()
  paths=cs.snapshot_file_paths(str(row.id));manifest=json.loads(next(x for x in paths if x.name.endswith('.json')).read_text())
  meta.write_text(json.dumps({'row':raw,'detail':detail,'replayContext':manifest['replayContext'],'effectiveParams':manifest['source']['effectiveParams'],'files':[{'path':str(x.relative_to(root)),'sha256':hashlib.sha256(x.read_bytes()).hexdigest(),'bytes':x.stat().st_size} for x in paths]},ensure_ascii=False,indent=2)+'\n')
 else:
  raw=json.loads(meta.read_text());values=raw['row']
  for k in ('ts_start','ts_end','expires_at','created_at'):values[k]=datetime.datetime.fromisoformat(values[k])
  row=CalculationDatasetSnapshot(**values);loaded=await cs.load_snapshot_for_replay(_LoadDB(row)._db,str(row.id))
  assert loaded.manifest['replayContext']==raw['replayContext']
  assert loaded.manifest['source']['effectiveParams']==raw['effectiveParams']
  assert cs.compute_input_hash(loaded.manifest,loaded.arrays)==row.input_hash
  for f in raw['files']:
   p=root/f['path'];assert p.stat().st_size==f['bytes'] and hashlib.sha256(p.read_bytes()).hexdigest()==f['sha256']
  with patch('app.services.result_ledger.get_record_by_id',new=AsyncMock(return_value=_record_stub('legacy-record',row.id,raw['detail']))),patch('app.tasks.kpi_calc._build_data_planner',side_effect=AssertionError('no current source')):
   report=await cs.replay_result_record(_LoadDB(row)._db,'legacy-record')
  assert report['identical'] and report['replayedMetrics']==raw['detail']
 print(json.dumps({'phase':sys.argv[1],'inputHash':row.input_hash,'result':'PASS','actualDatabaseIO':False}))
asyncio.run(main())
