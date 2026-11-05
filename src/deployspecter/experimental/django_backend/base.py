"""EXPERIMENTAL trusted Django backend: Django's own PostgreSQL ``DatabaseWrapper`` over the actor's stepped connection.

Django 5.2 opens connections in ``DatabaseWrapper.get_new_connection(conn_params)``, which calls
``psycopg.connect(**conn_params)`` (or ``self.pool.getconn()`` with ``OPTIONS["pool"]``), and builds cursors in
``create_cursor``. For a named cursor in client-binding mode it constructs ``ServerSideCursor(self.connection, ...)``
directly, which would bypass the connection's cursor method and so the gate. This subclass changes only those seams:

* ``get_new_connection`` returns the stepped connection the harness handed to this thread (``handover``), exactly once.
  A second or reopened connection, a different host, port or database, a connection whose cursors aren't the gated
  class for the configured binding mode, ``OPTIONS["pool"]`` or a non-default isolation level is refused.
* ``create_cursor(name)`` refuses server-side (named) cursors: ``QuerySet.iterator()`` fails closed unless
  ``DISABLE_SERVER_SIDE_CURSORS`` is set, in which case Django uses an ordinary, gated cursor.
* ``pool`` is never built.

Everything else (autocommit management, ``atomic`` and savepoints, the time-zone setup, the ORM's SQL) is Django's.
Nothing is patched; Django loads this module because ``ENGINE`` names it.
"""

from __future__ import annotations

import threading
from typing import Any

from django.db.backends.postgresql import base as pg

from ..stepping import Refused, SteppedClientCursor, SteppedConnection, SteppedCursor

_HANDOVER = threading.local()


def handover(conn: SteppedConnection) -> None:
    """Give this thread's next (and only) Django connection: called by the trusted handler wrapper."""
    _HANDOVER.conn, _HANDOVER.taken = conn, False


class GatedServerBindingCursor(pg.CursorMixin, SteppedCursor):
    """Django's server-side-binding cursor (``callproc`` and all), gated."""


class GatedClientCursor(pg.CursorMixin, SteppedClientCursor):
    """Django's default client-side-binding cursor, gated."""


def cursor_class(server_side_binding: bool) -> type:
    return GatedServerBindingCursor if server_side_binding else GatedClientCursor


class DatabaseWrapper(pg.DatabaseWrapper):
    @property
    def pool(self) -> None:  # Django's pool is never built; OPTIONS["pool"] is refused in get_new_connection
        return None

    def get_new_connection(self, conn_params: dict[str, Any]) -> SteppedConnection:
        options = self.settings_dict.get("OPTIONS", {})
        if options.get("pool"):
            raise Refused("Django connection pooling isn't supported: one dedicated stepped connection per actor")
        if options.get("isolation_level") is not None:
            raise Refused("a non-default isolation level isn't supported by the trusted backend")
        conn = getattr(_HANDOVER, "conn", None)
        if conn is None or getattr(_HANDOVER, "taken", True):
            raise Refused("Django asked for a connection this thread wasn't given (a second or reopened connection)")
        info = conn.info
        want = (conn_params.get("host"), int(conn_params.get("port") or 5432), conn_params.get("dbname"))
        if (info.host, info.port, info.dbname) != want:
            raise Refused(f"the settings target {want} isn't the actor's connection")
        binding = options.get("server_side_binding") is True
        if not issubclass(conn.cursor_factory, cursor_class(binding)):
            raise Refused("the actor's connection doesn't use the gated cursor for this binding mode")
        _HANDOVER.taken = True
        self.isolation_level = pg.IsolationLevel.READ_COMMITTED
        return conn

    def create_cursor(self, name: str | None = None) -> Any:
        if name:
            raise Refused("server-side (named) cursors aren't supported; set DISABLE_SERVER_SIDE_CURSORS")
        return super().create_cursor(name)
