# Claude Code handoff: JetAI (formerly Agent Workshop Hub)

Read this first, then `docs/coding-agent-platform-plan.md` (status table, designs, prioritized plan, acceptance scenario), `docs/README.md` (index) and `docs/verification.md` (what is verified where).

## Project objective
JetAI (제타아이) is a Windows-first, local-first platform that **saves development tokens** by using **free and local AI first**: chat with automatic model selection, compression, boards of several models, and now **code work** (a coding agent edits a copy of the user's project; the user reviews the diff and decides). Ollama is optional, not required: the built-in engine downloads llama.cpp and a model itself. Target machine: RTX 3060 12GB, RAM 64GB, Windows 11.

## Project scope: two separate projects
- **JetAI (this repository)**: judge new features by whether they serve free/local-first development work, with explicit opt-in, approval and cost guards.
- **Jet Studio is a different project** for **producing Shorts (short-form videos)**. Do not add shorts/video/image-generation features, assets or naming here. The earlier image/ComfyUI features were removed and handed over (`docs/handoff/jet-studio-comfyui-prompt.md`); do not re-add them. If a request is ambiguous about which project it belongs to, ask before changing anything.

## Current state (2026-10-23)
Everything below is on `main` unless marked **open PR**; "verified" levels are recorded per item in `docs/verification.md` (mock test / GitHub runner / **observed on the user's PC**). Do not call a mock test hardware validation.

**Chat and platform (unchanged core)**: chat app (`workshop/app_page.py`, `static/app.js`, `chat_api.py`), onboarding wizard (`setup_page.py`), auto mode (`arbiter.py` → `router.py` → `runstats.py`), usage ledger, digest/log pre-reducer/cache/boards/queue, built-in engine (`engine.py`, llama.cpp + GGUF, downloads only after the user confirmed the exact size), Mercury 3 behind `quotaguard.py` (no real call with a key yet), Telegram remote (`remote.py`, mock-tested only).

**Done since the 2026-10-22 handoff (all merged, PRs #35–#61)**
- **Code work engine**: direct mode (`directmode.py`), best-of-N, mixed models, action loop (experimental, worse than direct mode with 7B: do not tune it), project registry, policy/tools/audit, eval harness (`agenteval.py`). Context hygiene L3 (only tests that import the edited module are sent; failure logs reduced), input-length probe (`ctxprobe.py`), hardware-fit memory model (`engine.fit_with_context`), code-run outcome recording (`runstats` kind `code:<mode>`, not yet used for routing).
- **Browser (phase 2, partial)**: `cdp.py` (stdlib CDP client; localhost only; observe/approval modes) and `webcheck.py` + "웹 화면 확인" card on the run detail (read-only: no clicks/typing, audited in `audit.jsonl`). Not wired into the agent loop. Windows UI Automation and vision are **not started**.
- **Windows packaging**: `JetAI-Setup.exe` (`scripts/Setup.cs`, `scripts/build-installer.ps1`; per user, bundled Python, default folder **`C:\JetAI`**, removes only the former default `%LOCALAPPDATA%\Programs\JetAI`), `JetAI.exe` starts the server **without a PowerShell/console window** (`scripts/JetAI.cs`: hidden `python -m workshop.dashboard`, log `~/.agent-workshop/logs/jetai-server.log`, **tray icon** 열기/로그 보기/종료 owns the server). Silent install/uninstall and the hidden launcher were verified on the PC; the wizard GUI, shortcuts and tray menu clicks were **not eyeballed**.
- **UI (docs/ui-plan.md U1, docs/jetai-ui-improvement-plan.md P0/P1)**: Claude-style chat (model picker inside the composer), collapsible/resizable sidebar (`Ctrl+B` hides, drag edge, state in localStorage), code screen in three columns (runs | conversation | changes panel with diff), tools in the top bar, composer chips (project chip menu with native folder picker `folderpick.py`, mode, model, ⚙ options), run summary card, plan/work/verify timeline, status filter/sort/group-by-project, system status popover, **task management**: rename, archive, trash (no auto-delete), restore, purge (trash only, confirmed), bulk actions, rerun as a new run. Backend: `CodeRuns.rename/archive/unarchive/trash/restore/purge/bulk/rerun/list(view,q,limit)/counts`, `run.json` gained `title`, `archived_at`, `deleted_at` only.
- **Korean-only reply rule**: first system message of every chat (`chat_api.LANGUAGE_RULE`); whether tiny models obey it is unmeasured.
- Autonomous-agent plan decisions (`docs/autonomous-agent-plan.md`): D1 (zero dependencies), D3 (Observe → Approval order, no auto mode yet), D7 (work-branch commit only after approval) are approved; **D6 (JetStudio path) is still pending: do not touch Jet Studio until the user gives the path.** Mercury 3: allowed under FreeQuotaGuard, the user enters the key themself, never ask for or handle the key.
- Plans/docs: `docs/llm-efficiency-plan.md`, `docs/ui-plan.md`, `docs/jetai-ui-*.md`, `docs/jetai-task-deletion-rca.md` (deletion was missing, not buggy), `docs/computer-use.md`, `docs/autonomous-agent-plan.md`.

**Open PRs (not merged; merge only when the user asks, CI green on the latest commit)**
| PR | Branch | What | Notes |
|---|---|---|---|
| #62 | `feat/eval-multifile` | 4 multi-file/vague eval tasks, direct mode keeps existing test files read-only when other files can be edited, prompt hints | merge first |
| #66 | `feat/direct-sequential` | create new files first, edit one file at a time with earlier changes shown, Python syntax check after each edit | stacked on #62; **no measurable gain** (9/20 vs 8/20 and 10/20: noise), kept as harmless |
| #64 | `feat/engine-qwen35-9b` | Qwen3.5 9B (`unsloth/Qwen3.5-9B-GGUF`, Q4_K_M, 5,680,522,464 bytes) moved from `CANDIDATES` to installable `MODELS` | touches `engine.py` + `docs/verification.md` |
| #65 | `feat/discovery-d0` | discovery inbox D0 (`discovery.py`, "발견" screen, `/api/v1/discovery*`): stores/shows cards, **no network**, off by default | adds a sidebar item + `view-discovery` |
| #63 | `docs/discovery-helper` | `docs/discovery-helper-plan.md` (D0–D5) | docs only |
| #68 | `eval/qwen35-9b` | `docs/agent-eval.md`: Qwen3.5 9B measurement (13/20 vs 7B 8–10/20, 5–10x slower) | stacked on #62, docs only |
| #67 | `docs/handoff-refresh-1023` | this handoff refresh | docs only |
All PRs append to `docs/verification.md`, so merges conflict there: keep **both** sides (a script that drops the marker lines is enough; check for nested `<<<<<<<` blocks afterwards).

**Just finished (recorded in PR #68, not yet merged)**: the Qwen3.5 9B measurement. Same 4 tasks x 5 runs, direct mode, 3 attempts, code of PR #62: **9B 13/20** (vague-bug 5/5, three-files 3/5, move-helper 0/5, log-bug 5/5) vs the 7B's 8-10/20 in the same code; per-task time 70-225 s vs 10-16 s. Read it as "somewhat better but much slower", not as proof (20 trials, the 7B itself moved 8-10). **move-helper fails with both models in the same way** (`title_line` loses the `!` from `shout`): suspect the task wording/prompt, not model size; look at a real output before changing anything. Thinking mode off (`enable_thinking:false`) is fast but gave a wrong answer on a trivial check; `/no_think` has no effect.
- The background engine process that served the 9B was stopped (`%TEMP%/engine.stop`). Nothing of this work is left running. If you need the 9B again: `Engine.start("qwen3.5-9b")` (it takes ~11.9 GB of the 12 GB VRAM; unload Ollama models first; the provider is `jetai-engine/qwen3.5-9b`).
- Measurement files and throwaway scripts are in `%TEMP%/evalout` and `%TEMP%/agg.py`, `%TEMP%/measure2.sh` (outside the repo, may disappear): the command is in "Quick commands" and the result tables are in `docs/agent-eval.md`.
- The 9B file is installed at `~/.agent-workshop/engine/models/Qwen3.5-9B-Q4_K_M.gguf` (user-approved download, 5.29 GiB). A stale partial `Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf.part` (3.96 GB) from an earlier attempt is also there; do not delete it without asking.
- Extra git worktrees next to the repo (clean, all pushed): `wt-disc` (#65), `wt-seq` (#66), `wt-eval` (#68), `wt-handoff` (#67). Remove them with `git worktree remove` after the PRs are merged. The main checkout was left on `feat/engine-qwen35-9b`.

## Next tasks (priority order; see the plans for acceptance criteria)
0. **Done since the last refresh (in PR #66, not merged)**: move-helper was failing with *both* models for a reason in the direct-mode pipeline, not the model: a new file was created without the original code (so the `!` was lost), and edit markers (`SEARCH`/`=======`) were accepted as file content. Fixed in `directmode.py` (reference files shown when creating a new file; marker lines refused). move-helper went 0-1/5 -> 4/5 (5/5 alone). Total 7B 12/20 (was 8-10/20). three-files stays 0/5: invented variable names (`total_price`) in the call site.
1. Ask the user whether the 9B (13/20, much slower) should become a selectable/default code model, and investigate why **move-helper** fails with both models (read an actual output first). A thinking model needs the answer ceilings `LOCALIZE_TOKENS/WRITE_TOKENS` to stay generous.
2. Merge the open PRs when the user asks (order #62 → #66 and #68, then #64, #65, #63, #67), rebuild the installer (`powershell -File scripts/build-installer.ps1`, ~11 MB, writes `dist\JetAI-Setup.exe`, git-ignored) and tell the user to quit the running JetAI from the tray first.
3. Discovery helper D1 onward (`docs/discovery-helper-plan.md`): Hugging Face model candidates with the PC-fit check, then "add" through the existing exact-size flow. Recommend only; never auto-install; external text is data, not instructions.
4. UI plan left: U2 (Ctrl+K palette), U3 (diff line comments), U4; per-service connection-state chips; keyboard-only and light-theme checks were not done.
5. Agent quality: weakest case is **several files edited consistently** with the 7B (three-files 1/5, move-helper 1/5): try bigger models, smaller steps, or better feedback; repeat measurements (5+ runs) before claiming gains.
6. Computer use: wire the browser into policy/audit (observe → approval), then Windows UI Automation, then vision. Login/payment screen detection is missing.
7. Existing P1 items: work-branch commit (ask first), retry/escalation, diff secret scan, route code runs by recorded outcomes (L7).
- Ask the user before: merging, paid calls, downloads (state the exact size), real-hardware or live-service runs, touching `~/.agent-workshop` data (the user's own run records were soft-deleted by us only for **eval** runs, 384 of them, now in the trash tab), the Telegram token.

**Needs Sonnet review before anyone changes it (do not do alone):** (a) whether to route three-files-style failures back with the exact `NameError` line (touches the repair loop in `directmode.py`); (b) making 9B a default code model (model routing); (c) any change to how `engine.py` lists or installs models; (d) the move/rename prompt wording (affects all direct-mode edits; verify with 4 tasks x 5 runs before keeping it).

## Essential safety and design constraints
- No automatic paid API calls, credentials overwrite, model downloads without the user's confirmed size, destructive installation, or automatic merges. Existing subscriptions (Codex, Claude Code, OpenAI login, OpenCode Zen key) must not be used by automation unless verified and explicitly confirmed.
- Never expose the server publicly; keep 127.0.0.1 binding and request validation. Remote control is outgoing-only.
- Never auto-retry side-effecting tasks; code work runs in a copy, a retry uses a **new** copy and a stronger model needs the user's confirmation.
- Use temporary worktrees, folders and databases for tests; tests must not touch the real `~/.agent-workshop`.
- Keep secrets outside version control; never show a key again after it was stored.
- Distinguish: code committed, tests passed on the GitHub runners, and tested on the user's Windows PC.
- Project name is **JetAI**. The repository, the Python package `workshop` and the data folder `~/.agent-workshop` keep their old names on purpose (renaming would break existing keys and chats); do not move or delete the data folder.
- Windows pitfalls already hit (keep them in mind): do not decode git output with the console code page (keep patches as bytes); start `.exe` files directly instead of `.cmd` shims; guard files that a thread and the API both rewrite with a lock and retry; set "ready" only after the work it implies is done; `param()` defaults that use `$PSScriptRoot` are empty on some PowerShell 5.1 hosts (compute them inside the script); `Environment.GetFolderPath(UserProfile)` ignores a changed `USERPROFILE` (a launcher test with a temp home still logs to the real profile).

## Working rules
- Check the "already exists" table in `docs/coding-agent-platform-plan.md` (appendix A) before building anything; do not duplicate.
- One small change at a time: implement → test → commit on a branch → pull request. Do not push to `main` directly; never enable auto-merge; merge only when the user asks and CI is green on the latest commit.
- **Every change is recorded in Markdown** in the same PR: facts and results in `docs/verification.md`, status in `docs/improvements.md` and the plan, behaviour in the relevant guide. Update this file when the state or the next tasks change so the next agent can continue.
- If something fails, say so with the output; do not label unverified items as done.
- **Token saving (user request)**: run only the tests related to your change locally; the full suite (~4 min, 629+ tests) runs in each PR's CI. Keep messages short; verify UI by measuring in the built-in browser (resize to the target width, then click/measure with JS) and say what was *not* checked.
- Tool pitfalls hit this session: a heredoc passed to `python -` mangles backslash escapes (a backslash followed by n or b turned into a newline or a backspace character; write a script file with the Write tool, then run it); a background job started with an inner `&` dies when the tool call ends (run the long command itself in the background); never chain `sleep` (use an `until` loop); `pkill` does not exist on this Windows shell (use `Get-CimInstance Win32_Process`); an agent-eval run writes eval records into the real `~/.agent-workshop/coderuns`, so move them to the trash afterwards (`CodeRuns.trash`, soft delete).
- Measurements: 4 tasks × 5 runs still swing by a few passes between identical settings; call a difference real only when it is clearly outside that (the 7B ranged 8–10/20 across three variants).
- A known flaky CI test (`test_token_is_never_forwarded_through_a_redirect`, Windows) was changed in PR #57 on a hypothesis (read the POST body before replying); watch whether it recurs.

## Quick commands
```powershell
python -m unittest discover -s tests          # all tests (~2 min)
python -m workshop.diagnostics --no-services  # read-only environment check
python -m workshop.catalog_check              # do the catalog services still answer (anonymous GET, no keys)
.\jetai.ps1 -DryRun                           # what the launcher would do
.\scripts\build-exe.ps1                       # build JetAI.exe next to jetai.ps1
.\scripts\build-installer.ps1                 # dist\JetAI-Setup.exe (downloads the embeddable Python once; JetAI.exe is built inside)
python -m workshop.agenteval --model ollama/qwen2.5-coder:7b --mode direct --attempts 3 --tasks vague-bug,three-files,move-helper,log-bug --out result.md   # eval harness (writes runs into the real data folder: trash them afterwards)
```
Test fixtures: fake OpenAI-compatible, GitHub/Hugging Face and Telegram servers and a fake OpenCode live in `tests/`; real-service checks are done by hand and recorded as such.

## Definition of done for the current milestone
One development task requested in the app → JetAI reads the project, works in a safe branch/worktree, runs build and tests, has the result reviewed, and reports the changed files and results in the screen and in a Markdown report, with the user deciding apply / commit to a work branch / discard. Acceptance scenario and steps: section 7 of the plan.
