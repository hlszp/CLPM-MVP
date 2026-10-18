"""Exercise actual frozen audit callback only; never run algorithms or sockets."""

import argparse
import ast
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    source = args.source.read_bytes()
    tree = ast.parse(source)
    entry = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
    guard = next(n for n in entry.body if isinstance(n, ast.FunctionDef) and n.name == 'no_network')
    namespace = {'network_attempts': []}
    exec(compile(ast.Module(body=[guard], type_ignores=[]), str(args.source), 'exec'), namespace)
    observations = []
    for event, values in [
        ('socket.connect', (None, ('192.0.2.1', 80))),
        ('socket.getaddrinfo', ('example.invalid', 80, 0, 0, 0)),
        ('socket.gethostbyname', ('example.invalid',)),
        ('socket.gethostbyaddr', ('192.0.2.1',)),
        ('socket.sendto', (None, b'input', ('192.0.2.1', 80))),
        ('socket.sendmsg', (None, ('192.0.2.1', 80))),
        ('import', ('app.main',)),
    ]:
        refused = False
        try:
            namespace['no_network'](event, values)
        except RuntimeError:
            refused = True
        observations.append({'event': event, 'refused': refused})
    result = {'sourceSha256': hashlib.sha256(source).hexdigest(), 'probeSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'events': observations, 'networkAttemptsRecorded': namespace['network_attempts'], 'actualSocketCalls': 0, 'productImports': 0, 'algorithmsInvoked': 0, 'allRequiredEventsRefused': all(item['refused'] for item in observations)}
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result))
    return 0 if result['allRequiredEventsRefused'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
