# Task queue prototype

Run tests:

```bash
python3 -m unittest discover -s tests -v
```

This module manages durable task states only. It does not execute agents, install models, run image generation, or invoke paid APIs.

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
Not yet wired into the board/chat flows; failure stats must be supplied by the caller.
