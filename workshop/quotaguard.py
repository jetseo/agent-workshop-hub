"""Free-quota guard: a fail-closed gate in front of services whose free allowance cannot be read from an API.

A guarded service (provider entry field `guard`) is used only while ALL of these hold; otherwise every call is
refused before it leaves the PC, and the model drops out of the free pool so the router falls back to other free or
local models:

  1. the user switched it on,
  2. the user confirmed that the account has NO payment method (the only way a provider without a spending cap can be
     kept from charging; JetAI cannot read this from the provider, so it is an explicit, dated confirmation),
  3. a remaining free balance is known and fresh (read from the provider's own dashboard and typed in by the user,
     with the time it was read; JetAI subtracts what it has used since),
  4. the credits have not passed the user-entered expiry date,
  5. the request (estimated tokens plus a safety margin) fits the remaining balance and the daily/monthly soft limits,
  6. fewer than `max_concurrent` requests are in flight (reservations, so parallel agents cannot overshoot).

A billing/credit/payment error from the provider switches the service off until the user turns it back on. Nothing
here ever picks a paid fallback. Internal counters are NOT the provider's real balance: they only make the manual
figure safer, and they expire (`balance_max_age_hours`) because other tools may use the same key.
"""
import json
import os
import re
import threading
import time

GUARDS = {
    "inception": {"label": "Mercury 3 (Inception)", "hosts": ["api.inceptionlabs.ai"], "model_hint": "mercury-3"},
}
DEFAULTS = {"safety_margin_percent": 10, "max_request_output_tokens": 4096, "daily_soft_limit_tokens": 1_000_000,
            "monthly_soft_limit_tokens": 10_000_000, "max_concurrent_requests": 2, "timeout_seconds": 60, "balance_max_age_hours": 24}
LIMITS = {"safety_margin_percent": (0, 90), "max_request_output_tokens": (16, 65536), "daily_soft_limit_tokens": (1000, 10**10),
          "monthly_soft_limit_tokens": (1000, 10**11), "max_concurrent_requests": (1, 8), "timeout_seconds": (5, 300), "balance_max_age_hours": (1, 168)}
BILLING_WORDS = re.compile(r"HTTP 402|insufficient|credit|payment|billing|quota exceeded|exceeded your|out of tokens|balance", re.I)
AUTH_WORDS = re.compile(r"HTTP 401|token rejected|HTTP 403|access denied", re.I)


class QuotaBlocked(RuntimeError):
    """Raised before any network call. The message says why, in Korean, for the screen."""


def _day(now):
    return time.strftime("%Y-%m-%d", time.localtime(now))


def _month(now):
    return time.strftime("%Y-%m", time.localtime(now))


def _fresh_state():
    return {"enabled": False, "attested_no_payment_at": 0, "balance_tokens": None, "balance_checked_at": 0, "used_since_check": 0,
            "valid_until": 0, "day": "", "day_tokens": 0, "month": "", "month_tokens": 0, "total_tokens": 0, "blocked_reason": "",
            "settings": dict(DEFAULTS), "last_error": ""}


