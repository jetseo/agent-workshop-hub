"""One look for the server-rendered pages (tasks, setup, boards, usage) so they match the chat app in both themes.

Colours are the chat app's tokens. A page follows the app theme when it is shown inside the app window (the app sets
data-theme on the frame) and the system theme when it is opened on its own."""

TOKENS = (
    ":root{color-scheme:light dark;--bg:#fff;--bg2:#f7f7f8;--bg3:#ececf1;--fg:#1f2328;--muted:#6b7280;--border:#e5e7eb;"
    "--accent:#0f9d85;--accent-fg:#fff;--danger:#d92d20;--ok:#16a34a;--code:#f4f4f5}"
    "@media(prefers-color-scheme:dark){:root:not([data-theme='light']){--bg:#212121;--bg2:#171717;--bg3:#2a2a2a;--fg:#ececec;"
    "--muted:#9b9b9b;--border:#333;--accent:#19c3a4;--accent-fg:#06201a;--danger:#ff6b6b;--ok:#4ade80;--code:#161616}}"
    ":root[data-theme='dark']{color-scheme:dark;--bg:#212121;--bg2:#171717;--bg3:#2a2a2a;--fg:#ececec;--muted:#9b9b9b;--border:#333;"
    "--accent:#19c3a4;--accent-fg:#06201a;--danger:#ff6b6b;--ok:#4ade80;--code:#161616}"
    ":root[data-theme='light']{color-scheme:light}"
)

BASE = (
    "*{box-sizing:border-box}"
    "body{font-family:system-ui,'Malgun Gothic','Apple SD Gothic Neo',sans-serif;background:var(--bg);color:var(--fg);"
    "max-width:920px;margin:28px auto;padding:0 18px;line-height:1.5}"
    "h1{font-size:1.5rem;margin:0 0 6px}h2{font-size:1.05rem;margin:0 0 8px}p{color:var(--muted)}small{color:var(--muted)}"
    "a{color:var(--accent)}"
    "section,.job{background:var(--bg2);border:1px solid var(--border);border-radius:14px;padding:16px;margin:14px 0}"
    "input,select,textarea,button{font:inherit;padding:9px 11px;border-radius:10px;border:1px solid var(--border);"
    "background:var(--bg);color:var(--fg);max-width:100%}"
    "input[type=text],input[type=password],input:not([type]),textarea,select{width:100%;margin:4px 0 10px}"
    "input[type=checkbox],input[type=radio]{width:auto;margin:0 6px 0 0}"
    "button{cursor:pointer;background:var(--accent);color:var(--accent-fg);border:0;font-weight:600}"
    "button:hover{filter:brightness(1.06)}"
    "code,pre{background:var(--code);border-radius:6px;padding:2px 6px;font-family:ui-monospace,Consolas,monospace}"
    "pre{white-space:pre-wrap;overflow-wrap:anywhere;padding:10px}"
    "table{border-collapse:collapse}td,th{padding:6px 10px;text-align:left;border-bottom:1px solid var(--border)}"
    ".tag{color:var(--accent);font-weight:600}"
    "ul{padding-left:20px}li{margin:4px 0}"
    "@media(max-width:600px){body{margin:12px auto;padding:0 12px}section,.job{padding:12px}"
    "input,select,button{width:100%;min-height:44px;margin:4px 0}}"
)

CSS = TOKENS + BASE
HEAD_ICON = '<link rel="icon" type="image/svg+xml" href="/static/jetai-icon.svg">'
