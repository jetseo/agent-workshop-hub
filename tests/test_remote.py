import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

from workshop import remote as rem
from workshop.queue import Queue
from workshop.remote import Message, RemoteControl, TelegramChannel, TelegramError
from tests.test_chat_api import ApiBase

TOKEN = "123456789:AAFakeTokenForTests_abcdefghijklmnopqrstu"
OWNER, STRANGER = 111, 222


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class FakeTelegram:
    """Stands in for api.telegram.org."""

    def __init__(self):
        self.updates, self.sent, self.calls, self.revoked = [], [], [], False

    def add(self, update_id, user, text, chat_type="private", date=1000, chat=None):
        self.updates.append({"update_id": update_id, "message": {"from": {"id": user, "first_name": "Kim"}, "chat": {"id": chat or user, "type": chat_type},
                                                                  "text": text, "date": date}})

    def open(self, request, timeout=0):
        assert request.full_url.startswith("https://api.telegram.org/bot"), request.full_url
        if self.revoked:
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)
        method = request.full_url.rsplit("/", 1)[1]
        params = json.loads(request.data or b"{}")
        self.calls.append((method, params))
        if method == "getMe":
            result = {"id": 1, "username": "jetai_test_bot"}
        elif method == "getUpdates":
            offset = params.get("offset")
            if offset == -1:
                result = self.updates[-1:]
            else:
                result = [u for u in self.updates if offset is None or u["update_id"] >= offset]
        elif method == "sendMessage":
            self.sent.append((params["chat_id"], params["text"]))
            result = {"message_id": len(self.sent)}
        else:
            raise urllib.error.URLError("unknown method")
        return Resp(json.dumps({"ok": True, "result": result}).encode())


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class Base(unittest.TestCase):
    def setUp(self):
        self.base = ApiBase("run")
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.base.onboard()
        self.dir = Path(self.base.tmp.name)
        self.queue = Queue(self.dir / "q.db")
        self.net, self.clock = FakeTelegram(), Clock()
        self.rc = RemoteControl(self.base.ctx, self.queue, home=self.dir / "remote", clock=self.clock, opener=self.net)
        self.addCleanup(self.rc.stop)
        self.replies = []

    def say(self, user, text, chat_type="private", date=None):
        message = Message(1, user, user, chat_type, text, self.clock.now if date is None else date, "Kim")
        self.replies.clear()
        self.rc.handle(message, self.replies.append)
        return "\n".join(self.replies)

    def pair(self):
        code = self.rc.setup_telegram(TOKEN)["code"]
        self.rc.stop()
        self.assertIn("연결되었습니다", self.say(OWNER, "/start " + code))


class ChannelTests(unittest.TestCase):
    def test_token_format_and_hosts(self):
        for bad in ("", "abc", "123:short", None):
            with self.assertRaises(TelegramError):
                TelegramChannel(bad)
        net = FakeTelegram()
        channel = TelegramChannel(TOKEN, opener=net)
        self.assertEqual(channel.whoami()["username"], "jetai_test_bot")

    def test_poll_parses_messages_and_send_splits_long_text(self):
        net = FakeTelegram()
        net.add(5, OWNER, "hello", date=42)
        channel = TelegramChannel(TOKEN, opener=net)
        message = channel.poll(0)[0]
        self.assertEqual((message.update_id, message.user_id, message.chat_type, message.text, message.date), (5, OWNER, "private", "hello", 42))
        channel.send(OWNER, "x" * 9000)
        self.assertEqual([len(t) for _c, t in net.sent], [3800, 3800, 1400])
        self.assertEqual(channel.latest_update_id(), 5)

    def test_errors_never_contain_the_token(self):
        net = FakeTelegram()
        net.revoked = True
        with self.assertRaises(TelegramError) as caught:
            TelegramChannel(TOKEN, opener=net).whoami()
        self.assertIn("토큰", str(caught.exception))
        self.assertNotIn(TOKEN, str(caught.exception))

        class Offline:
            def open(self, request, timeout=0):
                raise urllib.error.URLError("no route " + request.full_url)
        with self.assertRaises(TelegramError) as caught:
            TelegramChannel(TOKEN, opener=Offline()).whoami()
        self.assertNotIn(TOKEN, str(caught.exception))


