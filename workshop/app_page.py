"""The chat-style app window at /app: sidebar, model picker, conversation, onboarding wizard, and the
Setup / Tasks / Boards / Terminal views. All behaviour lives in /static/app.js (no inline scripts)."""
import html

STATIC_FILES = {
    "xterm.js": "text/javascript; charset=utf-8", "addon-fit.js": "text/javascript; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8", "xterm.css": "text/css; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
    "jetai-icon.svg": "image/svg+xml", "jetai-mark.svg": "image/svg+xml", "jetai.ico": "image/x-icon",
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
            '<meta name="color-scheme" content="light dark"><title>JetAI</title>'
            '<link rel="icon" type="image/svg+xml" href="/static/jetai-icon.svg"><link rel="alternate icon" href="/favicon.ico">'
            '<link rel="stylesheet" href="/static/xterm.css"><link rel="stylesheet" href="/static/app.css"></head>'
            '<body data-csrf="' + esc(csrf) + '" data-terminal="' + esc(terminal_state) + '" data-term-token="' + esc(term_token) + '">'
            '<div id="app">'
            '<aside id="side"><div class="side-top"><span class="brand"><svg class="logo" viewBox="200 320 860 610" width="32" height="22" aria-hidden="true" fill="currentColor"><path d="M215 608C215 598 235 592 255 588L890 462C905 459 912 466 908 474L655 830C640 855 600 880 560 900C540 910 520 918 513 908L598 722C600 712 592 708 580 712L440 785C420 793 360 795 353 787C350 782 355 778 365 772L700 565C715 558 730 558 727 566C724 572 715 576 700 582L415 672C405 676 395 676 385 674L228 632C218 629 215 620 215 608Z"/><path d="M966 335Q974 406 1043 414Q974 421 966 497Q958 421 889 411Q958 404 966 335Z"/></svg>JetAI</span>'
            '<span class="side-btns"><button id="side-hide" class="icon" aria-label="사이드바 숨기기 (Ctrl+B)" title="사이드바 숨기기 (Ctrl+B)">&#x2039;</button><button id="side-close" class="icon" aria-label="사이드바 닫기" title="사이드바 닫기">&#x2715;</button></span></div>'
            '<button id="newchat" class="newchat"><i>+</i>새 채팅</button>'
            '<nav class="side-nav"><button data-view="code"><i>&#9998;</i>코드 작업</button><button data-view="settings"><i>&#9881;</i>설정</button>'
            '<button data-view="tasks"><i>&#9776;</i>작업</button><button data-view="boards"><i>&#9636;</i>보드</button>'
            '<button data-view="usage"><i>&#9645;</i>사용량</button>'
            '<button data-view="digest"><i>&#8681;</i>압축</button>'
            + terminal_nav +
            '</nav>'
            '<div class="side-label">최근 항목</div><div id="chatlist" class="chat-list" role="list"></div>'
            '<div class="side-foot">로컬 전용 · 127.0.0.1</div></aside>'
            '<div id="side-gutter" class="gutter" role="separator" aria-orientation="vertical" aria-label="메뉴 폭 조절(좌우 화살표, 더블클릭으로 초기화)" tabindex="0"></div>'
            '<div id="scrim" hidden></div>'
            '<section id="stage"><header class="topbar"><button id="side-open" class="icon" aria-label="사이드바 열기" title="사이드바">&#9776;</button>'
            '<span id="tb-left" class="tb-slot"></span><button type="button" class="help" data-help="auto" aria-label="자동 선택 설명" title="자동 선택은 어떻게 동작하나요?">?</button><span class="spacer"></span>'
            '<span id="tb-right" class="tb-slot"></span><button id="theme" class="icon" aria-label="테마 전환" title="테마 전환">&#9680;</button></header>'
            '<main>'
            '<div id="view-chat" class="view"><div id="scroller"><div id="welcome"></div><div id="messages" aria-live="polite"></div></div>'
            '<form id="composer" autocomplete="off"><div class="box"><textarea id="input" rows="1" maxlength="20000" '
            'placeholder="무엇을 도와드릴까요?" aria-label="메시지"></textarea>'
            '<div class="box-row"><span class="spacer"></span>'
            '<div class="model-wrap"><button type="button" id="modelbtn" aria-haspopup="listbox" aria-expanded="false">모델 선택<i>&#9662;</i></button>'
            '<div id="modelmenu" class="menu" role="listbox" hidden></div></div>'
            '<button id="send" type="submit" aria-label="보내기" disabled>&#8593;</button></div></div>'
            '<p class="hint">Enter로 전송 · Shift+Enter로 줄바꿈 · 모델이 틀릴 수 있으니 중요한 내용은 확인하세요.<span id="usage-chip" hidden></span></p></form></div>'
            '<div id="view-code" class="view" hidden><div id="code-root" class="cw"></div></div>'
            '<div id="view-digest" class="view" hidden><div class="digest"><h2>긴 글 압축</h2>'
            '<p class="sub">긴 로그·문서를 무료/로컬 모델로 먼저 줄인 뒤 유료 모델에 넘기면 토큰을 아낄 수 있습니다.</p>'
            '<p class="warn">요약은 틀리거나 내용을 빠뜨릴 수 있습니다. 오류·경로·숫자는 원문으로 다시 확인하세요.</p>'
            '<label>모델 <select id="dg-model"></select></label>'
            '<label>집중할 내용(선택) <input id="dg-focus" maxlength="200" placeholder="예: 실패한 테스트와 원인"></label>'
            '<label>파일 불러오기(최대 300KB) <input id="dg-file" type="file"></label>'
            '<textarea id="dg-text" rows="10" maxlength="300000" placeholder="여기에 긴 글을 붙여 넣으세요" aria-label="압축할 글"></textarea>'
            '<label><input id="dg-prefilter" type="checkbox" checked> 로그 전처리(반복 줄 합치기·오류 줄 위주, 모델 호출 전 무료)</label>'
            '<label id="dg-cost-row" hidden><input id="dg-cost" type="checkbox"> 이 모델은 유료일 수 있음을 확인했고 비용을 감수합니다</label>'
            '<div class="row"><button id="dg-run" class="cta" type="button">압축하기</button><span id="dg-est"></span></div>'
            '<div id="dg-status" aria-live="polite"></div><div id="dg-notices"></div>'
            '<div id="dg-result" hidden><div class="row"><b>결과</b><button id="dg-copy" class="btn" type="button">복사</button></div>'
            '<pre id="dg-out"></pre><p id="dg-stats"></p></div></div></div>'
            '<div id="view-settings" class="view" hidden><div class="settings"><h2>설정</h2>'
            '<section id="set-env"><h3>이 PC</h3><p id="set-env-text" class="sub">확인하는 중…</p></section>'
            '<section id="set-remote"><h3>모바일 원격 제어 (텔레그램) <button type="button" class="help" data-help="remote" aria-label="설명">?</button></h3>'
            '<p class="sub">폰의 텔레그램에서 질문하고, 작업을 승인·취소하고, 상태를 볼 수 있습니다. 이 PC가 밖으로만 연결하므로 포트를 열지 않습니다.</p>'
            '<p class="warn">대화 내용이 텔레그램 서버를 거칩니다(종단간 암호화 아님). 민감한 내용은 보내지 마세요. 연결은 내 계정 하나만 허용됩니다.</p>'
            '<div id="rem-body"></div></section>'
            '<section id="set-quota"><h3>무료 한도 보호 서비스 (Mercury 3)</h3>'
            '<p class="sub">공급자가 무료 잔액·결제 차단을 API로 알려 주지 않는 서비스는, 아래 확인을 모두 마쳤을 때만 호출합니다. 하나라도 빠지면 호출이 막히고 다른 무료·로컬 모델로 넘어갑니다. 기본값은 꺼짐입니다.</p>'
            '<div id="quota-body"></div></section>'
            '<section id="set-engine"><h3>내장 로컬 엔진 <button type="button" class="help" data-help="engine" aria-label="설명">?</button></h3>'
            '<p class="sub">Ollama를 따로 설치하지 않고, JetAI가 직접 모델을 받아 이 PC에서 실행합니다. 받기 전에 크기를 확인합니다.</p>'
            '<p id="eng-hw" class="sub"></p><div id="eng-list"></div><div id="eng-job" hidden></div>'
            '<label class="check"><input id="eng-auto" type="checkbox"> JetAI를 켤 때 마지막 모델을 자동으로 시작</label> '
            '<button id="eng-stop" class="btn" type="button" hidden>엔진 끄기</button> <button id="eng-clear" class="btn" type="button" hidden>엔진·모델 모두 지우기</button></section>'
            '<section id="set-watch"><h3>새 모델 자동 추가 <button type="button" class="help" data-help="newmodels" aria-label="설명">?</button></h3>'
            '<label class="check"><input id="set-auto" type="checkbox"> 실행할 때마다 새 무료·로컬 모델을 확인해 자동으로 추가</label> '
            '<button id="set-check" class="btn" type="button">지금 확인</button> <span id="set-check-msg" class="sub"></span></section>'
            '<section id="set-services"><div class="row"><h3>연결된 서비스</h3>'
            '<button id="set-add" class="btn" type="button">+ 서비스 추가</button></div><div id="set-list"></div></section>'
            '<section><h3>내 데이터</h3><p class="sub">키·대화·사용량은 이 PC의 <code>.agent-workshop</code> 폴더에만 저장됩니다. '
            '백업은 터미널에서 <code>python -m workshop.backup export</code>(키 제외)로 만들 수 있습니다.</p></section>'
            '</div></div>'
            '<iframe id="view-tasks" class="view" src="about:blank" data-src="/" title="작업" hidden></iframe>'
            '<iframe id="view-boards" class="view" src="about:blank" data-src="/boards" title="보드" hidden></iframe>'
            '<iframe id="view-usage" class="view" src="about:blank" data-src="/usage" title="사용량" hidden></iframe>'
            + terminal_pane +
            '</main></section><div id="wizard" hidden></div><div id="helpbox" hidden></div><div id="toast" role="status" hidden></div></div>'
            '<script src="/static/xterm.js"></script><script src="/static/addon-fit.js"></script>'
            '<script src="/static/app.js"></script></body></html>')
