"""A local TCP proxy for injecting network faults between a client and PostgreSQL. Test-only."""

from __future__ import annotations

import selectors
import socket
import threading


class FaultProxy:
    """Forwards everything until told otherwise.

    ``drop_client_after``: once client bytes containing this token have been forwarded, the client's socket is closed,
    while the server side stays open, so the server still finishes the statement and the reply is lost.
    ``blackhole``: stops forwarding in both directions, with every socket left open, like an unresponsive server.
    """

    def __init__(self, target_host: str, target_port: int) -> None:
        self.target = (target_host, target_port)
        self.drop_client_after: bytes | None = None
        self.blackhole = False
        self.dropped = threading.Event()
        self._server = socket.create_server(("127.0.0.1", 0))
        self.port = self._server.getsockname()[1]
        self._closed = threading.Event()
        self._sockets: list[socket.socket] = []
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def _accept(self) -> None:
        while not self._closed.is_set():
            try:
                client, _ = self._server.accept()
            except OSError:
                return
            upstream = socket.create_connection(self.target)
            self._sockets += [client, upstream]
            threading.Thread(target=self._pump, args=(client, upstream), daemon=True).start()

    def _pump(self, client: socket.socket, upstream: socket.socket) -> None:
        selector = selectors.DefaultSelector()
        selector.register(client, selectors.EVENT_READ)
        selector.register(upstream, selectors.EVENT_READ)
        client_open = True
        try:
            while not self._closed.is_set():
                for key, _ in selector.select(timeout=0.05):
                    if self.blackhole:
                        continue
                    data = key.fileobj.recv(65536)
                    if not data:
                        return
                    if key.fileobj is client:
                        upstream.sendall(data)
                        token = self.drop_client_after
                        if token is not None and token in data and not self.dropped.is_set():
                            self.dropped.set()
                            selector.unregister(client)
                            client.close()
                            client_open = False
                    elif client_open:
                        client.sendall(data)
        except OSError:
            return
        finally:
            selector.close()
            upstream.close()
            if client_open:
                client.close()

    def close(self) -> None:
        self._closed.set()
        self._server.close()
        for sock in self._sockets:
            try:
                sock.close()
            except OSError:
                pass
