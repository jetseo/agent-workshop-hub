"""Model router: pick the cheapest adequate model for a task. Pure decision logic, never calls a model.

Order of preference is always local/free first. A paid model is never chosen automatically:
when only paid models fit, the result carries needs_approval=True and the caller must ask.

Run: python -m workshop.router "fix the failing test" --kind code --role coder --model ollama/qwen2.5-coder:7b
"""
import argparse
import json
import re
import sys
import time

from .modelpick import capability
from .modelpolicy import classify

ROLES = ("planner", "router", "coder", "reviewer", "tester", "reporter")
LEVELS = ("easy", "medium", "hard")
# Roles that need at least this much capability regardless of prompt size.
ROLE_FLOOR = {"planner": "medium", "reviewer": "medium", "coder": "easy", "tester": "easy",
              "reporter": "easy", "router": "easy"}
HARD_WORDS = ("architecture", "refactor", "migrate", "concurrency", "race condition", "security",
              "아키텍처", "리팩터", "마이그레이션", "동시성", "보안")
MIN_RUNS_FOR_STATS = 3
MAX_FAILURE_RATE = 0.5


def estimate_difficulty(prompt, *, kind="text", role="coder", files=0):
    """Rough, explainable difficulty: prompt size, files touched, risky keywords, then the role floor."""
    text = (prompt or "").lower()
    score = 0
    score += 0 if len(text) < 600 else 1 if len(text) < 3000 else 2
    score += 0 if files <= 1 else 1 if files <= 5 else 2
    score += 1 if any(word in text for word in HARD_WORDS) else 0
    score += 1 if kind == "code" and files == 0 and len(text) > 1500 else 0
    level = "easy" if score <= 1 else "medium" if score <= 3 else "hard"
    floor = ROLE_FLOOR.get(role, "easy")
    return max(level, floor, key=LEVELS.index)


def _size_b(model):
    """Parameter count in billions parsed from the model name (7b, 32b ...); 0 when unknown."""
    match = re.search(r"(\d+(?:\.\d+)?)b\b", model.lower())
    return float(match.group(1)) if match else 0.0


def _rank(model, *, kind, difficulty, runs=0):
    """Lower sorts first. Easy work takes the smallest adequate model; hard work the largest."""
    size = _size_b(model)
    coder_bonus = 0 if (kind == "code" and "coder" in model.lower()) else 1
    size_key = size if difficulty == "easy" else -size
    return (coder_bonus, size_key, runs, model)      # equal models: the less used one first, spreading free limits


COOLDOWN_AFTER_ONE_FAILURE = 60     # seconds: a rate-limited free model is left alone briefly
COOLDOWN_AFTER_REPEATED_FAILURES = 600


def _healthy(model, stats, now=None):
    entry = (stats or {}).get(model)
    if not entry:
        return True
    streak, last = entry.get("streak_fail", 0), entry.get("last_ts", 0)
    if streak and last:
        wait = COOLDOWN_AFTER_ONE_FAILURE if streak == 1 else COOLDOWN_AFTER_REPEATED_FAILURES
        if (time.time() if now is None else now) - last < wait:
            return False                    # cooling down after a failure (e.g. 429): try the others first
    if entry.get("runs", 0) < MIN_RUNS_FOR_STATS:
        return True
    return entry.get("failures", 0) / entry["runs"] <= MAX_FAILURE_RATE


