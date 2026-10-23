import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from workshop import chat, quotaguard, usage
from workshop.providers import Providers
from workshop.quotaguard import FreeQuotaGuard, QuotaBlocked

GID = "inception"


class Fake(BaseHTTPRequestHandler):
    """A fake OpenAI-compatible server: never a real provider, so no test can cost money."""
    mode = "ok"
    hits = 0
    delay = 0.0
    seen_keys = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        type(self).hits += 1
        type(self).seen_keys.append(self.headers.get("Authorization"))
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.delay:
            time.sleep(self.delay)
        if self.mode == "402":
            body = b'{"error":{"message":"insufficient credits"}}'
            self.send_response(402)
        elif self.mode == "401":
            body = b"{}"
            self.send_response(401)
        elif self.mode == "nousage":
            body = json.dumps({"choices": [{"message": {"content": "hi"}}]}).encode()
            self.send_response(200)
        else:
            body = json.dumps({"choices": [{"message": {"content": "hi"}}], "usage": {"prompt_tokens": 40, "completion_tokens": 10}}).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class GuardCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = [1_800_000_000.0]
        self.guard = FreeQuotaGuard(Path(self.tmp.name) / "q.json", clock=lambda: self.now[0])
        self._old = quotaguard.get()
        quotaguard.install(self.guard)
        self.addCleanup(quotaguard.install, self._old)
        Fake.mode, Fake.hits, Fake.delay, Fake.seen_keys = "ok", 0, 0.0, []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.provider = {"base_url": "http://127.0.0.1:%d/v1" % self.server.server_port, "guard": GID, "api_key": "sk-secret-0123456789", "data_policy": "cloud-may-train"}

    def open_it(self, balance=1_000_000):
        self.guard.attest_no_payment(GID, True)
        self.guard.set_balance(GID, balance)
        self.guard.set_enabled(GID, True)

    def ask(self, text="안녕", **kw):
        return chat.chat(self.provider, "mercury-3", [{"role": "user", "content": text}], max_tokens=kw.pop("max_tokens", 256), **kw)


class DecisionTests(GuardCase):
    def test_off_by_default_and_nothing_leaves_the_pc(self):
        with self.assertRaises(QuotaBlocked):
            self.ask()
        self.assertEqual(Fake.hits, 0)
        self.assertEqual(usage.tier_for(self.provider, "mercury-3"), "other")                    # a closed guard is never "free"

    def test_enough_verified_balance_approves_and_records_real_usage(self):
        self.open_it()
        self.assertEqual(self.ask(), "hi")
        status = self.guard.status(GID)
        self.assertEqual((status["total_tokens"], status["remaining_tokens"], status["session_tokens"]), (50, 999_950, 50))
        self.assertEqual(usage.tier_for(self.provider, "mercury-3"), "free")

    def test_insufficient_balance_blocks(self):
        self.open_it(balance=100)
        with self.assertRaises(QuotaBlocked) as caught:
            self.ask(max_tokens=4096)
        self.assertIn("잔액", str(caught.exception))
        self.assertEqual(Fake.hits, 0)

    def test_unknown_balance_blocks_and_cannot_be_switched_on(self):
        self.guard.attest_no_payment(GID, True)
        with self.assertRaises(ValueError):
            self.guard.set_enabled(GID, True)
        self.assertEqual(self.guard.status(GID)["balance_label"], "확인 불가 · 호출 차단")          # never an invented number
        self.assertIsNone(self.guard.status(GID)["remaining_tokens"])

    def test_payment_confirmation_is_required(self):
        self.guard.set_balance(GID, 10_000)
        with self.assertRaises(ValueError):
            self.guard.set_enabled(GID, True)

    def test_a_stale_balance_blocks_until_it_is_read_again(self):
        self.open_it()
        self.now[0] += 25 * 3600
        with self.assertRaises(QuotaBlocked) as caught:
            self.ask()
        self.assertIn("시간이 지남", str(caught.exception))
        self.guard.set_balance(GID, 500_000)
        self.assertEqual(self.ask(), "hi")

    def test_expired_credits_block(self):
        self.guard.attest_no_payment(GID, True)
        self.guard.set_balance(GID, 1_000_000, valid_until=int(self.now[0]) + 3600)
        self.guard.set_enabled(GID, True)
        self.assertEqual(self.ask(), "hi")
        self.now[0] += 2 * 3600
        self.guard.set_balance(GID, 1_000_000, valid_until=int(self.now[0]) - 10)
        with self.assertRaises(QuotaBlocked) as caught:
            self.ask()
        self.assertIn("기한", str(caught.exception))

    def test_daily_soft_limit_blocks_but_the_next_day_recovers(self):
        self.open_it()
        self.guard.set_limits(GID, daily_soft_limit_tokens=1000)
        self.assertEqual(self.ask(max_tokens=300), "hi")
        with self.assertRaises(QuotaBlocked) as caught:
            self.ask(max_tokens=900)
        self.assertIn("일일", str(caught.exception))
        self.now[0] += 26 * 3600
        self.guard.set_balance(GID, 900_000)
        self.assertEqual(self.ask(max_tokens=300), "hi")

    def test_missing_usage_is_recorded_as_the_reserved_estimate_never_less(self):
        self.open_it()
        Fake.mode = "nousage"
        self.ask(max_tokens=200)
        self.assertGreater(self.guard.status(GID)["total_tokens"], 200)


