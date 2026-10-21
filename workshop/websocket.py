"""Minimal RFC 6455 WebSocket (server side, standard library only).

Supports text/binary messages, fragmentation, ping/pong and close. Client frames must be masked.
"""
import base64
import hashlib
import struct
import threading

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
TEXT, BINARY, CLOSE, PING, PONG, CONT = 0x1, 0x2, 0x8, 0x9, 0xA, 0x0


class WebSocketClosed(Exception):
    """The connection ended (clean close, EOF, or a protocol violation)."""


def accept_key(client_key):
    return base64.b64encode(hashlib.sha1((client_key + GUID).encode("ascii")).digest()).decode("ascii")


def _read_exact(stream, count):
    data = b""
    while len(data) < count:
        chunk = stream.read(count - len(data))
        if not chunk:
            raise WebSocketClosed("connection closed")
        data += chunk
    return data


class WebSocket:
    def __init__(self, rfile, wfile, max_message=1 << 20):
        self.rfile, self.wfile, self.max_message = rfile, wfile, max_message
        self._send_lock = threading.Lock()
        self.closed = False

    # --- sending ----------------------------------------------------------------------------
    def _send_frame(self, opcode, payload):
        header = bytes([0x80 | opcode])
        size = len(payload)
        if size < 126:
            header += bytes([size])
        elif size < 1 << 16:
            header += bytes([126]) + struct.pack(">H", size)
        else:
            header += bytes([127]) + struct.pack(">Q", size)
        with self._send_lock:
            self.wfile.write(header + payload)
            self.wfile.flush()

    def send(self, data, opcode=BINARY):
        if isinstance(data, str):
            data, opcode = data.encode("utf-8"), TEXT
        self._send_frame(opcode, data)

    def close(self, code=1000):
        if not self.closed:
            self.closed = True
            try:
                self._send_frame(CLOSE, struct.pack(">H", code))
            except (OSError, ValueError):
                pass

    # --- receiving --------------------------------------------------------------------------
    def _read_frame(self):
        first, second = _read_exact(self.rfile, 2)
        fin, rsv, opcode = first & 0x80, first & 0x70, first & 0x0F
        if rsv or not second & 0x80:
            self.close(1002)
            raise WebSocketClosed("protocol error (reserved bits or unmasked client frame)")
        size = second & 0x7F
        if size == 126:
            size = struct.unpack(">H", _read_exact(self.rfile, 2))[0]
        elif size == 127:
            size = struct.unpack(">Q", _read_exact(self.rfile, 8))[0]
        if size > self.max_message or (opcode >= 0x8 and (size > 125 or not fin)):
            self.close(1009)
            raise WebSocketClosed("frame too large or malformed control frame")
        mask = _read_exact(self.rfile, 4)
        payload = bytearray(_read_exact(self.rfile, size))
        for i in range(size):
            payload[i] ^= mask[i & 3]
        return bool(fin), opcode, bytes(payload)

    def recv(self):
        """-> (opcode, payload) of the next complete data message. Control frames are handled here."""
        message, kind = b"", None
        while True:
            fin, opcode, payload = self._read_frame()
            if opcode == PING:
                self._send_frame(PONG, payload)
            elif opcode == PONG:
                continue
            elif opcode == CLOSE:
                self.close(1000)
                raise WebSocketClosed("closed by peer")
            elif opcode in (TEXT, BINARY):
                if kind is not None:
                    self.close(1002)
                    raise WebSocketClosed("new message inside a fragmented one")
                kind, message = opcode, payload
                if fin:
                    return kind, message
            elif opcode == CONT:
                if kind is None:
                    self.close(1002)
                    raise WebSocketClosed("unexpected continuation frame")
                message += payload
                if len(message) > self.max_message:
                    self.close(1009)
                    raise WebSocketClosed("message too large")
                if fin:
                    return kind, message
            else:
                self.close(1002)
                raise WebSocketClosed("unknown opcode")
