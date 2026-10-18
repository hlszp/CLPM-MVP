"""Read-only private-broker guard shared by Keeper operations."""
import hashlib
import re
from datetime import datetime, timezone
import redis


def private_broker_snapshot():
    broker = redis.Redis(host='127.0.0.1', port=17123, db=1, decode_responses=True)
    cache = redis.Redis(host='127.0.0.1', port=17123, db=0, decode_responses=True)
    try:
        queues = {key: broker.llen(key) for key in ('default', 'default\x06\x163', 'default\x06\x166', 'default\x06\x169', 'dead_letter', 'dead_letter\x06\x163', 'dead_letter\x06\x166', 'dead_letter\x06\x169')}
        unacked = [{'deliveryTag': key, 'rawSha256': hashlib.sha256(value.encode()).hexdigest()} for key, value in broker.hgetall('unacked').items()]
        tasks = {}
        for key in cache.scan_iter(match='task:*', count=100):
            if re.fullmatch(r'task:[0-9a-f-]{36}', key) and cache.type(key) == 'hash':
                tasks[key] = cache.hgetall(key)
        nonterminal = {key: value for key, value in tasks.items() if str(value.get('status', '')).upper() not in ('SUCCESS', 'FAILED', 'CANCELLED', 'COMPLETED')}
        return {'observedAt': datetime.now(timezone.utc).isoformat(), 'redisPort': 17123, 'brokerDb': 1, 'queues': queues, 'unacked': unacked, 'taskRecords': tasks, 'nonterminalTaskRecords': nonterminal, 'quiescent': not any(queues.values()) and not unacked and not nonterminal}
    finally:
        broker.close(); cache.close()
