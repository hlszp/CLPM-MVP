from pathlib import Path
import hashlib
import json
import yaml

root = Path('/Users/zhangping/DEV/clpm-remediation-20261017')
result = {'workflows': {}}
for path in (root / '.github/workflows').glob('*.yml'):
    document = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    result['workflows'][path.name] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                    'triggers': document['on'], 'permissions': document['permissions']}
    assert document['permissions'] == {'contents': 'read'}
    for job in document['jobs'].values():
        assert job['runs-on'] == 'ubuntu-latest' and 'environment' not in job
        assert job.get('env', {}).get('ENV') != 'production'
        for step in job['steps']:
            assert not any(token in step.get('uses', '').lower() for token in ['deploy', 'ssh', 'scp'])
    if path.name == 'ci.yml':
        critical = document['jobs']['critical-e2e']
        assert critical['env']['CLPM_TEST_MODE'] == '0'
        assert critical['env']['CLPM_CALC_SNAPSHOT_DIR'] == '${{ runner.temp }}/critical-e2e/calc-snapshots'
        assert critical['env']['CLPM_BUILD_CODE_SHA'] == '${{ github.sha }}'
        assert set(critical['services']) == {'postgres', 'redis', 'tdengine'}
        steps = critical['steps']
        configure = next(index for index, step in enumerate(steps) if '--configure-services' in step.get('run', ''))
        startup = next(index for index, step in enumerate(steps) if 'uvicorn app.main:app' in step.get('run', ''))
        assert configure < startup
        assert all(step.get('continue-on-error') != 'true' for step in steps)
        upload = next(step for step in steps if step.get('uses', '').startswith('actions/upload-artifact'))
        assert upload['if'] == 'always()' and upload['with']['if-no-files-found'] == 'error'
        assert 'backend/logs/' in upload['with']['path'] and '/tmp/critical-frontend.log' in upload['with']['path']
        result['critical'] = {'serviceConfigurationStep': configure, 'realLifespanStartupStep': startup,
                              'testMode': '0', 'services': sorted(critical['services']),
                              'workerLogsPreserved': True, 'criticalContinueOnError': False,
                              'productionDeploymentActions': []}
print(json.dumps(result, ensure_ascii=False, indent=2))