class ConcurrencyAndFailureTests(GuardCase):
    def test_parallel_requests_cannot_overshoot_the_balance_or_the_concurrency_limit(self):
        self.open_it(balance=2000)
        tickets, blocked = [], 0
        for _ in range(6):
            try:
                tickets.append(self.guard.begin(GID, 100, 400))               # each needs about 550: 3 would exceed 2000
            except QuotaBlocked:
                blocked += 1
        self.assertEqual(len(tickets), 2)                                      # max_concurrent_requests defaults to 2
        self.assertEqual(blocked, 4)
        for ticket in tickets:
            self.guard.settle(ticket, 10, 10)
        self.assertEqual(self.guard.begin(GID, 100, 400)["gid"], GID)         # room again after settling

    def test_real_threads_never_exceed_the_limit(self):
        self.open_it()
        Fake.delay = 0.3
        results, lock = [], threading.Lock()

        def work():
            try:
                self.ask()
                outcome = "ok"
            except QuotaBlocked:
                outcome = "blocked"
            with lock:
                results.append(outcome)
        threads = [threading.Thread(target=work) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results.count("ok"), 2)
        self.assertEqual(results.count("blocked"), 4)
        self.assertEqual(Fake.hits, 2)

    def test_a_credit_error_switches_it_off_and_no_paid_fallback_is_attempted(self):
        self.open_it()
        Fake.mode = "402"
        with self.assertRaises(RuntimeError):
            self.ask()
        self.assertEqual(Fake.hits, 1)                                        # exactly one attempt: no retry loop
        status = self.guard.status(GID)
        self.assertFalse(status["enabled"])
        self.assertIn("크레딧", status["reason"])
        with self.assertRaises(QuotaBlocked):
            self.ask()
        self.assertEqual(Fake.hits, 1)
        self.assertEqual(usage.tier_for(self.provider, "mercury-3"), "other")

    def test_a_rejected_key_switches_it_off(self):
        self.open_it()
        Fake.mode = "401"
        with self.assertRaises(RuntimeError):
            self.ask()
        self.assertFalse(self.guard.status(GID)["enabled"])

    def test_reservations_are_returned_after_a_failure(self):
        self.open_it()
        Fake.mode = "nope"
        self.server.RequestHandlerClass.mode = "ok"
        self.provider["base_url"] = "http://127.0.0.1:9/v1"                   # nothing listens there
        for _ in range(4):
            with self.assertRaises(RuntimeError):
                self.ask()
        self.provider["base_url"] = "http://127.0.0.1:%d/v1" % self.server.server_port
        self.assertEqual(self.ask(), "hi")                                    # no leaked reservation blocked this

    def test_streaming_counts_what_was_spent(self):
        self.open_it()

        class Stream(Fake):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for line in ('{"choices":[{"delta":{"content":"a"}}]}', '{"choices":[],"usage":{"prompt_tokens":7,"completion_tokens":3}}'):
                    self.wfile.write(("data: " + line + "\n\n").encode())
                self.wfile.write(b"data: [DONE]\n\n")
        server = ThreadingHTTPServer(("127.0.0.1", 0), Stream)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        provider = {**self.provider, "base_url": "http://127.0.0.1:%d/v1" % server.server_port}
        text = "".join(chat.chat_stream(provider, "mercury-3", [{"role": "user", "content": "hi"}], max_tokens=100))
        self.assertEqual(text, "a")
        self.assertEqual(self.guard.status(GID)["total_tokens"], 10)


