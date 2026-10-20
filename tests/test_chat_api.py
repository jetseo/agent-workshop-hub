import http.client
import json
import socket
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from workshop.chat_api import Context
from workshop.chat_store import ChatStore
from workshop.dashboard import make_handler
from workshop.providers import Providers
from workshop.queue import Queue

CSRF = "csrf-for-chat-tests"
SECRET = "sk-test-SECRETKEY-1234567890"


class MockOpenAI(BaseHTTPRequestHandler):
    """Tiny OpenAI-compatible server: /v1/models and /v1/chat/completions (plain and SSE)."""

    def log_message(self, *args):
        pass

    def _json(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.server.requests.append(("GET", self.path, self.headers.get("Authorization"), None))
        if self.path == "/v1/models":
            return self._json(200, {"data": [{"id": "demo-model"}, {"id": "demo-free:free"}, {"id": "other"}]})
        self._json(404, {"error": "nope"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode())
        self.server.requests.append(("POST", self.path, self.headers.get("Authorization"), body))
        model = body["model"]
        if model == "ratelimit":
            return self._json(429, {"error": {"message": "slow down"}})
        reply = "Echo: " + body["messages"][-1]["content"]
        if not body.get("stream"):
            return self._json(200, {"choices": [{"message": {"content": reply}}]})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        words = ["tok%d " % i for i in range(60)] if model == "slow" else [w + " " for w in reply.split(" ")]
        try:
            for word in words:
                chunk = {"choices": [{"delta": {"content": word}}]}
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                self.wfile.flush()
                time.sleep(0.15 if model == "slow" else 0.005)
            if model == "midfail":
                self.wfile.write(b'data: {"error": {"message": "upstream exploded"}}\n\n')
            self.wfile.write(b"data: [DONE]\n\n")
        except (BrokenPipeError, ConnectionResetError):
            self.server.disconnected = True


def start(handler, **kw):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.requests, server.disconnected = [], False
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class ApiBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        self.mock = start(MockOpenAI)
        self.addCleanup(lambda: (self.mock.shutdown(), self.mock.server_close()))
        self.mock_url = "http://127.0.0.1:%d/v1" % self.mock.server_port
        self.providers = Providers(d / "providers.json")
        self.ctx = Context(self.providers, ChatStore(d / "chats"))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(
            Queue(d / "q.db"), CSRF, str(d / "boards"), providers=self.providers, api_ctx=self.ctx))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close()))
        self.port = self.server.server_port
        self.transcript = []      # every response body we saw, to prove the token never appears

    def call(self, method, path, body=None, headers=None, raw_body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        hdrs = {"X-CSRF-Token": CSRF, "Content-Type": "application/json"}
        hdrs.update(headers or {})
        hdrs = {k: v for k, v in hdrs.items() if v is not None}
        data = raw_body if raw_body is not None else (json.dumps(body).encode() if body is not None else None)
        conn.request(method, "/api/v1" + path, body=data, headers=hdrs)
        response = conn.getresponse()
        text = response.read().decode("utf-8")
        self.transcript.append(text)
        return response.status, text

    def jcall(self, method, path, body=None, **kw):
        status, text = self.call(method, path, body, **kw)
        return status, json.loads(text)

    def sse(self, chat_id, body):
        status, text = self.call("POST", "/chats/" + chat_id + "/send", body)
        if status != 200:
            return status, json.loads(text)
        events = []
        for block in text.strip().split("\n\n"):
            lines = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line)
            events.append((lines["event"], json.loads(lines["data"])))
        return status, events

    def onboard(self, **extra):
        body = dict(service="custom", custom_name="mock", custom_url=self.mock_url, model="demo-model",
                    prompt="hi", save="on", key=SECRET)
        body.update(extra)
        status, result = self.jcall("POST", "/onboard/test", body)
        self.assertEqual(status, 200)
        return result

    def new_chat(self, model="mock/demo-model"):
        status, chat = self.jcall("POST", "/chats", {"model": model})
        self.assertEqual(status, 201)
        return chat["id"]


