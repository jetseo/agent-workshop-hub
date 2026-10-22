# Claude Code handoff: JetAI (formerly Agent Workshop Hub)

Read this first, then `docs/coding-agent-platform-plan.md` (status table, designs, prioritized plan, acceptance scenario), `docs/README.md` (index) and `docs/verification.md` (what is verified where).

## Project objective
JetAI (제타아이) is a Windows-first, local-first platform that **saves development tokens** by using **free and local AI first**: chat with automatic model selection, compression, boards of several models, and now **code work** (a coding agent edits a copy of the user's project; the user reviews the diff and decides). Ollama is optional, not required: the built-in engine downloads llama.cpp and a model itself. Target machine: RTX 3060 12GB, RAM 64GB, Windows 11.

## Project scope: two separate projects
- **JetAI (this repository)**: judge new features by whether they serve free/local-first development work, with explicit opt-in, approval and cost guards.
- **Jet Studio is a different project** for **producing Shorts (short-form videos)**. Do not add shorts/video/image-generation features, assets or naming here. The earlier image/ComfyUI features were removed and handed over (`docs/handoff/jet-studio-comfyui-prompt.md`); do not re-add them. If a request is ambiguous about which project it belongs to, ask before changing anything.

## Current state (2026-10-22)
Everything below is on `main` unless marked "PR"; "verified" levels are recorded per item in `docs/verification.md` (mock test / GitHub runner / **observed on the user's PC**). Do not call a mock test hardware validation.
- Chat app (`workshop/app_page.py`, `static/app.js`, `chat_api.py`): onboarding wizard with free services (`setup_page.py`), daily discovery of new services (`servicefeed.py`), model curation (`modelpick.py`, `modelinfo.py`, `modelwatch.py`).
- **Auto mode**: `arbiter.py` (code / everyday / research + level) → `router.py` (local/free models only, cooldowns, load spreading, fallback) → `runstats.py`; usage ledger and savings in `usage.py`.
- Token savers: digest, log pre-reducer, cache, multi-model boards, queue/worker.
- **Built-in engine** (`engine.py`): downloads llama.cpp and a GGUF only after the user confirmed the exact size; observed on the PC with a 0.5B model.
- **Code work** (screen "코드", a Claude Code/Codex-style session: session list | timeline | composer with `/` commands): OpenCode in a throw-away git worktree (`codework.py`). Around it, all on `main`: project reading (`projectmap.py`), blocked-command approval (`cmdguard.py`: once / project / decline, never for dangerous ones), JetAI-run build/test checks on request (`checks.py`), second-opinion review by another free/local model (`codereview.py`), apply / **undo** (reverse patch), per-run `report.md` + `metrics.json` (`codereport.py`), optional work guidance (`guidance.py`, **off by default**: no accuracy gain and 2x time on easy tasks).
- **Evaluation harness** (`agenteval.py`, `python -m workshop.agenteval --model X --guidance both`): first real measurement in `docs/agent-eval.md` (free cloud model 5/5 on 5 easy tasks, one run each; local 7B/12B failed as agents earlier). Harder tasks and the built-in 7B/14B Coder are **not measured**.
- **Mercury 3 + FreeQuotaGuard** (PR #32 until merged; `quotaguard.py`, `docs/providers/mercury3.md`, `docs/architecture/free-quota-guard.md`): fail-closed, off by default, opens only with the user's no-payment confirmation + manually entered balance (<24 h old); the user approved this mode and enters the key themself. Never used by code work (OpenCode bypasses the guard). No real call with a key has been made yet.
- **Phone control** (`remote.py`): Telegram bot, mock-tested only.
- Launchers: `jetai.ps1`, `JetAI.exe`, portable package, WSL scripts, diagnostics.

## Next tasks (see the plans for details and acceptance criteria)
- P0 left: (2) built-in 7B Coder tool-call test (needs the user's OK for a ~4.7 GB download); measure harder tasks with the harness.
- **P1-11 direct mode (Agentless-style: JetAI localizes → model writes a diff as text → JetAI applies and tests)** and **P1-12 best-of-N**: the most promising way to get value from weak/free models, see `docs/multi-agent-plan.md` (M1, M2). Adopt only if the harness shows a gain.
- Other P1: goal-input orchestrator (P1-1), work-branch commit (P1-5, changes the "never commit" default: ask first), retry/escalation (P1-6), diff secret scan (P1-7).
- Ask the user before: merging, paid calls, downloads, real-hardware or live-service runs, the Telegram token.

## Essential safety and design constraints
- No automatic paid API calls, credentials overwrite, model downloads without the user's confirmed size, destructive installation, or automatic merges. Existing subscriptions (Codex, Claude Code, OpenAI login, OpenCode Zen key) must not be used by automation unless verified and explicitly confirmed.
- Never expose the server publicly; keep 127.0.0.1 binding and request validation. Remote control is outgoing-only.
- Never auto-retry side-effecting tasks; code work runs in a copy, a retry uses a **new** copy and a stronger model needs the user's confirmation.
- Use temporary worktrees, folders and databases for tests; tests must not touch the real `~/.agent-workshop`.
- Keep secrets outside version control; never show a key again after it was stored.
- Distinguish: code committed, tests passed on the GitHub runners, and tested on the user's Windows PC.
- Project name is **JetAI**. The repository, the Python package `workshop` and the data folder `~/.agent-workshop` keep their old names on purpose (renaming would break existing keys and chats); do not move or delete the data folder.
- Windows pitfalls already hit (keep them in mind): do not decode git output with the console code page (keep patches as bytes); start `.exe` files directly instead of `.cmd` shims; guard files that a thread and the API both rewrite with a lock and retry; set "ready" only after the work it implies is done.

## Working rules
- Check the "already exists" table in `docs/coding-agent-platform-plan.md` (appendix A) before building anything; do not duplicate.
- One small change at a time: implement → test → commit on a branch → pull request. Do not push to `main` directly; never enable auto-merge; merge only when the user asks and CI is green on the latest commit.
- **Every change is recorded in Markdown** in the same PR: facts and results in `docs/verification.md`, status in `docs/improvements.md` and the plan, behaviour in the relevant guide. Update this file when the state or the next tasks change so the next agent can continue.
- If something fails, say so with the output; do not label unverified items as done.

## Quick commands
```powershell
python -m unittest discover -s tests          # all tests (~2 min)
python -m workshop.diagnostics --no-services  # read-only environment check
python -m workshop.catalog_check              # do the catalog services still answer (anonymous GET, no keys)
.\jetai.ps1 -DryRun                           # what the launcher would do
.\scripts\build-exe.ps1                       # build JetAI.exe next to jetai.ps1
```
Test fixtures: fake OpenAI-compatible, GitHub/Hugging Face and Telegram servers and a fake OpenCode live in `tests/`; real-service checks are done by hand and recorded as such.

## Definition of done for the current milestone
One development task requested in the app → JetAI reads the project, works in a safe branch/worktree, runs build and tests, has the result reviewed, and reports the changed files and results in the screen and in a Markdown report, with the user deciding apply / commit to a work branch / discard. Acceptance scenario and steps: section 7 of the plan.
