"""Non-author document checks. Writes only this verifier's evidence folder."""
import datetime
import hashlib
import json
import re
from pathlib import Path

STATE = Path('/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态')
EVIDENCE = STATE / '终局整改-证据/直接整改复验-20261017T093240Z'
OWN = EVIDENCE / 'docs-non-author'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def numbered_matches(text, pattern):
    return [{'line': n, 'text': line} for n, line in enumerate(text.splitlines(), 1)
            if re.search(pattern, line)]


result = {'observedAt': datetime.datetime.now(datetime.timezone.utc).isoformat()}
backups = json.loads((EVIDENCE / 'f05/before-documents-manifest.json').read_text())
result['backups'] = []
for item in backups:
    actual = digest(Path(item['backup']))
    result['backups'].append({**item, 'actualSha256': actual, 'matches': actual == item['sha256']})
assert len(backups) == 7 and all(item['matches'] for item in result['backups'])

names = ['P7汇总与G7结论.md', '任务看板.md', '终局整改台账.md', '终局整改-遗留索引.md',
         '终局整改-运行来源台账.md', '终局整改-独立复验报告.md',
         'P6-任务状态/STATE.md', 'P6P7-对账矩阵与结论修正-20261017.md',
         '终局整改-整改后复验报告.md', '终局整改-直接整改执行.md']
result['documents'] = {}
for name in names:
    path = STATE / name
    content = path.read_text()
    result['documents'][name] = {
        'path': str(path), 'bytes': path.stat().st_size, 'sha256': digest(path),
        'historicalOrBlanketClaimMatches': numbered_matches(
            content, r'全部完成|全部通过|G0.{0,3}G7.{0,8}(全过|PASS)|已部署|本仓工作已尽|验收链全绿'),
    }

waiver_patterns = {
    'P6-07': r'^\|\s*P6-07.*跨仓',
    'IAR-A01-A28': r'^\|\s*IAR-A01.*A28.*全量',
    '岗位走查': r'^\|\s*真实岗位走查',
    '现场校准': r'^\|\s*P7-01.*现场校准',
}
result['waiverRows'] = {}
for name in names[:4]:
    content = (STATE / name).read_text()
    rows = {key: numbered_matches(content, pattern) for key, pattern in waiver_patterns.items()}
    for key, matched in rows.items():
        assert len(matched) == 1, (name, key, matched)
        line = matched[0]['text']
        assert '用户豁免' in line and ('未验' in line or '不等于' in line), (name, key, line)
        assert len(line.split('|')) >= 6, (name, key, line)
    assert '恢复' in content and 'G5' in content and '900s' in content and '600s' in content
    assert 'DEC-05' in content and 'D03' in content and 'D04' in content
    result['waiverRows'][name] = rows

state_text = (STATE / 'P6-任务状态/STATE.md').read_text()
result['checkpointPointers'] = {}
for cp in (61, 62, 71, 72):
    matches = numbered_matches(state_text, rf'^{cp}\.\s+CP')
    assert len(matches) == 1 and '→' in matches[0]['text']
    assert ('推翻' in matches[0]['text'] or 'CP74' in matches[0]['text'])
    result['checkpointPointers'][str(cp)] = matches[0]
assert 'CP80' in state_text and '历史' in state_text[:1500]

correction = (STATE / 'P6P7-对账矩阵与结论修正-20261017.md').read_text()
assert 'PRE-RULING SNAPSHOT' in correction[:1800] and 'SUPERSEDED' in correction[:1800]
result['correctionInvalidation'] = numbered_matches(correction, 'PRE-RULING|SUPERSEDED')

old_report = (STATE / '终局整改-独立复验报告.md').read_text()
marker = [line for line in old_report.splitlines() if line.startswith('> **历史输入标记')]
assert len(marker) == 1 and '不能将本报告中的PASS作为现行销项' in marker[0]
backup = next(Path(item['backup']) for item in backups if item['path'].endswith('/终局整改-独立复验报告.md'))
result['oldAuthorReportBodyPreserved'] = old_report.replace(marker[0] + '\n\n', '', 1) == backup.read_text()
assert result['oldAuthorReportBodyPreserved']

protected = {
    '终局对标检查报告.md': '517e91b57f2e2ff3c182be30d32ffb329d9c7b9397e2d3395188f190fe167016',
    '终局整改-非作者复验回执.md': '54cc82ca97e64fb7fd5314b14b41d5444ccaaf719b8478651d919e7a50f853e6',
}
result['protectedExpectedShaProvenance'] = '主协调本轮交接记录提供的开工前SHA；本脚本只独立重算当前文件，不能用当前单份文件自证过去。'
result['protected'] = []
for name, expected in protected.items():
    actual = digest(STATE / name)
    result['protected'].append({'path': str(STATE / name), 'expectedSha256': expected,
                                'actualSha256': actual, 'matches': actual == expected})
assert all(row['matches'] for row in result['protected'])

final_report = (STATE / '终局整改-整改后复验报告.md').read_text()
result['finalReportState'] = {'sha256': digest(STATE / '终局整改-整改后复验报告.md'),
                            'inProgress': '进行中' in final_report, 'text': final_report}
result['scope'] = 'F05/F06/F08 current document consistency only; final report not yet signed; F02 capacity and F03 actual business CI remain unverified.'
(OWN / 'documents-independent-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'backups': len(backups), 'documents': len(names), 'waiverDocuments': 4,
                  'checkpointPointers': [61, 62, 71, 72], 'protectedMatches': 2,
                  'oldAuthorReportBodyPreserved': result['oldAuthorReportBodyPreserved'],
                  'finalReportInProgress': result['finalReportState']['inProgress']}, ensure_ascii=False))