class PairingTests(Base):
    def test_strangers_get_no_answer_before_and_after_pairing(self):
        self.assertEqual(self.say(STRANGER, "/start 123456"), "")                     # nothing configured yet
        code = self.rc.setup_telegram(TOKEN)["code"]
        self.rc.stop()
        self.assertEqual(self.say(STRANGER, "/start 000000" if code != "000000" else "/start 111111"), "")      # wrong code: silence
        self.assertEqual(self.say(STRANGER, "hello"), "")
        self.assertIn("연결되었습니다", self.say(OWNER, "/start " + code))
        self.assertEqual(self.say(STRANGER, "/status"), "")                           # not the owner: silence
        self.assertEqual(self.say(STRANGER, "/start " + code), "")                    # the code is single-use
        self.assertTrue(self.rc.status()["paired"])

    def test_group_chats_and_expired_codes_are_ignored(self):
        code = self.rc.setup_telegram(TOKEN)["code"]
        self.rc.stop()
        self.assertEqual(self.say(OWNER, "/start " + code, chat_type="group"), "")
        self.clock.now += rem.PAIR_TTL_SECONDS + 1
        self.assertEqual(self.say(OWNER, "/start " + code), "")
        self.assertFalse(self.rc.status()["paired"])
        self.assertFalse(self.rc.status()["pairing"])
        code2 = self.rc.repair()["code"]                                              # a fresh window can be opened from the PC app
        self.assertIn("연결되었습니다", self.say(OWNER, "/start " + code2))

    def test_token_is_stored_privately_and_never_in_the_status_or_state_file(self):
        self.rc.setup_telegram(TOKEN)
        self.rc.stop()
        self.assertNotIn(TOKEN, (self.dir / "remote" / "remote.json").read_text(encoding="utf-8"))
        self.assertNotIn(TOKEN, json.dumps(self.rc.status()))
        self.assertIn(TOKEN, (self.dir / "remote" / "remote-secrets.json").read_text(encoding="utf-8"))

    def test_invalid_token_stores_nothing_and_disconnect_clears_everything(self):
        self.net.revoked = True
        with self.assertRaises(TelegramError):
            self.rc.setup_telegram(TOKEN)
        self.assertFalse((self.dir / "remote" / "remote-secrets.json").exists())
        self.net.revoked = False
        self.pair()
        self.rc.disconnect()
        self.assertEqual(self.rc.status(), {"configured": False, "bot": "", "paired": False, "owner": "", "pairing": False,
                                            "running": False, "last_error": ""})
        self.assertFalse((self.dir / "remote" / "remote-secrets.json").exists())


class CommandTests(Base):
    def setUp(self):
        super().setUp()
        self.pair()

    def test_help_status_models_usage(self):
        self.assertIn("/approve", self.say(OWNER, "/help"))
        status = self.say(OWNER, "/status")
        for part in ("연결 서비스", "내장 엔진", "작업 큐", "오늘 토큰"):
            self.assertIn(part, status)
        self.assertIn("mock", self.say(OWNER, "/models"))
        self.assertIn("오늘 토큰", self.say(OWNER, "/usage"))
        self.assertIn("알 수 없는 명령", self.say(OWNER, "/nonsense"))

    def test_old_messages_are_never_replayed_and_rate_limit_applies(self):
        self.assertEqual(self.say(OWNER, "/status", date=self.clock.now - rem.MAX_AGE_SECONDS - 5), "")
        for _ in range(rem.RATE_LIMIT_PER_MINUTE):
            self.say(OWNER, "/help")
        self.assertEqual(self.say(OWNER, "/help"), "")                                # the 21st in a minute is dropped
        self.clock.now += 61
        self.assertIn("/approve", self.say(OWNER, "/help"))

    def test_jobs_list_and_failure_reasons(self):
        job = self.queue.add("code", "고칠 작업", {"workspace": "x"})
        self.queue.claim()
        self.queue.finish(job, {"error": "모델이 응답하지 않음"}, success=False)
        self.assertIn("고칠 작업", self.say(OWNER, "/jobs"))
        self.assertIn("모델이 응답하지 않음", self.say(OWNER, "/failures"))

    def test_approve_needs_a_number_and_a_second_confirmation(self):
        job = self.queue.add("code", "승인할 작업", {"workspace": "x"}, require_approval=True)
        self.assertIn("/jobs", self.say(OWNER, "/approve 1"))                         # no list shown yet
        self.say(OWNER, "/jobs")
        ask = self.say(OWNER, "/approve 1")
        self.assertIn("/yes", ask)
        self.assertIn("파일을 바꿀 수", ask)                                           # code work carries a warning
        self.assertEqual(self.queue.list()[0]["state"], "pending_approval")           # nothing happened yet
        self.assertIn("승인했습니다", self.say(OWNER, "/yes"))
        self.assertEqual(self.queue.list()[0]["state"], "queued")
        self.assertIn("확인할 작업이 없습니다", self.say(OWNER, "/yes"))               # one confirmation, one action

    def test_confirmation_expires(self):
        self.queue.add("code", "늦은 승인", {"workspace": "x"}, require_approval=True)
        self.say(OWNER, "/jobs")
        self.say(OWNER, "/approve 1")
        self.clock.now += rem.CONFIRM_TTL_SECONDS + 1
        self.assertIn("확인할 작업이 없습니다", self.say(OWNER, "/yes"))
        self.assertEqual(self.queue.list()[0]["state"], "pending_approval")

    def test_cancel_is_immediate_and_retry_is_confirmed(self):
        waiting = self.queue.add("code", "취소할 작업", {"workspace": "x"}, require_approval=True)
        self.say(OWNER, "/jobs")
        self.assertIn("취소했습니다", self.say(OWNER, "/cancel 1"))
        self.assertEqual(self.queue.list()[0]["state"], "cancelled")
        self.say(OWNER, "/jobs")
        self.assertEqual(self.say(OWNER, "/jobs"), "처리할 작업이 없습니다.")
        failed = self.queue.add("code", "다시 할 작업", {"workspace": "x"})
        self.queue.claim()
        self.queue.finish(failed, {"error": "x"}, success=False)
        self.say(OWNER, "/jobs")
        self.assertIn("/yes", self.say(OWNER, "/retry 1"))
        self.assertIn("대기열", self.say(OWNER, "/yes"))
        self.assertEqual([j for j in self.queue.list() if j["id"] == failed][0]["state"], "queued")

    def test_plain_text_is_answered_by_auto_mode_in_one_phone_conversation(self):
        answer = self.say(OWNER, "안녕하세요 반가워요")
        self.assertIn("Echo", answer)                                                 # the mock service echoes the question
        self.assertIn("—", answer)                                                    # which model/kind answered
        chats = self.base.ctx.store.list()
        self.assertEqual(len(chats), 1)
        self.say(OWNER, "/ask 두 번째 질문")
        self.assertEqual(len(self.base.ctx.store.list()), 1)                          # same conversation
        self.assertEqual(len(self.base.ctx.store.get(chats[0]["id"])["messages"]), 4)
        self.say(OWNER, "/new")
        self.say(OWNER, "새 대화 질문")
        self.assertEqual(len(self.base.ctx.store.list()), 2)

    def test_a_failing_command_is_reported_not_raised(self):
        self.rc.queue = None
        self.assertIn("사용할 수 없음", self.say(OWNER, "/status"))
        self.assertIn("/jobs", self.say(OWNER, "/approve 3"))


