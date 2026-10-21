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
            '<button data-view="digest"><i>&#8681;</i>압축</button>'
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
            '<div id="view-digest" class="view" hidden><div class="digest"><h2>긴 글 압축</h2>'
            '<p class="sub">긴 로그·문서를 무료/로컬 모델로 먼저 줄인 뒤 유료 모델에 넘기면 토큰을 아낄 수 있습니다.</p>'
            '<p class="warn">요약은 틀리거나 내용을 빠뜨릴 수 있습니다. 오류·경로·숫자는 원문으로 다시 확인하세요.</p>'
            '<label>모델 <select id="dg-model"></select></label>'
            '<label>집중할 내용(선택) <input id="dg-focus" maxlength="200" placeholder="예: 실패한 테스트와 원인"></label>'
            '<label>파일 불러오기(최대 300KB) <input id="dg-file" type="file"></label>'
            '<textarea id="dg-text" rows="10" maxlength="300000" placeholder="여기에 긴 글을 붙여 넣으세요" aria-label="압축할 글"></textarea>'
            '<label id="dg-cost-row" hidden><input id="dg-cost" type="checkbox"> 이 모델은 유료일 수 있음을 확인했고 비용을 감수합니다</label>'
            '<div class="row"><button id="dg-run" class="cta" type="button">압축하기</button><span id="dg-est"></span></div>'
            '<div id="dg-status" aria-live="polite"></div><div id="dg-notices"></div>'
            '<div id="dg-result" hidden><div class="row"><b>결과</b><button id="dg-copy" class="btn" type="button">복사</button></div>'
            '<pre id="dg-out"></pre><p id="dg-stats"></p></div></div></div>'
            '<iframe id="view-settings" class="view" src="about:blank" data-src="/setup" title="설정" hidden></iframe>'
            '<iframe id="view-tasks" class="view" src="about:blank" data-src="/" title="작업" hidden></iframe>'
            '<iframe id="view-boards" class="view" src="about:blank" data-src="/boards" title="보드" hidden></iframe>'
            '<iframe id="view-usage" class="view" src="about:blank" data-src="/usage" title="사용량" hidden></iframe>'
            + terminal_pane +
            '</main></section><div id="wizard" hidden></div><div id="toast" role="status" hidden></div></div>'
            '<script src="/static/xterm.js"></script><script src="/static/addon-fit.js"></script>'
            '<script src="/static/app.js"></script></body></html>')
