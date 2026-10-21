"""Model router: pick the cheapest adequate model for a task. Pure decision logic, never calls a model.

Order of preference is always local/free first. A paid model is never chosen automatically:
when only paid models fit, the result carries needs_approval=True and the caller must ask.

Run: python -m workshop.router "fix the failing test" --kind code --role coder --model ollama/qwen2.5-coder:7b
"""
import argparse
import json
import re
import sys

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


def _rank(model, *, kind, difficulty):
    """Lower sorts first. Easy work takes the smallest adequate model; hard work the largest."""
    size = _size_b(model)
    coder_bonus = 0 if (kind == "code" and "coder" in model.lower()) else 1
    size_key = size if difficulty == "easy" else -size
    return (coder_bonus, size_key, model)


def _healthy(model, stats):
    entry = (stats or {}).get(model)
    if not entry or entry.get("runs", 0) < MIN_RUNS_FOR_STATS:
        return True
    return entry.get("failures", 0) / entry["runs"] <= MAX_FAILURE_RATE


def route(prompt, models, *, kind="text", role="coder", files=0, stats=None, allow_paid=False):
    """Return {model, tier, difficulty, needs_approval, reason}.

    models: candidate model ids. stats: optional {model: {"runs": n, "failures": m}} so a model that
    keeps failing is skipped. allow_paid only means paid models may be *suggested*; they still come
    back with needs_approval=True.
    """
    if role not in ROLES:
        raise ValueError("Unknown role: " + str(role))
    if kind not in ("text", "code"):
        raise ValueError("Unknown kind: " + str(kind))
    difficulty = estimate_difficulty(prompt, kind=kind, role=role, files=files)
    unique = sorted({m for m in models if isinstance(m, str) and "/" in m})
    healthy = [m for m in unique if _healthy(m, stats)]
    skipped = [m for m in unique if m not in healthy]
    for tier in ("local", "free"):
        pool = [m for m in healthy if classify(m) == tier]
        if pool:
            pick = min(pool, key=lambda m: _rank(m, kind=kind, difficulty=difficulty))
            reason = difficulty + " task -> cheapest adequate " + tier + " model"
            if skipped:
                reason += " (skipped for high failure rate: " + ", ".join(skipped) + ")"
            return {"model": pick, "tier": tier, "difficulty": difficulty,
                    "needs_approval": False, "reason": reason}
    paid = [m for m in healthy if classify(m) == "paid"]
    if paid and allow_paid:
        pick = min(paid, key=lambda m: _rank(m, kind=kind, difficulty="easy"))
        return {"model": pick, "tier": "paid", "difficulty": difficulty, "needs_approval": True,
                "reason": "no local/free model available; paid model suggested, explicit approval required"}
    return {"model": None, "tier": None, "difficulty": difficulty, "needs_approval": bool(paid),
            "reason": "no usable local/free model" + ("; paid models exist but were not allowed" if paid else "")}


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
