"""Offline-only negative control: remove updated_at from state token in memory."""
import hashlib

def pytest_configure(config):
    from app.services import loop_batch_restore as service
    service._token = lambda loop: hashlib.sha256(service._json(service._state(loop)).encode()).hexdigest()
