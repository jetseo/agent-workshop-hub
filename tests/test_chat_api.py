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
from workshop.usage import UsageLedger

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
        if model == "keyed" and (self.headers.get("Authorization") or "").endswith("LIMITED00"):
            return self._json(429, {"error": {"message": "this key is out of quota"}})
        if model.startswith("ratelimit"):
            return self._json(429, {"error": {"message": "slow down"}})
        if model == "badopt" and "stream_options" in body:
            return self._json(400, {"error": {"message": "unknown field stream_options"}})
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
            if (body.get("stream_options") or {}).get("include_usage") and model != "nousage":
                usage = {"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 7}}
                self.wfile.write(("data: " + json.dumps(usage) + "\n\n").encode())
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
        self.ledger = UsageLedger(d / "usage.jsonl")
        self.ctx = Context(self.providers, ChatStore(d / "chats"), ledger=self.ledger)
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
        try:
            conn.request(method, "/api/v1" + path, body=data, headers=hdrs)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            # The server may answer (e.g. 413) and close before the whole body is sent; the
            # reply is still waiting on the socket (seen on Windows runners).
            if data is None or len(data) < 65536:
                raise
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
        try:
            oversized = self.call("POST", "/chats", raw_body=b"x" * 300_000)[0]
        except (ConnectionError, BrokenPipeError):
            oversized = 413      # the server rejected the body and closed while we were still sending (seen on Windows)
        self.assertEqual(oversized, 413)
        self.assertEqual(self.call("GET", "/nothing")[0], 404)
        self.assertEqual(self.call("PUT", "/state")[0], 501)           # verbs the server does not implement
        self.assertEqual(self.call("DELETE", "/state")[0], 404)

    def test_onboarding_flow_lists_models_tests_saves_and_never_leaks_the_token(self):
        status, state = self.jcall("GET", "/state")
        self.assertFalse(state["onboarded"])
        self.assertIn("openrouter", [c["id"] for c in state["catalog"]])
        status, listing = self.jcall("POST", "/onboard/models", dict(service="custom", custom_name="mock",
                                                                     custom_url=self.mock_url, key=SECRET))
        self.assertCountEqual(listing["models"], ["demo-model", "demo-free:free", "other"])      # tidied and ranked, nothing lost\n        self.assertEqual(listing["total"], 3)\n        self.assertTrue(set(listing["recommended"]) <= set(listing["models"]))
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
        self.assertIn("동의", result["error"])

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
        status, events = self.sse(chat_id, {"content": "hello", "fallback": False})
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


class UsageTests(ApiBase):
    def setUp(self):
        super().setUp()
        self.onboard()
        self.ledger = UsageLedger(self.ledger.path)

    def summary(self):
        return self.jcall("GET", "/usage")[1]

    def test_service_reported_numbers_are_recorded_by_tier(self):
        self.sse(self.new_chat(), {"content": "hi"})
        s = self.summary()
        self.assertEqual(s["totals"]["local"], {"requests": 1, "tokens": 18})      # 11 prompt + 7 completion, as reported
        self.assertEqual(s["totals"]["free"]["tokens"] + s["totals"]["other"]["tokens"], 0)
        self.assertEqual(s["estimated_share"], 0.0)
        self.assertEqual(s["free_share"], 1.0)
        self.assertEqual(s["by_model"][0]["model"], "mock/demo-model")

    def test_estimates_are_flagged_when_the_service_reports_nothing(self):
        self.sse(self.new_chat("mock/nousage"), {"content": "안녕하세요 오늘 날씨가 어떤가요"})
        s = self.summary()
        self.assertGreater(s["totals"]["local"]["tokens"], 5)
        self.assertEqual(s["estimated_share"], 1.0)

    def test_server_that_rejects_stream_options_still_works(self):
        status, events = self.sse(self.new_chat("mock/badopt"), {"content": "hi"})
        self.assertEqual(events[-1][0], "done")
        posts = [r[3] for r in self.mock.requests if r[0] == "POST" and r[3]["model"] == "badopt"]
        self.assertEqual(len(posts), 2)
        self.assertIn("stream_options", posts[0])
        self.assertNotIn("stream_options", posts[1])

    def test_stopped_answers_are_counted_too(self):
        chat_id = self.new_chat("mock/slow")
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        body = json.dumps({"content": "go"}).encode()
        sock.sendall(("POST /api/v1/chats/%s/send HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nX-CSRF-Token: %s\r\n"
                      "Content-Type: application/json\r\nContent-Length: %d\r\n\r\n" % (chat_id, self.port, CSRF, len(body))).encode() + body)
        seen = b""
        while b"tok2" not in seen:
            seen += sock.recv(4096)
        sock.close()
        for _ in range(60):
            if self.summary()["totals"]["local"]["requests"]:
                break
            time.sleep(0.1)
        s = self.summary()
        self.assertEqual(s["totals"]["local"]["requests"], 1)
        self.assertEqual(s["estimated_share"], 1.0)

    def test_tier_rules(self):
        from workshop.usage import tier_of
        self.assertEqual(tier_of("local", "anything"), "local")
        self.assertEqual(tier_of("cloud-may-train", "x-free"), "free")
        self.assertEqual(tier_of("cloud-may-train", "meta/llama:free"), "free")
        self.assertEqual(tier_of("cloud-may-train", "gpt-big"), "other")
        self.assertEqual(tier_of("unknown", "free-ish"), "other")

    def test_ledger_stores_no_text_and_is_private(self):
        self.sse(self.new_chat(), {"content": "아주 비밀스러운 질문입니다"})
        content = self.ledger.path.read_text(encoding="utf-8")
        self.assertNotIn("비밀", content)
        self.assertNotIn("Echo", content)
        if __import__("os").name == "posix":
            import os, stat
            self.assertEqual(stat.S_IMODE(os.stat(self.ledger.path).st_mode), 0o600)

    def test_usage_page_renders_and_escapes(self):
        self.ledger.record(provider="p", model="<script>alert(1)</script>", tier="other", prompt_tokens=5,
                           completion_tokens=5, estimated=False)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/usage")
        page = conn.getresponse().read().decode("utf-8")
        self.assertIn("사용량", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("<script>alert", page)

    def test_ledger_windows_corrupt_lines_and_estimates(self):
        from workshop.usage import estimate_tokens
        now = 1_800_000_000
        self.ledger.record(provider="p", model="old", tier="free", prompt_tokens=100, completion_tokens=0, estimated=False, when=now - 40 * 86400)
        self.ledger.record(provider="p", model="new", tier="free", prompt_tokens=1, completion_tokens=2, estimated=False, when=now)
        with open(self.ledger.path, "a", encoding="utf-8") as handle:
            handle.write("{broken\n")
        s = self.ledger.summary(days=30, now=now)
        self.assertEqual(s["totals"]["free"], {"requests": 1, "tokens": 3})          # 40-day-old row is outside the window
        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(estimate_tokens("abcd"), 1)
        self.assertGreater(estimate_tokens("가나다라마바사"), estimate_tokens("abcdefg"))


class FallbackTests(ApiBase):
    def setUp(self):
        super().setUp()
        self.onboard()
        for model in ("ratelimit", "ratelimit-free", "demo-free:free"):
            self.providers.add_model("mock", model)

    def deltas(self, events):
        return "".join(e[1]["t"] for e in events if e[0] == "delta")

    def test_rate_limited_model_falls_back_to_another_free_or_local_model(self):
        chat_id = self.new_chat("mock/ratelimit")
        status, events = self.sse(chat_id, {"content": "ping"})
        names = [e[0] for e in events]
        self.assertIn("notice", names)
        notice = [e[1] for e in events if e[0] == "notice"][0]
        self.assertIn("429", notice["message"])
        self.assertTrue(notice["model"].startswith("mock/"))
        self.assertNotEqual(notice["model"], "mock/ratelimit")
        self.assertEqual(names[-1], "done")
        served = events[-1][1]["model"]
        self.assertEqual(served, notice["model"])
        self.assertEqual(self.deltas(events).strip(), "Echo: ping")
        chat = self.jcall("GET", "/chats/" + chat_id)[1]
        self.assertEqual(chat["messages"][-1]["model"], served)                     # the UI can say who answered
        s = self.jcall("GET", "/usage")[1]
        self.assertEqual([m["model"] for m in s["by_model"]], [served])          # usage is booked to the model that served it

    def test_fallback_can_be_switched_off(self):
        status, events = self.sse(self.new_chat("mock/ratelimit"), {"content": "ping", "fallback": False})
        self.assertNotIn("notice", [e[0] for e in events])
        self.assertEqual(events[-1][0], "error")
        self.assertIn("429", events[-1][1]["message"])

    def test_no_switch_after_part_of_the_answer_was_shown(self):
        status, events = self.sse(self.new_chat("mock/midfail"), {"content": "x"})
        names = [e[0] for e in events]
        self.assertNotIn("notice", names)
        self.assertEqual(names[-1], "error")
        self.assertGreater(names.count("delta"), 0)

    def test_every_failure_is_reported_when_nothing_works(self):
        # only rate-limited models are registered, so the primary and its one candidate both fail
        self.providers.add("mock", base_url=self.mock_url, data_policy="local", models=["ratelimit", "ratelimit-free"])
        chat_id = self.new_chat("mock/ratelimit")
        status, events = self.sse(chat_id, {"content": "again"})
        notices = [e for e in events if e[0] == "notice"]
        self.assertEqual(len(notices), 1)
        self.assertEqual(events[-1][0], "error")
        self.assertIn("모든 모델이 실패", events[-1][1]["message"])
        self.assertIn("mock/ratelimit:", events[-1][1]["message"])
        self.assertIn("mock/ratelimit-free:", events[-1][1]["message"])
        chat = self.jcall("GET", "/chats/" + chat_id)[1]
        self.assertEqual([m["role"] for m in chat["messages"]], ["user"])           # the question is kept for a retry


class DigestApiTests(ApiBase):
    def setUp(self):
        super().setUp()
        self.onboard()
        for model in ("ratelimit-free", "demo-free:free", "pricey"):
            self.providers.add_model("mock", model)

    def digest(self, body):
        status, text = self.call("POST", "/digest", body)
        if status != 200:
            return status, json.loads(text)
        events = []
        for block in text.strip().split("\n\n"):
            lines = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line)
            events.append((lines["event"], json.loads(lines["data"])))
        return status, events

    def test_digest_accepts_the_auto_model(self):
        self.providers.add_model("mock", "tiny-3b")
        status, events = self.digest({"model": "auto", "text": "긴 글 " * 50})
        self.assertEqual(status, 200)
        self.assertEqual(events[-1][0], "done")
        self.assertTrue(events[-1][1]["model"].startswith("mock/"))

    def test_digest_auto_without_candidates_is_refused(self):
        from workshop import chat_api
        saved = chat_api.auto_candidates
        chat_api.auto_candidates = lambda ctx: []
        self.addCleanup(lambda: setattr(chat_api, "auto_candidates", saved))
        status, body = self.digest({"model": "auto", "text": "abc " * 30})
        self.assertEqual(status, 400)

    def test_digest_streams_progress_and_books_usage_as_digest(self):
        status, events = self.digest({"model": "mock/demo-model", "text": "긴 글 " * 50})
        self.assertEqual(status, 200)
        names = [e[0] for e in events]
        self.assertEqual(names[-1], "done")
        self.assertIn("progress", names)
        self.assertTrue(events[-1][1]["digest"])
        sources = set(self.ledger.summary()["by_source"])
        self.assertEqual(sources, {"digest"})

    def test_paid_model_needs_cost_confirmation(self):
        self.providers.add("mock", base_url=self.mock_url, data_policy="cloud-no-train", models=["pricey"])
        self.providers.set_consent("mock", True)
        status, body = self.digest({"model": "mock/pricey", "text": "abc " * 30})
        self.assertEqual(status, 403)
        status, events = self.digest({"model": "mock/pricey", "text": "abc " * 30, "confirm_cost": True})
        self.assertEqual((status, events[-1][0]), (200, "done"))

    def test_unconsented_cloud_is_refused(self):
        self.providers.add("mock", base_url=self.mock_url, data_policy="cloud-no-train", models=["demo-free:free"])
        status, body = self.digest({"model": "mock/demo-free:free", "text": "abc " * 30})
        self.assertEqual(status, 403)

    def test_empty_text_is_rejected(self):
        status, body = self.digest({"model": "mock/demo-model", "text": "  "})
        self.assertEqual(status, 400)

    def test_rate_limited_model_falls_back_with_notice(self):
        status, events = self.digest({"model": "mock/ratelimit-free", "text": "abc " * 30})
        names = [e[0] for e in events]
        self.assertIn("notice", names)
        self.assertEqual(names[-1], "done")

    def test_no_fallback_reports_the_error(self):
        status, events = self.digest({"model": "mock/ratelimit-free", "text": "abc " * 30, "fallback": False})
        self.assertEqual(events[-1][0], "error")


class RpmLimitTests(ApiBase):
    def setUp(self):
        super().setUp()
        from workshop import ratelimit
        ratelimit.reset()
        self.addCleanup(ratelimit.reset)
        self.onboard()
        self.providers.add("mock", base_url=self.mock_url, data_policy="local", rpm=1)

    def test_second_request_over_the_limit_is_stopped_before_reaching_the_service(self):
        chat_id = self.new_chat("mock/demo-model")
        status, events = self.sse(chat_id, {"content": "one", "fallback": False})
        self.assertEqual(events[-1][0], "done")
        posts = lambda: len([r for r in self.mock.requests if r[0] == "POST"])
        before = posts()
        status, events = self.sse(chat_id, {"content": "two", "fallback": False})
        self.assertEqual(events[-1][0], "error")
        self.assertIn("requests/minute", events[-1][1]["message"])
        self.assertEqual(posts(), before)           # nothing was sent, so no 429 and no wasted quota


class EnvApiTests(ApiBase):
    def test_env_reports_what_is_found_without_changing_anything(self):
        self.ctx.env_fn = lambda: {"os": "Windows", "ollama": {"running": False, "models": []}}
        status, body = self.jcall("GET", "/env")
        self.assertEqual((status, body["os"]), (200, "Windows"))
        self.assertEqual(self.providers.load(), {})

    def test_real_inspection_survives_everything_being_absent(self):
        from workshop import envcheck
        def boom(timeout=1):
            raise OSError("no ollama")
        result = envcheck.inspect(ollama_fn=boom, which=lambda name: None)
        self.assertEqual(result["ollama"], {"running": False, "models": []})
        self.assertFalse(result["wsl"]["available"])
        self.assertFalse(result["opencode"])
        found = envcheck.inspect(ollama_fn=lambda timeout=1: ["llama3.2:3b"], which=lambda name: "/x/" + name)
        self.assertEqual(found["ollama"]["models"], ["llama3.2:3b"])
        self.assertTrue(found["opencode"])


class MultiAccountTests(ApiBase):
    def setUp(self):
        super().setUp()
        from workshop import ratelimit
        ratelimit.reset()
        self.addCleanup(ratelimit.reset)
        self.onboard(key="first-LIMITED00")
        self.providers.add_model("mock", "keyed")

    def auth_sequence(self):
        return [r[2] for r in self.mock.requests if r[0] == "POST" and r[3]["model"] == "keyed"]

    def add_account(self, label="개인2", key="second-GOODKEY00", **extra):
        body = {"name": "mock", "label": label, "key": key, "confirm_own": True}
        body.update(extra)
        return self.jcall("POST", "/providers/accounts", body)

    def test_a_429_on_one_account_moves_to_the_next_account_not_the_next_model(self):
        self.assertEqual(self.add_account()[0], 200)
        status, events = self.sse(self.new_chat("mock/keyed"), {"content": "hi", "fallback": False})
        self.assertEqual(events[-1][0], "done")
        self.assertEqual(self.auth_sequence(), ["Bearer first-LIMITED00", "Bearer second-GOODKEY00"])
        self.assertNotIn("notice", [e[0] for e in events])           # no model switch was needed

    def test_without_a_working_second_account_the_429_still_reaches_the_user(self):
        status, events = self.sse(self.new_chat("mock/keyed"), {"content": "hi", "fallback": False})
        self.assertEqual(events[-1][0], "error")
        self.assertIn("429", events[-1][1]["message"])

    def test_adding_needs_the_ownership_confirmation(self):
        status, body = self.add_account(confirm_own=False)
        self.assertEqual(status, 400)
        self.assertIn("약관", body["error"])
        self.assertEqual(self.providers.list_accounts("mock")[0]["label"], "기본")
        self.assertEqual(len(self.providers.list_accounts("mock")), 1)

    def test_tokens_are_masked_everywhere_and_never_in_providers_json(self):
        self.add_account()
        status, state = self.jcall("GET", "/state")
        accounts = state["providers"][0]["accounts"]
        self.assertEqual([a["label"] for a in accounts], ["기본", "개인2"])
        self.assertNotIn("GOODKEY00", json.dumps(state))
        self.assertNotIn("GOODKEY00", self.providers.path.read_text())
        self.assertEqual(accounts[1]["token"], "\u2022\u2022\u2022\u2022EY00")

    def test_bad_input_is_rejected_and_removal_deletes_the_key(self):
        self.assertEqual(self.add_account(label="bad label!")[0], 400)
        self.assertEqual(self.add_account(label="기본")[0], 400)
        self.assertEqual(self.add_account(key="short")[0], 400)
        self.assertEqual(self.add_account(rpm=-1)[0], 400)
        self.add_account()
        status, body = self.jcall("POST", "/providers/accounts/remove", {"name": "mock", "label": "개인2"})
        self.assertEqual([a["label"] for a in body["accounts"]], ["기본"])
        self.assertNotIn("GOODKEY00", self.providers.secrets_path.read_text())
        self.add_account()
        self.jcall("POST", "/providers/remove", {"name": "mock"})
        self.assertNotIn("GOODKEY00", self.providers.secrets_path.read_text())

    def test_each_account_has_its_own_rpm_window(self):
        from workshop import ratelimit
        self.add_account(rpm=1)
        provider = self.providers.get("mock")
        self.assertEqual([a["label"] for a in provider["accounts"]], ["기본", "개인2"])
        self.assertTrue(ratelimit.try_acquire(provider, "개인2", 1))
        self.assertFalse(ratelimit.try_acquire(provider, "개인2", 1))
        self.assertTrue(ratelimit.try_acquire(provider, "기본", 1))     # the other account is unaffected


class ExplainedErrorTests(ApiBase):
    def test_a_failed_connection_test_explains_the_cause_in_korean_and_keeps_the_raw_message(self):
        body = dict(service="custom", custom_name="mock", custom_url=self.mock_url, model="ratelimit", prompt="hi", save="on")
        status, result = self.jcall("POST", "/onboard/test", body)
        self.assertFalse(result["ok"])
        self.assertIn("사용 한도에 도달", result["error"])
        self.assertIn("HTTP 429", result["error"])
        self.assertEqual(self.providers.load(), {})                        # a failed test saves nothing

    def test_chat_errors_are_explained_too(self):
        self.onboard()
        self.providers.add_model("mock", "ratelimit")
        status, events = self.sse(self.new_chat("mock/ratelimit"), {"content": "x", "fallback": False})
        self.assertEqual(events[-1][0], "error")
        self.assertIn("사용 한도에 도달", events[-1][1]["message"])
        self.assertIn("429", events[-1][1]["message"])


class FallbackCandidateTests(unittest.TestCase):
    def test_candidates_never_include_paid_or_unconsented_models(self):
        from workshop.chat_api import fallback_candidates
        with tempfile.TemporaryDirectory() as d:
            providers = Providers(Path(d) / "p.json")
            providers.add("loc", base_url="http://127.0.0.1:1/v1", data_policy="local", models=["a", "b-free"])
            providers.add("cc", base_url="https://c.example.com/v1", data_policy="cloud-may-train", consented=True,
                          models=["x-free", "y", "z:free"])
            providers.add("nc", base_url="https://n.example.com/v1", data_policy="cloud-may-train", models=["n-free"])
            ctx = Context(providers, ChatStore(Path(d) / "chats"), ledger=UsageLedger(Path(d) / "u.jsonl"))
            refs = fallback_candidates(ctx, "cc/y")
            self.assertEqual(refs[0], "cc/y")
            self.assertEqual(refs[1:], ["loc/a", "loc/b-free", "cc/x-free"])      # local first, capped at 3
            self.assertNotIn("nc/n-free", refs)                                  # no consent
            self.assertNotIn("cc/y", refs[1:])                                   # the paid-looking model is only ever the primary
            self.assertEqual(fallback_candidates(ctx, "cc/y", enabled=False), ["cc/y"])
            self.assertNotIn("cc/z:free", refs)                                  # cap reached, still never a non-free model


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


class AutoModelTests(ApiBase):
    def setUp(self):
        super().setUp()
        from workshop.runstats import RunStats
        self.onboard()
        for model in ("small-3b", "large-32b"):
            self.providers.add_model("mock", model)
        self.stats = RunStats(Path(self.tmp.name) / "runstats.jsonl")
        for _ in range(4):                                             # park the onboarding model so sizes decide
            self.stats.record("mock/demo-model", ok=False, seconds=1, kind="chat")

    def test_auto_picks_the_cheapest_adequate_model_and_keeps_auto_selected(self):
        chat_id = self.new_chat("auto")
        status, events = self.sse(chat_id, {"content": "hello", "model": "auto"})
        self.assertEqual(events[-1][0], "done")
        served = events[-1][1]["model"]
        self.assertEqual(served, "mock/small-3b")
        self.assertTrue(events[0][1]["auto"])
        chat = self.jcall("GET", "/chats/" + chat_id)[1]
        self.assertEqual(chat["model"], "auto")                       # the picker keeps showing 'auto'
        self.assertEqual(chat["messages"][-1]["model"], served)       # but the answer says who really answered
        self.assertEqual([m["model"] for m in self.jcall("GET", "/usage")[1]["by_model"]], ["mock/small-3b"])

    def test_hard_questions_go_to_the_larger_model(self):
        question = "refactor the architecture of this module " + "details " * 500
        status, events = self.sse(self.new_chat("auto"), {"content": question, "model": "auto"})
        self.assertEqual(events[-1][1]["model"], "mock/large-32b")

    def test_models_that_may_cost_money_are_never_candidates(self):
        from workshop import chat_api
        self.providers.add("cloudy", base_url="https://api.example.com/v1", data_policy="cloud-may-train",
                           models=["gpt-9", "tiny-1b-free"])
        self.assertFalse([r for r in chat_api.auto_candidates(self.ctx) if r.startswith("cloudy/")])  # not consented
        self.providers.set_consent("cloudy")
        refs = chat_api.auto_candidates(self.ctx)
        self.assertIn("cloudy/tiny-1b-free", refs)
        self.assertNotIn("cloudy/gpt-9", refs)

    def test_auto_skips_a_model_that_keeps_failing(self):
        for _ in range(4):
            self.stats.record("mock/small-3b", ok=False, seconds=1, kind="chat")
        status, events = self.sse(self.new_chat("auto"), {"content": "hello", "model": "auto"})
        self.assertEqual(events[-1][1]["model"], "mock/large-32b")

    def test_runs_are_recorded_for_later_routing(self):
        self.sse(self.new_chat("auto"), {"content": "hello", "model": "auto"})
        stats = self.stats.load()
        self.assertEqual(stats["mock/small-3b"]["runs"], 1)
        self.assertEqual(stats["mock/small-3b"]["failures"], 0)

    def test_regenerate_with_auto_uses_the_last_question(self):
        chat_id = self.new_chat("auto")
        self.sse(chat_id, {"content": "first", "model": "auto"})
        status, events = self.sse(chat_id, {"regenerate": True, "model": "auto"})
        self.assertEqual(events[-1][0], "done")

    def test_auto_without_any_local_or_free_model_is_refused(self):
        from workshop import chat_api
        self.providers.add("only", base_url="https://api.example.com/v1", data_policy="cloud-may-train", models=["gpt-9"])
        saved = chat_api.auto_candidates
        chat_api.auto_candidates = lambda ctx: []
        self.addCleanup(lambda: setattr(chat_api, "auto_candidates", saved))
        status, body = self.jcall("POST", "/chats", {"model": "auto"})
        self.assertEqual(status, 400)
        self.assertIn("로컬·무료", body["error"])


class OpenUrlTests(ApiBase):
    def setUp(self):
        super().setUp()
        self.opened = []
        self.ctx.open_url_fn = self.opened.append

    def test_catalog_pages_open_in_the_default_browser(self):
        status, body = self.jcall("POST", "/open-url", {"url": "https://console.groq.com/keys"})
        self.assertEqual((status, body), (200, {"ok": True}))
        self.assertEqual(self.opened, ["https://console.groq.com/keys"])

    def test_anything_else_is_refused(self):
        for bad in ("http://console.groq.com/keys", "https://evil.example.com/", "https://console.groq.com@evil.example.com/",
                    "file:///C:/Windows/System32/calc.exe", "javascript:alert(1)", "ms-msdt:/x", "", None, 5,
                    "https://console.groq.com/" + "a" * 600):
            status, body = self.jcall("POST", "/open-url", {"url": bad})
            self.assertEqual(status, 400, bad)
        self.assertEqual(self.opened, [])

    def test_every_catalog_token_page_is_allowed(self):
        from workshop import chat_api, setup_page
        hosts = chat_api.allowed_link_hosts()
        for entry in setup_page.CATALOG:
            if entry["key_url"]:
                self.assertIn(entry["key_url"].split("/")[2], hosts, entry["id"])

class ArbiterChatTests(ApiBase):
    def setUp(self):
        super().setUp()
        from workshop.runstats import RunStats
        self.onboard()
        for model in ("tiny-3b", "mid-flash", "big-pro"):
            self.providers.add_model("mock", model)
        stats = RunStats(Path(self.tmp.name) / "runstats.jsonl")
        for _ in range(4):
            stats.record("mock/demo-model", ok=False, seconds=1, kind="chat")

    def meta(self, events):
        return events[0][1]

    def test_meta_reports_the_kind_of_work_and_why(self):
        chat_id = self.new_chat("auto")
        status, events = self.sse(chat_id, {"content": "안녕 오늘 기분 어때?", "model": "auto"})
        meta = self.meta(events)
        self.assertEqual((meta["task"], meta["level"]), ("chat", 1))
        self.assertIn("일상 질문", meta["why"])
        self.assertEqual(meta["model"], "mock/tiny-3b")

    def test_research_goes_to_the_strong_model_and_code_is_recognised(self):
        status, events = self.sse(self.new_chat("auto"), {"content": "전기차 시장 동향을 조사하고 업체를 비교 분석해서 근거와 출처가 있는 보고서로 정리해줘", "model": "auto"})
        self.assertEqual(self.meta(events)["task"], "research")
        self.assertEqual(self.meta(events)["model"], "mock/big-pro")
        status, events = self.sse(self.new_chat("auto"), {"content": "파이썬 함수에서 에러가 나는데 디버그 방법 알려줘", "model": "auto"})
        self.assertEqual(self.meta(events)["task"], "code")

    def test_a_short_follow_up_keeps_the_conversation_kind(self):
        chat_id = self.new_chat("auto")
        self.sse(chat_id, {"content": "Traceback (most recent call last):\n  File \"a.py\", line 1\nValueError: x\n이 에러 고쳐줘", "model": "auto"})
        status, events = self.sse(chat_id, {"content": "왜 그래?", "model": "auto"})
        self.assertEqual(self.meta(events)["task"], "code")

    def test_a_fixed_model_choice_skips_the_arbiter(self):
        status, events = self.sse(self.new_chat("mock/tiny-3b"), {"content": "전기차 시장 동향 비교 분석 보고서", "model": "mock/tiny-3b"})
        self.assertNotIn("task", self.meta(events))
        self.assertEqual(self.meta(events)["model"], "mock/tiny-3b")

class ModelNewsApiTests(ApiBase):
    def test_state_carries_news_and_settings_and_ack_clears_the_news(self):
        self.onboard()
        self.ctx.watch._push({"ts": 1, "service": "mock", "added": ["x-free"], "about": []})
        status, state = self.jcall("GET", "/state")
        self.assertEqual(state["news"][0]["added"], ["x-free"])
        self.assertTrue(state["settings"]["auto_add_models"])
        self.assertEqual(self.jcall("POST", "/news/ack", {})[0], 200)
        self.assertEqual(self.jcall("GET", "/state")[1]["news"], [])

    def test_settings_switch_and_validation(self):
        status, body = self.jcall("POST", "/settings", {"auto_add_models": False})
        self.assertEqual((status, body["settings"]["auto_add_models"]), (200, False))
        self.assertFalse(self.jcall("GET", "/state")[1]["settings"]["auto_add_models"])
        for bad in ({"auto_add_models": "yes"}, {}, {"other": True}):
            self.assertEqual(self.jcall("POST", "/settings", bad)[0], 400)

    def test_check_endpoint_runs_the_watcher_without_touching_the_network_for_the_catalog(self):
        self.onboard()
        self.ctx.info.fetch = lambda: {"data": []}                     # junk download is ignored, nothing real is fetched
        status, result = self.jcall("POST", "/models/check", {})
        self.assertEqual(status, 200)
        self.assertGreaterEqual(result["checked"], 1)
        self.providers.add_model("mock", "tmp")                        # first look only remembers; a second check adds nothing new
        self.assertEqual(self.jcall("POST", "/models/check", {})[1]["added"], {})

    def test_onboard_listing_carries_catalog_info_when_it_knows_the_model(self):
        from workshop.modelinfo import ModelInfo
        self.ctx.info = ModelInfo(Path(self.tmp.name) / "mi.json", fetch=lambda: {"data": [
            {"id": "x/demo-model", "context_length": 300000, "reasoning": True, "description": "A demo.", "pricing": {"prompt": "0", "completion": "0"}}
            ] + [{"id": "f/m%d" % i, "pricing": {}} for i in range(30)]})
        self.ctx.info.refresh()
        status, listing = self.jcall("POST", "/onboard/models", dict(service="custom", custom_name="mock", custom_url=self.mock_url, key=SECRET))
        self.assertIn("추론", listing["info"].get("demo-model", {}).get("tags", []))

class CatalogRefreshApiTests(ApiBase):
    README = ("  ### [Fresh Free AI](https://fresh.example.com/keys)\n  | **Rate Limits** | 10 RPM |\n"
              "  | **OpenAI Compat** | Yes - `https://api.fresh.example.com/v1` |\n")

    def setUp(self):
        super().setUp()
        from workshop import setup_page
        self.addCleanup(setup_page.set_extra, [])
        self.ctx.feed.fetch = lambda: self.README
        self.ctx.feed.prober = lambda url: (401, "x")

    def test_refresh_adds_newly_listed_services_to_the_catalog_and_state(self):
        status, result = self.jcall("POST", "/catalog/refresh", {"force": True})
        self.assertEqual(status, 200)
        self.assertEqual(result["added"], ["Fresh Free AI"])
        self.assertIn("fresh-free-ai", [c["id"] for c in result["catalog"]])
        state = self.jcall("GET", "/state")[1]
        self.assertIn("fresh-free-ai", [c["id"] for c in state["catalog"]])
        self.assertEqual(state["feed"]["count"], 1)

    def test_plain_refresh_is_throttled(self):
        self.jcall("POST", "/catalog/refresh", {"force": True})
        again = self.jcall("POST", "/catalog/refresh", {})[1]
        self.assertEqual((again["skipped"], again["added"]), ("recently refreshed", []))

    def test_a_discovered_service_can_be_listed_for_models_only_with_a_key(self):
        self.jcall("POST", "/catalog/refresh", {"force": True})
        status, body = self.jcall("POST", "/onboard/models", {"service": "fresh-free-ai"})
        self.assertEqual(status, 400)                                       # needs a token like every cloud service