def route(prompt, models, *, kind="text", role="coder", files=0, stats=None, allow_paid=False, tier_fn=None, now=None):
    """Return {model, tier, difficulty, needs_approval, reason}.

    models: candidate model ids. stats: optional {model: {"runs": n, "failures": m}} so a model that
    keeps failing is skipped. allow_paid only means paid models may be *suggested*; they still come
    back with needs_approval=True. tier_fn maps a model id to "local"/"free"/"paid" (default: by name,
    see modelpolicy.classify); the orchestrator passes one that also knows registered providers.
    """
    classify_model = tier_fn or classify
    if role not in ROLES:
        raise ValueError("Unknown role: " + str(role))
    if kind not in ("text", "code"):
        raise ValueError("Unknown kind: " + str(kind))
    difficulty = estimate_difficulty(prompt, kind=kind, role=role, files=files)
    unique = sorted({m for m in models if isinstance(m, str) and "/" in m})
    healthy = [m for m in unique if _healthy(m, stats, now)]
    used = lambda m: (stats or {}).get(m, {}).get("runs", 0)
    skipped = [m for m in unique if m not in healthy]
    for tier in ("local", "free"):
        pool = [m for m in healthy if classify_model(m) == tier]
        if pool:
            pick = min(pool, key=lambda m: _rank(m, kind=kind, difficulty=difficulty, runs=used(m)))
            reason = difficulty + " task -> cheapest adequate " + tier + " model"
            if skipped:
                reason += " (skipped, failing or cooling down: " + ", ".join(skipped) + ")"
            return {"model": pick, "tier": tier, "difficulty": difficulty,
                    "needs_approval": False, "reason": reason}
    paid = [m for m in healthy if classify_model(m) == "paid"]
    if paid and allow_paid:
        pick = min(paid, key=lambda m: _rank(m, kind=kind, difficulty="easy", runs=used(m)))
        return {"model": pick, "tier": "paid", "difficulty": difficulty, "needs_approval": True,
                "reason": "no local/free model available; paid model suggested, explicit approval required"}
    return {"model": None, "tier": None, "difficulty": difficulty, "needs_approval": bool(paid),
            "reason": "no usable local/free model" + ("; paid models exist but were not allowed" if paid else "")}


def _fit(model, task):
    """Lower is better. Missing capability costs three times more than spare capability; a coder model is a
    bonus for code and a handicap for everyday chat; a reasoning model is a bonus for research."""
    cap = capability(model)
    need = task["level"]
    gap = cap["level"] - need
    cost = (-gap * 3.0) if gap < 0 else gap * 1.0
    if task["type"] == "code":
        cost -= 1.5 if cap["coder"] else 0.0
    elif task["type"] == "research":
        cost -= 1.0 if cap["reasoning"] else 0.0
        cost += 0.5 if cap["coder"] else 0.0
    else:
        cost += 0.5 if cap["coder"] else 0.0
    return cost


def route_task(task, models, *, stats=None, tier_fn=None, now=None):
    """Pick the model whose strength best matches what the arbiter decided (`task`: type + level).

    Unlike route(), free and local models compete in one pool, so a hard research question goes to a strong free
    cloud model instead of a tiny local one; when two fit equally, local wins (private, no limits), then the
    less used one. Paid models are never chosen. Returns {model, tier, type, level, reason}.
    """
    classify_model = tier_fn or classify
    unique = sorted({m for m in models if isinstance(m, str) and "/" in m})
    healthy = [m for m in unique if _healthy(m, stats, now)]
    skipped = [m for m in unique if m not in healthy]
    pool = [m for m in healthy if classify_model(m) in ("local", "free")]
    if not pool:
        return {"model": None, "tier": None, "type": task["type"], "level": task["level"],
                "reason": "no usable local/free model" + (" (cooling down: " + ", ".join(skipped) + ")" if skipped else "")}
    used = lambda m: (stats or {}).get(m, {}).get("runs", 0)
    pick = min(pool, key=lambda m: (_fit(m, task), 0 if classify_model(m) == "local" else 1, used(m), m))
    cap = capability(pick)
    reason = "need level %d, model level %d%s" % (task["level"], cap["level"], ", coder" if cap["coder"] else "")
    if cap["level"] < task["level"]:
        reason += " (strongest available; stronger free models are cooling down or not connected)"
    if skipped:
        reason += " (skipped, failing or cooling down: " + ", ".join(skipped) + ")"
    return {"model": pick, "tier": classify_model(pick), "type": task["type"], "level": task["level"], "reason": reason}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Pick a model for a task (decision only, no model call)")
    parser.add_argument("prompt")
    parser.add_argument("--kind", default="text", choices=("text", "code"))
    parser.add_argument("--role", default="coder", choices=ROLES)
    parser.add_argument("--files", type=int, default=0)
    parser.add_argument("--model", action="append", default=[], help="Candidate model id (repeatable)")
    parser.add_argument("--allow-paid", action="store_true", help="Allow suggesting a paid model (still needs approval)")
    args = parser.parse_args(argv)
    models = args.model
    if not models:
        from .adapters import ollama_models
        try:
            models = ["ollama/" + name for name in ollama_models()]
        except (OSError, ValueError, TimeoutError) as error:
            print("No --model given and local Ollama is unreachable: " + str(error), file=sys.stderr)
            return 1
    decision = route(args.prompt, models, kind=args.kind, role=args.role, files=args.files,
                     allow_paid=args.allow_paid)
    print(json.dumps(decision, ensure_ascii=False, indent=2))
    return 0 if decision["model"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
