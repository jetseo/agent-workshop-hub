# Long-running worker (opt-in)

Start a continuously polling worker only after reviewing your OpenCode provider, spending policy, ComfyUI workflows, and workspace permissions.

```bash
python3 -m workshop.worker --watch --allow-opencode --allow-comfyui --interval 3
```

This polls the SQLite queue every three seconds and processes one task at a time. Press Ctrl+C to stop. Only code and image tasks have executors. Image tasks wait up to 300 seconds for ComfyUI history completion.

**Security and reliability caveats:**
- Enabling this mode authorizes future queued code and image jobs to run. Anyone with access to the queue database or dashboard task creation can influence execution. Keep it private and use dedicated worktrees.
- There is no spending cap, container sandbox, GPU resource manager, or per-job approval before execution. This is an experimental worker, not a secure unattended production service.
- The queue can contain plan/qa tasks that are not executable. They are marked failed if claimed by the worker.
- A crash while executing a job leaves it running; on restart, the worker marks it failed rather than replaying it.
- The worker and dashboard are separate processes and both must be started manually.
