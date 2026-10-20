# Browser end-to-end checks (manual, not part of CI)

Need Node, `npm i playwright-core`, and a Chromium binary (`CHROMIUM_PATH`). They drive the real pages under the
real Content-Security-Policy (CSP is never bypassed).

## Chat window and onboarding (`chat-browser.js`)
```bash
python3 scripts/e2e/mock_openai.py 8761 &                      # OpenAI-compatible mock (streaming, markdown, slow replies)
HOME=$(mktemp -d) python3 -m workshop.dashboard --port 8760 --enable-terminal &
mkdir -p /tmp/shots
CHROMIUM_PATH=/path/to/chrome node scripts/e2e/chat-browser.js 8760 "$(cat $HOME/.agent-workshop/terminal.key)" 8761 /tmp/shots
```
Walks the first-run wizard (service -> connection -> models -> test & save), Korean chat with streaming, IME-safe Enter,
safe markdown (HTML and `javascript:` links stay inert), stop keeps the partial answer, the model picker with the full
model list, sidebar rename/delete/persistence across reload, other views, terminal tab, dark theme and a narrow window,
and fails on any console error. Screenshots are written to the last argument.

## Terminal (`terminal-browser.js`)
Key entry and clean URL, typing a command and seeing the output, resize -> `stty size`, locked state without the cookie.

Last run (2026-10-20, Linux container, Chromium 1194): both pass; the chat run passed 6 of 6 repeated runs.
