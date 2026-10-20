"""The tabbed desktop-style shell at /app: Setup, Tasks, Boards and (optionally) a terminal."""
import html

STATIC_FILES = {
    "xterm.js": "text/javascript; charset=utf-8", "addon-fit.js": "text/javascript; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8", "xterm.css": "text/css; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
}


def render(terminal_state, term_token=""):
    """terminal_state: 'off' (not enabled), 'locked' (enabled, no entry cookie) or 'ready'."""
    terminal_tab = '<button data-tab="terminal" aria-selected="false">터미널</button>' if terminal_state != "off" else ""
    if terminal_state == "locked":
        terminal_pane = ('<div id="pane-terminal" hidden><div id="locked"><h2>터미널 잠김</h2><p>이 터미널은 사용자 계정 권한의 셸입니다. '
                         "서버를 시작한 콘솔에 출력된 <code>…/app?key=…</code> 링크로 한 번 접속하면 열립니다.</p></div></div>")
    elif terminal_state == "ready":
        terminal_pane = ('<div id="pane-terminal" hidden><div id="term"></div><div id="term-bar"><span id="term-status"></span>'
                         '<button id="term-reconnect" hidden>다시 연결</button><span>이 셸은 WSL 안에서 사용자 권한으로 실행됩니다.</span></div></div>')
    else:
        terminal_pane = ""
    return ('<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>Agent Workshop</title>"
            '<link rel="stylesheet" href="/static/xterm.css"><link rel="stylesheet" href="/static/app.css"></head>'
            '<body data-terminal="' + html.escape(terminal_state) + '" data-term-token="' + html.escape(term_token, quote=True) + '">'
            '<nav><button data-tab="setup" aria-selected="true">설정</button>'
            '<button data-tab="tasks" aria-selected="false">작업</button>'
            '<button data-tab="boards" aria-selected="false">보드</button>' + terminal_tab +
            '<span class="spacer"></span><small>Agent Workshop Hub · 로컬 전용 127.0.0.1</small></nav>'
            '<main><iframe id="pane-setup" src="/setup" title="설정"></iframe>'
            '<iframe id="pane-tasks" src="/" title="작업" hidden></iframe>'
            '<iframe id="pane-boards" src="/boards" title="보드" hidden></iframe>' + terminal_pane + "</main>"
            '<script src="/static/xterm.js"></script><script src="/static/addon-fit.js"></script>'
            '<script src="/static/app.js"></script></body></html>')
