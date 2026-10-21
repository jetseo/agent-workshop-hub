# Read-only integration diagnostics and opt-in OpenCode adapter

From the repository root:

```bash
python3 -m workshop.doctor
python3 -m unittest discover -s tests -v
```

The doctor command checks for CLI executables and probes Ollama on loopback. It does not install, execute or modify them.

`workshop.adapters.run_opencode(prompt, workspace, enabled=True)` is a low-level **opt-in** integration point. It runs `opencode run` only in an existing Git repository or worktree. This function is **not wired to the task queue** yet. OpenCode may modify files and consume paid model credits based on its own configuration. Never enable it for untrusted prompts without sandboxing, worktree isolation, budget limits and approval.

## Known limitations

- Existing repository directories are accepted by the adapter; dedicated worktree enforcement is a future requirement.
- No background worker, API server, UI, model routing, or unattended installer.
- Loopback service checks assume Ollama can be reached from the process host; Windows/WSL2 networking may require configuration.
- CLI arguments and behavior must be verified against installed OpenCode version.
