# Task queue prototype

Run tests:

```bash
python3 -m unittest discover -s tests -v
```

This module manages durable task states only. It does not execute agents, install models, or invoke paid APIs.

The queue defaults to manual review before completion. Interrupted running tasks are marked failed rather than automatically replayed. Add heartbeat leases, task dependencies, audit logs, and isolated executors before production use.

## Notes

- Failed or interrupted tasks are never retried automatically because they may have side effects.
  `Queue.retry(id)` is an explicit operator action: it re-queues a failed/cancelled task only while
  `attempts < max_attempts` (default 2); `force=True` overrides after the side effects were reviewed.
- The unmerged `feat/durable-task-queue` branch is left untouched; this branch already
  provides the durable SQLite queue, approvals, and heartbeat-based recovery.

## Dependencies, cancel, checkpoint, concurrency (2026-10-21)
- `add(..., depends_on=[ids])`: only existing jobs may be named (no cycles). A job is claimed only when
  every dependency is `done` (an approved result); `review`, `failed` and `cancelled` dependencies block it.
  `waiting_on(id)` explains what a job is waiting for.
- `cancel(id)`: queued/pending/failed/review jobs become `cancelled` at once. A running job is only flagged
  (`cancel_requested`) and ends as `cancelled` when its worker reports; the executor process is not killed.
- `checkpoint(id, data)`: progress JSON for a running job, kept after a crash or stale recovery for the operator.
- `claim(max_running=N)` / `worker --max-running N`: cap on concurrently running jobs across all workers.
- Old databases are migrated additively (new columns only).

## Model router (`workshop/router.py`)
Decision only, no model call: `route(prompt, models, kind, role, files, stats)` returns model, tier,
difficulty and reason. Local first, then free; paid is never picked automatically (`needs_approval=True`
and only when `allow_paid`). Easy work gets the smallest adequate model, hard work the largest; code prefers
`*coder*` names; models with a failure rate above 50% over at least 3 runs are skipped.
`python -m workshop.router "prompt" --kind code --role coder [--model id ...]`.

### Board wiring and run statistics
A board step may use `"model": "auto"` (optional `"role"`: planner/router/coder/reviewer/tester/reporter).
`python -m workshop.orchestrator spec.json --auto-model ollama/qwen2.5-coder:7b --auto-model ...` routes each
auto step just before it runs, among the given candidates only; a registered provider's tier comes from its data
policy. Paid models are never picked: with no local/free candidate the step fails (`RoutingError`) before any call.
The chosen model is written back to the board. Every executed step's outcome (success/failure, seconds) is appended to
`~/.agent-workshop/runstats.jsonl` (`workshop/runstats.py`, no prompts or outputs); permission refusals are not counted,
and a model with more than 50% failures over >= 3 runs in the last 30 days is skipped next time.
Not yet wired into chat or the queue worker; the cost side is still the usage ledger (`/usage`).

## Result cache (`workshop/cache.py`, opt-in `--cache`)
Identical text-step requests (same model id + same final prompt) are answered from `~/.agent-workshop/cache`
instead of calling a model again. Text replies only (code steps have side effects and are never cached). Consent and
cloud gates run before the cache, so a cached reply never bypasses them. Entries are private files (0600), expire after
7 days, the store is capped at 500 entries, and a cache I/O error counts as a miss. Cached replies contain prompt-derived
content, so the folder stays outside version control. Each hit appends estimated saved tokens to `cache/hits.jsonl`;
the orchestrator prints `{"cache": {"hits", "tokens_saved"}}` in its summary (estimates, like all board token counts).
Not yet shown on the usage page; chat requests are not cached.

## 자동 선택이 기본 (2026-10-21)
사용자는 서비스를 연결하고 대화만 하면 됩니다. 채팅·압축의 기본 모델은 **자동(로컬·무료 우선)** 입니다.
- 후보: 연결된 서비스 중 로컬이거나 `-free`/`:free` 모델, 클라우드는 연결할 때 동의한 서비스만. 요금이 날 수 있는 모델은 후보가 아닙니다.
- 선택: 질문 난이도(길이·위험 키워드)에 따라 쉬우면 가장 작은 모델, 어려우면 큰 모델. 코드 질문은 `coder` 모델 우선. 같은 급이면 최근에 덜 쓴 모델을 골라 무료 한도를 나눠 씁니다.
- 쿨다운: 방금 실패(예: 429 한도)한 모델은 60초, 연속 실패면 10분 동안 건너뛰고, 최근 30일 실패율이 50%를 넘는 모델도 제외합니다.
- 실패하면 기존의 자동 전환(다른 무료·로컬 모델로 이어서 답)이 이어서 동작합니다. 답변 아래에 실제로 답한 모델이 표시됩니다.
- 수동으로 특정 모델을 고르는 것도 계속 가능합니다(모델 선택 메뉴).
