"""The chat-style app window at /app: sidebar, model picker, conversation, onboarding wizard, and the
Setup / Tasks / Boards / Terminal views. All behaviour lives in /static/app.js (no inline scripts)."""
import html

STATIC_FILES = {
    "xterm.js": "text/javascript; charset=utf-8", "addon-fit.js": "text/javascript; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8", "xterm.css": "text/css; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
}


def render(terminal_state, term_token="", csrf=""):
    """terminal_state: 'off' (not enabled), 'locked' (enabled, no entry cookie) or 'ready'."""
    esc = lambda value: html.escape(value, quote=True)
    terminal_nav = '<button data-view="terminal"><i>&gt;_</i>터미널</button>' if terminal_state != "off" else ""
    if terminal_state == "locked":
        terminal_pane = ('<div id="view-terminal" class="view" hidden><div id="locked"><h2>터미널 잠김</h2><p>이 터미널은 사용자 계정 권한의 셸입니다. '
                         "서버를 시작한 콘솔에 출력된 <code>…/app?key=…</code> 링크로 한 번 접속하면 열립니다.</p></div></div>")
    elif terminal_state == "ready":
        terminal_pane = ('<div id="view-terminal" class="view" hidden><div id="term"></div><div id="term-bar"><span id="term-status"></span>'
                         '<button id="term-reconnect" hidden>다시 연결</button><span>이 셸은 WSL 안에서 사용자 권한으로 실행됩니다.</span></div></div>')
    else:
        terminal_pane = ""
    return ('<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="color-scheme" content="light dark"><title>Agent Workshop</title>'
            '<link rel="stylesheet" href="/static/xterm.css"><link rel="stylesheet" href="/static/app.css"></head>'
            '<body data-csrf="' + esc(csrf) + '" data-terminal="' + esc(terminal_state) + '" data-term-token="' + esc(term_token) + '">'
            '<div id="app">'
            '<aside id="side"><div class="side-top"><span class="brand">Agent Workshop</span>'
            '<button id="side-close" class="icon" aria-label="사이드바 닫기" title="사이드바 닫기">&#x2715;</button></div>'
            '<button id="newchat" class="newchat"><i>+</i>새 채팅</button>'
            '<div id="chatlist" class="chat-list" role="list"></div>'
            '<nav class="side-nav"><button data-view="settings"><i>&#9881;</i>설정</button>'
            '<button data-view="tasks"><i>&#9776;</i>작업</button><button data-view="boards"><i>&#9636;</i>보드</button>'
            '<button data-view="usage"><i>&#9645;</i>사용량</button>'
            + terminal_nav +
            '</nav><div class="side-foot">로컬 전용 · 127.0.0.1</div></aside>'
            '<div id="scrim" hidden></div>'
            '<section id="stage"><header class="topbar"><button id="side-open" class="icon" aria-label="사이드바 열기" title="사이드바">&#9776;</button>'
            '<div class="model-wrap"><button id="modelbtn" aria-haspopup="listbox" aria-expanded="false">모델 선택<i>&#9662;</i></button>'
            '<div id="modelmenu" class="menu" role="listbox" hidden></div></div><span class="spacer"></span>'
            '<button id="theme" class="icon" aria-label="테마 전환" title="테마 전환">&#9680;</button></header>'
            '<main>'
            '<div id="view-chat" class="view"><div id="scroller"><div id="welcome"></div><div id="messages" aria-live="polite"></div></div>'
            '<form id="composer" autocomplete="off"><div class="box"><textarea id="input" rows="1" maxlength="20000" '
            'placeholder="메시지를 입력하세요" aria-label="메시지"></textarea>'
            '<button id="send" type="submit" aria-label="보내기" disabled>&#8593;</button></div>'
            '<p class="hint">Enter로 전송 · Shift+Enter로 줄바꿈 · 모델이 틀릴 수 있으니 중요한 내용은 확인하세요.<span id="usage-chip" hidden></span></p></form></div>'
            '<iframe id="view-settings" class="view" src="about:blank" data-src="/setup" title="설정" hidden></iframe>'
            '<iframe id="view-tasks" class="view" src="about:blank" data-src="/" title="작업" hidden></iframe>'
            '<iframe id="view-boards" class="view" src="about:blank" data-src="/boards" title="보드" hidden></iframe>'
            '<iframe id="view-usage" class="view" src="about:blank" data-src="/usage" title="사용량" hidden></iframe>'
            + terminal_pane +
            '</main></section><div id="wizard" hidden></div><div id="toast" role="status" hidden></div></div>'
            '<script src="/static/xterm.js"></script><script src="/static/addon-fit.js"></script>'
            '<script src="/static/app.js"></script></body></html>')
