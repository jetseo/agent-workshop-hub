# Claude Code handoff: JetAI (formerly Agent Workshop Hub)

Read this first, then `docs/coding-agent-platform-plan.md` (status table, designs, prioritized plan, acceptance scenario), `docs/README.md` (index) and `docs/verification.md` (what is verified where).

## Project objective
JetAI (제타아이) is a Windows-first, local-first platform that **saves development tokens** by using **free and local AI first**: chat with automatic model selection, compression, boards of several models, and now **code work** (a coding agent edits a copy of the user's project; the user reviews the diff and decides). Ollama is optional, not required: the built-in engine downloads llama.cpp and a model itself. Target machine: RTX 3060 12GB, RAM 64GB, Windows 11.

## Project scope: two separate projects
- **JetAI (this repository)**: judge new features by whether they serve free/local-first development work, with explicit opt-in, approval and cost guards.
- **Jet Studio is a different project** for **producing Shorts (short-form videos)**. Do not add shorts/video/image-generation features, assets or naming here. The earlier image/ComfyUI features were removed and handed over (`docs/handoff/jet-studio-comfyui-prompt.md`); do not re-add them. If a request is ambiguous about which project it belongs to, ask before changing anything.

## Current state (2026-10-22)
Everything below is on `main`; "verified" levels are recorded per item in `docs/verification.md` (mock test / GitHub runner / **observed on the user's PC**). Do not call a mock test hardware validation.
- Chat app (`workshop/app_page.py`, `static/app.js`, `chat_api.py`): onboarding wizard with 18 free services (`setup_page.py`), grouped and searchable; daily discovery of new services (`servicefeed.py`); model list curation and recommendations (`modelpick.py`); public model catalog for strength/capabilities (`modelinfo.py`); new-model watcher (`modelwatch.py`).
- **Auto mode**: `arbiter.py` classifies each question (code / everyday / research + level), `router.py` picks the local/free model whose strength fits, with cooldowns and load spreading; failures fall back to the next fit; per-model results in `runstats.py`; usage ledger and savings in `usage.py`.
- Token savers: digest (`digest.py`), log pre-reducer (`logsum.py`), result cache (`cache.py`), multi-model boards (`board.py`, `orchestrator.py`, `plan_board.py`), queue with dependencies/cancel/retry (`queue.py`, `worker.py`).
- **Built-in engine** (`engine.py`): plans and, only after the user confirmed the exact size, downloads llama.cpp (GitHub) and a GGUF model (Hugging Face), verifies SHA-256, runs `llama-server` on 127.0.0.1, registers it as local service `jetai-engine`. Observed on the PC with a 0.5B model.
- **Code work** (`codework.py`, `adapters.py`, `worktree.py`, screen "코드"): OpenCode (native Windows `opencode.exe`) in a throw-away git worktree with an isolated empty config, minimal environment, deny-by-default shell, no web, no access outside the worktree; diff review; apply (never commits) or discard. Observed with a free cloud model; **small local models (7B/12B) cannot drive the agent** (documented in `docs/code-work.md`).
- **Phone control** (`remote.py`, settings): Telegram bot, outgoing polling only, one paired owner, two-step approvals. Mock-tested only; no real bot token was used yet.
- Launchers: `jetai.ps1`, `JetAI.exe` (built by `scripts/build-exe.ps1`), portable package (`scripts/build-package.ps1`), WSL scripts, diagnostics (`diagnostics.py`, `doctor.py`, `preflight.py`).

## Next tasks (see the plan for details and acceptance criteria)
P0 status: DONE (4) undo, (5) report + metrics, (6) project reading, (7) subscription research (`docs/subscription-tools.md`: Codex / Claude Code stay disconnected), (3) blocked-command approval (`cmdguard.py`, PR #25; mock-tested only). Skeleton DONE for (1) evaluation harness (`workshop/agenteval.py`) but **no real model has been measured yet**. Open: (2) 7B Coder tool-call test in the built-in engine (needs the user's OK for a ~4.7 GB download). Then P1 in the plan (goal-input orchestrator, JetAI-run build/test, review stage, work-branch commit).

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
