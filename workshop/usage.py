"""Token usage ledger: how much work went to free/local models and how much to anything else.

One JSON line per request in ~/.agent-workshop/usage.jsonl (0600). No prompt or reply text is stored.
Counts come from the service's own `usage` when it reports one; otherwise they are estimated from text length
and flagged `estimated`. Nothing here converts tokens to money (prices are unknown and change).
"""
import html
import json
import math
import os
import threading
import time
from pathlib import Path

TIERS = ("local", "free", "other")
TIER_LABELS = {"local": "내 PC(로컬)", "free": "무료 모델", "other": "그 밖(유료일 수 있음)"}
MAX_READ_BYTES = 20 * 1024 * 1024


def default_path():
    return Path.home() / ".agent-workshop" / "usage.jsonl"


def estimate_tokens(text):
    """Rough count when the service reports none: ~4 characters per token for ASCII, ~1.5 for Korean/CJK."""
    if not text:
        return 0
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    return max(1, math.ceil(ascii_chars / 4 + (len(text) - ascii_chars) / 1.5))


def tier_of(data_policy, model):
    if data_policy == "local":
        return "local"
    return "free" if str(model).endswith(("-free", ":free")) else "other"


class UsageLedger:
    def __init__(self, path=None):
        self.path = Path(path) if path else default_path()
        self._lock = threading.Lock()

    def record(self, *, provider, model, tier, prompt_tokens, completion_tokens, estimated, source="chat", when=None):
        if tier not in TIERS:
            raise ValueError("Unknown tier")
        line = json.dumps({"ts": when if when is not None else time.time(), "provider": provider, "model": model,
                           "tier": tier, "prompt": max(0, int(prompt_tokens)), "completion": max(0, int(completion_tokens)),
                           "estimated": bool(estimated), "source": source}, ensure_ascii=False)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def _records(self):
        if not self.path.exists():
            return []
        with open(self.path, "rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            if size > MAX_READ_BYTES:
                handle.seek(size - MAX_READ_BYTES)
                handle.readline()                      # drop the cut first line
            data = handle.read().decode("utf-8", "replace")
        out = []
        for raw in data.splitlines():
            try:
                record = json.loads(raw)
                if record.get("tier") in TIERS and isinstance(record.get("ts"), (int, float)):
                    out.append(record)
            except ValueError:
                continue
        return out

    def summary(self, days=30, now=None):
        now = time.time() if now is None else now
        local_now = time.localtime(now)
        midnight = time.mktime((local_now.tm_year, local_now.tm_mon, local_now.tm_mday, 0, 0, 0, 0, 0, -1))
        cutoff = midnight - (days - 1) * 86400
        blank = lambda: {"requests": 0, "tokens": 0}
        totals = {t: blank() for t in TIERS}
        today = {t: blank() for t in TIERS}
        by_model, by_day, by_source = {}, {}, {}
        estimated = every = 0
        for r in self._records():
            if r["ts"] < cutoff:
                continue
            tokens = int(r.get("prompt", 0)) + int(r.get("completion", 0))
            tier = r["tier"]
            totals[tier]["requests"] += 1; totals[tier]["tokens"] += tokens
            if r["ts"] >= midnight:
                today[tier]["requests"] += 1; today[tier]["tokens"] += tokens
            key = (str(r.get("provider", "")) + "/" + str(r.get("model", "")), tier)
            entry = by_model.setdefault(key, blank()); entry["requests"] += 1; entry["tokens"] += tokens
            day = time.strftime("%m-%d", time.localtime(r["ts"]))
            by_day.setdefault(day, {t: 0 for t in TIERS})[tier] += tokens
            src = by_source.setdefault(str(r.get("source", "chat")), blank()); src["requests"] += 1; src["tokens"] += tokens
            every += tokens
            if r.get("estimated"):
                estimated += tokens
        models = sorted(({"model": k[0], "tier": k[1], **v} for k, v in by_model.items()), key=lambda m: -m["tokens"])[:10]
        saved = totals["local"]["tokens"] + totals["free"]["tokens"]
        return {"days": days, "totals": totals, "today": today, "by_model": models, "by_source": by_source,
                "by_day": [{"day": d, **by_day[d]} for d in sorted(by_day)][-14:],
                "all_tokens": every, "free_or_local_tokens": saved,
                "free_share": round(saved / every, 3) if every else 0.0,
                "estimated_share": round(estimated / every, 3) if every else 0.0}


def _n(value):
    return "{:,}".format(int(value))


def render_page(summary):
    """Self-contained dark page for the app window's Usage view (all values escaped)."""
    totals, esc = summary["totals"], html.escape
    peak = max([1] + [d["local"] + d["free"] + d["other"] for d in summary["by_day"]])
    colors = {"local": "#19c3a4", "free": "#5aa9ff", "other": "#f59e0b"}
    cards = "".join('<div class="card"><div class="k">%s</div><div class="v" style="color:%s">%s</div><div class="s">요청 %s회</div></div>'
                    % (esc(TIER_LABELS[t]), colors[t], _n(totals[t]["tokens"]), _n(totals[t]["requests"])) for t in TIERS)
    rows = "".join("<tr><td>%s</td><td><span class='dot' style='background:%s'></span>%s</td><td>%s</td><td>%s</td></tr>"
                   % (esc(m["model"]), colors[m["tier"]], esc(TIER_LABELS[m["tier"]]), _n(m["requests"]), _n(m["tokens"]))
                   for m in summary["by_model"]) or "<tr><td colspan='4'>아직 기록이 없습니다.</td></tr>"
    days = "".join("<div class='day'><span class='d'>%s</span><div class='bar'>%s</div><span class='t'>%s</span></div>" % (
        esc(d["day"]), "".join("<i style='width:%.1f%%;background:%s' title='%s'></i>" % (100.0 * d[t] / peak, colors[t], esc(TIER_LABELS[t]))
                               for t in TIERS if d[t]), _n(d["local"] + d["free"] + d["other"])) for d in summary["by_day"]) \
        or "<p>아직 기록이 없습니다.</p>"
    share = "%.0f%%" % (summary["free_share"] * 100)
    labels = {"chat": "채팅", "board": "보드(멀티 모델 작업)", "digest": "긴 글 압축"}
    sources = " · ".join(esc(labels.get(k, k)) + " " + _n(v["tokens"]) for k, v in sorted(summary.get("by_source", {}).items())) or "기록 없음"
    note = ("일부 수치는 서비스가 사용량을 알려주지 않아 글자 수로 추정했습니다 (%.0f%%)." % (summary["estimated_share"] * 100)
            if summary["estimated_share"] else "모든 수치는 서비스가 보고한 사용량입니다.")
    return ("<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>사용량</title><style>:root{--bg:#111827;--fg:#e5e7eb;--card:#1f2937;--line:#374151;--muted:#9ca3af}"
            ":root[data-theme='light']{--bg:#fff;--fg:#1f2328;--card:#f7f7f8;--line:#e5e7eb;--muted:#6b7280}"
            "body{font-family:system-ui,'Malgun Gothic',sans-serif;background:var(--bg);color:var(--fg);max-width:900px;margin:28px auto;padding:0 16px}"
            "h1{margin:0 0 4px}p.sub{color:var(--muted);margin:0 0 18px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}"
            ".card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px}.k{color:var(--muted);font-size:13px}.v{font-size:28px;font-weight:700}.s{color:var(--muted);font-size:13px}"
            "h2{font-size:16px;margin:26px 0 8px}table{width:100%;border-collapse:collapse}td,th{padding:7px 8px;border-bottom:1px solid var(--line);text-align:left;font-size:14px}"
            ".dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px}.day{display:flex;align-items:center;gap:10px;margin:5px 0}"
            ".d{width:48px;color:var(--muted);font-size:13px}.bar{flex:1;display:flex;height:14px;background:var(--card);border-radius:7px;overflow:hidden}.bar i{display:block;height:100%}"
            ".t{width:90px;text-align:right;font-size:13px;color:var(--muted)}small{color:var(--muted)}</style></head><body>"
            "<h1>사용량</h1><p class='sub'>최근 " + str(int(summary["days"])) + "일 · 무료·로컬 모델이 처리한 비중 <b>" + share + "</b></p>"
            "<div class='cards'>" + cards + "</div>"
            + "<p class='sub'>출처별 토큰: " + sources + "</p>"
            + "<h2>하루별 토큰</h2>" + days
            + "<h2>모델별 (상위 10)</h2><table><tr><th>모델</th><th>구분</th><th>요청</th><th>토큰</th></tr>" + rows + "</table>"
            + "<p><small>" + esc(note) + " 금액으로 환산하지 않습니다(서비스별 단가가 다르고 자주 바뀜). "
              "'그 밖' 구분은 이름이 -free/:free로 끝나지 않는 클라우드 모델이며 요금이 부과될 수 있습니다. 대화 내용은 이 기록에 저장되지 않습니다.</small></p></body></html>")
