"""Non-author F04 real HTTP/PostgreSQL checks in the designated private database.

Never imports application code, never calls computation/queue/TDengine endpoints.
All inserted subjects/objects are AUDIT-F04 owned; unrelated roles/data are untouched.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
from uuid import NAMESPACE_URL, uuid4, uuid5

import asyncpg
import bcrypt
from dotenv import dotenv_values
import httpx

HERE = Path(__file__).resolve().parent
DATABASE = 'clpm_final_reverify_20261017_093240_phase2'
BASE = 'http://127.0.0.1:17261/api/v1'
RUN = os.environ.get('F04_REUSE_RUN') or 'AUDIT-F04-' + uuid4().hex[:7]
assert RUN.startswith('AUDIT-F04-')
PASSWORD = 'Audit-test-20261017!'
FIELDS = ('control_action_direction', 'op_output_lower_limit', 'op_output_upper_limit',
          'dcs_model_id', 'engineering_template_id', 'engineering_template_version')
RESULT = {'run': RUN, 'database': DATABASE, 'api': BASE, 'startedAt': datetime.now(timezone.utc).isoformat(),
          'checks': [], 'requests': [], 'objects': {}}


def uid(name):
    return str(uuid5(NAMESPACE_URL, f'{RUN}:{name}'))


def serial(value):
    if isinstance(value, (asyncpg.Record, dict)):
        return {k: serial(v) for k, v in dict(value).items()}
    if isinstance(value, (list, tuple)):
        return [serial(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def save():
    (HERE / 'api-pg-results.json').write_text(json.dumps(serial(RESULT), ensure_ascii=False, indent=2)+'\n')


def check(name, passed, evidence=None):
    RESULT['checks'].append({'name': name, 'passed': bool(passed), 'evidence': serial(evidence)})
    save()
    print(name, 'PASS' if passed else 'FAIL', flush=True)
    return passed


async def main():
    cfg = dotenv_values('/Users/zhangping/DEV/CLPM-MVP/backend/.env')
    connection_options = dict(host=cfg.get('POSTGRES_HOST','localhost'),port=int(cfg.get('POSTGRES_PORT','17102')),
                              user=cfg.get('POSTGRES_USER','clpm'),password=cfg['POSTGRES_PASSWORD'],
                              database=DATABASE,server_settings={'application_name': RUN, 'statement_timeout': '30000'})
    db = await asyncpg.connect(**connection_options)
    client = httpx.AsyncClient(base_url=BASE,trust_env=False,timeout=60)
    try:
        assert await db.fetchval('SELECT current_database()') == DATABASE
        # No global role/permission matrix changes: create isolated users under existing roles.
        users = {kind: uid('user-'+kind) for kind in ('owner','peer','viewer')}
        owner_name = RUN+'-owner'
        unit_a, unit_b = uid('unit-a'), uid('unit-b')
        templates = {key: uid('template-'+key) for key in ('a','b','target','deleted')}
        models = {key: uid('model-'+key) for key in ('a','b')}
        loop_ids = [uid(f'loop-{index:04}') for index in range(1000)]
        aux_ids = [uid(f'aux-{index:02}') for index in range(12)]
        RESULT['objects'] = {'users': users,'ownerName':owner_name,'unitA':unit_a,'unitB':unit_b,
                             'templates':templates,'models':models,'loops':loop_ids,'auxLoops':aux_ids}
        password_hash = bcrypt.hashpw(PASSWORD.encode(),bcrypt.gensalt()).decode()
        baseline = {}
        async with db.transaction():
            for index, unit in enumerate((unit_a,unit_b)):
                await db.execute('INSERT INTO plant_node(id,name,type,parent_id,is_kpi_enabled) VALUES($1::uuid,$2,$3,NULL,FALSE) ON CONFLICT(id) DO NOTHING',unit,RUN+f'-UNIT-{index}','UNIT')
            for key, user_id in users.items():
                await db.execute('''INSERT INTO sys_user(id,username,password_hash,display_name,role,is_active)
                                    VALUES($1::uuid,$2,$3,$4,$5,TRUE) ON CONFLICT(id) DO NOTHING''',user_id,RUN+'-'+key,password_hash,RUN+'-'+key,'SPONSOR' if key=='viewer' else 'ADMIN')
                await db.execute('INSERT INTO sys_user_scope(user_id,scope_type,node_id,updated_by) VALUES($1::uuid,$2,$3::uuid,$4) ON CONFLICT(user_id) DO NOTHING',user_id,'NODE_SUBTREE',unit_a,RUN)
            vendor = uid('vendor')
            await db.execute('INSERT INTO dcs_vendor(id,code,name) VALUES($1::uuid,$2,$3) ON CONFLICT(id) DO NOTHING',vendor,RUN,RUN)
            for key, model in models.items():
                await db.execute('INSERT INTO dcs_model(id,vendor_id,code,name) VALUES($1::uuid,$2::uuid,$3,$4) ON CONFLICT(id) DO NOTHING',model,vendor,RUN+'-'+key,RUN+'-'+key)
            for key, template in templates.items():
                await db.execute('''INSERT INTO engineering_template(id,template_key,name,category,current_version,created_by)
                                    VALUES($1::uuid,$2,$3,'RESPONSE',2,$4) ON CONFLICT(id) DO NOTHING''',template,RUN+'-'+key,RUN+'-'+key,RUN)
                for version in (1,2):
                    await db.execute('''INSERT INTO engineering_template_version(id,template_id,version,profile,reason,published_by)
                                        VALUES($1::uuid,$2::uuid,$3,'{}'::jsonb,$4::text,$4::text) ON CONFLICT(id) DO NOTHING''',uid(f'version-{key}-{version}'),template,version,RUN)
            records = []
            for index, loop_id in enumerate([*loop_ids,*aux_ids]):
                mode = index % 4
                state = {
                    'control_action_direction': ('UNKNOWN','DIRECT','REVERSE','UNKNOWN')[mode],
                    'op_output_lower_limit': None if mode==0 else 5.0+mode,
                    'op_output_upper_limit': None if mode==0 else 95.0,
                    'dcs_model_id': None if mode==0 else models['a' if mode%2 else 'b'],
                    'engineering_template_id': None if mode==0 else templates['a' if mode<3 else 'b'],
                    'engineering_template_version': None if mode in (0,3) else mode,
                }
                baseline[loop_id] = state
                records.append((loop_id,RUN+f'-LOOP-{index:04}',unit_a,state['control_action_direction'],
                                state['op_output_lower_limit'],state['op_output_upper_limit'],state['dcs_model_id'],
                                state['engineering_template_id'],state['engineering_template_version'],RUN))
            await db.executemany('''INSERT INTO loop_ledger(id,tag_name,unit_id,control_action_direction,
                                  op_output_lower_limit,op_output_upper_limit,dcs_model_id,
                                  engineering_template_id,engineering_template_version,status,is_active,include_in_evaluation,created_by,updated_by)
                                  VALUES($1::uuid,$2,$3::uuid,$4,$5,$6,$7::uuid,$8::uuid,$9,'PARTIAL',FALSE,FALSE,$10,$10) ON CONFLICT(id) DO NOTHING''',records)
        (HERE/'baseline-states.json').write_text(json.dumps(baseline,indent=2)+'\n')

        async def request(label, method, path, token=None, payload=None):
            assert path.startswith(('/auth/','/loops/batch'))
            started=time.monotonic()
            response=await client.request(method,path,headers={'Authorization':'Bearer '+token} if token else {},json=payload)
            try:
                body=response.json()
            except ValueError:
                body={'text':response.text}
            record={'label':label,'method':method,'path':path,'request':payload,'status':response.status_code,
                    'elapsed':time.monotonic()-started,'response':body,'responseSha256':hashlib.sha256(response.content).hexdigest()}
            if path=='/auth/login':
                record['request']={'username':payload['username'],'password':'REDACTED'}
                record['response']={'code':body.get('code'),'message':body.get('message'),'data':{'user':body.get('data',{}).get('user'),'tokens':'REDACTED'}}
            RESULT['requests'].append(record); save()
            return response.status_code,body

        async def data(label,method,path,token,payload=None):
            status, body=await request(label,method,path,token,payload)
            if status != 200 or body.get('code') not in (0, '0'):
                raise AssertionError(f'{label}: unexpected HTTP/business status {status}/{body.get("code")}')
            return body['data']

        tokens={}
        for key in users:
            status,body=await request('login-'+key,'POST','/auth/login',payload={'username':RUN+'-'+key,'password':PASSWORD})
            assert status==200, (key,status,body)
            tokens[key]=body['data']['accessToken']
        owner=tokens['owner']

        async def states(ids):
            rows=await db.fetch('SELECT id::text,'+','.join(FIELDS)+',updated_at FROM loop_ledger WHERE id=ANY($1::uuid[]) ORDER BY id',ids)
            return {str(r['id']):{field:serial(r[field]) for field in FIELDS} for r in rows}

        async def preview(ids,updates,operation=None):
            operation=operation or str(uuid4())
            payload={'loopIds':ids,'updates':updates,'operationId':operation,'dryRun':True}
            result=await data('preview-'+operation,'POST','/loops/batch-config',owner,payload)
            payload.update(dryRun=False,expectedStateTokens={r['loopId']:r['stateToken'] for r in result['results'] if r.get('stateToken')})
            return payload,result

        updates={'controlActionDirection':'DIRECT','opOutputLowerLimit':None,'opOutputUpperLimit':90,
                 'dcsModelId':models['b'],'engineeringTemplateId':templates['target'],'engineeringTemplateVersion':1}
        chunks=[loop_ids[index:index+200] for index in range(0,1000,200)]
        prepared=[]
        for ids in chunks:
            payload,result=await preview(ids,updates)
            assert len(result['results'])==200
            prepared.append(payload)
        snapshot_count=await db.fetchval("SELECT count(*) FROM sys_audit_log WHERE operation_type='LOOP_BATCH_SNAPSHOT' AND target_id=$1",users['owner'])
        check('preview_1000_zero_write',await states(loop_ids)=={k:baseline[k] for k in loop_ids} and snapshot_count==0,{'count':1000,'snapshots':snapshot_count})
        first=await asyncio.gather(*(data('concurrent-execute-'+str(index),'POST','/loops/batch-config',owner,prepared[0]) for index in (1,2)))
        check('same_operation_real_concurrency_serialized',sorted(bool(r.get('replayed')) for r in first)==[False,True],first)
        for payload in prepared[1:]:
            result=await data('execute-'+payload['operationId'],'POST','/loops/batch-config',owner,payload)
            check('chunk_200_affected_'+payload['operationId'],result['affected']==200 and len(result['results'])==200)
        target={'control_action_direction':'DIRECT','op_output_lower_limit':None,'op_output_upper_limit':90.0,
                'dcs_model_id':models['b'],'engineering_template_id':templates['target'],'engineering_template_version':1}
        check('apply_all_1000_real_pg',await states(loop_ids)=={k:target for k in loop_ids})
        snapshots=[p['operationId'] for p in prepared]
        saved_entries=[]
        for snapshot in snapshots:
            result=await data('read-snapshot-'+snapshot,'GET',f'/loops/batch-snapshots/{snapshot}?offset=0&limit=200',owner)
            saved_entries.extend(result['items'])
        check('all_snapshot_before_states_server_saved',len(saved_entries)==1000 and all(e['before']==baseline[e['loopId']] for e in saved_entries))
        replays=await data('execute-replay','POST','/loops/batch-config',owner,prepared[0])
        count=await db.fetchval("SELECT count(*) FROM sys_audit_log WHERE operation_type='LOOP_BATCH_SNAPSHOT' AND target_id=$1",users['owner'])
        check('execute_replay_no_duplicate_snapshot',replays['replayed'] and count==5,{'snapshotCount':count})
        changed={**prepared[0],'updates':{**updates,'engineeringTemplateVersion':2}}
        status,_=await request('operation-payload-reuse','POST','/loops/batch-config',owner,changed)
        check('different_payload_same_operation_409',status==409)

        # A PG trigger limited to this owner+operation tests atomic rollback at audit INSERT.
        atomic_id=str(uuid4()); atomic_payload,_=await preview([aux_ids[0]],updates,atomic_id)
        function='audit_f04_'+uuid4().hex[:10]; trigger=function+'_trigger'
        async def create_blocker(operation, target_condition):
            await db.execute(f'''CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF NEW.operation_type='{operation}' AND {target_condition} THEN
                RAISE EXCEPTION 'AUDIT-F04 controlled audit failure'; END IF; RETURN NEW; END $$''')
            await db.execute(f'CREATE TRIGGER {trigger} BEFORE INSERT ON sys_audit_log FOR EACH ROW EXECUTE FUNCTION {function}()')
        async def remove_blocker():
            await db.execute(f'DROP TRIGGER IF EXISTS {trigger} ON sys_audit_log')
            await db.execute(f'DROP FUNCTION IF EXISTS {function}()')
        await create_blocker('LOOP_BATCH_SNAPSHOT',f"NEW.id='{atomic_id}'::uuid")
        try:
            status,_=await request('atomic-snapshot-audit-failure','POST','/loops/batch-config',owner,atomic_payload)
            check('snapshot_and_business_same_transaction',status==500 and await states([aux_ids[0]])=={aux_ids[0]:baseline[aux_ids[0]]} and await db.fetchval('SELECT count(*) FROM sys_audit_log WHERE id=$1::uuid',atomic_id)==0)
        finally:
            await remove_blocker()
        await create_blocker('LOOP_BATCH_RESTORE',f"NEW.target_id='{loop_ids[0]}'")
        try:
            status,_=await request('atomic-restore-audit-failure','POST',f'/loops/batch-snapshots/{snapshots[0]}/restore',owner,{'reason':RUN+' atomic failure','loopIds':[loop_ids[0]],'dryRun':False})
            check('restore_and_audit_same_transaction',status==500 and await states([loop_ids[0]])=={loop_ids[0]:target})
        finally:
            await remove_blocker()
        for snapshot,ids in zip(snapshots,chunks):
            result=await data('preview-restore-'+snapshot,'POST',f'/loops/batch-snapshots/{snapshot}/restore',owner,{'reason':RUN,'loopIds':ids,'dryRun':True})
            check('preview_restore_denominator_'+snapshot,len(result['results'])==200)
        check('restore_preview_zero_write',await states(loop_ids)=={k:target for k in loop_ids})
        first_restore=await asyncio.gather(*(data('concurrent-restore-'+str(index),'POST',f'/loops/batch-snapshots/{snapshots[0]}/restore',owner,{'reason':RUN,'loopIds':chunks[0],'dryRun':False}) for index in (1,2)))
        check('concurrent_restore_exactly_once',sorted(r['affected'] for r in first_restore)==[0,200],first_restore)
        for snapshot,ids in list(zip(snapshots,chunks))[1:]:
            result=await data('restore-'+snapshot,'POST',f'/loops/batch-snapshots/{snapshot}/restore',owner,{'reason':RUN,'loopIds':ids,'dryRun':False})
            check('restore_chunk_200_'+snapshot,result['affected']==200 and len(result['results'])==200)
        check('all_1000_original_null_follow_pin_dcs_exact_restore',await states(loop_ids)=={k:baseline[k] for k in loop_ids})
        restore_count=await db.fetchval("SELECT count(*) FROM sys_audit_log WHERE operation_type='LOOP_BATCH_RESTORE' AND target_id=ANY($1::text[])",loop_ids)
        repeated=await data('restore-replay','POST',f'/loops/batch-snapshots/{snapshots[0]}/restore',owner,{'reason':RUN,'loopIds':chunks[0],'dryRun':False})
        repeat_count=await db.fetchval("SELECT count(*) FROM sys_audit_log WHERE operation_type='LOOP_BATCH_RESTORE' AND target_id=ANY($1::text[])",loop_ids)
        check('restore_replay_no_duplicate_or_rewrite',repeated['affected']==0 and all(r['status']=='ALREADY_RESTORED' for r in repeated['results']) and restore_count==repeat_count==1000)

        # Ownership and permission checks use genuine logins/current subjects.
        for path,method,payload in [(f'/loops/batch-snapshots/{snapshots[0]}','GET',None),(f'/loops/batch-snapshots/{snapshots[0]}/restore','POST',{'reason':RUN,'dryRun':False})]:
            status,_=await request('other-owner-denied',method,path,tokens['peer'],payload)
            check('other_admin_cannot_claim_snapshot_'+method,status==404)
        for path,method,payload in [('/loops/batch-snapshots','GET',None),(f'/loops/batch-snapshots/{snapshots[0]}','GET',None),(f'/loops/batch-snapshots/{snapshots[0]}/restore','POST',{'reason':RUN,'dryRun':True}),('/loops/batch-config','POST',prepared[0])]:
            status,_=await request('sponsor-denied',method,path,tokens['viewer'],payload)
            check('sponsor_denied_'+method+'_'+path,status==403)
        status,_=await request('unauthenticated-denied','GET','/loops/batch-snapshots')
        check('no_token_denied',status==401)
        await db.execute('UPDATE sys_user SET username=$2 WHERE id=$1::uuid',users['owner'],RUN+'-renamed')
        await db.execute('UPDATE sys_user SET username=$2 WHERE id=$1::uuid',users['peer'],owner_name)
        status,_=await request('renamed-stable-owner','GET',f'/loops/batch-snapshots/{snapshots[0]}',owner)
        check('owner_rename_retains_stable_id_ownership',status==200)
        status,_=await request('same-name-new-id-denied','GET',f'/loops/batch-snapshots/{snapshots[0]}',tokens['peer'])
        check('same_username_different_id_cannot_claim',status==404)
        await db.execute('UPDATE sys_user SET is_active=FALSE WHERE id=$1::uuid',users['owner'])
        try:
            status,_=await request('revoked-subject-replay-denied','POST','/loops/batch-config',owner,prepared[0])
            check('disabled_subject_cannot_replay_old_success',status in (401,403))
        finally:
            await db.execute('UPDATE sys_user SET is_active=TRUE WHERE id=$1::uuid',users['owner'])

        # Scope is established only on our own new user; moving our own row outside it
        # tests current scope on replay without editing global role matrices/revision.
        scoped_payload,_=await preview([aux_ids[1]],updates)
        scoped=await data('scope-seed-execute','POST','/loops/batch-config',owner,scoped_payload)
        await db.execute('UPDATE loop_ledger SET unit_id=$2::uuid WHERE id=$1::uuid',aux_ids[1],unit_b)
        try:
            status,_=await request('scope-replay-denied','POST','/loops/batch-config',owner,scoped_payload)
            check('idempotent_replay_rechecks_current_scope',status==403)
            status,_=await request('scope-restore-denied','POST',f'/loops/batch-snapshots/{scoped["snapshotId"]}/restore',owner,{'reason':RUN,'dryRun':False})
            check('restore_rechecks_current_scope',status==403)
        finally:
            await db.execute('UPDATE loop_ledger SET unit_id=$2::uuid WHERE id=$1::uuid',aux_ids[1],unit_a)

        # ABA edit and real FOR UPDATE race, confined to auxiliary owned loops.
        aba_payload,_=await preview([aux_ids[2]],updates)
        original=baseline[aux_ids[2]]['control_action_direction']
        await db.execute("UPDATE loop_ledger SET control_action_direction='DIRECT',updated_at=clock_timestamp() WHERE id=$1::uuid",aux_ids[2])
        await db.execute('UPDATE loop_ledger SET control_action_direction=$2,updated_at=clock_timestamp() WHERE id=$1::uuid',aux_ids[2],original)
        result=await data('aba-conflict','POST','/loops/batch-config',owner,aba_payload)
        check('ABA_detected_even_same_engineering_values',result['affected']==0 and result['results'][0]['status']=='CONFLICT')
        lock_payload,_=await preview([aux_ids[3]],updates)
        lock_db=await asyncpg.connect(**connection_options)
        tx=lock_db.transaction(); await tx.start()
        try:
            await lock_db.fetchrow('SELECT id FROM loop_ledger WHERE id=$1::uuid FOR UPDATE',aux_ids[3])
            job=asyncio.create_task(data('locked-row-concurrent-write','POST','/loops/batch-config',owner,lock_payload))
            await asyncio.sleep(0.2)
            waited=not job.done()
            await lock_db.execute("UPDATE loop_ledger SET control_action_direction='REVERSE',updated_at=clock_timestamp() WHERE id=$1::uuid",aux_ids[3])
            await tx.commit()
            result=await job
            check('real_pg_row_lock_then_reread_conflict',waited and result['affected']==0 and result['results'][0]['status']=='CONFLICT')
        finally:
            if not tx._state.name == 'COMMITTED':
                await tx.rollback()
            await lock_db.close()
        conflict_payload,_=await preview(aux_ids[4:6],updates)
        conflict=await data('partial-seed','POST','/loops/batch-config',owner,conflict_payload)
        await db.execute('UPDATE loop_ledger SET op_output_upper_limit=88,updated_at=clock_timestamp() WHERE id=$1::uuid',aux_ids[4])
        result=await data('partial-restore-conflict','POST',f'/loops/batch-snapshots/{conflict["snapshotId"]}/restore',owner,{'reason':RUN,'dryRun':False})
        check('partial_conflict_preserves_later_edit',result['affected']==1 and [r['status'] for r in result['results']]==['CONFLICT','OK'] and (await states([aux_ids[4]]))[aux_ids[4]]['op_output_upper_limit']==88)
        # Missing original template is a per-row failure; no arbitrary client before state.
        await db.execute('UPDATE loop_ledger SET engineering_template_id=$2::uuid,engineering_template_version=1 WHERE id=$1::uuid',aux_ids[6],templates['deleted'])
        deleted_payload,_=await preview([aux_ids[6]],updates)
        deleted=await data('missing-template-seed','POST','/loops/batch-config',owner,deleted_payload)
        await db.execute('DELETE FROM engineering_template_version WHERE template_id=$1::uuid',templates['deleted'])
        await db.execute('DELETE FROM engineering_template WHERE id=$1::uuid',templates['deleted'])
        result=await data('missing-original-template','POST',f'/loops/batch-snapshots/{deleted["snapshotId"]}/restore',owner,{'reason':RUN,'dryRun':False})
        check('missing_original_template_fails_explicitly',result['affected']==0 and result['results'][0]['status']=='FAIL')
        status,_=await request('oversized-201-denied','POST','/loops/batch-config',owner,{'loopIds':loop_ids[:201],'updates':updates,'operationId':str(uuid4()),'dryRun':True})
        check('request_201_rejected',status==422)
        status,_=await request('foreign-loop-restore-denied','POST',f'/loops/batch-snapshots/{snapshots[0]}/restore',owner,{'reason':RUN,'loopIds':[aux_ids[11]],'dryRun':False})
        check('cannot_restore_arbitrary_loop',status==422)
        RESULT['final1000States']=await states(loop_ids)
        RESULT['status']='PASS' if all(c['passed'] for c in RESULT['checks']) else 'REWORK'
    except BaseException as exc:
        RESULT['fatal']={'type':type(exc).__name__,'message':str(exc)}
        RESULT['status']='BLOCKED_BY_EXCEPTION'
        raise
    finally:
        RESULT['finishedAt']=datetime.now(timezone.utc).isoformat(); save()
        await client.aclose(); await db.close()


if __name__=='__main__':
    asyncio.run(main())
