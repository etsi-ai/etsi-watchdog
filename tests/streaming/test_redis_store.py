# tests/streaming/test_redis_store.py

import pytest
import fakeredis
from etsi.watchdog.streaming.state.redis_store import RedisStateStore
from etsi.watchdog.streaming.state.base import FenceMismatchError, StateStoreError


@pytest.fixture
def fake_redis_client():
    server = fakeredis.FakeServer()
    client = fakeredis.FakeRedis(server=server, decode_responses=False)
    yield client
    client.close()


@pytest.fixture
def redis_store(fake_redis_client):
    owner_key = "wd:v1:test_group:owner"
    fake_redis_client.set(owner_key, b"token-owner-1")
    store = RedisStateStore(redis_client=fake_redis_client, owner_key=owner_key)
    yield store
    store.close()


def test_redis_store_basic_crud(redis_store):
    # Initial state
    assert redis_store.get("k1") is None
    assert redis_store.get_many(["k1", "k2"]) == {}

    # Write batch without fence
    redis_store.write_batch(
        puts={"k1": b"val1", "k2": b"val2", "other:k3": b"val3"},
        deletes=[],
    )

    assert redis_store.get("k1") == b"val1"
    assert redis_store.get("k2") == b"val2"
    assert redis_store.get_many(["k1", "k2", "nonexistent"]) == {"k1": b"val1", "k2": b"val2"}

    # Prefix scan
    assert redis_store.list_prefix("k") == ["k1", "k2"]
    assert redis_store.list_prefix("other:") == ["other:k3"]

    # Delete via write_batch
    redis_store.write_batch(puts={}, deletes=["k1"])
    assert redis_store.get("k1") is None
    assert redis_store.get("k2") == b"val2"


def test_redis_store_type_validation(redis_store):
    with pytest.raises(TypeError, match="must be bytes"):
        redis_store.write_batch(puts={"k1": "string_not_bytes"}, deletes=[])  # type: ignore


def test_redis_store_fencing_success(redis_store, fake_redis_client):
    owner_key = "wd:v1:test_group:owner"
    fake_redis_client.set(owner_key, b"valid-fence-token")

    redis_store.write_batch(
        puts={"k1": b"val1", "k2": b"val2"},
        deletes=[],
        fence="valid-fence-token",
    )

    assert redis_store.get("k1") == b"val1"
    assert redis_store.get("k2") == b"val2"


def test_redis_store_fencing_mismatch_atomicity(redis_store, fake_redis_client):
    owner_key = "wd:v1:test_group:owner"
    fake_redis_client.set(owner_key, b"real-owner-token")

    # Set an initial value
    redis_store.write_batch(puts={"existing": b"initial"}, deletes=[])

    # Attempt write with mismatched fence token
    with pytest.raises(FenceMismatchError, match="fence mismatch"):
        redis_store.write_batch(
            puts={"existing": b"overwritten", "new_key": b"new_val"},
            deletes=["existing"],
            fence="stale-zombie-token",
        )

    # Verify atomic rollback: nothing was applied
    assert redis_store.get("existing") == b"initial"
    assert redis_store.get("new_key") is None


def test_redis_store_ttl_application(redis_store, fake_redis_client):
    owner_key = "wd:v1:test_group:owner"
    fake_redis_client.set(owner_key, b"token-1")

    redis_store.write_batch(
        puts={"temp_key": b"temp_val", "perm_key": b"perm_val"},
        deletes=[],
        ttl={"temp_key": 60},
        fence="token-1",
    )

    assert redis_store.get("temp_key") == b"temp_val"
    assert redis_store.get("perm_key") == b"perm_val"

    ttl_temp = fake_redis_client.ttl("temp_key")
    assert 0 < ttl_temp <= 60
    assert fake_redis_client.ttl("perm_key") == -1  # No TTL
