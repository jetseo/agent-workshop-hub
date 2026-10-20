import io
import socket
import struct
import unittest
from workshop.websocket import WebSocket, WebSocketClosed, accept_key, TEXT, BINARY, PING, CLOSE


def client_frame(opcode, payload=b"", fin=True, masked=True, rsv=0):
    first = (0x80 if fin else 0) | rsv | opcode
    size = len(payload)
    if size < 126:
        head = bytes([first, (0x80 if masked else 0) | size])
    elif size < 1 << 16:
        head = bytes([first, (0x80 if masked else 0) | 126]) + struct.pack(">H", size)
    else:
        head = bytes([first, (0x80 if masked else 0) | 127]) + struct.pack(">Q", size)
    if not masked:
        return head + payload
    mask = b"\x11\x22\x33\x44"
    return head + mask + bytes(b ^ mask[i & 3] for i, b in enumerate(payload))


def make(data, **kw):
    out = io.BytesIO()
    return WebSocket(io.BytesIO(data), out, **kw), out


class WebSocketTests(unittest.TestCase):
    def test_accept_key_matches_rfc_example(self):
        self.assertEqual(accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_masked_text_and_binary_messages_including_large(self):
        for payload in (b"hello", b"x" * 300, b"y" * 70000):
            ws, _ = make(client_frame(TEXT, payload))
            self.assertEqual(ws.recv(), (TEXT, payload))
        ws, _ = make(client_frame(BINARY, b"\x00\xff"))
        self.assertEqual(ws.recv(), (BINARY, b"\x00\xff"))

    def test_fragmented_message_is_reassembled(self):
        data = client_frame(TEXT, b"he", fin=False) + client_frame(0x0, b"llo")
        self.assertEqual(make(data)[0].recv(), (TEXT, b"hello"))

    def test_ping_gets_pong_and_close_is_acknowledged(self):
        ws, out = make(client_frame(PING, b"p") + client_frame(TEXT, b"x"))
        self.assertEqual(ws.recv(), (TEXT, b"x"))
        self.assertEqual(out.getvalue(), b"\x8a\x01p")
        ws, out = make(client_frame(CLOSE, struct.pack(">H", 1000)))
        with self.assertRaises(WebSocketClosed):
            ws.recv()
        self.assertEqual(out.getvalue()[0], 0x88)

    def test_protocol_violations_close_the_connection(self):
        cases = [client_frame(TEXT, b"x", masked=False),          # clients must mask
                 client_frame(TEXT, b"x", rsv=0x40),               # reserved bit
                 client_frame(0x0, b"x"),                          # continuation without start
                 client_frame(PING, b"z" * 200),                   # oversized control frame
                 client_frame(0x3, b"x")]                          # unknown opcode
        for data in cases:
            with self.assertRaises(WebSocketClosed, msg=data[:4]):
                make(data)[0].recv()

    def test_message_size_limit_and_eof(self):
        with self.assertRaises(WebSocketClosed):
            make(client_frame(TEXT, b"x" * 2000), max_message=1000)[0].recv()
        with self.assertRaises(WebSocketClosed):
            make(b"\x81")[0].recv()          # truncated header

    def test_server_frames_are_unmasked_and_sized_correctly(self):
        ws, out = make(b"")
        ws.send("hi")
        ws.send(b"z" * 200)
        ws.send(b"w" * 70000)
        data = out.getvalue()
        self.assertEqual(data[:4], b"\x81\x02hi"[:4])
        self.assertEqual(data[4], 0x82)
        self.assertEqual(data[5], 126)
        self.assertEqual(struct.unpack(">H", data[6:8])[0], 200)

    def test_works_over_a_real_socket_pair(self):
        a, b = socket.socketpair()
        self.addCleanup(a.close); self.addCleanup(b.close)
        server = WebSocket(a.makefile("rb"), a.makefile("wb"))
        b.sendall(client_frame(TEXT, "한글".encode("utf-8")))
        self.assertEqual(server.recv(), (TEXT, "한글".encode("utf-8")))


if __name__ == "__main__":
    unittest.main()
