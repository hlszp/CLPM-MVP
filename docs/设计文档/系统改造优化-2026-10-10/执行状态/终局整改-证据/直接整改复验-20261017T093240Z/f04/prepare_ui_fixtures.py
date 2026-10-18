from pathlib import Path
import asyncio,asyncpg,datetime,json,subprocess,uuid
OUT=Path(__file__).parent
DB='clpm_final_reverify_20261017_093240_phase2'
async def main():
 info=json.loads(subprocess.run(['docker','inspect','clpm-mvp-postgres'],capture_output=True,text=True,check=True).stdout)[0]
 env=dict(x.split('=',1) for x in info['Config']['Env'] if '=' in x)
 conn=await asyncpg.connect(host='127.0.0.1',port=17102,user=env.get('POSTGRES_USER','clpm'),password=env['POSTGRES_PASSWORD'],database=DB)
 try:
  rows=[(str(uuid.uuid5(uuid.NAMESPACE_URL,'clpm-audit-f04-ui-20261017-'+str(i))),f'AUDIT-F04-UI-{i:04}') for i in range(1,252)]
  assert not await conn.fetchval("SELECT count(*) FROM loop_ledger WHERE tag_name LIKE 'AUDIT-F04-UI-%'")
  await conn.executemany("INSERT INTO loop_ledger(id,tag_name,description,status,is_active,include_in_evaluation,created_by,updated_by) VALUES($1::uuid,$2,'isolated UI test input, no measurement output','PARTIAL',FALSE,FALSE,'AUDIT-F04-UI','AUDIT-F04-UI')",rows)
  state=[dict(r) for r in await conn.fetch("SELECT id::text,tag_name,engineering_template_id,engineering_template_version FROM loop_ledger WHERE tag_name LIKE 'AUDIT-F04-UI-%' ORDER BY tag_name")]
  assert len(state)==251 and all(r['engineering_template_id'] is None and r['engineering_template_version'] is None for r in state)
  (OUT/'ui-fixture-baseline.json').write_text(json.dumps({'timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),'database':DB,'count':len(state),'test_only':True,'rows':state},ensure_ascii=False,indent=2)+'\n')
  print('created 251 own inactive synthetic input loops; all template/pin NULL')
 finally:await conn.close()
asyncio.run(main())