class LoopTests(Base):
    def test_backlog_from_before_start_is_skipped_and_new_messages_are_handled(self):
        self.net.add(1, OWNER, "/help", date=self.clock.now)                          # sent while JetAI was off
        self.pair_for_loop()
        self.net.sent.clear()
        self.rc.step()
        self.assertEqual(self.net.sent, [])                                           # not replayed
        self.net.add(50, OWNER, "/help", date=self.clock.now)
        self.assertEqual(self.rc.step(), 1)
        self.assertEqual(len(self.net.sent), 1)
        self.assertEqual(self.rc.step(), 0)                                           # offset advanced: no repeat

    def pair_for_loop(self):
        code = self.rc.setup_telegram(TOKEN)["code"]
        self.rc.stop()
        self.rc.channel, self.rc._offset = None, None
        self.rc.step()                                                                # polling starts: whatever was sent before is skipped
        self.net.add(10, OWNER, "/start " + code, date=self.clock.now)                # the phone sends the code afterwards
        self.rc.step()

    def test_alerts_are_batched_into_one_message_per_minute_and_old_jobs_are_not_announced(self):
        old = self.queue.add("code", "예전 작업", {"workspace": "x"}, require_approval=True)
        self.pair_for_loop()
        self.rc.step()                                                                # first look: remembers the old job
        self.net.sent.clear()
        for title in ("A 작업", "B 작업", "C 작업"):
            self.queue.add("code", title, {"workspace": "x"}, require_approval=True)
        self.rc.step()
        self.assertEqual(len(self.net.sent), 1)                                       # three jobs, ONE message
        text = self.net.sent[0][1]
        self.assertIn("3건", text)
        self.assertNotIn("예전 작업", text)
        self.queue.add("code", "D 작업", {"workspace": "x"}, require_approval=True)
        self.rc.step()
        self.assertEqual(len(self.net.sent), 1)                                       # inside the minute: held back
        self.clock.now += rem.ALERT_BATCH_SECONDS + 1
        self.rc.step()
        self.assertEqual(len(self.net.sent), 2)
        self.assertIn("D 작업", self.net.sent[1][1])

    def test_background_thread_runs_and_stops_cleanly(self):
        self.rc.setup_telegram(TOKEN)
        self.assertTrue(self.rc.status()["running"])
        self.rc.stop()
        self.assertFalse(self.rc.status()["running"])


class RemoteApiTests(Base):
    def test_api_never_returns_the_token_and_reports_status(self):
        base = self.base
        base.ctx.remote = self.rc
        status, body = base.jcall("GET", "/remote")
        self.assertEqual((status, body["configured"]), (200, False))
        status, body = base.jcall("POST", "/remote/telegram", {"token": TOKEN})
        self.assertEqual(status, 200)
        self.assertEqual(body["bot"], "@jetai_test_bot")
        self.assertRegex(body["code"], r"\d{6}")
        self.assertNotIn(TOKEN, json.dumps(body))
        self.assertNotIn(TOKEN, json.dumps(base.jcall("GET", "/remote")[1]))
        self.assertEqual(base.jcall("POST", "/remote/telegram", {"token": "nope"})[0], 400)
        self.assertEqual(base.jcall("POST", "/remote/telegram", {})[0], 400)
        self.assertEqual(base.jcall("POST", "/remote/repair", {})[0], 200)
        self.assertEqual(base.jcall("POST", "/remote/disconnect", {})[0], 200)
        self.assertEqual(base.jcall("POST", "/remote/repair", {})[0], 400)            # nothing connected any more
        self.assertEqual(base.jcall("POST", "/remote/other", {})[0], 404)


if __name__ == "__main__":
    unittest.main()
