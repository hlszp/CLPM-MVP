"""Pure synthetic counterexamples for config admission, never actual evidence.

The same rejection assertions can be run against an explicit old/new source.
"""

from copy import deepcopy
import importlib.util
import os
from pathlib import Path
import sys

import pytest

import adapt_capacity_artifacts as adapter_module
from test_adapt_capacity_full_shape import fixture

if os.environ.get("CLPM_ADAPTER_IDENTITY_SOURCE"):
    spec=importlib.util.spec_from_file_location("identity_subject",os.environ["CLPM_ADAPTER_IDENTITY_SOURCE"])
    adapter_module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter_module)


@pytest.mark.parametrize("missing",["configDigest","algorithmVersion"])
def test_null_frozen_identity_rejected_at_configuration_admission(tmp_path,missing):
    _,_,old_scope,_,_,_=fixture(tmp_path,"a")
    root=tmp_path/"identity-originals"
    root.mkdir()
    def file(name,value):
        path=root/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(adapter_module.canonical(value))
        return path
    loops=[]
    for row in old_scope['loops'].values():
        loops.append({**deepcopy(row),'complex_loop_group_id':None,'is_active':True,'status':'READY','include_in_evaluation':True})
    tree=[{'id':node,'parent_id':None,'is_kpi_enabled':True} for node in old_scope['scopes']]
    roles=['PV','SP','OP','MODE','PID_P','PID_I','PID_D']
    mappings=[{'loop_id':row['id'],'tag_role':role,'tag_id':row['id']+'-'+role} for row in loops for role in roles]
    file('pg-raw/loop_ledger.json',loops)
    file('pg-raw/plant_node.json',tree)
    file('pg-raw/loop_tag_mapping.json',mappings)
    file('pg-raw/tag_registry.json',[{'id':row['tag_id']} for row in mappings])
    source=root/'sources/app/tasks/kpi_calc.py'
    source.parent.mkdir(parents=True)
    source.write_text("ALGORITHM_VERSION = 'fixture-v'\nasync def _do_calculate_node_kpi():\n    return select(PlantNode).where(PlantNode.is_kpi_enabled.is_(True))\n")
    digest=None if missing=='configDigest' else 'd'*64
    version=None if missing=='algorithmVersion' else 'fixture-v'
    config_path=file('config-freeze.json',{'status':'FROZEN','candidateSha':'a'*40,'configDigest':digest,'versions':{'algorithmVersion':version},'finishedAt':'2026-10-17T07:59:00+00:00','syntheticCounterexampleOnly':True})
    entries=[{'file':str(p.relative_to(root)),'bytes':p.stat().st_size,'sha256':adapter_module.digest(p.read_bytes())} for p in sorted(root.rglob('*')) if p.is_file()]
    manifest=file('sha256-manifest.json',{'files':entries})
    after=file('after.json',{'readonly':'on','isolation':'repeatable read','snapshot':'synthetic','observedAt':'2026-10-17T08:01:00+00:00','loops':loops,'tree':tree})
    isolation=file('isolation.json',{'syntheticCounterexampleOnly':True})
    subject=adapter_module.Adapter(tmp_path,tmp_path/'new-identity-sidecars')
    plan={'candidateSha':'a'*40,'configDigest':digest,'configFreezeManifest':subject.ref(manifest),'configFreeze':subject.ref(config_path),'nodeScopeAfter':subject.ref(after),'configurationIsolation':subject.ref(isolation)}
    with pytest.raises(adapter_module.MissingEvidence):
        subject.config(plan)
