"""EXPERIMENTAL trusted fixture: the async (``psycopg.AsyncConnection``) version of ``reservations_mixed``.

The same application, written as asyncio code. It has the same schema, expand migration, statements and defect as
the sync module, and its handlers are ``async def f(conn, **args)``. The buggy backfill reads ``reserved`` and writes
the value it read in a later statement; the fixed one reads ``FOR UPDATE``. Reset, the independent oracle (I1–I4) and
the state digest are the sync fixture's own, so the two engines are compared on one specification.
"""

from __future__ import annotations

from typing import Any

from .. import oracle
from ..stepping import Handler
from . import reservations_mixed as sync_app
from .reservations_mixed import (
    BACKFILL_READ_BUGGY,
    BACKFILL_READ_FIXED,
    BACKFILL_WRITE,
    DELETE_RES,
    INSERT_RES,
    MIGRATION_TOUCHING,
    SELECT_RES,
    SELECT_V1,
    SELECT_V2,
    UPDATE_V1,
    UPDATE_V2,
    _allow,
)

SCHEMA = sync_app.SCHEMA
ASYNC = True
AUTOCOMMIT = True  # as the sync fixture: every transaction boundary is an explicit, gated call


async def _one(conn: Any, sql: str, params: tuple[Any, ...]) -> Any:
    cur = await conn.execute(sql, params)
    return await cur.fetchone()


async def reserve_v1(conn: Any, *, event_id: int, holder: str, qty: int) -> dict[str, Any]:
    async with conn.transaction():
        row = await _one(conn, SELECT_V1, (event_id,))
        if row is None:
            return {"status": "no_event"}
        capacity, reserved = row
        if reserved + qty > capacity:
            return {"status": "rejected", "available": capacity - reserved}
        await conn.execute(UPDATE_V1, (reserved + qty, event_id))
        rid = (await _one(conn, INSERT_RES, (event_id, holder, qty)))[0]
    return {"status": "reserved", "reservation": rid, "reserved_after": reserved + qty}


async def release_v1(conn: Any, *, reservation_id: int) -> dict[str, Any]:
    async with conn.transaction():
        row = await _one(conn, SELECT_RES, (reservation_id,))
        if row is None:
            return {"status": "no_reservation"}
        event_id, qty = row
        _capacity, reserved = await _one(conn, SELECT_V1, (event_id,))
        await conn.execute(UPDATE_V1, (reserved - qty, event_id))
        await conn.execute(DELETE_RES, (reservation_id,))
    return {"status": "released", "reserved_after": reserved - qty}


async def reserve_v2(conn: Any, *, event_id: int, holder: str, qty: int) -> dict[str, Any]:
    async with conn.transaction():
        row = await _one(conn, SELECT_V2, (event_id,))
        if row is None:
            return {"status": "no_event"}
        capacity, reserved, reserved_v2 = row
        if reserved + qty > capacity:
            return {"status": "rejected", "available": capacity - reserved}
        await conn.execute(UPDATE_V2, (reserved + qty, None if reserved_v2 is None else reserved_v2 + qty, event_id))
        rid = (await _one(conn, INSERT_RES, (event_id, holder, qty)))[0]
    return {"status": "reserved", "reservation": rid, "reserved_after": reserved + qty}


async def release_v2(conn: Any, *, reservation_id: int) -> dict[str, Any]:
    async with conn.transaction():
        row = await _one(conn, SELECT_RES, (reservation_id,))
        if row is None:
            return {"status": "no_reservation"}
        event_id, qty = row
        _capacity, reserved, reserved_v2 = await _one(conn, SELECT_V2, (event_id,))
        await conn.execute(UPDATE_V2, (reserved - qty, None if reserved_v2 is None else reserved_v2 - qty, event_id))
        await conn.execute(DELETE_RES, (reservation_id,))
    return {"status": "released", "reserved_after": reserved - qty}


async def _backfill(conn: Any, read: str, batch: int) -> dict[str, Any]:
    filled = 0
    while True:
        async with conn.transaction():
            cur = await conn.execute(read, (batch,))
            rows = await cur.fetchall()
            for event_id, reserved in rows:  # the value written is the value this job read
                await conn.execute(BACKFILL_WRITE, (reserved, event_id))
                filled += 1
        if len(rows) < batch:
            return {"status": "done", "filled": filled}


async def backfill_v2_buggy(conn: Any, *, batch: int = 10) -> dict[str, Any]:
    return await _backfill(conn, BACKFILL_READ_BUGGY, batch)


async def backfill_v2_fixed(conn: Any, *, batch: int = 10) -> dict[str, Any]:
    return await _backfill(conn, BACKFILL_READ_FIXED, batch)


def registry(variant: str) -> dict[tuple[str, str], Handler]:
    if variant not in ("buggy", "fixed"):
        raise ValueError("variant must be buggy or fixed")
    reserve_args = {"event_id": int, "holder": str, "qty": int}
    backfill = backfill_v2_buggy if variant == "buggy" else backfill_v2_fixed
    read = BACKFILL_READ_BUGGY if variant == "buggy" else BACKFILL_READ_FIXED
    handlers = [
        Handler("v1", "reserve", reserve_v1, _allow(SELECT_V1, UPDATE_V1, INSERT_RES), reserve_args),
        Handler("v1", "release", release_v1, _allow(SELECT_RES, SELECT_V1, UPDATE_V1, DELETE_RES),
                {"reservation_id": int}),
        Handler("v2", "reserve", reserve_v2, _allow(SELECT_V2, UPDATE_V2, INSERT_RES), reserve_args,
                MIGRATION_TOUCHING),
        Handler("v2", "release", release_v2, _allow(SELECT_RES, SELECT_V2, UPDATE_V2, DELETE_RES),
                {"reservation_id": int}, MIGRATION_TOUCHING),
        Handler("job", "backfill_v2", backfill, _allow(read, BACKFILL_WRITE), {"batch": int}, MIGRATION_TOUCHING),
    ]
    return {(h.app_version, h.name): h for h in handlers}


reset = sync_app.reset
read_state = oracle.read_state
expected_initial = oracle.expected_initial


def check(state: dict[str, list[dict[str, Any]]]) -> list[str]:
    return oracle.check(state, backfill_done=False)


def check_run(initial: Any, final: dict[str, list[dict[str, Any]]], results: dict[str, Any]) -> list[str]:
    done = any(isinstance(r, dict) and r.get("status") == "done" and "filled" in r for r in results.values())
    return oracle.check(final, backfill_done=done)
