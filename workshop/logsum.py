"""Deterministic log reducer: shrink a long log before any model sees it. Calls no model, spends no tokens.

Steps: strip colour codes -> collapse repeated lines (also lines that differ only in timestamps, numbers,
ids or addresses) into one line with a count -> keep error/warning lines with a little context, plus the
head and tail -> cut to a character budget. Lines that carry the failure are never dropped for size
before routine lines are.

Run: python -m workshop.logsum build.log [--max-chars 6000]
"""
import argparse
import re
import sys

from .usage import estimate_tokens

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
IMPORTANT = re.compile(r"error|exception|traceback|fatal|fail|panic|critical|denied|refused|timeout|"
                       r"timed out|assert|segfault|warn|오류|에러|실패|경고", re.I)
VOLATILE = [(re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<ts>"),
            (re.compile(r"\b\d{1,2}:\d{2}:\d{2}(?:[.,]\d+)?\b"), "<time>"),
            (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<hex>"),
            (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F-]{27}\b"), "<uuid>"),
            (re.compile(r"\d+"), "<n>")]
HEAD_LINES, TAIL_LINES, CONTEXT = 15, 40, 1
MAX_LINE = 400


def _shape(line):
    for pattern, token in VOLATILE:
        line = pattern.sub(token, line)
    return line.strip()


def _collapse(lines):
    """[(text, count)] merging consecutive lines of the same shape; the first occurrence is kept verbatim."""
    out = []
    for line in lines:
        shape = _shape(line)
        if out and out[-1][2] == shape and shape:
            out[-1][1] += 1
        else:
            out.append([line, 1, shape])
    return out


def reduce_log(text, max_chars=6000):
    """Return {"text", "stats"}. stats: lines_before/after, chars_before/after, tokens_before/after (estimates)."""
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    clean = ANSI.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    raw_lines = [line[:MAX_LINE] + (" …" if len(line) > MAX_LINE else "") for line in clean.split("\n")]
    entries = _collapse(raw_lines)
    keep = set(range(min(HEAD_LINES, len(entries))))
    keep.update(range(max(0, len(entries) - TAIL_LINES), len(entries)))
    important = [i for i, (line, _count, _shape_) in enumerate(entries) if IMPORTANT.search(line)]
    for i in important:
        keep.update(range(max(0, i - CONTEXT), min(len(entries), i + CONTEXT + 1)))

    def render(indices):
        out, previous = [], None
        for i in sorted(indices):
            if previous is not None and i != previous + 1:
                out.append("[... %d routine lines omitted ...]" % sum(e[1] for e in entries[previous + 1:i]))
            line, count, _ = entries[i]
            out.append(line + ("  (x%d)" % count if count > 1 else ""))
            previous = i
        if previous is not None and previous + 1 < len(entries):
            out.append("[... %d routine lines omitted ...]" % sum(e[1] for e in entries[previous + 1:]))
        return "\n".join(out)

    body = render(keep)
    if len(body) > max_chars:
        # Over budget: drop routine lines first (head/tail context), keep every important line, then trim important ones.
        core = set(important)
        body = render(core)
        if len(body) > max_chars:
            half = max_chars // 2
            body = body[:half] + "\n[... %d characters omitted ...]\n" % (len(body) - max_chars) + body[-half:]
    stats = {"lines_before": len(raw_lines), "lines_after": body.count("\n") + 1,
             "chars_before": len(text), "chars_after": len(body),
             "tokens_before": estimate_tokens(text), "tokens_after": estimate_tokens(body)}
    return {"text": body, "stats": stats}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Shrink a log without calling any model")
    parser.add_argument("file", help="Log file, or - for stdin")
    parser.add_argument("--max-chars", type=int, default=6000)
    args = parser.parse_args(argv)
    if args.max_chars < 500:
        parser.error("--max-chars must be at least 500")
    raw = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8", errors="replace").read()
    result = reduce_log(raw, args.max_chars)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(result["text"])
    stats = result["stats"]
    print("\n[logsum] %d -> %d lines, ~%d -> ~%d tokens (estimates)" % (
        stats["lines_before"], stats["lines_after"], stats["tokens_before"], stats["tokens_after"]), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