class FreeQuotaGuard:
    def __init__(self, path=None, *, clock=time.time):
        self.path = path
        self.clock = clock
        self._lock = threading.RLock()
        self._data = {}
        self._reserved = {}          # guard id -> [count, tokens], in memory only (an in-flight request dies with the process)
        self._session = {}
        self._load()

    # ---- persistence ---------------------------------------------------------------------------------------
    def _load(self):
        if not self.path:
            return
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            for gid, state in data.items():
                if gid in GUARDS and isinstance(state, dict):
                    merged = _fresh_state()
                    merged.update({k: v for k, v in state.items() if k in merged and k != "settings"})
                    merged["settings"] = {**DEFAULTS, **{k: v for k, v in (state.get("settings") or {}).items() if k in DEFAULTS and isinstance(v, int)}}
                    self._data[gid] = merged

    def _save(self):
        if not self.path:
            return
        tmp = str(self.path) + ".tmp"
        os.makedirs(os.path.dirname(str(self.path)) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(self._data, handle, ensure_ascii=False, indent=1)
        for attempt in range(5):
            try:
                os.replace(tmp, self.path)
                return
            except PermissionError:                          # Windows: another thread/process holds the file for a moment
                if attempt == 4:
                    raise
                time.sleep(0.05)

    def _state(self, gid):
        if gid not in GUARDS:
            raise ValueError("알 수 없는 보호 대상입니다.")
        return self._data.setdefault(gid, _fresh_state())

    # ---- decisions -----------------------------------------------------------------------------------------
    def _why_closed(self, gid, now):
        """None when the service may be used, else the reason (Korean)."""
        state = self._state(gid)
        settings = state["settings"]
        if not state["enabled"]:
            return state["blocked_reason"] or "꺼져 있음(기본값). 사용하려면 설정에서 직접 켜야 합니다."
        if not state["attested_no_payment_at"]:
            return "결제수단이 등록되지 않았음을 확인하지 않아 차단"
        if state["balance_tokens"] is None or not state["balance_checked_at"]:
            return "확인 불가 · 호출 차단: 공급자 대시보드에서 확인한 무료 잔액이 입력되지 않음"
        if now - state["balance_checked_at"] > settings["balance_max_age_hours"] * 3600:
            return "확인 불가 · 호출 차단: 무료 잔액 확인 후 %d시간이 지남(다시 확인해 입력)" % settings["balance_max_age_hours"]
        if state["valid_until"] and now > state["valid_until"]:
            return "무료 크레딧 사용 기한이 지나 차단"
        if state["balance_tokens"] - state["used_since_check"] <= 0:
            return "무료 잔액 소진으로 차단"
        return None

    def is_open(self, gid, now=None):
        with self._lock:
            try:
                return self._why_closed(gid, now if now is not None else self.clock()) is None
            except ValueError:
                return False

    def begin(self, gid, est_prompt_tokens, max_output_tokens):
        """Reserve room for one request or raise QuotaBlocked. Call `settle` or `fail` with the ticket afterwards."""
        now = self.clock()
        with self._lock:
            reason = self._why_closed(gid, now)
            if reason:
                raise QuotaBlocked(reason)
            state = self._state(gid)
            settings = state["settings"]
            reserved = self._reserved.setdefault(gid, [0, 0])
            if reserved[0] >= settings["max_concurrent_requests"]:
                raise QuotaBlocked("동시 요청 한도(%d개)에 도달" % settings["max_concurrent_requests"])
            out = min(int(max_output_tokens or settings["max_request_output_tokens"]), settings["max_request_output_tokens"])
            need = int((max(0, int(est_prompt_tokens)) + out) * (1 + settings["safety_margin_percent"] / 100.0)) + 1
            if need > state["balance_tokens"] - state["used_since_check"] - reserved[1]:
                raise QuotaBlocked("남은 무료 잔액으로 이 요청(예상 %d토큰, 여유분 포함)을 보장할 수 없어 차단" % need)
            self._roll(state, now)
            if state["day_tokens"] + reserved[1] + need > settings["daily_soft_limit_tokens"]:
                raise QuotaBlocked("일일 내부 한도(%d토큰)에 도달" % settings["daily_soft_limit_tokens"])
            if state["month_tokens"] + reserved[1] + need > settings["monthly_soft_limit_tokens"]:
                raise QuotaBlocked("월간 내부 한도(%d토큰)에 도달" % settings["monthly_soft_limit_tokens"])
            reserved[0] += 1
            reserved[1] += need
            return {"gid": gid, "need": need, "done": False}

    def settle(self, ticket, prompt_tokens=None, completion_tokens=None):
        """Record what was really used. If the provider reported nothing, the reserved estimate is recorded (never less)."""
        if not ticket or ticket["done"]:
            return
        with self._lock:
            ticket["done"] = True
            reserved = self._reserved.setdefault(ticket["gid"], [0, 0])
            reserved[0] = max(0, reserved[0] - 1)
            reserved[1] = max(0, reserved[1] - ticket["need"])
            if isinstance(prompt_tokens, int) and isinstance(completion_tokens, int) and prompt_tokens >= 0 and completion_tokens >= 0:
                used = prompt_tokens + completion_tokens
            else:
                used = ticket["need"]
            state = self._state(ticket["gid"])
            now = self.clock()
            self._roll(state, now)
            state["used_since_check"] += used
            state["day_tokens"] += used
            state["month_tokens"] += used
            state["total_tokens"] += used
            self._session[ticket["gid"]] = self._session.get(ticket["gid"], 0) + used
            self._save()

    def fail(self, ticket, error):
        """The request failed: give the room back; a billing/credit/auth error also switches the service off."""
        if not ticket or ticket["done"]:
            return
        with self._lock:
            ticket["done"] = True
            reserved = self._reserved.setdefault(ticket["gid"], [0, 0])
            reserved[0] = max(0, reserved[0] - 1)
            reserved[1] = max(0, reserved[1] - ticket["need"])
            text = str(error)[:200]
            state = self._state(ticket["gid"])
            state["last_error"] = text
            if BILLING_WORDS.search(text):
                self._disable(state, "공급자가 크레딧·결제 문제를 알려 자동으로 껐습니다(무료 한도 소진 또는 프로모션 종료일 수 있음)")
            elif AUTH_WORDS.search(text):
                self._disable(state, "공급자가 키를 거절해 자동으로 껐습니다")
            self._save()

    def _disable(self, state, reason):
        state["enabled"] = False
        state["blocked_reason"] = reason

    def _roll(self, state, now):
        if state["day"] != _day(now):
            state["day"], state["day_tokens"] = _day(now), 0
        if state["month"] != _month(now):
            state["month"], state["month_tokens"] = _month(now), 0

    # ---- user actions --------------------------------------------------------------------------------------
    def attest_no_payment(self, gid, confirmed):
        with self._lock:
            state = self._state(gid)
            state["attested_no_payment_at"] = self.clock() if confirmed else 0
            if not confirmed:
                self._disable(state, "결제수단 미등록 확인이 취소되어 껐습니다")
            self._save()

    def set_balance(self, gid, tokens, valid_until=0):
        if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens < 0 or tokens > 10**13:
            raise ValueError("잔액은 0 이상의 정수(토큰 수)로 입력하세요.")
        with self._lock:
            state = self._state(gid)
            state["balance_tokens"], state["balance_checked_at"], state["used_since_check"] = tokens, self.clock(), 0
            state["valid_until"] = int(valid_until or 0)
            self._save()

    def set_enabled(self, gid, enabled):
        with self._lock:
            state = self._state(gid)
            if not enabled:
                state["enabled"] = False
                state["blocked_reason"] = "사용자가 껐습니다"
                self._save()
                return
            state["enabled"], state["blocked_reason"] = True, ""
            reason = self._why_closed(gid, self.clock())
            if reason:
                state["enabled"], state["blocked_reason"] = False, ""
                raise ValueError("켤 수 없습니다: " + reason.replace("확인 불가 · 호출 차단: ", ""))
            self._save()

    def set_limits(self, gid, **values):
        with self._lock:
            state = self._state(gid)
            for key, value in values.items():
                low, high = LIMITS.get(key, (None, None))
                if low is None or not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
                    raise ValueError("설정값이 올바르지 않습니다: " + str(key))
                state["settings"][key] = value
            self._save()

    # ---- read side -----------------------------------------------------------------------------------------
    def timeout_for(self, gid, requested):
        with self._lock:
            return min(requested, self._state(gid)["settings"]["timeout_seconds"])

    def status(self, gid):
        with self._lock:
            now = self.clock()
            state = self._state(gid)
            self._roll(state, now)
            reason = self._why_closed(gid, now)
            known = state["balance_tokens"] is not None and state["balance_checked_at"] and \
                now - state["balance_checked_at"] <= state["settings"]["balance_max_age_hours"] * 3600
            remaining = state["balance_tokens"] - state["used_since_check"] if known else None
            daily = state["settings"]["daily_soft_limit_tokens"]
            return {"id": gid, "label": GUARDS[gid]["label"], "open": reason is None, "reason": reason or "", "enabled": state["enabled"],
                    "attested_no_payment_at": state["attested_no_payment_at"], "balance_known": bool(known), "remaining_tokens": remaining,
                    "balance_checked_at": state["balance_checked_at"], "valid_until": state["valid_until"],
                    "total_tokens": state["total_tokens"], "session_tokens": self._session.get(gid, 0),
                    "day_tokens": state["day_tokens"], "month_tokens": state["month_tokens"], "settings": dict(state["settings"]),
                    "may_run_out_today": bool(known and state["day_tokens"] > daily * 0.8), "last_error": state["last_error"],
                    "balance_label": ("%d 토큰 남음(직접 입력한 값에서 사용량을 뺀 추정)" % remaining) if known else "확인 불가 · 호출 차단"}


# ---- the process-wide guard --------------------------------------------------------------------------------
_current = FreeQuotaGuard(None)        # not installed: in memory, switched off, so everything guarded stays blocked


def install(guard):
    global _current
    _current = guard
    return guard


def get():
    return _current


def guard_id(provider):
    gid = (provider or {}).get("guard")
    return gid if gid in GUARDS else None


def estimate_tokens(messages):
    """Deliberately high (about one token per two characters): an over-estimate only makes the guard stricter."""
    chars = 0
    for message in messages or []:
        content = message.get("content") if isinstance(message, dict) else ""
        chars += len(content) if isinstance(content, str) else len(json.dumps(content, ensure_ascii=False))
    return chars // 2 + 8 * len(messages or [])
