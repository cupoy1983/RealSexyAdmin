"""Real TCP/WebSocket-upgrade boundary: revoke an already connected idle viewer."""

import socket
import threading
from http.server import ThreadingHTTPServer
from socketserver import BaseRequestHandler, ThreadingTCPServer
from types import SimpleNamespace

import pytest

from app.plugin.infra.crawler import login_websocket
from app.plugin.infra.crawler.login_access import LoginAccess, ticket
from app.plugin.infra.crawler.login_service import handler


class EchoWebsocket(BaseRequestHandler):
    def handle(self):
        self.request.settimeout(3)
        headers = b''
        try:
            while b'\r\n\r\n' not in headers:
                data = self.request.recv(4096)
                if not data:
                    return
                headers += data
            self.request.sendall(b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n')
            while data := self.request.recv(4096):
                self.request.sendall(data)
        except OSError:
            pass


def connect(server, cookie, *, window_id, origin=None):
    client = socket.create_connection(server.server_address, timeout=3)
    client.settimeout(3)
    host = f'127.0.0.1:{server.server_port}'
    request = (f'GET /vnc?window={window_id} HTTP/1.1\r\nHost: {host}\r\nOrigin: {origin or "http://" + host}\r\n'
               f'Cookie: {cookie}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
               'Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n')
    client.sendall(request.encode('ascii'))
    response = b''
    while b'\r\n\r\n' not in response:
        data = client.recv(4096)
        if not data:
            break
        response += data
    return client, response


@pytest.mark.parametrize('end', ['cancel', 'expire', 'replace'])
def test_active_viewer_cannot_cross_window_boundary(tmp_path, monkeypatch, end):
    now = [1000]
    access = LoginAccess(tmp_path / 'key.json', clock=lambda: now[0])
    access.begin_window({'platform': '1688', 'account': 'a', 'expires_at': 1900})
    cookie = 'collector_login=' + access.exchange(ticket(access.key, now[0]))
    original_window = access.window_id
    with ThreadingTCPServer(('127.0.0.1', 0), EchoWebsocket) as upstream:
        upstream.daemon_threads = True
        original = login_websocket.relay
        monkeypatch.setattr(login_websocket, 'relay', lambda h, a: original(h, a, upstream.server_address))
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(SimpleNamespace(), None, access))
        server.daemon_threads = True
        threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (upstream, server)]
        for thread in threads:
            thread.start()
        client = fresh = denied = None
        try:
            client, response = connect(server, cookie, window_id=original_window)
            assert response.startswith(b'HTTP/1.1 101')
            # One valid masked binary frame; the relay treats WebSocket bytes as opaque.
            frame = b'\x82\x81\x00\x00\x00\x00x'
            client.sendall(frame)
            assert client.recv(len(frame)) == frame
            if end == 'expire':
                now[0] = 1900
            elif end == 'replace':
                access.begin_window({'platform': '1688', 'account': 'b', 'expires_at': 2800})
            else:
                access.close_window()
            # No further client traffic: even idle upgraded sockets must be closed.
            assert client.recv(1) == b''
            assert not access.authorized(cookie)
            access.close_window()
            access.begin_window({'platform': '1688', 'account': 'b', 'expires_at': 2800})
            denied, response = connect(server, cookie, window_id=original_window)
            assert not response.startswith(b'HTTP/1.1 101')
            new_cookie = 'collector_login=' + access.exchange(ticket(access.key, now[0]))
            # Same browser's old tab automatically gets the new Cookie, but its
            # old viewer URL must still be rejected.
            denied.close()
            denied, response = connect(server, new_cookie, window_id=original_window)
            assert not response.startswith(b'HTTP/1.1 101')
            fresh, response = connect(server, new_cookie, window_id=access.window_id)
            assert response.startswith(b'HTTP/1.1 101')
            fresh.sendall(frame)
            assert fresh.recv(len(frame)) == frame
        finally:
            access.close_window()
            for connection in (client, fresh, denied):
                if connection is not None:
                    connection.close()
            server.shutdown()
            server.server_close()
            upstream.shutdown()
            for thread in threads:
                thread.join(timeout=3)
