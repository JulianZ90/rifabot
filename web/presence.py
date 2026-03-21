"""
In-memory presence system for numbered raffles.
Tracks which numbers each anonymous session is currently viewing/selecting,
and broadcasts updates to connected SSE clients.

Designed for single-process Railway deployment.
"""
from __future__ import annotations
import asyncio
import time
from collections import defaultdict

# {rifa_id: {uid: {numero: timestamp}}}
_sessions: dict[int, dict[str, dict[int, float]]] = defaultdict(lambda: defaultdict(dict))

# {rifa_id: [asyncio.Queue]}  — one queue per SSE connection
_sse_queues: dict[int, list[asyncio.Queue]] = defaultdict(list)

PRESENCE_TTL = 30  # seconds — drop stale entries

_COLORS = [
    "#ef4444", "#f97316", "#eab308", "#22c55e",
    "#3b82f6", "#8b5cf6", "#ec4899", "#06b6d4",
]


def uid_to_color(uid: str) -> str:
    return _COLORS[hash(uid) % len(_COLORS)]


def uid_to_initials(uid: str) -> str:
    """Short display label for the presence avatar."""
    # uid can be anything — just take last 2 chars as label
    return uid[-2:].upper() if len(uid) >= 2 else uid.upper()


def get_presence_snapshot(rifa_id: int) -> dict[int, list[dict]]:
    """
    Returns {numero: [{"uid": uid, "color": color, "initials": initials}, ...]}
    Prunes expired entries in-place.
    """
    now = time.time()
    result: dict[int, list[dict]] = defaultdict(list)
    rifa_sessions = _sessions.get(rifa_id, {})

    for uid, numbers in list(rifa_sessions.items()):
        for numero, ts in list(numbers.items()):
            if now - ts >= PRESENCE_TTL:
                del numbers[numero]
            else:
                result[numero].append({
                    "uid": uid,
                    "color": uid_to_color(uid),
                    "initials": uid_to_initials(uid),
                })
        if not numbers:
            del rifa_sessions[uid]

    return dict(result)


def _broadcast(rifa_id: int, data: dict) -> None:
    for q in list(_sse_queues.get(rifa_id, [])):
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            pass


async def update_presence(rifa_id: int, uid: str, numeros: list[int]) -> None:
    """
    Mark `uid` as selecting `numeros` for `rifa_id`.
    Clears any previously selected numbers for this uid first.
    Pass empty list to just clear.
    """
    now = time.time()
    _sessions[rifa_id][uid] = {n: now for n in numeros}
    if not numeros:
        _sessions[rifa_id].pop(uid, None)

    _broadcast(rifa_id, get_presence_snapshot(rifa_id))


async def clear_presence(rifa_id: int, uid: str) -> None:
    if rifa_id in _sessions:
        _sessions[rifa_id].pop(uid, None)
    _broadcast(rifa_id, get_presence_snapshot(rifa_id))
