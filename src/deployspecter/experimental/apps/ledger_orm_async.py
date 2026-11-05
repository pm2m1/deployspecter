"""EXPERIMENTAL trusted fixture: the ledger service on SQLAlchemy 2's asyncio extension and psycopg 3's async dialect.

The same model, schema, defect and oracle as ``ledger_orm``, written with ``AsyncEngine`` and ``AsyncSession``:
``await session.get``, attribute changes flushed as UPDATEs, ``session.add`` flushed as an INSERT ... RETURNING,
``await session.scalars(select(...))``, ``async with session.begin()``, explicit ``await session.commit()`` and
``rollback()``, and the pool's checkout and return.

Binding, without patching SQLAlchemy or psycopg: ``create_async_engine("postgresql+psycopg://",
async_creator=..., poolclass=StaticPool, use_native_hstore=False)``. The ``async_creator`` hands over the actor's one
``AsyncSteppedConnection`` exactly once, and only to the task that owns it. A second DBAPI connection, a creator
awaited from another task, or a pooled connection used by another task is refused. Native hstore is off because the
dialect's first connect would otherwise call psycopg's ``TypeInfo.fetch``, which builds a plain ``AsyncCursor`` that
bypasses ``cursor_factory``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from ..stepping import Handler, Refused
from . import ledger_orm as L
from .ledger_orm import Account, Entry, InsufficientFunds

SCHEMA = L.SCHEMA
ASYNC = True
AUTOCOMMIT = False


def bind_async_engine(conn: Any, *, poolclass: Any = StaticPool, **engine_kw: Any) -> Any:
    owner = asyncio.current_task()
    handed: list[Any] = []

    async def creator() -> Any:
        if asyncio.current_task() is not owner:
            raise Refused("the engine's creator was awaited from another task")
        if handed:
            raise Refused("the engine asked for a second DBAPI connection; only the actor's stepped connection exists")
        handed.append(conn)
        return conn

    kw: dict[str, Any] = {"use_native_hstore": False}
    kw.update(engine_kw)
    return create_async_engine("postgresql+psycopg://", async_creator=creator, poolclass=poolclass, **kw)


async def withdraw(session: AsyncSession, *, account_id: int, amount: int, lock: bool) -> dict[str, Any]:
    acct = await session.get(Account, account_id, with_for_update=lock)
    if acct is None:
        await session.rollback()
        return {"status": "no_account"}
    if acct.balance < amount:
        await session.rollback()
        return {"status": "insufficient"}
    acct.balance = acct.balance - amount  # computed in Python from what the read returned
    entry = Entry(account_id=account_id, delta=-amount)
    session.add(entry)
    await session.flush()
    entry_id = entry.id
    await session.commit()
    return {"status": "ok", "entry": entry_id}


async def deposit(session: AsyncSession, *, account_id: int, amount: int, lock: bool) -> dict[str, Any]:
    async with session.begin():
        acct = await session.get(Account, account_id, with_for_update=lock)
        if acct is None:
            raise LookupError(f"account {account_id}")
        acct.balance = acct.balance + amount
        session.add(Entry(account_id=account_id, delta=amount))
    return {"status": "ok"}


async def transfer(session: AsyncSession, *, src: int, dst: int, amount: int, lock: bool) -> dict[str, Any]:
    if src == dst:
        return {"status": "same_account"}
    stmt = select(Account).where(Account.id.in_([src, dst])).order_by(Account.id)
    if lock:
        stmt = stmt.with_for_update()
    async with session.begin():
        accounts = {a.id: a for a in await session.scalars(stmt)}
        if len(accounts) != 2:
            raise LookupError("account")
        if accounts[src].balance < amount:
            raise InsufficientFunds(f"account {src}")
        accounts[src].balance = accounts[src].balance - amount
        accounts[dst].balance = accounts[dst].balance + amount
        session.add_all([Entry(account_id=src, delta=-amount), Entry(account_id=dst, delta=amount)])
    return {"status": "ok"}


def _entry(fn: Any, *, lock: bool, poolclass: Any = StaticPool) -> Any:
    async def handler(conn: Any, **args: Any) -> Any:
        engine = bind_async_engine(conn, poolclass=poolclass)
        try:
            async with AsyncSession(engine) as session:
                return await fn(session, lock=lock, **args)
        except InsufficientFunds:
            return {"status": "insufficient"}
        finally:
            await engine.dispose(close=False)  # forget the pool; the harness closes the actor's connection
    handler.__name__ = fn.__name__
    handler.__qualname__ = fn.__qualname__
    handler.__module__ = fn.__module__
    return handler


def registry(variant: str, *, poolclass: Any = StaticPool) -> dict[tuple[str, str], Handler]:
    if variant not in ("buggy", "fixed"):
        raise ValueError("variant must be buggy or fixed")
    lock = variant == "fixed"
    sync = L.registry(variant)  # the same reviewed statements: the async dialect sends the same SQL
    out = {}
    for (version, name), fn in (("v1", "withdraw"), withdraw), (("v1", "deposit"), deposit), \
            (("v1", "transfer"), transfer):
        h = sync[(version, name)]
        out[(version, name)] = Handler(version, name, _entry(fn, lock=lock, poolclass=poolclass), h.allowed_sql,
                                       h.args, setup_sql=h.setup_sql)
    return out


reset = L.reset
read_state = L.read_state
expected_initial = L.expected_initial
check = L.check