class SecretsAndPersistenceTests(GuardCase):
    def test_the_key_never_appears_in_state_status_or_errors(self):
        self.open_it()
        Fake.mode = "402"
        with self.assertRaises(RuntimeError) as caught:
            self.ask()
        dump = json.dumps(self.guard.status(GID)) + Path(self.guard.path).read_text(encoding="utf-8") + str(caught.exception)
        self.assertNotIn("sk-secret", dump)

    def test_state_survives_a_restart_and_a_new_guard_starts_closed(self):
        self.open_it()
        self.ask()
        again = FreeQuotaGuard(self.guard.path, clock=lambda: self.now[0])
        self.assertEqual(again.status(GID)["total_tokens"], 50)
        self.assertTrue(again.is_open(GID))
        self.assertFalse(FreeQuotaGuard(None).is_open(GID))                    # not installed / no state: closed

    def test_limits_are_validated(self):
        for bad in ({"max_concurrent_requests": 0}, {"safety_margin_percent": 99}, {"daily_soft_limit_tokens": "many"}, {"timeout_seconds": True}):
            with self.assertRaises(ValueError):
                self.guard.set_limits(GID, **bad)

    def test_the_timeout_is_capped_by_the_guard(self):
        self.assertEqual(self.guard.timeout_for(GID, 120), 60)
        self.assertEqual(self.guard.timeout_for(GID, 20), 20)


class ProviderIntegrationTests(GuardCase):
    def test_a_guarded_provider_is_stored_and_listed_as_not_free_until_the_guard_opens(self):
        providers = Providers(Path(self.tmp.name) / "providers.json")
        providers.add("mercury", base_url="https://api.inceptionlabs.ai/v1", data_policy="cloud-may-train", guard="inception", models=["mercury-3"])
        entry = providers.get("mercury")
        self.assertEqual(entry["guard"], "inception")
        self.assertNotIn("free_all", entry)
        self.assertEqual(usage.tier_for(entry, "mercury-3"), "other")
        self.open_it()
        self.assertEqual(usage.tier_for(entry, "mercury-3"), "free")
        providers.add("mercury", base_url="https://api.inceptionlabs.ai/v1", data_policy="cloud-may-train", models=["mercury-3"])
        self.assertEqual(providers.get("mercury")["guard"], "inception")         # re-registering keeps the guard

    def test_code_work_refuses_guarded_services(self):
        from workshop.codework import CodeRuns, CodeWorkError
        providers = Providers(Path(self.tmp.name) / "providers.json")
        providers.add("mercury", base_url="https://api.inceptionlabs.ai/v1", data_policy="cloud-may-train", guard="inception", models=["mercury-3"])
        providers.set_consent("mercury")
        self.open_it()
        runs = CodeRuns(providers, find_binary=lambda: "x")
        self.assertEqual([o for o in runs.model_options() if o["ref"].startswith("mercury/")], [])
        with self.assertRaises(CodeWorkError) as caught:
            runs._resolve_model("mercury/mercury-3", False)
        self.assertIn("한도 보호", str(caught.exception))

    def test_unguarded_providers_are_unchanged(self):
        plain = {**self.provider}
        plain.pop("guard")
        self.assertEqual(chat.chat(plain, "m", [{"role": "user", "content": "x"}], max_tokens=10), "hi")
        self.assertEqual(self.guard.status(GID)["total_tokens"], 0)


