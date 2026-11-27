"""Opaque WebSocket relay whose lifetime is bound to one manual login window."""

import base64
import select
import socket
from urllib.parse import parse_qs, urlsplit


def relay(handler, access, upstream_address=('127.0.0.1', 6080)):
    """Keep websockify private and revoke even an already upgraded idle connection."""
    upstream = None
    client = handler.connection
    upgraded = False
    try:
        key = handler.headers.get('Sec-WebSocket-Key', '')
        if (handler.headers.get('Upgrade', '').lower() != 'websocket'
                or handler.headers.get('Sec-WebSocket-Version') != '13'
                or len(base64.b64decode(key, validate=True)) != 16):
            raise ValueError('invalid_websocket')
        upstream = socket.create_connection(upstream_address, timeout=2)
        client.settimeout(2)
        query = parse_qs(urlsplit(handler.path).query)
        window_id = query.get('window', [None])[0]
        lease = access.register_connections(handler.headers.get('Cookie'), client, upstream, window_id=window_id)
        headers = ['GET /websockify HTTP/1.1', 'Host: 127.0.0.1', 'Upgrade: websocket',
                   'Connection: Upgrade', 'Sec-WebSocket-Version: 13', 'Sec-WebSocket-Key: ' + key]
        protocol = handler.headers.get('Sec-WebSocket-Protocol')
        if protocol:
            if protocol not in {'binary', 'base64', 'binary, base64'}:
                raise ValueError('invalid_protocol')
            headers.append('Sec-WebSocket-Protocol: ' + protocol)
        access.forward(lease, upstream, ('\r\n'.join(headers) + '\r\n\r\n').encode('ascii'))
        response = b''
        while b'\r\n\r\n' not in response:
            data = upstream.recv(4096)
            if not data or len(response) + len(data) > 16384:
                raise ValueError('invalid_upstream_handshake')
            response += data
        if response.split(b'\r\n', 1)[0].split(b' ')[1:2] != [b'101']:
            raise ValueError('upstream_upgrade_failed')
        access.forward(lease, client, response)
        upgraded = True
        while access.connection_live(lease):
            readable, _, _ = select.select([client, upstream], [], [], 0.2)
            for source in readable:
                data = source.recv(65536)
                if not data:
                    return
                access.forward(lease, upstream if source is client else client, data)
    except (OSError, ValueError, UnicodeError):
        if not upgraded:
            try:
                handler.reply(401, {'error': '窗口授权无效或远程通道不可用，请重新申请入口'})
            except OSError:
                pass
    finally:
        access.unregister_connections(client, upstream)
        if upstream is not None:
            upstream.close()
        handler.close_connection = True