class AuthAndOnboardingTests(ApiBase):
    def test_every_api_call_needs_host_origin_and_csrf_header(self):
        self.assertEqual(self.call("GET", "/state", headers={"X-CSRF-Token": None})[0], 403)
        self.assertEqual(self.call("GET", "/state", headers={"X-CSRF-Token": "wrong"})[0], 403)
        self.assertEqual(self.call("GET", "/state", headers={"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.call("GET", "/state", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.call("POST", "/chats", {"model": ""}, headers={"X-CSRF-Token": "wrong"})[0], 403)
        self.assertEqual(self.call("GET", "/state")[0], 200)
        self.assertEqual(self.call("GET", "/state", headers={"Origin": "http://127.0.0.1:%d" % self.port})[0], 200)

    def test_request_shape_limits(self):
        self.assertEqual(self.call("POST", "/chats", raw_body=b"{not json")[0], 400)
        self.assertEqual(self.call("POST", "/chats", raw_body=b'{"model": ""}', headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.call("POST", "/chats", raw_body=b"x" * 300_000)[0], 413)
        self.assertEqual(self.call("GET", "/nothing")[0], 404)
        self.assertEqual(self.call("PUT", "/state")[0], 501)           # verbs the server does not implement
        self.assertEqual(self.call("DELETE", "/state")[0], 404)

    def test_onboarding_flow_lists_models_tests_saves_and_never_leaks_the_token(self):
        status, state = self.jcall("GET", "/state")
        self.assertFalse(state["onboarded"])
        self.assertIn("openrouter", [c["id"] for c in state["catalog"]])
        status, listing = self.jcall("POST", "/onboard/models", dict(service="custom", custom_name="mock",
                                                                     custom_url=self.mock_url, key=SECRET))
        self.assertEqual(listing["models"], ["demo-model", "demo-free:free", "other"])
        self.assertEqual(self.providers.load(), {})                 # listing models saves nothing
        result = self.onboard()
        self.assertTrue(result["ok"] and result["saved"], result)
        self.assertEqual(result["reply"], "Echo: hi")
        status, state = self.jcall("GET", "/state")
        provider = state["providers"][0]
        self.assertTrue(state["onboarded"])
        self.assertEqual((provider["name"], provider["models"], provider["consented"]), ("mock", ["demo-model"], True))
        self.assertTrue(provider["token"].startswith("saved "))
        # the mock saw the real token, but no API response ever contained it
        self.assertTrue(any(r[2] == "Bearer " + SECRET for r in self.mock.requests))
        self.assertFalse(any(SECRET in text for text in self.transcript))

    def test_failed_test_saves_nothing_and_cloud_needs_consent(self):
        bad = self.onboard(model="ratelimit")
        self.assertFalse(bad["ok"])
        self.assertIn("429", bad["error"])
        self.assertEqual(self.providers.load(), {})
        status, result = self.jcall("POST", "/onboard/test", dict(service="groq", key=SECRET, model="m", prompt="x"))
        self.assertFalse(result["ok"])
        self.assertIn("consent", result["error"].lower())

    def test_remove_provider_clears_token(self):
        self.onboard()
        self.assertEqual(self.jcall("POST", "/providers/remove", {"name": "mock"})[0], 200)
        self.assertEqual(self.providers.load(), {})
        self.assertNotIn(SECRET, self.providers.secrets_path.read_text(encoding="utf-8"))
        self.assertEqual(self.jcall("POST", "/providers/remove", {"name": "mock"})[0], 400)


class ChatFlowTests(ApiBase):
    def setUp(self):
        super().setUp()
        self.onboard()

    def test_streaming_send_persists_history_and_titles_the_chat(self):
        chat_id = self.new_chat()
        status, events = self.sse(chat_id, {"content": "안녕 하세요"})
        names = [e[0] for e in events]
        self.assertEqual(status, 200)
        self.assertEqual(names[0], "meta")
        self.assertEqual(names[-1], "done")
        self.assertGreater(names.count("delta"), 1)                  # arrives as several chunks
        self.assertEqual("".join(e[1]["t"] for e in events if e[0] == "delta").strip(), "Echo: 안녕 하세요")
        self.assertEqual(events[-1][1]["title"], "안녕 하세요")
        status, chat = self.jcall("GET", "/chats/" + chat_id)
        self.assertEqual([m["role"] for m in chat["messages"]], ["user", "assistant"])
        self.sse(chat_id, {"content": "둘째"})
        sent = [r[3] for r in self.mock.requests if r[0] == "POST"][-1]
        self.assertEqual([m["role"] for m in sent["messages"]], ["user", "assistant", "user"])   # history is sent
        self.assertTrue(sent["stream"])

    def test_chat_management(self):
        a, b = self.new_chat(), self.new_chat()
        self.sse(a, {"content": "first"})
        status, listing = self.jcall("GET", "/chats")
        self.assertEqual([c["id"] for c in listing["chats"]], [a, b])
        self.assertEqual(self.jcall("PATCH", "/chats/" + b, {"title": "이름 변경"})[1]["title"], "이름 변경")
        self.assertEqual(self.jcall("PATCH", "/chats/" + b, {"title": ""})[0], 400)
        self.assertEqual(self.jcall("DELETE", "/chats/" + b)[0], 200)
        self.assertEqual(self.jcall("GET", "/chats/" + b)[0], 404)
        for bad in ("../etc", "nope", "00000000-0000-0000-0000-000000000000"):
            self.assertEqual(self.jcall("GET", "/chats/" + bad)[0], 404, bad)

    def test_regenerate_replaces_the_last_answer(self):
        chat_id = self.new_chat()
        self.sse(chat_id, {"content": "once"})
        status, events = self.sse(chat_id, {"regenerate": True})
        self.assertEqual(events[-1][0], "done")
        chat = self.jcall("GET", "/chats/" + chat_id)[1]
        self.assertEqual([m["role"] for m in chat["messages"]], ["user", "assistant"])
        empty = self.new_chat()
        self.assertEqual(self.sse(empty, {"regenerate": True})[0], 400)

    def test_provider_errors_are_reported_and_the_question_is_kept(self):
        chat_id = self.new_chat("mock/ratelimit")
        status, events = self.sse(chat_id, {"content": "hello"})
        self.assertEqual(events[-1][0], "error")
        self.assertIn("429", events[-1][1]["message"])
        chat = self.jcall("GET", "/chats/" + chat_id)[1]
        self.assertEqual([m["role"] for m in chat["messages"]], ["user"])
        status, events = self.sse(self.new_chat("mock/midfail"), {"content": "x"})
        self.assertEqual(events[-1][0], "error")
        self.assertIn("upstream exploded", events[-1][1]["message"])

    def test_validation_and_cloud_consent_gate(self):
        chat_id = self.new_chat()
        for body in ({"content": ""}, {"content": "   "}, {"content": "x" * 20_001}, {"content": 5}, {}):
            self.assertEqual(self.sse(chat_id, body)[0], 400, body)
        self.assertEqual(self.sse(chat_id, {"content": "hi", "model": "nope/x"})[0], 400)
        self.assertEqual(self.sse(chat_id, {"content": "hi", "model": "bad model"})[0], 400)
        self.providers.add("cloud", base_url="https://api.example.com/v1", data_policy="cloud-may-train")
        status, error = self.sse(chat_id, {"content": "hi", "model": "cloud/x"})
        self.assertEqual(status, 403)
        self.assertEqual(self.jcall("POST", "/chats", {"model": "cloud/x"})[0], 403)
        self.assertEqual(len([r for r in self.mock.requests if r[0] == "POST"]), 1)   # only the onboarding test

    def test_one_response_at_a_time_and_stop_saves_the_partial_answer(self):
        chat_id = self.new_chat("mock/slow")
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        body = json.dumps({"content": "go"}).encode()
        sock.sendall(("POST /api/v1/chats/%s/send HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nX-CSRF-Token: %s\r\n"
                      "Content-Type: application/json\r\nContent-Length: %d\r\n\r\n" % (chat_id, self.port, CSRF, len(body))).encode() + body)
        seen = b""
        while b"tok2" not in seen:
            seen += sock.recv(4096)
        status, events = self.sse(chat_id, {"content": "second"})                # while the first is streaming
        self.assertEqual(events[-1][0], "error")
        self.assertIn("이미", events[-1][1]["message"])
        sock.close()                                                              # the browser pressed stop
        for _ in range(60):
            chat = self.jcall("GET", "/chats/" + chat_id)[1]
            if len(chat["messages"]) == 2:
                break
            time.sleep(0.1)
        self.assertEqual([m["role"] for m in chat["messages"]], ["user", "assistant"])
        self.assertIn("(중단됨)", chat["messages"][1]["content"])
        self.assertIn("tok0", chat["messages"][1]["content"])
        self.assertEqual(self.ctx.active, set())                                  # the lock was released
        self.assertEqual(self.sse(self.new_chat(), {"content": "again"})[1][-1][0], "done")


class StreamParserTests(unittest.TestCase):
    def test_chat_stream_parses_sse_and_stops_at_done(self):
        from workshop.chat import chat_stream
        server = start(MockOpenAI)
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        provider = {"base_url": "http://127.0.0.1:%d/v1" % server.server_port, "api_key": SECRET}
        deltas = list(chat_stream(provider, "demo-model", [{"role": "user", "content": "a b c"}]))
        self.assertEqual("".join(deltas).strip(), "Echo: a b c")
        self.assertEqual(server.requests[-1][2], "Bearer " + SECRET)
        with self.assertRaises(RuntimeError) as ctx:
            list(chat_stream(provider, "ratelimit", [{"role": "user", "content": "x"}]))
        self.assertIn("429", str(ctx.exception))
        with self.assertRaises(RuntimeError) as ctx:
            list(chat_stream(provider, "midfail", [{"role": "user", "content": "x"}]))
        self.assertIn("upstream exploded", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()


class ModelManagementTests(ApiBase):
    def test_registered_service_models_can_be_listed_and_added_without_a_new_token(self):
        self.onboard()
        status, listing = self.jcall("POST", "/onboard/models", {"service": "mock"})     # by registered name
        self.assertEqual(status, 200)
        self.assertIn("demo-free:free", listing["models"])
        self.assertTrue(any(r[2] == "Bearer " + SECRET for r in self.mock.requests if r[1] == "/v1/models"))  # stored token is used
        self.assertEqual(self.jcall("POST", "/providers/models", {"name": "mock", "model": "demo-free:free"})[0], 200)
        state = self.jcall("GET", "/state")[1]
        self.assertEqual(state["providers"][0]["models"], ["demo-free:free", "demo-model"])
        chat_id = self.new_chat("mock/demo-free:free")
        self.assertEqual(self.sse(chat_id, {"content": "hi"})[1][-1][0], "done")
        for bad in ({"name": "nope", "model": "x"}, {"name": "mock", "model": ""}, {"name": "mock", "model": "has space"},
                    {"name": "mock", "model": "x" * 201}):
            self.assertEqual(self.jcall("POST", "/providers/models", bad)[0], 400, bad)
        self.assertEqual(self.jcall("POST", "/onboard/models", {"service": "not-registered"})[0], 400)
        self.assertFalse(any(SECRET in text for text in self.transcript))
