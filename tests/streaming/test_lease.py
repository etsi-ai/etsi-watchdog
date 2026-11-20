# tests/streaming/test_lease.py

import pytest
import fakeredis
from etsi.watchdog.streaming.state.lease import RedisOwnerLease
from etsi.watchdog.streaming.state.base import LeaseAcquisitionError, LeaseLostError


@pytest.fixture
def fake_redis_client():
    server = fakeredis.FakeServer()
    client = fakeredis.FakeRedis(server=server, decode_responses=False)
    yield client
    client.close()


def test_lease_acquisition_and_release(fake_redis_client):
    owner_key = "wd:v1:group_a:owner"
    lease1 = RedisOwnerLease(fake_redis_client, owner_key=owner_key, lease_ttl_seconds=10.0, token="inst-1")

    assert not lease1.is_held()
    assert lease1.acquire(wait_timeout_seconds=1.0)
    assert lease1.is_held()
    assert lease1.get_owner() == "inst-1"

    # Second instance cannot acquire
    lease2 = RedisOwnerLease(fake_redis_client, owner_key=owner_key, lease_ttl_seconds=10.0, token="inst-2")
    with pytest.raises(LeaseAcquisitionError):
        lease2.acquire(wait_timeout_seconds=0.2, poll_interval_seconds=0.05)

    # First instance releases
    assert lease1.release()
    assert not lease1.is_held()
    assert lease1.get_owner() is None

    # Now second instance can acquire immediately
    assert lease2.acquire(wait_timeout_seconds=1.0)
    assert lease2.is_held()
    assert lease2.get_owner() == "inst-2"


def test_lease_renew_and_lost_detection(fake_redis_client):
    owner_key = "wd:v1:group_b:owner"
    lease = RedisOwnerLease(fake_redis_client, owner_key=owner_key, lease_ttl_seconds=10.0, token="inst-active")

    assert lease.acquire(wait_timeout_seconds=1.0)
    assert lease.renew()

    # Simulate token override by another entity or expiration
    fake_redis_client.set(owner_key, b"inst-hijacker")

    with pytest.raises(LeaseLostError):
        lease.renew()


def test_lease_expiry_allows_takeover(fake_redis_client):
    owner_key = "wd:v1:group_c:owner"
    lease1 = RedisOwnerLease(fake_redis_client, owner_key=owner_key, lease_ttl_seconds=0.2, token="inst-short")
    assert lease1.acquire(wait_timeout_seconds=1.0)

    # Wait for natural TTL expiry in fake server
    # fakeredis FakeServer does not advance real time automatically unless we wait or use pexpire
    import time
    time.sleep(0.3)

    lease2 = RedisOwnerLease(fake_redis_client, owner_key=owner_key, lease_ttl_seconds=5.0, token="inst-next")
    assert lease2.acquire(wait_timeout_seconds=1.0, poll_interval_seconds=0.05)
    assert lease2.is_held()
