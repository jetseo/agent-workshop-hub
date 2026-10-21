# Task queue prototype

Run tests:

```bash
python3 -m unittest discover -s tests -v
```

This module manages durable task states only. It does not execute agents, install models, run image generation, or invoke paid APIs.

The queue defaults to manual review before completion. Interrupted running tasks are marked failed rather than automatically replayed. Add heartbeat leases, task dependencies, audit logs, and isolated executors before production use.

## Notes

- `max_attempts` is retained for schema compatibility but is intentionally unused: failed or
  interrupted tasks are never retried automatically because they may have side effects.
  Review the result, then add the task again.
- The unmerged `feat/durable-task-queue` branch is left untouched; this branch already
  provides the durable SQLite queue, approvals, and heartbeat-based recovery.
