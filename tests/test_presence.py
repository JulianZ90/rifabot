"""Tests for the in-memory presence system."""
import asyncio
import time

import pytest

from web.presence import (
    update_presence,
    clear_presence,
    get_presence_snapshot,
    uid_to_color,
    _sessions,
    _sse_queues,
    PRESENCE_TTL,
)


def _clear_all():
    _sessions.clear()
    _sse_queues.clear()


@pytest.fixture(autouse=True)
def clean_state():
    _clear_all()
    yield
    _clear_all()


# ─────────────────────────────────────────────
# get_presence_snapshot
# ─────────────────────────────────────────────

async def test_snapshot_empty():
    snap = get_presence_snapshot(rifa_id=1)
    assert snap == {}


async def test_snapshot_after_update():
    await update_presence(rifa_id=1, uid="userA", numeros=[5, 10])
    snap = get_presence_snapshot(rifa_id=1)
    assert 5 in snap
    assert 10 in snap
    assert any(v["uid"] == "userA" for v in snap[5])
    assert any(v["uid"] == "userA" for v in snap[10])


async def test_snapshot_multiple_users_same_number():
    await update_presence(rifa_id=1, uid="userA", numeros=[42])
    await update_presence(rifa_id=1, uid="userB", numeros=[42])
    snap = get_presence_snapshot(rifa_id=1)
    assert len(snap[42]) == 2
    uids = {v["uid"] for v in snap[42]}
    assert uids == {"userA", "userB"}


async def test_snapshot_prunes_expired_entries():
    await update_presence(rifa_id=1, uid="userA", numeros=[7])
    # Manually backdate the timestamp so it's expired
    _sessions[1]["userA"][7] = time.time() - PRESENCE_TTL - 1
    snap = get_presence_snapshot(rifa_id=1)
    assert 7 not in snap


# ─────────────────────────────────────────────
# update_presence
# ─────────────────────────────────────────────

async def test_update_replaces_previous_selection():
    await update_presence(rifa_id=1, uid="userA", numeros=[1, 2])
    await update_presence(rifa_id=1, uid="userA", numeros=[3])
    snap = get_presence_snapshot(rifa_id=1)
    assert 1 not in snap
    assert 2 not in snap
    assert 3 in snap


async def test_update_empty_list_clears_user():
    await update_presence(rifa_id=1, uid="userA", numeros=[5])
    await update_presence(rifa_id=1, uid="userA", numeros=[])
    snap = get_presence_snapshot(rifa_id=1)
    assert snap == {}


async def test_update_broadcasts_to_sse_queue():
    queue: asyncio.Queue = asyncio.Queue()
    _sse_queues[1].append(queue)

    await update_presence(rifa_id=1, uid="userA", numeros=[99])
    assert not queue.empty()
    data = queue.get_nowait()
    assert 99 in data


# ─────────────────────────────────────────────
# clear_presence
# ─────────────────────────────────────────────

async def test_clear_presence_removes_user():
    await update_presence(rifa_id=1, uid="userA", numeros=[10, 20])
    await clear_presence(rifa_id=1, uid="userA")
    snap = get_presence_snapshot(rifa_id=1)
    assert snap == {}


async def test_clear_presence_nonexistent_uid_ok():
    await clear_presence(rifa_id=1, uid="nobody")  # should not raise


# ─────────────────────────────────────────────
# uid_to_color
# ─────────────────────────────────────────────

def test_uid_to_color_deterministic():
    assert uid_to_color("abc") == uid_to_color("abc")


def test_uid_to_color_returns_valid_hex():
    color = uid_to_color("user123")
    assert color.startswith("#")
    assert len(color) == 7
