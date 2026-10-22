"""Control JetAI from a phone through a messenger. The PC only makes OUTGOING connections (no port is opened).

Structure: a command core (`RemoteControl.handle`) that does not know any messenger, and a channel adapter
(`TelegramChannel`) that long-polls the Telegram Bot API. Other messengers can be added as another channel with
`poll()` / `send()` (see docs/remote-control.md for the candidates).

Safety rules (all enforced here, all covered by tests):
  * opt-in: nothing runs until the user pastes a bot token in settings; the token lives in its own 0600 file;
  * pairing: the first account that sends the one-time code shown in the PC app becomes the only owner;
    everyone else (and every group chat) is ignored silently - no reply at all;
  * stale commands are never replayed: the backlog is skipped at start and messages older than 2 minutes are dropped;
  * running work needs a second step: /approve and /retry ask for /yes first;
  * at most 20 commands a minute; replies are cut to the messenger's limit; alerts are batched;
  * the conversation goes through Telegram's servers (not end-to-end encrypted): the settings screen says so.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

API_HOST = "api.telegram.org"
POLL_SECONDS = 25
MAX_REPLY = 3800
MAX_AGE_SECONDS = 120
PAIR_TTL_SECONDS = 600
CONFIRM_TTL_SECONDS = 120
RATE_LIMIT_PER_MINUTE = 20
ALERT_STATES = ("pending_approval", "review", "failed")
ALERT_BATCH_SECONDS = 60
TOKEN = re.compile(r"\d{6,12}:[A-Za-z0-9_-]{30,60}")
STATE_LABEL = {"queued": "대기", "pending_approval": "실행 승인 대기", "running": "실행 중", "review": "결과 검토 대기",
               "done": "완료", "failed": "실패", "cancelled": "취소됨"}

HELP = ("JetAI 원격 제어\n"
        "글을 그냥 보내면 AI가 알맞은 모델로 답합니다 (/ask 질문 도 같음)\n"
        "/status  상태 요약    /models  연결된 모델    /usage  오늘 사용량\n"
        "/jobs  작업 목록    /failures  최근 실패 원인\n"
        "/approve 번호  실행 승인    /cancel 번호  취소    /retry 번호  다시 실행 (승인·재실행은 /yes 로 한 번 더 확인)\n"
        "/new  새 대화 시작    /help  이 도움말")


class Message:
    def __init__(self, update_id, user_id, chat_id, chat_type, text, date, name=""):
        self.update_id, self.user_id, self.chat_id, self.chat_type = update_id, user_id, chat_id, chat_type
        self.text, self.date, self.name = text, date, name


class TelegramError(Exception):
    pass


class TelegramChannel:
    """Telegram Bot API over HTTPS with long polling. Only api.telegram.org is ever contacted."""

    def __init__(self, token, opener=None):
        if not TOKEN.fullmatch(token or ""):
            raise TelegramError("봇 토큰 형식이 올바르지 않습니다. BotFather가 알려 준 123456789:AA... 형태를 붙여 넣으세요.")
        self._base = "https://%s/bot%s/" % (API_HOST, token)
        self._token = token
        self.opener = opener or urllib.request.build_opener()

    def _call(self, method, params=None, timeout=35):
        body = json.dumps(params or {}).encode("utf-8")
        request = urllib.request.Request(self._base + method, data=body, headers={"Content-Type": "application/json", "User-Agent": "jetai-remote"})
        try:
            with self.opener.open(request, timeout=timeout) as response:
                data = json.loads(response.read(2_000_000).decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code in (401, 404):
                raise TelegramError("토큰이 올바르지 않거나 취소되었습니다.") from None
            raise TelegramError("텔레그램 응답 오류 (%d)" % error.code) from None
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise TelegramError("텔레그램에 연결하지 못했습니다: " + str(getattr(error, "reason", error))[:100].replace(self._token, "***")) from None
        if not data.get("ok"):
            raise TelegramError("텔레그램이 요청을 거절했습니다.")
        return data["result"]

    def whoami(self):
        return self._call("getMe", timeout=15)

    def poll(self, offset=None, timeout=POLL_SECONDS):
        params = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset
        out = []
        for update in self._call("getUpdates", params, timeout=timeout + 10):
            message = update.get("message") or {}
            sender, chat = message.get("from") or {}, message.get("chat") or {}
            out.append(Message(update["update_id"], sender.get("id"), chat.get("id"), chat.get("type"), message.get("text") or "",
                               message.get("date", 0), (sender.get("first_name") or sender.get("username") or "")[:40]))
        return out

    def latest_update_id(self):
        result = self._call("getUpdates", {"offset": -1, "timeout": 0}, timeout=15)
        return result[-1]["update_id"] if result else None

    def send(self, chat_id, text):
        for start in range(0, max(1, len(text)), MAX_REPLY):
            self._call("sendMessage", {"chat_id": chat_id, "text": text[start:start + MAX_REPLY] or "(빈 응답)", "disable_web_page_preview": True}, timeout=20)


def _hash_code(code, salt):
    return hashlib.sha256((salt + code).encode()).hexdigest()


class RemoteControl:
    def __init__(self, ctx, queue=None, *, home=None, channel_factory=TelegramChannel, clock=time.time, opener=None):
        self.ctx, self.queue = ctx, queue
        self.home = Path(home) if home else ctx.providers.path.parent
        self.state_path, self.secret_path = self.home / "remote.json", self.home / "remote-secrets.json"
        self.channel_factory, self.clock, self.opener = channel_factory, clock, opener
        self.channel = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._offset = None
        self._pending = {}            # owner confirmation: {"action", "job", "title", "expires"}
        self._numbers = {}            # job numbers shown by /jobs -> job id
        self._recent = []             # timestamps of handled commands (rate limit)
        self._seen_states = None      # job id -> state, for batched alerts
        self._alerts, self._last_alert = [], 0.0
        self.last_error = ""

    # ---- stored state -------------------------------------------------------------------------------------------
    def _read(self):
        with self._lock:                         # the polling thread and the API both use this file
            try:
                return json.loads(self.state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}

    def _write(self, data):
        with self._lock:
            self.home.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            for attempt in range(5):             # Windows refuses to replace a file another program has open for a moment
                try:
                    os.replace(tmp, self.state_path)
                    return
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.05)

    def _token(self):
        try:
            return json.loads(self.secret_path.read_text(encoding="utf-8")).get("telegram_token")
        except (OSError, ValueError):
            return None

    def _save_token(self, token):
        self.home.mkdir(parents=True, exist_ok=True)
        if token is None:
            self.secret_path.unlink(missing_ok=True)
            return
        fd = os.open(self.secret_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"telegram_token": token}, handle)

    # ---- setup from the PC app ----------------------------------------------------------------------------------
    def setup_telegram(self, token):
        """Check the token, store it privately, and open a pairing window. Returns what the app shows the user."""
        token = (token or "").strip()
        channel = self.channel_factory(token, opener=self.opener) if self.opener else self.channel_factory(token)
        info = channel.whoami()                                       # proves the token works before anything is stored
        self.disconnect()
        self._save_token(token)
        code = self._new_pairing()
        state = self._read()
        state["bot"] = "@" + str(info.get("username", ""))
        self._write(state)
        self.start()
        return {"bot": state["bot"], "code": code, "expires_in": PAIR_TTL_SECONDS}

    def _new_pairing(self):
        code = "%06d" % secrets.randbelow(1_000_000)
        salt = secrets.token_hex(8)
        state = self._read()
        state["pair"] = {"salt": salt, "hash": _hash_code(code, salt), "expires": self.clock() + PAIR_TTL_SECONDS}
        state.pop("owner", None)
        self._write(state)
        return code

    def repair(self):
        if not self._token():
            raise TelegramError("먼저 봇 토큰을 연결하세요.")
        return {"code": self._new_pairing(), "expires_in": PAIR_TTL_SECONDS}

    def disconnect(self):
        self.stop()
        self._save_token(None)
        self._write({})
        self._pending.clear()
        self.channel = None

    def status(self):
        state = self._read()
        pair = state.get("pair")
        pairing = bool(pair and pair["expires"] > self.clock() and not state.get("owner"))
        return {"configured": bool(self._token()), "bot": state.get("bot", ""), "paired": bool(state.get("owner")),
                "owner": (state.get("owner") or {}).get("name", ""), "pairing": pairing,
                "running": bool(self._thread and self._thread.is_alive()), "last_error": self.last_error}

    # ---- command core (messenger independent) -------------------------------------------------------------------
    def handle(self, message, reply):
        """Process one incoming message. `reply(text)` sends an answer. Unknown senders get NO answer."""
        state = self._read()
        if message.chat_type != "private" or not message.text:
            return
        if self.clock() - float(message.date or 0) > MAX_AGE_SECONDS and message.date:
            return                                                    # old (queued while we were off): never replay
        owner = state.get("owner")
        text = message.text.strip()
        if not owner:
            self._try_pair(state, message, text, reply)
            return
        if message.user_id != owner["user_id"]:
            return                                                    # a stranger: silence
        now = self.clock()
        self._recent = [t for t in self._recent if now - t < 60]
        if len(self._recent) >= RATE_LIMIT_PER_MINUTE:
            return
        self._recent.append(now)
        try:
            reply(self._dispatch(text))
        except Exception as error:                                    # a bad command must never kill the loop
            reply("처리하지 못했습니다: " + str(error)[:200])

    def _try_pair(self, state, message, text, reply):
        pair = state.get("pair")
        match = re.fullmatch(r"/start(?:@\w+)?\s+(\d{6})", text)
        if not pair or not match or pair["expires"] < self.clock():
            return                                                    # not pairing, or wrong/late code: stay silent
        if not hmac.compare_digest(_hash_code(match.group(1), pair["salt"]), pair["hash"]):
            return
        state["owner"] = {"user_id": message.user_id, "chat_id": message.chat_id, "name": message.name or "owner"}
        state.pop("pair", None)
        self._write(state)
        reply("연결되었습니다. 이제 이 대화로 JetAI를 조작할 수 있습니다.\n\n" + HELP)

    def _dispatch(self, text):
        command, _, rest = text.partition(" ")
        command, rest = command.lower().split("@")[0], rest.strip()
        if command in ("/start", "/help"):
            return HELP
        if command == "/status":
            return self._status_text()
        if command == "/models":
            return self._models_text()
        if command == "/usage":
            return self._usage_text()
        if command == "/jobs":
            return self._jobs_text()
        if command == "/failures":
            return self._failures_text()
        if command in ("/approve", "/retry", "/cancel"):
            return self._job_action(command[1:], rest)
        if command == "/yes":
            return self._confirm()
        if command == "/new":
            state = self._read()
            state.pop("chat_id", None)
            self._write(state)
            return "새 대화를 시작합니다."
        if command == "/ask":
            return self._ask(rest) if rest else "질문을 함께 보내 주세요. 예: /ask 파이썬 리스트 뒤집는 법"
        if command.startswith("/"):
            return "알 수 없는 명령입니다. /help"
        return self._ask(text)

    # ---- the commands -------------------------------------------------------------------------------------------
    def _status_text(self):
        providers = self.ctx.providers.load()
        models = sum(len(p.get("models", [])) for p in providers.values())
        engine = self.ctx.engine.status()
        lines = ["JetAI 상태", "연결 서비스 %d개 · 모델 %d개" % (len(providers), models),
                 "내장 엔진: " + ("실행 중" if engine["ready"] else "꺼짐")]
        lines.append(self._queue_summary())
        lines.append(self._usage_text())
        return "\n".join(lines)

    def _models_text(self):
        lines = []
        for name, entry in self.ctx.providers.load().items():
            models = entry.get("models", [])
            if models:
                lines.append("%s%s: %s" % (name, " (내 PC)" if entry["data_policy"] == "local" else "", ", ".join(models[:6]) + (" …" if len(models) > 6 else "")))
        return "\n".join(lines) or "연결된 모델이 없습니다."

    def _usage_text(self):
        today = self.ctx.ledger.summary(days=1)["today"]
        free = today["local"]["tokens"] + today["free"]["tokens"]
        return "오늘 토큰: 무료·로컬 %s · 그 밖(요금 가능) %s" % (format(free, ","), format(today["other"]["tokens"], ","))

    def _jobs(self):
        return [j for j in (self.queue.list() if self.queue else []) if j["state"] in ("pending_approval", "queued", "running", "review", "failed")][-10:]

    def _queue_summary(self):
        if not self.queue:
            return "작업 큐: 사용할 수 없음"
        counts = {}
        for job in self.queue.list():
            counts[job["state"]] = counts.get(job["state"], 0) + 1
        return "작업 큐: " + (", ".join("%s %d" % (STATE_LABEL.get(k, k), v) for k, v in counts.items()) or "비어 있음")

    def _jobs_text(self):
        jobs = self._jobs()
        self._numbers = {i + 1: job["id"] for i, job in enumerate(jobs)}
        if not jobs:
            return "처리할 작업이 없습니다."
        return "\n".join("%d. [%s] %s (%s)" % (i + 1, STATE_LABEL.get(j["state"], j["state"]), j["title"][:50], j["kind"]) for i, j in enumerate(jobs))

    def _failures_text(self):
        failed = [j for j in (self.queue.list() if self.queue else []) if j["state"] == "failed"][-5:]
        if not failed:
            return "최근 실패한 작업이 없습니다."
        out = []
        for job in failed:
            try:
                reason = json.loads(job["result"] or "null")
                reason = (reason.get("error") if isinstance(reason, dict) else str(reason)) or "원인 기록 없음"
            except ValueError:
                reason = "원인 기록 없음"
            out.append("• %s — %s" % (job["title"][:40], str(reason)[:160]))
        return "최근 실패 원인\n" + "\n".join(out)

    def _job_action(self, action, rest):
        number = int(rest) if rest.isdigit() else None
        job_id = self._numbers.get(number)
        if job_id is None:
            return "먼저 /jobs 로 목록을 보고, 그 번호를 쓰세요. 예: /%s 2" % action
        job = next((j for j in self.queue.list() if j["id"] == job_id), None) if self.queue else None
        if job is None:
            return "작업을 찾지 못했습니다."
        if action == "cancel":
            return "%s: %s" % (job["title"][:40], {"cancelled": "취소했습니다", "cancel_requested": "취소를 요청했습니다(실행 중인 작업은 끝나면 취소로 표시)"}[self.queue.cancel(job_id)])
        self._pending = {"action": action, "job": job_id, "title": job["title"], "expires": self.clock() + CONFIRM_TTL_SECONDS}
        what = "실행을 승인" if action == "approve" else "다시 실행"
        warn = " 코드 작업은 PC에서 AI가 파일을 바꿀 수 있습니다." if job["kind"] == "code" else ""
        return "'%s' 작업의 %s할까요?%s\n맞으면 2분 안에 /yes" % (job["title"][:40], what, warn)

    def _confirm(self):
        pending, self._pending = self._pending, {}
        if not pending or pending["expires"] < self.clock():
            return "확인할 작업이 없습니다(시간이 지났거나 요청이 없음)."
        if pending["action"] == "approve":
            job = next((j for j in self.queue.list() if j["id"] == pending["job"]), None)
            if job and job["state"] == "pending_approval":
                self.queue.authorize(pending["job"])
                return "실행을 승인했습니다: " + pending["title"][:40]
            self.queue.approve(pending["job"])                       # a finished result waiting for review
            return "결과를 승인했습니다: " + pending["title"][:40]
        self.queue.retry(pending["job"])
        return "다시 대기열에 넣었습니다: " + pending["title"][:40]

    def _ask(self, text):
        """Plain questions go through the same auto mode as the app, in one persistent 'phone' conversation."""
        from . import chat_api
        state = self._read()
        chat_id = state.get("chat_id")
        try:
            self.ctx.store.get(chat_id)
        except Exception:
            chat_id = self.ctx.store.create("auto")["id"]
            self.ctx.store.update(chat_id, title="텔레그램")
            state["chat_id"] = chat_id
            self._write(state)
        answer, error, model, why = [], "", "", ""
        try:
            stream = chat_api._send(self.ctx, chat_id, {"content": text, "model": "auto"})
            for event, data in stream.events:
                if event == "meta":
                    model, why = data.get("model", ""), data.get("why", "")
                elif event == "delta":
                    answer.append(data["t"])
                elif event == "done":
                    model = data.get("model", model)
                elif event == "error":
                    error = data.get("message", "")
        except chat_api.ApiError as problem:
            return "질문을 처리하지 못했습니다: " + problem.message
        text_out = "".join(answer).strip()
        if not text_out:
            return "답을 받지 못했습니다: " + (error or "알 수 없는 오류")
        return text_out + ("\n\n— " + (why or model) if (why or model) else "")

    # ---- batched alerts -----------------------------------------------------------------------------------------
    def check_jobs(self, send):
        """Collect jobs that newly need attention and send ONE message per minute at most (no alert storms)."""
        if not self.queue:
            return
        now = self.clock()
        current = {j["id"]: j for j in self.queue.list()}
        if self._seen_states is None:
            self._seen_states = {k: v["state"] for k, v in current.items()}
            return                                                    # first look: remember, never announce old jobs
        for job_id, job in current.items():
            if job["state"] in ALERT_STATES and self._seen_states.get(job_id) != job["state"]:
                self._alerts.append("• [%s] %s" % (STATE_LABEL[job["state"]], job["title"][:50]))
        self._seen_states = {k: v["state"] for k, v in current.items()}
        if self._alerts and now - self._last_alert >= ALERT_BATCH_SECONDS:
            send("확인이 필요한 작업 %d건\n%s\n(/jobs 로 확인)" % (len(self._alerts), "\n".join(self._alerts[:8])))
            self._alerts, self._last_alert = [], now

    # ---- the polling loop ---------------------------------------------------------------------------------------
    def step(self):
        """One poll cycle (also what the tests drive). Returns the number of messages seen."""
        if self.channel is None:
            token = self._token()
            if not token:
                return 0
            self.channel = self.channel_factory(token, opener=self.opener) if self.opener else self.channel_factory(token)
        if self._offset is None:
            latest = self.channel.latest_update_id()                  # skip everything sent while JetAI was off
            self._offset = (latest + 1) if latest is not None else 0
        messages = self.channel.poll(self._offset)
        for message in messages:
            self._offset = max(self._offset, message.update_id + 1)
            self.handle(message, lambda text, m=message: self.channel.send(m.chat_id, text))
        owner = self._read().get("owner")
        if owner:
            self.check_jobs(lambda text: self.channel.send(owner["chat_id"], text))
        return len(messages)

    def _loop(self):
        wait = 2
        while not self._stop.is_set():
            try:
                self.step()
                self.last_error, wait = "", 2
            except TelegramError as error:
                self.last_error = str(error)
                if "토큰" in self.last_error:
                    return                                            # a revoked token will not fix itself
                self._stop.wait(wait)
                wait = min(wait * 2, 60)
            except Exception as error:
                self.last_error = "내부 오류: " + str(error)[:120]
                self._stop.wait(wait)
                wait = min(wait * 2, 60)

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive() or not self._token():
                return
            self._stop.clear()
            self._offset, self.channel = None, None
            self._thread = threading.Thread(target=self._loop, name="remote-telegram", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=3)
        self._thread = None