class GuardByHostTests(GuardCase):
    """A guarded service stays guarded however it was registered: the guard follows the address, not only the `guard` field."""

    def test_the_guard_is_found_from_the_address(self):
        self.assertEqual(quotaguard.guard_for_url("https://api.inceptionlabs.ai/v1"), GID)
        self.assertEqual(quotaguard.guard_for_url("https://API.InceptionLabs.AI/v1"), GID)
        for other in ("https://api.inceptionlabs.ai.evil.example/v1", "https://evil-api.inceptionlabs.ai.example/v1",
                      "https://example.com/api.inceptionlabs.ai", "http://127.0.0.1:11434/v1", "", None, 5):
            self.assertIsNone(quotaguard.guard_for_url(other), other)

    def test_a_service_added_without_the_guard_field_is_guarded_anyway(self):
        providers = Providers(Path(self.tmp.name) / "providers.json")
        providers.add("custom-mercury", base_url="https://api.inceptionlabs.ai/v1", data_policy="cloud-may-train", free_all=True, models=["mercury-3"])
        entry = providers.get("custom-mercury")
        self.assertEqual(entry["guard"], GID)                                      # derived from the address
        self.assertEqual(usage.tier_for(entry, "mercury-3"), "other")             # "free_all" does not make it free: the guard decides

    def test_a_chat_to_such_a_service_is_refused_before_any_network_call(self):
        def no_network(*a, **k):
            raise AssertionError("a request left the PC")
        old = chat._open
        chat._open = no_network
        self.addCleanup(setattr, chat, "_open", old)
        provider = {"base_url": "https://api.inceptionlabs.ai/v1", "api_key": "sk-secret-0123456789", "data_policy": "cloud-may-train"}     # no `guard`
        with self.assertRaises(QuotaBlocked):
            chat.chat(provider, "mercury-3", [{"role": "user", "content": "hi"}], max_tokens=16)
        with self.assertRaises(QuotaBlocked):
            list(chat.chat_stream(provider, "mercury-3", [{"role": "user", "content": "hi"}], max_tokens=16))

    def test_code_work_refuses_it_too(self):
        from workshop.codework import CodeRuns, CodeWorkError
        providers = Providers(Path(self.tmp.name) / "providers.json")
        providers.add("custom-mercury", base_url="https://api.inceptionlabs.ai/v1", data_policy="cloud-may-train", models=["mercury-3"])
        providers.set_consent("custom-mercury")
        runs = CodeRuns(providers, find_binary=lambda: "x")
        self.assertEqual([o for o in runs.model_options() if o["ref"].startswith("custom-mercury/")], [])
        with self.assertRaises(CodeWorkError):
            runs._resolve_model("custom-mercury/mercury-3", False)


class FallbackTests(GuardCase):
    def test_the_router_drops_a_closed_service_and_uses_other_free_models(self):
        from workshop import router
        tiers = lambda m: usage.tier_for(self.provider, "mercury-3") if m.startswith("mercury/") else "free"      # noqa: E731
        task = {"type": "code", "level": 2}
        closed = router.route_task(task, ["mercury/mercury-3", "other/free-model"], tier_fn=tiers)
        self.assertEqual(closed["model"], "other/free-model")
        self.open_it()
        self.assertIn(router.route_task(task, ["mercury/mercury-3"], tier_fn=tiers)["model"], ("mercury/mercury-3",))


if __name__ == "__main__":
    unittest.main()


class QuotaApiTests(unittest.TestCase):
    def setUp(self):
        from tests.test_chat_api import ApiBase
        self.base = ApiBase("run")
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self._old = quotaguard.get()
        self.addCleanup(quotaguard.install, self._old)

    def call(self, method, body=None):
        return self.base.jcall(method, "/quota", body)

    def test_status_defaults_to_closed_and_never_shows_a_guessed_number(self):
        status, body = self.call("GET")
        self.assertEqual(status, 200)
        guard = body["guards"][0]
        self.assertFalse(guard["open"])
        self.assertEqual(guard["balance_label"], "확인 불가 · 호출 차단")
        self.assertEqual(guard["registered"], [])

    def test_enabling_needs_the_confirmation_and_the_balance_in_order(self):
        status, body = self.call("POST", {"guard": GID, "action": "enable", "enabled": True})
        self.assertEqual(status, 400)
        self.assertEqual(self.call("POST", {"guard": GID, "action": "attest", "confirmed": True})[0], 200)
        self.assertEqual(self.call("POST", {"guard": GID, "action": "enable", "enabled": True})[0], 400)         # balance still unknown
        self.assertEqual(self.call("POST", {"guard": GID, "action": "balance", "tokens": "lots"})[0], 400)
        self.assertEqual(self.call("POST", {"guard": GID, "action": "balance", "tokens": 5_000_000})[0], 200)
        status, body = self.call("POST", {"guard": GID, "action": "enable", "enabled": True})
        self.assertEqual((status, body["open"]), (200, True))
        status, body = self.call("POST", {"guard": GID, "action": "attest", "confirmed": False})           # withdrawing the confirmation switches it off
        self.assertFalse(body["open"])

    def test_unknown_guard_and_action_are_rejected(self):
        self.assertEqual(self.call("POST", {"guard": "nope", "action": "attest", "confirmed": True})[0], 400)
        self.assertEqual(self.call("POST", {"guard": GID, "action": "pay"})[0], 400)
