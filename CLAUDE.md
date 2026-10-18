# Claude Code handoff: Agent Workshop Hub

## Project objective
Build a reliable, local-first AI agent orchestration platform for a Windows + WSL2 environment. Connect OpenCode, local Ollama models, optional cloud providers, and ComfyUI. The user has explicitly postponed service naming, logo, and branding. Focus exclusively on technical reliability and verification.

## Current repository state
- Existing prototype: Python local-only dashboard (`workshop/dashboard.py`), SQLite queue (`workshop/queue.py`), opt-in worker (`workshop/worker.py`), OpenCode/ComfyUI adapters, PowerShell + WSL launcher, installer scripts, GitHub Actions tests.
- ComfyUI prompt IDs are now validated immediately after submission, before polling/downloading. Tests added in `tests/test_worker.py`.
- Added `workshop/diagnostics.py` for non-destructive environment checks, local Ollama/ComfyUI connectivity and an isolated temporary SQLite approval/claim/review/done self-test.
- Added `tests/test_diagnostics.py` to cover diagnostics and offline optional services.
- These changes were committed to the default branch via GitHub; do not assume CI or Windows hardware validation has passed.

## First tasks for Claude Code
1. Fetch the latest default branch and inspect `git status`; never discard uncommitted local work.
2. Run `python -m unittest discover -s tests -v`, `python -m workshop.diagnostics --no-services`, and platform-appropriate lint/syntax checks. Fix any failures and add regression tests.
3. Integrate `python -m workshop.diagnostics --json --no-services` into `.github/workflows/validate.yml` on both Linux and Windows runners. A previous remote edit attempt was blocked, so this step is NOT complete.
4. Verify the Windows launcher, WSL distro selection, path conversion, dependency detection and error messages without destructive installation.
5. When actual hardware access is available, verify local Ollama and ComfyUI health and execute one deliberately approved end-to-end test task in a disposable worktree. Record actual outcomes; do not label mock tests as hardware validation.
6. Review worker interruption, stale-job recovery, concurrency and side-effect safeguards. Preserve explicit opt-in for any AI execution.
7. Improve Korean dashboard labels and actionable diagnostic error messages only after reliability and tests are green.

## Essential safety and design constraints
- No automatic paid API calls, credentials overwrite, model downloads, destructive installation, or automatic merges.
- Never expose the dashboard publicly; retain loopback binding and request validation.
- Never auto-retry side-effecting tasks; use explicit review and approval.
- Use temporary worktrees and temporary databases for tests.
- Keep secrets outside version control.
- Distinguish code committed, tests passed in CI, and tested on user's Windows PC.
- Keep project name Agent Workshop Hub as a temporary internal name. Do not rename until the user decides.

## Quick commands
```bash
python -m unittest discover -s tests -v
python -m workshop.diagnostics --no-services
python -m workshop.diagnostics --json
python -m workshop.diagnostics --json --strict
bash -n scripts/start.sh scripts/bootstrap.sh
```

Windows PowerShell launcher dry-run:
```powershell
.\start.ps1 -DryRun
```

## Definition of done for next milestone
- Linux/Windows CI passes unit tests and offline diagnostics.
- Windows/WSL prerequisite failures produce clear, actionable messages.
- Optional integrations offline do not fail mandatory checks.
- Real hardware E2E is documented as passed only after observed execution.
- Commit tested code with a concise changelog and report remaining limitations.
