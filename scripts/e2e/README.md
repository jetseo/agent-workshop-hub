# Browser end-to-end check for the /app terminal (manual, not part of CI)

Needs Node, `npm i playwright-core`, and a Chromium binary (`CHROMIUM_PATH`). Runs the real xterm.js page under
the real Content-Security-Policy (it never bypasses CSP).

```bash
HOME=$(mktemp -d) python3 -m workshop.dashboard --port 8799 --enable-terminal &   # note the printed key
CHROMIUM_PATH=/path/to/chrome node scripts/e2e/terminal-browser.js 8799 "$(cat $HOME/.agent-workshop/terminal.key)"
```
Checks: key entry cookie and clean URL, tabs/iframes, typing a command and seeing the output on screen,
resize -> `stty size`, locked state without the cookie, and no console errors.
Last run (2026-10-20, Linux container, Chromium 1194): all checks passed.
