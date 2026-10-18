from pathlib import Path
import asyncio,asyncpg,datetime,json,subprocess,sys
OUT=Path(__file__).parent
DB='clpm_final_reverify_20261017_093240_phase2'
async def main():
 cfg=json.loads(subprocess.run(['docker','inspect','clpm-mvp-postgres'],capture_output=True,text=True,check=True).stdout)[0]
 env=dict(x.split('=',1) for x in cfg['Config']['Env'] if '=' in x)
 conn=await asyncpg.connect(host='127.0.0.1',port=17102,user=env.get('POSTGRES_USER','clpm'),password=env['POSTGRES_PASSWORD'],database=DB)
 try:
  async with conn.transaction(readonly=True):
   sql="SELECT id::text,tag_name,control_action_direction,op_output_lower_limit,op_output_upper_limit,dcs_model_id::text,engineering_template_id::text,engineering_template_version,updated_at::text FROM loop_ledger WHERE tag_name LIKE 'AUDIT-F04-UI-%' ORDER BY tag_name"
   rows=[dict(r) for r in await conn.fetch(sql)]
   count=await conn.fetchval("SELECT count(*) FROM loop_ledger WHERE engineering_template_id='295fea85-2f64-4048-a644-95e1c90e58d1'")
   data={'timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),'database':DB,'readonly':True,'sql':sql,'count':len(rows),'target_template_count':count,'rows':rows}
  f=OUT/('ui-pg-'+sys.argv[1]+'.json');assert not f.exists();f.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
  print(json.dumps({'count':len(rows),'target_template_count':count,'null_template_count':sum(r['engineering_template_id'] is None for r in rows)}))
 finally:await conn.close()
asyncio.run(main())
