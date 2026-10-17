# Experimental opt-in OpenCode worker

This is **not** a background automation service. It executes at most one queued code job per invocation and only when explicitly enabled.

```bash
python3 -m workshop.cli add code "Add unit tests" --workspace /absolute/path/to/isolated/worktree
python3 -m workshop.worker --allow-opencode
python3 -m workshop.cli list
python3 -m workshop.cli approve JOB_ID
```

**Warning:** OpenCode may modify files, run commands, access the network and incur paid model costs depending on its configuration. Use a dedicated disposable Git worktree, restrict agent permissions and verify the provider and budget before execution. Never run against sensitive repositories or your main checkout.

Current limitations: no scheduler, no ComfyUI execution, no dashboard, no per-job model policy, and no sandbox. Failed code tasks must not be automatically replayed. The queue currently has a generic retry state, so operators must manually inspect any queued failed task before another worker run.
