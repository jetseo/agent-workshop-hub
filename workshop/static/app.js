(function () {
  "use strict";
  var body = document.body, CSRF = body.dataset.csrf, TERM = body.dataset.terminal;
  var state = { providers: [], catalog: [], onboarded: false, chats: [], chat: null, model: "",
                view: "chat", streaming: null, wiz: null, fallback: store("fallback") !== "0" };
  var VIEWS = ["chat", "settings", "tasks", "boards", "usage", "digest", "code", "discovery", "terminal"];
  var SUGGESTIONS = ["이 프로젝트의 빠른 시작 가이드를 5줄로 요약해줘",
                     "파이썬으로 CSV 파일을 읽어서 열별 합계를 구하는 코드를 작성해줘",
                     "다음 문장을 자연스러운 영어로 번역해줘: 오늘 회의는 3시에 시작합니다.",
                     "회의록을 깔끔하게 정리하는 방법을 단계별로 알려줘"];

  function $(id) { return document.getElementById(id); }
  function h(tag, props) {
    var el = document.createElement(tag), i;
    props = props || {};
    Object.keys(props).forEach(function (k) {
      var v = props[k];
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.slice(0, 2) === "on") el.addEventListener(k.slice(2), v);
      else if (v !== false && v != null) el.setAttribute(k, v);
    });
    for (i = 2; i < arguments.length; i++) append(el, arguments[i]);
    return el;
  }
  function append(el, c) {
    if (c == null || c === false) return;
    if (Array.isArray(c)) c.forEach(function (x) { append(el, x); });
    else el.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  function toast(message) {
    var t = $("toast");
    t.textContent = message; t.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(function () { t.hidden = true; }, 2200);
  }
  function store(key, value) { try { if (value === undefined) return localStorage.getItem(key); localStorage.setItem(key, value); } catch (e) { return null; } }

  /* ---------- API ---------- */
  async function api(method, path, data) {
    var res = await fetch("/api/v1" + path, { method: method,
      headers: { "X-CSRF-Token": CSRF, "Content-Type": "application/json" },
      body: data === undefined ? undefined : JSON.stringify(data) });
    var json = {};
    try { json = await res.json(); } catch (e) { /* empty body */ }
    if (!res.ok) throw new Error(json.error || ("HTTP " + res.status));
    return json;
  }
  async function* sseEvents(res) {
    var reader = res.body.getReader(), dec = new TextDecoder("utf-8"), buf = "", r, idx;
    for (;;) {
      r = await reader.read();
      if (r.done) break;
      buf += dec.decode(r.value, { stream: true });
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        var block = buf.slice(0, idx), ev = { event: "message", data: "" };
        buf = buf.slice(idx + 2);
        block.split("\n").forEach(function (line) {
          if (line.indexOf("event: ") === 0) ev.event = line.slice(7);
          else if (line.indexOf("data: ") === 0) ev.data += line.slice(6);
        });
        try { ev.data = JSON.parse(ev.data); } catch (e) { continue; }
        yield ev;
      }
    }
  }

  /* ---------- safe markdown (everything is escaped before any tag is added) ---------- */
  function esc(s) { return s.replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function inline(s) {
    return s.replace(/`([^`\n]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
      .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  }
  function mdBlocks(text) {
    var out = [], para = [], list = null, m;
    function flushPara() { if (para.length) { out.push("<p>" + inline(esc(para.join("\n"))).replace(/\n/g, "<br>") + "</p>"); para = []; } }
    function flushList() {
      if (list) { out.push("<" + list.tag + ">" + list.items.map(function (i) { return "<li>" + inline(esc(i)) + "</li>"; }).join("") + "</" + list.tag + ">"); list = null; }
    }
    text.split("\n").forEach(function (line) {
      if ((m = /^(#{1,3})\s+(.*)$/.exec(line))) { flushPara(); flushList(); out.push("<h" + m[1].length + ">" + inline(esc(m[2])) + "</h" + m[1].length + ">"); }
      else if ((m = /^\s*[-*]\s+(.*)$/.exec(line))) { flushPara(); if (!list || list.tag !== "ul") { flushList(); list = { tag: "ul", items: [] }; } list.items.push(m[1]); }
      else if ((m = /^\s*\d+[.)]\s+(.*)$/.exec(line))) { flushPara(); if (!list || list.tag !== "ol") { flushList(); list = { tag: "ol", items: [] }; } list.items.push(m[1]); }
      else if ((m = /^>\s?(.*)$/.exec(line))) { flushPara(); flushList(); out.push("<blockquote><p>" + inline(esc(m[1])) + "</p></blockquote>"); }
      else if (!line.trim()) { flushPara(); flushList(); }
      else { flushList(); para.push(line); }
    });
    flushPara(); flushList();
    return out.join("");
  }
  function renderMarkdown(src) {
    var parts = [], re = /```([\w+-]*)[^\S\n]*\n([\s\S]*?)(?:```|$)/g, last = 0, m;
    while ((m = re.exec(src))) {
      parts.push(mdBlocks(src.slice(last, m.index)));
      parts.push('<div class="codebox"><div class="bar"><span>' + esc(m[1] || "code") + '</span><button type="button" data-copy="1">복사</button></div><pre><code>' +
                 esc(m[2].replace(/\n$/, "")) + "</code></pre></div>");
      last = re.lastIndex;
      if (m[0].length === 0) re.lastIndex++;
    }
    parts.push(mdBlocks(src.slice(last)));
    return parts.join("");
  }
  function copyText(text) {
    // Copy synchronously inside the click first: navigator.clipboard can wait forever on a permission prompt.
    if (fallbackCopy(text)) { toast("복사했습니다"); return; }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { toast("복사했습니다"); }, function () { toast("복사하지 못했습니다"); });
    } else toast("복사하지 못했습니다");
  }
  function fallbackCopy(text) {
    var ta = h("textarea", { style: "position:fixed;top:0;left:0;opacity:0", "aria-hidden": "true" }), ok = false;
    ta.value = text; document.body.appendChild(ta); ta.select();
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    ta.remove();
    return ok;
  }

  /* ---------- theme / sidebar ---------- */
  function applyTheme(t) {
    document.documentElement.dataset.theme = t; store("theme", t);
    var b = $("theme");
    b.textContent = t === "dark" ? "\u2600" : "\u263E";               // sun in dark mode, moon in light mode
    b.title = b.ariaLabel = t === "dark" ? "밝은 테마로 전환" : "어두운 테마로 전환";
    syncFrames();
  }
  function syncFrames() {          // framed pages that support themes (the usage view) follow the app theme
    Array.prototype.forEach.call(document.querySelectorAll("iframe.view"), function (f) {
      try { if (f.contentDocument && f.contentDocument.documentElement) f.contentDocument.documentElement.dataset.theme = document.documentElement.dataset.theme; }
      catch (e) { /* cross-origin or not loaded yet */ }
    });
  }
  Array.prototype.forEach.call(document.querySelectorAll("iframe.view"), function (f) { f.addEventListener("load", syncFrames); });
  applyTheme(store("theme") || (window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
  $("theme").addEventListener("click", function () { applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"); });
  /* Draggable panel edge: pointer drag, arrow keys, double click resets. Width lives in a CSS variable and is remembered. */
  function makeResizer(gutter, o) {
    var rootEl = document.documentElement;
    function apply(w) { w = Math.max(o.min, Math.min(o.max(), Math.round(w))); rootEl.style.setProperty(o.cssVar, w + "px"); return w; }
    var saved = parseInt(store(o.key), 10); if (saved) apply(saved);
    function current() { return parseInt(getComputedStyle(rootEl).getPropertyValue(o.cssVar), 10) || o.def; }
    gutter.addEventListener("pointerdown", function (e) {
      if (e.button !== 0) return; e.preventDefault();
      gutter.setPointerCapture(e.pointerId); gutter.classList.add("drag"); body.classList.add("dragging");
      var left = o.origin();
      function move(ev) { apply(o.invert ? left - ev.clientX : ev.clientX - left); }
      function up() { gutter.classList.remove("drag"); body.classList.remove("dragging"); gutter.removeEventListener("pointermove", move); gutter.removeEventListener("pointerup", up); gutter.removeEventListener("pointercancel", up); store(o.key, String(current())); }
      gutter.addEventListener("pointermove", move); gutter.addEventListener("pointerup", up); gutter.addEventListener("pointercancel", up);
    });
    gutter.addEventListener("keydown", function (e) {
      var step = e.shiftKey ? 48 : 16;
      var grow = o.invert ? "ArrowLeft" : "ArrowRight", shrink = o.invert ? "ArrowRight" : "ArrowLeft";
      if (e.key === shrink) apply(current() - step); else if (e.key === grow) apply(current() + step); else if (e.key === "Home") apply(o.def); else return;
      e.preventDefault(); store(o.key, String(current()));
    });
    gutter.addEventListener("dblclick", function () { apply(o.def); store(o.key, String(o.def)); });
  }
  makeResizer($("side-gutter"), { cssVar: "--side-w", key: "side-w", min: 200, def: 268, max: function () { return Math.min(520, window.innerWidth * 0.5); }, origin: function () { return 0; } });
  function setSide(open) {
    var desk = window.innerWidth > 760;
    $("side").classList.toggle("collapsed", !open);
    body.classList.toggle("side-closed", !open);
    $("side-close").textContent = desk ? (open ? "\u2039" : "\u203A") : "\u2715";
    $("side-close").title = desk ? (open ? "아이콘만 보기" : "메뉴 펴기") : "사이드바 닫기";
    $("side-close").setAttribute("aria-label", $("side-close").title);
    $("scrim").hidden = !(open && window.innerWidth <= 760);
    store("side", open ? "1" : "0");
  }
  setSide(window.innerWidth > 760);
  var wasNarrow = window.innerWidth <= 760;
  window.addEventListener("resize", function () {          // shrinking the window must not leave the sidebar covering the chat
    var narrow = window.innerWidth <= 760;
    if (narrow && !wasNarrow) setSide(false);
    else if (!narrow && wasNarrow) setSide(true);
    wasNarrow = narrow;
  });
  $("side-close").addEventListener("click", function () { setSide($("side").classList.contains("collapsed")); });
  document.addEventListener("keydown", function (e) {
    if (!(e.ctrlKey || e.metaKey) || e.altKey || e.key.toLowerCase() !== "b") return;
    e.preventDefault();
    if (e.shiftKey) { if ($("code-root") && !$("view-code").hidden) toggleRuns(); }
    else if (window.innerWidth > 760) setHidden(!body.classList.contains("side-hidden"));
    else setSide($("side").classList.contains("collapsed"));
  });
  function setHidden(hide) { body.classList.toggle("side-hidden", hide && window.innerWidth > 760); store("side-hidden", hide ? "1" : "0"); }
  setHidden(store("side-hidden") === "1");
  $("side-hide").addEventListener("click", function () { setHidden(true); });
  $("side-open").addEventListener("click", function () { if (window.innerWidth > 760) setHidden(false); else setSide(true); });
  $("scrim").addEventListener("click", function () { setSide(false); });

  /* External links (token pages) open in the user's default browser. The app window is an Edge --app window,
     which would keep them inside Edge; the local server opens them instead (known service pages only). */
  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest('a[target="_blank"]');
    if (!a || !/^https:\/\//.test(a.href) || e.defaultPrevented) return;
    e.preventDefault();
    api("POST", "/open-url", { url: a.href }).catch(function () { window.open(a.href, "_blank", "noopener"); });
  });


  /* ---------- help (?) : short design notes for users ---------- */
  var HELP = {
    auto: { title: "자동 선택은 어떻게 동작하나요?", body: [
      ["목적", "여러 AI를 연결해 두면, 질문마다 알맞은 AI를 JetAI가 대신 골라 줍니다. 모델 이름을 몰라도 됩니다."],
      ["1단계 · 질문 파악(아비터)", "질문의 단어와 길이를 보고 종류를 정합니다: 코드(코드·에러·파일), 일상 질문(짧은 질문·번역), 심층 조사·분석(비교·조사·보고서·여러 항목·긴 글). 난이도(가벼움·보통·높음)도 함께 정합니다. 이 판단은 규칙으로만 하므로 토큰이 들지 않습니다."],
      ["2단계 · 모델 고르기(라우터)", "모델 이름으로 성능 등급(가벼움·균형·고성능)과 특기(코드·추론)를 짐작해, 질문에 맞는 모델을 고릅니다. 코드 질문에는 코드 모델, 어려운 조사에는 고성능·추론 모델, 가벼운 질문에는 작고 빠른 모델입니다. 비슷하면 내 PC 모델을 먼저, 그다음 덜 쓴 모델을 골라 무료 한도를 나눠 씁니다."],
      ["3단계 · 막히면 넘기기", "한도(429)나 오류가 나면 그 모델은 잠시(1분, 반복되면 10분) 쉬게 하고 다음으로 맞는 모델이 이어서 답합니다."],
      ["안전", "요금이 나올 수 있는 모델과 동의하지 않은 서비스는 후보가 아닙니다. 답변 위에 '자동 선택 — 종류 · 난이도 → 모델'이 표시됩니다."],
      ["한계", "이름으로 성능을 짐작하므로 틀릴 수 있습니다. 마음에 안 들면 모델 선택 메뉴에서 직접 고르거나 '다시 생성'을 누르세요."]] },
    continuity: { title: "모델이 바뀌어도 이어서 작업하나요?", body: [
      ["같은 대화 안에서는 이어집니다", "대화 내용은 JetAI가 이 PC에 저장하고, 질문할 때마다 최근 대화(약 24,000자)를 그때 답하는 모델에 함께 보냅니다. 그래서 모델이 바뀌어도 앞의 내용을 알고 답합니다."],
      ["대화가 아주 길어지면", "오래된 부분은 보내지 못합니다. 대신 '처음 요청'과 생략된 요청 목록을 메모로 붙여 목표를 잃지 않게 합니다. 이전 답변을 요약해 주지는 않습니다."],
      ["다른 대화와는 공유되지 않습니다", "새 대화를 열면 이전 대화를 기억하지 않습니다. 이어서 할 작업은 같은 대화에서 계속하세요. 여러 모델이 한 문서를 같이 쓰는 '보드'는 문서(.md)를 통해 정보를 공유합니다."],
      ["참고", "내용은 선택된 서비스로 전송됩니다. 클라우드 서비스는 연결할 때 동의한 곳만 쓰입니다."]] },
    code: { title: "코드 작업은 어떻게 안전한가요?", body: [
      ["무엇", "내 프로젝트의 파일을 읽고 고치는 코딩 에이전트(OpenCode)를 JetAI가 대신 실행하고 관리합니다. 채팅이 아니라 실제로 파일을 바꾸는 작업입니다."],
      ["복사본에서만", "프로젝트의 마지막 커밋을 일회용 git worktree로 복사해 그 안에서만 작업합니다. 내 폴더는 건드리지 않고, 끝나면 바뀐 내용(diff)을 보여 줍니다. 마음에 들면 '적용'(커밋은 하지 않음), 아니면 '버리기'."],
      ["권한", "에이전트는 복사본 안에서 읽기·검색·수정만 하고, 셸 명령은 허용 목록(git 상태·diff, 테스트 실행 등)만 됩니다. 인터넷 가져오기와 복사본 밖 폴더 접근은 막혀 있고, 내 PC의 OpenCode 설정·플러그인·유료 계정 키는 전달되지 않습니다(JetAI 전용 빈 설정으로 실행)."],
      ["모델과 비용", "무료·로컬 모델만 기본 허용이고, 요금이 나올 수 있는 모델은 비용 확인 체크가 필요합니다. OpenCode 자체 모델은 이름이 -free인 것만 됩니다. 자동은 코드 작업에 충분한 성능의 모델만 고릅니다."],
      ["한계", "작은 로컬 모델(약 15B 이하)은 파일을 고치는 도구 호출을 자주 틀려 작업이 실패합니다(이 PC에서 확인). 어려운 작업은 큰 모델이나 클라우드 무료 모델을 고르세요. 한 번에 작업 하나만 실행하고 20분이 넘으면 중단합니다."]] },
    remote: { title: "폰으로 원격 제어하면 어떻게 되나요?", body: [
      ["무엇", "폰의 텔레그램에서 JetAI 봇에게 글을 보내면 PC의 JetAI가 자동 선택으로 답하고, 작업 목록·상태·사용량을 보여 주고, 작업을 승인·취소·재실행할 수 있습니다."],
      ["안전", "PC가 텔레그램 서버로 밖으로만 연결해서 포트를 열지 않습니다. 처음 코드를 보낸 내 계정 하나만 주인이 되고, 다른 사람과 그룹 대화에는 아무 답도 하지 않습니다. 봇 토큰은 저장소 밖의 비공개 파일에만 저장합니다."],
      ["실행 승인", "작업 승인·재실행은 /yes 로 한 번 더 확인해야 하고, 코드 작업은 파일을 바꿀 수 있다는 경고가 붙습니다. JetAI를 켜기 전에 보낸 명령이나 2분 넘은 메시지는 실행하지 않습니다."],
      ["알림", "새로 승인·검토가 필요하거나 실패한 작업은 1분에 한 번 묶어서 알려 줍니다."],
      ["주의", "대화가 텔레그램 서버를 거치며 종단간 암호화가 아닙니다. 민감한 내용은 보내지 마세요. 끄려면 설정에서 '연결 해제'를 누르면 토큰도 지워집니다."]] },
    engine: { title: "내장 로컬 엔진이 뭔가요?", body: [
      ["무엇", "Ollama 같은 별도 프로그램 없이, JetAI가 직접 AI 모델 파일을 받아 이 PC에서 돌립니다. 인터넷 서비스가 아니므로 한도·요금이 없고 내용이 PC 밖으로 나가지 않습니다."],
      ["무엇을 받나요", "① 실행 엔진 llama.cpp(약 25~40MB, 그래픽 카드가 있으면 Vulkan 빌드) ② 모델 파일 1개(0.4~9GB). 출처는 GitHub(ggml-org/llama.cpp)와 Hugging Face뿐이고, 받기 전에 정확한 크기를 보여 드리며 확인을 눌러야만 시작합니다."],
      ["안전", "받은 파일은 공개된 SHA-256 값과 맞는지 검사합니다. 압축은 JetAI 폴더 안으로만 풀립니다. 엔진은 127.0.0.1에서만 열리고 관리자 권한을 쓰지 않으며, JetAI를 끄면 같이 꺼집니다. 설정에서 언제든 지울 수 있습니다."],
      ["성능", "그래픽 메모리가 모델 크기보다 크면 빠르게, 아니면 CPU로 느리게 돌아갑니다. 이 PC에 맞는 모델이 '추천'으로 표시됩니다. 한 번에 모델 하나만 실행하며 자동 선택은 실행 중인 모델을 로컬 모델로 취급합니다."],
      ["한계", "작은 모델은 클라우드의 큰 모델보다 답이 약합니다. 어려운 조사·긴 문서는 자동 선택이 클라우드 무료 모델로 보냅니다."]] },
    newmodels: { title: "새 모델은 어떻게 추가되나요?", body: [
      ["언제", "JetAI를 실행할 때마다(10분에 한 번까지) 연결한 서비스의 모델 목록을 다시 가져와 지난번과 비교합니다. 설정에서 '지금 확인'으로 바로 해 볼 수도 있습니다."],
      ["무엇을 추가하나요", "새로 생긴 모델 중 무료·로컬이고 채팅이 되는 모델만, 서비스마다 최대 2개(서로 다른 종류 우선)를 등록합니다. 요금이 날 수 있는 모델, 음성·이미지·임베딩·미리보기 모델은 추가하지 않습니다. 처음 확인할 때는 목록만 기억하고 추가하지 않습니다."],
      ["어떤 모델인지는 어떻게 아나요", "OpenRouter의 공개 모델 목록(키 불필요, 하루에 한 번만 받음)에서 같은 이름의 모델을 찾아 벤치마크 순위·추론 지원·문맥 길이·설명을 가져옵니다. 목록에 없는 모델은 이름으로 짐작합니다. 어떤 정보도 내 PC 밖으로 보내지 않습니다(목록을 받기만 함)."],
      ["다운로드는 하지 않습니다", "내 PC에 모델 파일을 받지 않습니다. 서비스가 이미 제공하는 이름을 등록할 뿐입니다. 끄려면 설정에서 체크를 해제하세요."]] },
    free: { title: "무료 서비스는 어떻게 구분하나요?", body: [
      ["무료로 보는 경우", "내 PC 모델, 이름이 -free 또는 :free로 끝나는 모델, 그리고 '무료 키'로 연결한 Gemini·Groq 등 무료 티어 서비스의 모델입니다."],
      ["주의", "결제 수단(카드)을 연결한 키는 한도를 넘으면 요금이 나올 수 있습니다. 무료로만 쓰려면 결제를 연결하지 않은 키를 쓰세요."],
      ["한도", "무료 한도는 서비스가 자주 바꿉니다. 한도에 걸리면 자동 선택이 다른 모델로 넘어갑니다."]] }
  };
  function openHelp(topic) {
    var t = HELP[topic]; if (!t) return;
    var box = $("helpbox"); box.textContent = ""; box.hidden = false;
    var card = h("div", { class: "card help-card", role: "dialog", "aria-modal": "true", "aria-label": t.title },
      h("header", {}, h("h2", { text: t.title }), h("button", { class: "icon", "aria-label": "닫기", text: "\u2715", onclick: closeHelp })));
    t.body.forEach(function (row) { card.appendChild(h("div", { class: "help-row" }, h("b", { text: row[0] }), h("p", { text: row[1] }))); });
    var tabs = h("div", { class: "help-tabs" }, Object.keys(HELP).map(function (k) {
      return h("button", { type: "button", class: "btn mini" + (k === topic ? " on" : ""), text: HELP[k].title.replace(/\?$/, ""), onclick: function () { openHelp(k); } }); }));
    card.appendChild(tabs);
    box.appendChild(card);
  }
  function closeHelp() { $("helpbox").hidden = true; }
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-help]");
    if (b) { e.preventDefault(); openHelp(b.dataset.help); return; }
    if (e.target === $("helpbox")) closeHelp();
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !$("helpbox").hidden) closeHelp(); });

  /* ---------- state ---------- */
  function modelOptions() {
    var o = [];
    state.providers.forEach(function (p) { p.models.forEach(function (m) { o.push({ ref: p.name + "/" + m, provider: p, model: m }); }); });
    return o;
  }
  function isFree(m) { return /(-free|:free)$/.test(m); }
  // Free also when the service was connected as an all-free tier (Gemini/Groq keys without billing) or marks names free.
  function isFreeIn(p, m) {
    return isFree(m) || !!(p && p.free_all) || !!(p && (p.free_match || []).some(function (f) { return String(m).toLowerCase().indexOf(f) >= 0; }));
  }
  // Same idea as the server's modelpick.capability(): a hint from the name, not a benchmark.
  function capText(m) {
    var n = m.toLowerCase(), size = (/(\d+(?:\.\d+)?)b\b/.exec(n) || [])[1], level = 2;
    if (/(^|[^a-z])(lite|mini|nano|small|instant|haiku|tiny|fast)([^a-z]|$)/.test(n)) level = 1;
    else if (/(^|[^a-z])(pro|large|opus|sonnet|maverick|ultra|max|plus|thinking|reason)([^a-z]|$)/.test(n) || size >= 60) level = 3;
    else if (/(^|[^a-z])flash([^a-z]|$)/.test(n)) level = 2;
    else if (size && size <= 15) level = 1;
    var parts = [level === 1 ? "가볍고 빠름" : level === 3 ? "고성능" : "균형"];
    if (/cod(er|estral|ex)|devstral/.test(n)) parts.push("코드");
    if (/(^|[^a-z])(r1|reason\w*|thinking|qwq|o1|o3)([^a-z]|$)/.test(n)) parts.push("추론");
    return parts.join(" · ");
  }
  function catalogFree(meta) { return { free_all: meta && meta.free === "all", free_match: (meta && meta.free_match) || [] }; }
  var AUTO = "auto";
  function hasAutoModel() {   // 'auto' routes among connected local/free models only (the server enforces it)
    return modelOptions().some(function (o) { return o.provider.policy === "local" || (o.provider.consented && isFreeIn(o.provider, o.model)); });
  }
  function modelLabel(ref) {
    if (ref === AUTO) return hasAutoModel() ? "자동 (로컬·무료 우선)" : "";
    var o = modelOptions().filter(function (x) { return x.ref === ref; })[0];
    return o ? o.provider.label + " · " + o.model : "";
  }
  function pickDefaultModel() {
    var opts = modelOptions();
    if (state.model === AUTO && hasAutoModel()) return;
    if (state.model && opts.some(function (o) { return o.ref === state.model; })) return;
    // The default is 'auto': the user connects services and just chats; the router picks the model.
    state.model = hasAutoModel() ? AUTO : (opts[0] ? opts[0].ref : "");
  }
  async function loadState() {
    var s = await api("GET", "/state");
    state.providers = s.providers; state.catalog = s.catalog; state.catalog_status = s.catalog_status; state.onboarded = s.onboarded;
    state.settings = s.settings || { auto_add_models: true };
    if (!state.feedTried) {                        // a (re)load looks for newly listed free services; the server throttles it
      state.feedTried = true;
      api("POST", "/catalog/refresh", {}).then(function (r) {
        if (r.added && r.added.length) { state.catalog = r.catalog; toastLong("새 무료 서비스 " + r.added.length + "개를 찾았습니다 (서비스 연결 화면의 '새로 발견된 서비스'): " + r.added.slice(0, 3).join(", ")); }
      }).catch(function () { /* offline: nothing to say */ });
    }
    pickDefaultModel(); renderModelBtn(); renderWelcome();
    if ((s.news || []).length) {                       // new free/local models were added at start-up: say so once
      var lines = [];
      s.news.forEach(function (n) { n.added.forEach(function (m) { lines.push(n.service + "/" + m); }); });
      state.news = s.news;
      toastLong("새 무료·로컬 모델을 추가했습니다: " + lines.slice(0, 4).join(", ") + (lines.length > 4 ? " 외 " + (lines.length - 4) + "개" : ""));
      api("POST", "/news/ack", {}).catch(function () { /* shown again next time */ });
    }
  }
  function toastLong(message) { toast(message); clearTimeout(toast.timer); toast.timer = setTimeout(function () { $("toast").hidden = true; }, 7000); }
  async function loadChats() { state.chats = (await api("GET", "/chats")).chats; pickDefaultModel(); renderChats(); renderModelBtn(); }

  /* ---------- usage chip ---------- */
  function fmt(n) { return n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? (n / 1e3).toFixed(1) + "k" : String(n); }
  async function loadUsage() {
    try {
      var u = await api("GET", "/usage"), t = u.today, chip = $("usage-chip");
      var cheap = t.local.tokens + t.free.tokens, other = t.other.tokens;
      chip.hidden = !(cheap || other);
      chip.textContent = "오늘 무료·로컬 " + fmt(cheap) + " · 그 밖 " + fmt(other) + " 토큰";
      chip.title = "내 PC나 -free/:free 모델이 처리한 토큰과 그 밖의 모델이 처리한 토큰. 사용량 탭에서 자세히 볼 수 있습니다.";
    } catch (e) { /* the chip is optional */ }
  }

  /* ---------- sidebar chat list ---------- */
  function groupName(ts) {
    var now = new Date(), d = new Date(ts * 1000), start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    if (d.getTime() >= start) return "오늘";
    if (d.getTime() >= start - 86400000) return "어제";
    if (d.getTime() >= start - 6 * 86400000) return "지난 7일";
    return "이전";
  }
  function renderChats() {
    var list = $("chatlist"), lastGroup = "";
    list.textContent = "";
    state.chats.forEach(function (c) {
      var g = groupName(c.updated);
      if (g !== lastGroup) { list.appendChild(h("div", { class: "group", text: g })); lastGroup = g; }
      var item = h("div", { class: "chat-item" + (state.chat && state.chat.id === c.id ? " active" : ""), role: "listitem", "data-id": c.id },
        h("button", { class: "t", text: c.title, title: c.title, onclick: function () { openChat(c.id); } }),
        h("span", { class: "acts" },
          h("button", { title: "이름 변경", "aria-label": "이름 변경", text: "✎", onclick: function (e) { e.stopPropagation(); renameChat(c, item); } }),
          h("button", { title: "삭제", "aria-label": "삭제", text: "✕", onclick: function (e) { e.stopPropagation(); deleteChat(c); } })));
      list.appendChild(item);
    });
  }
  function renameChat(c, item) {
    var input = h("input", { value: c.title, maxlength: "80", "aria-label": "대화 이름" });
    item.textContent = ""; item.appendChild(input); input.focus(); input.select();
    var finished = false;
    async function commit(save) {
      if (finished) return; finished = true;
      if (save && input.value.trim() && input.value.trim() !== c.title) {
        try { await api("PATCH", "/chats/" + c.id, { title: input.value.trim() }); if (state.chat && state.chat.id === c.id) state.chat.title = input.value.trim(); }
        catch (e) { toast(e.message); }
      }
      loadChats();
    }
    input.addEventListener("keydown", function (e) { if (e.key === "Enter" && !e.isComposing) commit(true); else if (e.key === "Escape") commit(false); });
    input.addEventListener("blur", function () { commit(true); });
  }
  async function deleteChat(c) {
    if (!window.confirm("'" + c.title + "' 대화를 삭제할까요?")) return;
    try { await api("DELETE", "/chats/" + c.id); } catch (e) { toast(e.message); return; }
    if (state.chat && state.chat.id === c.id) newChat();
    loadChats();
  }

  /* ---------- views ---------- */
  /* ---------- discovery inbox (D0: shows cards, never fetches or installs) ---------- */
  var KIND_NAMES = { model: "모델", service: "서비스", news: "소식" }, FIT_NAMES = { gpu: "이 PC GPU에서 실행 가능", partial: "일부만 GPU(느릴 수 있음)", cpu: "CPU로만 실행(느림)", no: "이 PC에서는 어려움" };
  async function renderDiscovery() {
    var root = $("disc-root"); if (!root) return;
    var d; try { d = await api("GET", "/discovery"); } catch (e) { root.textContent = e.message; return; }
    root.textContent = "";
    var sw = h("input", { type: "checkbox", id: "disc-enabled", checked: d.enabled, onchange: async function (e) { try { d = await api("POST", "/discovery/enable", { enabled: e.target.checked }); } catch (err) { toast(err.message); } renderDiscovery(); } });
    root.appendChild(h("h2", { text: "발견" }));
    root.appendChild(h("p", { class: "sub", text: "새로 나온 모델·서비스·소식을 찾아 **추천만** 합니다. 설치·등록·결제는 항상 사용자가 확인한 뒤에만 이뤄집니다. 기본은 꺼져 있습니다." }));
    root.appendChild(h("label", { class: "check" }, sw, h("span", { text: " 발견 도우미 켜기" })));
    root.appendChild(h("label", { class: "check", title: "도우미가 켜져 있을 때만, 마지막 확인이 일주일 지났으면 자동으로 한 번 읽어 카드를 추가합니다. 설치·다운로드는 하지 않습니다." },
      h("input", { type: "checkbox", id: "disc-weekly", checked: !(state.settings && state.settings.discovery_weekly === false),
        onchange: async function (e) { try { state.settings = (await api("POST", "/settings", { discovery_weekly: e.target.checked })).settings; } catch (err) { toast(err.message); } } }),
      h("span", { text: " 일주일에 한 번 자동으로 찾기" })));
    root.appendChild(h("button", { type: "button", class: "btn", id: "disc-run", text: d.running ? "찾는 중…" : "지금 찾아보기", disabled: !d.enabled || d.running,
      title: d.enabled ? "Hugging Face의 최근 GGUF 모델 후보와 GitHub의 OpenCode·Ollama 새 릴리스를 읽기만 합니다(익명 요청, 설치·다운로드 없음)." : "먼저 위에서 발견 도우미를 켜세요.",
      onclick: async function () {
        var b = this; b.disabled = true; b.textContent = "찾는 중…";
        try {
          await api("POST", "/discovery/run", {});
          for (var i = 0; i < 40; i++) { await new Promise(function (r) { setTimeout(r, 2000); }); var s = await api("GET", "/discovery"); if (!s.running) break; }
        } catch (e) { toast(e.message); }
        renderDiscovery();
      } }));
    var lr = d.last_run;
    root.appendChild(h("p", { class: "sub", id: "disc-last", text: lr ? "마지막 확인 " + new Date(lr.at * 1000).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "numeric", minute: "2-digit" }) + (lr.error ? " — " + lr.error : " — 새 카드 " + lr.added + "개") : "아직 찾아본 적이 없습니다. 카드의 모델은 이 PC에서 돌아갈지를 메모리 추정으로만 표시하며 성능은 측정되지 않았습니다." }));
    if (!d.cards.length) { root.appendChild(h("p", { class: "sub", id: "disc-empty", text: "아직 추천이 없습니다." })); }
    d.cards.forEach(function (c) {
      var facts = Object.keys(c.facts || {}).map(function (k) { return h("span", { class: "tag", text: k + ": " + c.facts[k] }); });
      root.appendChild(h("div", { class: "cw-card disc-card" },
        h("p", {}, h("span", { class: "tag", text: KIND_NAMES[c.kind] || c.kind }), " ", h("b", { text: c.title }), c.status === "new" ? h("span", { class: "tag", text: " 새" }) : ""),
        c.summary ? h("p", { class: "sub", text: (c.ai_summary ? "AI 요약(틀릴 수 있음): " : "") + c.summary }) : "",
        h("div", { class: "disc-facts" }, facts),
        c.fit ? h("p", { class: "sub", text: FIT_NAMES[c.fit] || "" }) : "",
        h("p", { class: "sub", text: c.measured ? "JetAI가 측정한 결과가 있습니다." : "성능은 측정되지 않았습니다." }),
        h("div", { class: "cw-actions" },
          h("a", { class: "btn", href: c.source_url, target: "_blank", rel: "noopener noreferrer", text: "출처 보기" }),
          c.kind === "model" && c.facts && c.facts["파일"] ? h("button", { type: "button", class: "btn primary", text: "추가(크기 확인)", title: "정확한 크기를 보여 드리고 확인하신 뒤에만 받습니다.",
            onclick: async function () {
              var b = this; b.disabled = true;
              try {
                var plan = await api("POST", "/engine/register", { repo: c.id.slice(3), file: c.facts["파일"] });
                var gib = function (n) { return (n / 1073741824).toFixed(2) + " GiB"; };
                var lines = plan.items.map(function (it) { return "· " + it.name + " — " + it.bytes.toLocaleString() + "바이트 (" + gib(it.bytes) + ") · " + it.source; });
                var ok = window.confirm("이 모델을 내려받을까요?\n\n" + c.title + " · 라이선스 " + plan.license + "\n" + lines.join("\n") + "\n\n합계 " + plan.total_bytes.toLocaleString() + "바이트 (" + gib(plan.total_bytes) + ")"
                  + (plan.enough_disk ? "" : "\n\n⚠ 디스크 여유 공간이 부족합니다.") + "\n\n내려받은 뒤에도 자동으로 실행하지 않습니다. 이 모델은 JetAI가 측정하기 전이라 성능을 알 수 없습니다.");
                if (!ok) { b.disabled = false; return; }
                await api("POST", "/engine/install", { model: plan.model, confirm_bytes: plan.total_bytes });
                toast("받기를 시작했습니다. 진행은 설정의 내장 엔진에서 보입니다.");
              } catch (e) { toast(e.message); }
              b.disabled = false;
            } }) : null,
          h("button", { type: "button", class: "btn", text: "무시", onclick: async function () { try { await api("POST", "/discovery/dismiss", { id: c.id }); } catch (err) { toast(err.message); } renderDiscovery(); } }))));
    });
    if (d.unread) { try { await api("POST", "/discovery/read", {}); } catch (e) { /* the dot may stay until next time */ } }
    discoveryDot(0);
  }
  function discoveryDot(count) { var dot = $("disc-dot"); if (dot) { dot.hidden = !count; dot.textContent = count ? String(count) : ""; } }
  async function pollDiscovery() { try { var d = await api("GET", "/discovery"); discoveryDot(d.unread); } catch (e) { /* the dot is a hint only */ } }
  function showView(name) {
    state.view = name;
    VIEWS.forEach(function (v) {
      var el = $("view-" + v);
      if (!el) return;
      el.hidden = (v !== name);
      if (v === name && el.tagName === "IFRAME" && el.getAttribute("src") === "about:blank") el.setAttribute("src", el.dataset.src);
    });
    Array.prototype.forEach.call(document.querySelectorAll(".side-nav [data-view]"), function (b) { b.classList.toggle("active", b.dataset.view === name); });
    $("modelbtn").parentNode.style.visibility = name === "chat" ? "visible" : "hidden";
    if (name === "terminal") startTerminal();
    if (name === "chat") { loadState(); loadUsage(); }
    if (name === "digest") digestInit();
    document.body.dataset.view = name;
    if (name === "code") renderCode();
    if (name === "discovery") renderDiscovery();
    if (name === "settings") renderSettings();
    if (window.innerWidth <= 760) setSide(false);
  }
  Array.prototype.forEach.call(document.querySelectorAll(".side-nav [data-view]"), function (b) { b.addEventListener("click", function () { showView(b.dataset.view); }); });

  /* ---------- chat view ---------- */
  function renderModelBtn() {
    var label = modelLabel(state.model);
    var btn = $("modelbtn");
    btn.firstChild.nodeValue = label || (state.onboarded ? "모델 선택" : "서비스 연결 필요");
  }
  function renderWelcome() {
    var w = $("welcome");
    w.textContent = "";
    if (state.chat && state.chat.messages.length) { w.hidden = true; return; }
    w.hidden = false;
    if (!state.onboarded) {
      w.append(h("h1", { text: "시작해 볼까요?" }),
        h("p", { text: "먼저 AI 서비스를 하나 연결하세요. 토큰 입력과 테스트까지 1분이면 끝납니다." }),
        h("button", { class: "cta", text: "서비스 연결하기", onclick: function () { openWizard(); } }));
      return;
    }
    w.append(h("h1", { text: "무엇을 도와드릴까요?" }), h("p", { text: state.model === AUTO ? "알맞은 AI를 자동으로 골라 답합니다. 한도가 차거나 오류가 나면 다른 무료·로컬 모델로 알아서 넘어갑니다." : (modelLabel(state.model) ? modelLabel(state.model) + " 모델로 대화합니다." : "") }),
      h("div", { class: "chips" }, SUGGESTIONS.map(function (s) {
        return h("button", { class: "chip", type: "button", text: s, onclick: function () { $("input").value = s; autosize(); updateSend(); $("input").focus(); } });
      })));
  }
  function scrollDown(force) {
    var s = $("scroller");
    if (force || s.scrollHeight - s.scrollTop - s.clientHeight < 140) s.scrollTop = s.scrollHeight;
  }
  function typing() { return h("span", { class: "typing", "aria-label": "답변 작성 중" }, h("b"), h("b"), h("b")); }
  function fillActions(actions, getText, withRegen, modelRef) {
    actions.textContent = "";
    actions.appendChild(h("button", { type: "button", text: "복사", onclick: function () { copyText(getText()); } }));
    if (withRegen) actions.appendChild(h("button", { type: "button", text: "다시 생성", "data-regen": "1", onclick: function () { regenerate(); } }));
    if (modelRef) actions.appendChild(h("span", { class: "who", "data-model": modelRef, title: "이 답변을 만든 모델", text: modelRef }));
  }
  function assistantNode(content, withRegen, modelRef) {
    var md = h("div", { class: "md" }), actions = h("div", { class: "actions" }), notices = h("div", { class: "notices" });
    md.innerHTML = renderMarkdown(content);
    fillActions(actions, function () { return content; }, withRegen, modelRef);
    return { md: md, actions: actions, notices: notices,
             wrap: h("div", { class: "msg assistant" }, h("div", { class: "avatar", text: "A" }), h("div", { class: "col" }, notices, md, actions)) };
  }
  function renderMessages() {
    var box = $("messages"), msgs = state.chat ? state.chat.messages : [];
    box.textContent = "";
    msgs.forEach(function (m, i) {
      if (m.role === "user") box.appendChild(h("div", { class: "msg user" }, h("div", { class: "bubble", text: m.content })));
      else box.appendChild(assistantNode(m.content, i === msgs.length - 1, m.model).wrap);
    });
    renderWelcome(); scrollDown(true);
  }
  function newChat() {
    state.chat = null; state.streaming = null;
    $("messages").textContent = ""; renderChats(); renderWelcome(); showView("chat");
    $("input").focus();
  }
  async function openChat(id) {
    if (state.streaming) { toast("응답을 만드는 중입니다. 중지하거나 끝날 때까지 기다려 주세요."); return; }
    try { state.chat = await api("GET", "/chats/" + id); } catch (e) { toast(e.message); return; }
    if (state.chat.model && ((state.chat.model === AUTO && hasAutoModel()) || modelOptions().some(function (o) { return o.ref === state.chat.model; }))) state.model = state.chat.model;
    renderModelBtn(); renderMessages(); renderChats(); showView("chat");
  }
  function updateSend() {
    var b = $("send"), busy = !!state.streaming;
    b.classList.toggle("stop", busy);
    b.textContent = busy ? "■" : "↑";
    b.setAttribute("aria-label", busy ? "중지" : "보내기");
    b.disabled = !busy && !$("input").value.trim();
  }
  function autosize() { var t = $("input"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 200) + "px"; }

  async function send(text, regenerateMode) {
    if (state.streaming) return;
    if (!state.model) { openWizard(); return; }
    var chat = state.chat;
    if (!chat) {
      try { chat = state.chat = await api("POST", "/chats", { model: state.model }); } catch (e) { toast(e.message); return; }
    }
    $("welcome").hidden = true;
    var box = $("messages");
    if (regenerateMode) {
      if (chat.messages.length && chat.messages[chat.messages.length - 1].role === "assistant") chat.messages.pop();
      if (box.lastChild && box.lastChild.classList.contains("assistant")) box.removeChild(box.lastChild);
    } else {
      chat.messages.push({ role: "user", content: text });
      box.appendChild(h("div", { class: "msg user" }, h("div", { class: "bubble", text: text })));
    }
    Array.prototype.forEach.call(box.querySelectorAll("[data-regen]"), function (b) { b.remove(); });   // only the newest answer can be regenerated
    var node = assistantNode("", false);
    node.md.textContent = ""; node.md.appendChild(typing());
    node.actions.hidden = true;
    box.appendChild(node.wrap); scrollDown(true);
    var ctrl = new AbortController();
    state.streaming = { ctrl: ctrl }; updateSend(); renderChats();
    var acc = "", failed = "", aborted = false, title = "", served = "", raf = 0;
    function paint() { raf = 0; node.md.innerHTML = renderMarkdown(acc); scrollDown(false); }
    try {
      var res = await fetch("/api/v1/chats/" + chat.id + "/send", { method: "POST", signal: ctrl.signal,
        headers: { "X-CSRF-Token": CSRF, "Content-Type": "application/json" },
        body: JSON.stringify(regenerateMode ? { regenerate: true, model: state.model, fallback: state.fallback } : { content: text, model: state.model, fallback: state.fallback }) });
      if (!res.ok) { var j = {}; try { j = await res.json(); } catch (e) { /* ignore */ } throw new Error(j.error || ("HTTP " + res.status)); }
      for await (var ev of sseEvents(res)) {
        if (ev.event === "meta") {
          loadChats();            // the chat now has its title: show it in the sidebar right away
          if (ev.data.why) node.notices.appendChild(h("div", { class: "notice route", text: "\u2699 자동 선택 \u2014 " + ev.data.why }));
        }
        else if (ev.event === "delta") { acc += ev.data.t; if (!raf) raf = requestAnimationFrame(paint); }
        else if (ev.event === "notice") node.notices.appendChild(h("div", { class: "notice", text: "\u21AA " + ev.data.message }));
        else if (ev.event === "done") { title = ev.data.title; served = ev.data.model || ""; }
        else if (ev.event === "error") failed = ev.data.message;
      }
    } catch (e) {
      if (e.name === "AbortError") aborted = true; else failed = e.message;
    } finally {
      if (raf) cancelAnimationFrame(raf);
      state.streaming = null;
    }
    if (aborted && acc) acc += "\n\n*(중단됨)*";
    var finalText = acc;
    // Finish in place: replacing the node would drop a click or text selection made at the last moment.
    node.md.innerHTML = acc ? renderMarkdown(acc) : (aborted ? "(중단됨)" : "");
    if (acc) {
      chat.messages.push({ role: "assistant", content: acc });
      fillActions(node.actions, function () { return finalText; }, true, served);
      node.actions.hidden = false;
    }
    if (failed) {
      node.wrap.querySelector(".col").appendChild(h("div", { class: "err" }, failed + " ",
        h("button", { class: "btn", type: "button", text: "다시 시도", onclick: function () { regenerate(); } })));
    }
    if (title) chat.title = title;
    updateSend(); await loadChats(); loadUsage();
  }
  function regenerate() { if (!state.streaming && state.chat) send(null, true); }

  $("composer").addEventListener("submit", function (e) {
    e.preventDefault();
    if (state.streaming) { state.streaming.ctrl.abort(); return; }
    var text = $("input").value.trim();
    if (!text) return;
    $("input").value = ""; autosize(); updateSend(); send(text, false);
  });
  $("input").addEventListener("input", function () { autosize(); updateSend(); });
  $("input").addEventListener("keydown", function (e) {
    // Korean/Japanese IME: Enter that commits a composition must not send the message.
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); if (!state.streaming) $("composer").requestSubmit(); }
  });
  $("newchat").addEventListener("click", newChat);
  $("messages").addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("button[data-copy]");
    if (b) copyText(b.closest(".codebox").querySelector("pre").textContent);
  });

  /* ---------- digest view ---------- */
  var digest = { running: null };
  function digestTier(ref) {
    if (ref === AUTO) return "free";                // auto only ever picks local/free models
    var o = modelOptions().filter(function (x) { return x.ref === ref; })[0];
    if (!o) return "other";
    if (o.provider.policy === "local" || isFreeIn(o.provider, o.model)) return "free";
    return "other";
  }
  function digestEstimate() {
    var n = $("dg-text").value.length;
    $("dg-est").textContent = n ? "약 " + Math.max(1, Math.ceil(n / 8000)) + "개 조각 · 요청 " + Math.max(1, Math.ceil(n / 8000)) + "회 이상" + ($("dg-prefilter") && $("dg-prefilter").checked ? " (전처리로 더 줄 수 있음)" : "") : "";
    $("dg-cost-row").hidden = digestTier($("dg-model").value) !== "other";
  }
  function digestInit() {
    var sel = $("dg-model"), prev = sel.value;
    sel.textContent = "";
    if (hasAutoModel()) sel.appendChild(h("option", { value: AUTO, text: "자동 (로컬·무료 우선)" }));
    modelOptions().forEach(function (o) { sel.appendChild(h("option", { value: o.ref, text: o.ref + (o.provider.policy === "local" ? " (로컬)" : isFreeIn(o.provider, o.model) ? " (무료)" : "") })); });
    if (prev) sel.value = prev; else if (state.model) sel.value = state.model;
    if (!sel.value && sel.options.length) sel.selectedIndex = 0;   // never leave the select empty (e.g. chat model is "auto")
    digestEstimate();
  }
  async function digestRun() {
    if (digest.running) { digest.running.abort(); return; }
    var text = $("dg-text").value;
    if (!text.trim()) { toast("압축할 글을 입력하세요"); return; }
    if (!$("dg-model").value) { openWizard(); return; }
    var ctrl = new AbortController(), failed = "", result = null;
    digest.running = ctrl;
    $("dg-run").textContent = "중단"; $("dg-result").hidden = true; $("dg-notices").textContent = ""; $("dg-status").textContent = "시작합니다…";
    try {
      var res = await fetch("/api/v1/digest", { method: "POST", signal: ctrl.signal,
        headers: { "X-CSRF-Token": CSRF, "Content-Type": "application/json" },
        body: JSON.stringify({ text: text, model: $("dg-model").value, focus: $("dg-focus").value, confirm_cost: $("dg-cost").checked, prefilter: $("dg-prefilter").checked, fallback: state.fallback }) });
      if (!res.ok) { var j = {}; try { j = await res.json(); } catch (e) { /* ignore */ } throw new Error(j.error || ("HTTP " + res.status)); }
      for await (var ev of sseEvents(res)) {
        if (ev.event === "meta") $("dg-status").textContent = "조각 " + ev.data.chunks + "개 처리 중…";
        else if (ev.event === "progress") $("dg-status").textContent = ev.data.done + "/" + ev.data.total + " 처리됨 (패스 " + ev.data.pass + ")";
        else if (ev.event === "notice") $("dg-notices").appendChild(h("div", { class: "notice", text: "\u21AA " + ev.data.message }));
        else if (ev.event === "done") result = ev.data;
        else if (ev.event === "error") failed = ev.data.message;
      }
    } catch (e) { failed = e.name === "AbortError" ? "중단했습니다" : e.message; }
    digest.running = null; $("dg-run").textContent = "압축하기";
    $("dg-status").textContent = failed;
    if (result) {
      $("dg-out").textContent = result.digest; $("dg-result").hidden = false;
      $("dg-stats").textContent = "원문 ~" + result.before_tokens + " → ~" + result.after_tokens + " 토큰 (" + result.reduction_pct + "% 감소, 추정) · 요청 " + result.requests + "회 · " + result.model +
        (result.prefilter ? " · 전처리로 ~" + result.prefilter.tokens_before + " → ~" + result.prefilter.tokens_after + " 토큰" : "") +
        (result.still_long ? " · 아직 깁니다. 한 번 더 압축해 보세요." : "");
      $("dg-status").textContent = "";
    }
    loadUsage();
  }
  $("dg-run").addEventListener("click", digestRun);
  $("dg-text").addEventListener("input", digestEstimate);
  $("dg-model").addEventListener("change", digestEstimate);
  $("dg-copy").addEventListener("click", function () { copyText($("dg-out").textContent); });
  $("dg-file").addEventListener("change", function () {
    var f = $("dg-file").files[0];
    if (!f) return;
    if (f.size > 300000) { toast("300KB 이하 파일만 불러올 수 있습니다"); $("dg-file").value = ""; return; }
    var r = new FileReader();
    r.onload = function () { $("dg-text").value = String(r.result); digestEstimate(); };
    r.readAsText(f);
  });

  /* ---------- model picker ---------- */
  function closeMenu() { $("modelmenu").hidden = true; $("modelbtn").setAttribute("aria-expanded", "false"); }
  async function chooseModel(ref) {
    state.model = ref; renderModelBtn(); renderWelcome(); closeMenu();
    if (state.chat) { try { await api("PATCH", "/chats/" + state.chat.id, { model: ref }); } catch (er) { toast(er.message); } }
  }
  function renderMenu() {
    var menu = $("modelmenu");
    menu.textContent = "";
    if (hasAutoModel()) {
      menu.appendChild(h("button", { type: "button", role: "option", class: state.model === AUTO ? "sel" : "", "data-ref": AUTO, onclick: function () { chooseModel(AUTO); } },
        h("span", { text: "자동 (로컬·무료 우선)" }), h("span", { class: "tag free", text: "추천" })));
    }
    state.providers.forEach(function (p) {
      if (!p.models.length) return;
      menu.appendChild(h("div", { class: "mh" }, p.label, h("span", { class: "tag", text: p.policy === "local" ? "내 PC" : "클라우드" })));
      p.models.forEach(function (m) {
        var ref = p.name + "/" + m;
        menu.appendChild(h("button", { type: "button", role: "option", class: ref === state.model ? "sel" : "", "data-ref": ref, onclick: function () { chooseModel(ref); } },
          h("span", { text: m }), isFreeIn(p, m) ? h("span", { class: "tag free", text: "무료" }) : null));
      });
      menu.appendChild(h("button", { type: "button", class: "more", "data-more": p.name, text: "모델 더 보기…", onclick: function (e) { e.stopPropagation(); renderMoreModels(p); } }));
    });
    menu.appendChild(h("label", { class: "opt", id: "fallback-opt" },
      h("input", { type: "checkbox", id: "fallback-toggle", checked: state.fallback ? "checked" : false, onchange: function (e) { state.fallback = e.target.checked; store("fallback", state.fallback ? "1" : "0"); } }),
      h("span", { text: "한도·오류가 나면 다른 무료/로컬 모델로 자동 전환 (요금이 나올 수 있는 모델로는 넘어가지 않습니다)" })));
    menu.appendChild(h("button", { type: "button", class: "foot", text: "+ 서비스 연결 · 서비스 추가", onclick: function () { closeMenu(); openWizard(); } }));
    menu.appendChild(h("button", { type: "button", class: "foot", id: "open-accounts", text: "계정(키) 여러 개 관리", onclick: function () { closeMenu(); openAccounts(); } }));
  }


  /* ---------- code work: agent session screen (timeline + diff + composer) ---------- */
  var codeTimer = null, codeOpen = "", codeStick = true, codeBusy = false;
  var CODE_STATUS = { running: "실행 중", review: "검토 대기", nochange: "변경 없음", failed: "실패", cancelled: "취소됨", applied: "적용됨", undone: "되돌림", discarded: "버림" };
  var CODE_COMMANDS = [
    { name: "apply", alias: "적용", help: "변경을 내 폴더에 적용(커밋 안 함)" },
    { name: "undo", alias: "되돌리기", help: "적용했던 변경을 되돌리기" },
    { name: "check", alias: "검사", help: "빌드·테스트 실행" },
    { name: "review", alias: "리뷰", help: "다른 모델에게 코드 리뷰 받기" },
    { name: "cancel", alias: "취소", help: "실행 중인 작업 멈추기" },
    { name: "discard", alias: "버리기", help: "복사본과 변경 버리기(기록은 남김)" },
    { name: "new", alias: "새작업", help: "새 작업 입력으로 돌아가기" }
  ];
  function codeBadge(status) {
    return h("span", { class: "code-badge st-" + status, text: CODE_STATUS[status] || status });
  }
  function codeBuildShell() {
    var root = $("code-root"); if (!root || root.dataset.built) return;
    root.dataset.built = "1"; root.textContent = "";
    var datalist = h("datalist", { id: "code-repos" });
    var repo = h("input", { type: "text", id: "code-repo", list: "code-repos", "aria-label": "프로젝트 폴더", placeholder: "프로젝트 폴더 (git 저장소)  예: D:\\projects\\myapp", value: store("code-repo") || "" });
    var pickBtn = h("button", { type: "button", class: "btn", id: "code-pick", text: "폴더 선택…", title: "내 PC의 프로젝트 폴더를 골라 JetAI가 작업할 영역으로 지정합니다(그 폴더의 복사본에서만 작업합니다).",
      onclick: async function () { try { pickBtn.disabled = true; var r = await api("POST", "/projects/pick", {}); if (r.path) { repo.value = r.path; store("code-repo", r.path); } else toast("폴더를 고르지 않았습니다."); } catch (e) { toast(e.message); } finally { pickBtn.disabled = false; } } });
    var project = h("select", { id: "code-project", "aria-label": "프로젝트", title: "등록한 프로젝트를 고르거나, 새 git 폴더를 등록합니다. 작업은 항상 그 프로젝트의 복사본에서만 합니다." });
    project.onchange = function () { codeProjectChanged(); };
    var settingsBtn = h("button", { type: "button", class: "btn", id: "code-project-settings", text: "⚙", title: "프로젝트 설정", "aria-label": "프로젝트 설정", onclick: function () { if ($("code-project").value) renderProjectSettings($("code-project").value); } });
    var model = h("select", { id: "code-model", "aria-label": "모델" });
    var mode = h("select", { id: "code-mode", "aria-label": "방식", title: "에이전트: OpenCode가 도구를 써서 직접 고칩니다. 직접 모드: JetAI가 단계를 진행하고 모델은 글로 수정안만 씁니다(약한 모델에 적합)." },
      h("option", { value: "agent", text: "에이전트" }), h("option", { value: "direct", text: "직접 모드" }), h("option", { value: "loop", text: "액션 루프(실험)" }));
    var mix = h("label", { class: "check cw-guide", id: "code-mix-row", hidden: true, title: "모델마다 잘 푸는 작업이 다릅니다. 첫 모델이 실패하면 다음 시도는 다른 무료·로컬 모델이 맡습니다." },
      h("input", { type: "checkbox", id: "code-mix" }), h("span", { text: " 다른 모델도 번갈아" }));
    var validate = h("label", { class: "check cw-guide", id: "code-validate-row", hidden: true }, h("input", { type: "checkbox", id: "code-validate", checked: true }), h("span", { text: " 테스트로 확인" }));
    var attempts = h("select", { id: "code-attempts", hidden: true, "aria-label": "시도 횟수", title: "테스트가 통과하는 첫 시도에서 멈추고, 아니면 최대 이 횟수까지 새 복사본에서 다시 시도해 가장 나은 결과를 고릅니다(무료·로컬 한도를 더 씁니다)." },
      [1, 2, 3, 5].map(function (n) { return h("option", { value: String(n), text: n === 1 ? "시도 1회" : "최대 " + n + "회 시도" }); }));
    attempts.onchange = codeModeChanged;
    mode.onchange = codeModeChanged;
    var prompt = h("textarea", { id: "code-prompt", rows: "2", maxlength: "8000", "aria-label": "할 일", placeholder: "무엇을 고칠까요?  ( / 로 명령, Ctrl+Enter로 보내기 )" });
    var send = h("button", { type: "submit", class: "btn primary", id: "code-start", "aria-label": "시작", text: "시작" });
    var cost = h("label", { class: "check", id: "code-cost-row", hidden: true }, h("input", { type: "checkbox", id: "code-cost" }), h("span", { text: " 요금이 나올 수 있는 모델임을 확인했고 감수합니다" }));
    var guide = h("label", { class: "check cw-guide" }, h("input", { type: "checkbox", id: "code-guidance", checked: !!(state.settings && state.settings.code_guidance === true),
      onchange: async function (e) { try { state.settings = (await api("POST", "/settings", { code_guidance: e.target.checked })).settings; } catch (err) { toast(err.message); } } }),
      h("span", { text: " 작업 지침" }));
    var menu = h("div", { id: "code-slash", class: "cw-slash", role: "listbox", hidden: true });
    var ctxBtn = h("button", { type: "button", class: "btn", id: "code-ctx-check", hidden: true, text: "입력 한도 점검", title: "이 모델이 한 번에 받는 입력의 길이를 시험 글 2개로 확인합니다(설정은 바꾸지 않습니다)", onclick: codeCtxCheck });
    var hiddenBox = h("div", { class: "cw-hidden", hidden: true }, project, settingsBtn, repo, pickBtn);
    var projChip = h("button", { type: "button", class: "chip-btn", id: "code-project-chip", "aria-haspopup": "true", "aria-expanded": "false", title: "작업할 프로젝트 폴더를 고릅니다(그 폴더의 복사본에서만 작업합니다)" },
      h("span", { text: "📁" }), h("span", { class: "lbl", id: "code-project-label", text: "프로젝트 선택" }), h("span", { text: "▾" }));
    var projMenu = h("div", { class: "cw-pop", id: "code-projmenu", role: "menu", hidden: true });
    projChip.addEventListener("click", function (e) { e.stopPropagation(); openProjectMenu(projMenu, projChip); });
    function settingBox(key, text, tip, fallback) {
      return h("label", { class: "check cw-guide", title: tip }, h("input", { type: "checkbox", id: "set-" + key, checked: !(state.settings && state.settings[key] === false) && (fallback || (state.settings && state.settings[key] === true)),
        onchange: async function (e) { try { state.settings = (await api("POST", "/settings", (function (o) { o[key] = e.target.checked; return o; })({}))).settings; } catch (err) { toast(err.message); } } }), h("span", { text: " " + text }));
    }
    var bigPref = settingBox("code_prefer_big", "설치돼 있으면 새 직접 모드 작업에 9B 사용", "내장 엔진에 Qwen3.5 9B가 설치돼 있으면 모델을 '자동'으로 둔 직접 모드 작업을 9B로 시작합니다(엔진을 켜고 GPU를 비웁니다). 7B보다 정확하지만 6~8배 느립니다.", true);
    var autoEsc = settingBox("code_auto_escalate", "7B가 실패하면 9B로 자동 재시도(한 번)", "직접 모드 작업이 실패하면 새 복사본에서 9B로 한 번만 자동으로 다시 합니다. 원래 작업은 그대로 남습니다.", true);
    var advMenu = h("div", { class: "cw-pop", id: "code-adv", hidden: true }, h("div", { class: "mh", text: "고급 옵션" }), attempts, mix, validate, guide, bigPref, autoEsc, ctxBtn);
    var advBtn = h("button", { type: "button", class: "chip-btn", id: "code-adv-btn", "aria-haspopup": "true", "aria-expanded": "false", title: "시도 횟수·테스트 확인·작업 지침 같은 고급 옵션", text: "⚙ 옵션" });
    advBtn.addEventListener("click", function (e) { e.stopPropagation(); togglePop(advMenu, advBtn); });
    if (!window.__cwPopClose) { window.__cwPopClose = true; document.addEventListener("click", function (e) { if (!e.target.closest || !e.target.closest(".cw-pop")) closePops(); }); document.addEventListener("keydown", function (e) { if (e.key === "Escape") closePops(); }); }
    var form = h("form", { id: "code-composer", class: "cw-composer", autocomplete: "off", onsubmit: function (e) { e.preventDefault(); codeSubmit(); } },
      h("div", { class: "cw-opts" }, h("div", { class: "cw-pop-wrap" }, projChip, projMenu), mode, model, h("div", { class: "cw-pop-wrap" }, advBtn, advMenu), hiddenBox),
      h("p", { class: "sub", id: "code-model-note" }), cost, menu,
      h("div", { class: "cw-box" }, prompt, send));
    prompt.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); codeSubmit(); }
      else if (e.key === "Escape") menu.hidden = true;
    });
    prompt.addEventListener("input", codeSlashMenu);
    model.onchange = codeModelNote;
    var stream = h("div", { id: "code-stream", class: "cw-stream", onscroll: function () { codeStick = stream.scrollTop + stream.clientHeight >= stream.scrollHeight - 40; } });
    var runTools = buildRunTools();
    var gutter = h("div", { class: "gutter g-runs", role: "separator", "aria-orientation": "vertical", "aria-label": "작업 목록 폭 조절(좌우 화살표, 더블클릭으로 초기화)", tabindex: "0" });
    makeResizer(gutter, { cssVar: "--runs-w", key: "runs-w", min: 180, def: 250, max: function () { return Math.min(520, root.clientWidth * 0.5); }, origin: function () { return root.getBoundingClientRect().left; } });
    var runsOpen = store("runs-open"); root.classList.toggle("runs-closed", runsOpen === "0" || (runsOpen === null && window.innerWidth < 900));
    var gutter2 = h("div", { class: "gutter g-changes", role: "separator", "aria-orientation": "vertical", "aria-label": "변경 사항 폭 조절(좌우 화살표, 더블클릭으로 초기화)", tabindex: "0" });
    makeResizer(gutter2, { cssVar: "--chg-w", key: "chg-w", min: 260, def: 380, invert: true, max: function () { return Math.min(760, root.clientWidth * 0.6); }, origin: function () { return root.getBoundingClientRect().right; } });
    var chgOpen = store("changes-open"); root.classList.toggle("changes-closed", chgOpen === "0" || (chgOpen === null && window.innerWidth < 1200));
    var changes = h("aside", { class: "cw-changes", id: "code-changes", "aria-label": "변경 사항" });
    root.append(datalist,
      h("aside", { class: "cw-side" }, h("button", { type: "button", class: "btn primary cw-new", text: "＋ 새 작업", onclick: function () { codeOpen = ""; renderCode(); } }), runTools[0], runTools[1], runTools[2], runTools[3], h("div", { id: "code-runs", class: "cw-list" }), runTools[4]),
      gutter,
      h("section", { class: "cw-main" }, h("div", { id: "code-env", class: "sub cw-env" }), stream, form), gutter2, changes);
    var tl = $("tb-left"), tr = $("tb-right");
    if (tl) { tl.textContent = ""; tl.appendChild(h("button", { type: "button", class: "btn", id: "code-toggle-runs", text: "☰ 작업 목록", title: "작업 목록 접기/펴기 (Ctrl+Shift+B)", "aria-controls": "code-runs", onclick: function () { toggleRuns(); } })); }
    if (tr) { tr.textContent = ""; var sysPop = h("div", { class: "cw-pop cw-sys", id: "code-sys", hidden: true }); var sysBtn = h("button", { type: "button", class: "btn", id: "code-sys-btn", text: "상태", "aria-haspopup": "true", "aria-expanded": "false", title: "Ollama·내장 엔진·OpenCode·메모리·그래픽 상태" });
      sysBtn.addEventListener("click", function (e) { e.stopPropagation(); openSystemPop(sysBtn, sysPop); });
      tr.appendChild(h("div", { class: "cw-pop-wrap" }, sysBtn, sysPop));
      tr.appendChild(h("button", { type: "button", class: "btn", id: "code-toggle-changes", title: "변경 사항 패널 접기/펴기", "aria-controls": "code-changes", onclick: function () { toggleChanges(); } }, "± 변경 사항", h("span", { id: "code-changes-count", class: "tag", hidden: true }))); }
    document.body.dataset.view = document.body.dataset.view || "code";
    syncRunsToggle(); renderChanges(null, {}); updateProjectChip();
  }
  var changeSel = "", changeSig = "";
  function toggleChanges(force) { var root = $("code-root"); if (!root) return; var closed = force === undefined ? !root.classList.contains("changes-closed") : !force; root.classList.toggle("changes-closed", closed); store("changes-open", closed ? "0" : "1"); var b = $("code-toggle-changes"); if (b) b.setAttribute("aria-expanded", closed ? "false" : "true"); }
  function renderChanges(r, per) {
    var box = $("code-changes"); if (!box) return;
    var files = (r && r.files) || [];
    var count = $("code-changes-count"); if (count) { count.textContent = String(files.length); count.hidden = !files.length; }
    var sig = (r ? r.id : "") + "|" + changeSel + "|" + files.map(function (f) { return f.path + f.added + f.deleted; }).join(",") + "|" + (r && r.patch ? r.patch.length : 0);
    if (sig === changeSig && box.childNodes.length) return;          // the 1.5 s refresh must not reset scrolling for an unchanged run
    changeSig = sig; box.textContent = "";
    var close = h("button", { type: "button", class: "icon", title: "패널 닫기", "aria-label": "변경 사항 패널 닫기", text: "✕", onclick: function () { toggleChanges(false); } });
    box.appendChild(h("div", { class: "chg-head" }, h("b", { text: "변경 사항" }), r && r.stat ? h("span", { class: "sub", text: r.stat }) : "", close));
    if (!files.length) { box.appendChild(h("p", { class: "sub", text: r ? "바뀐 파일이 없습니다." : "작업을 열면 바뀐 파일과 변경 내용이 여기에 나옵니다." })); return; }
    if (!files.some(function (f) { return f.path === changeSel; })) changeSel = files[0].path;
    var list = h("div", { class: "chg-list", role: "listbox" });
    files.forEach(function (f) {
      var kind = { A: "추가", M: "수정", D: "삭제", R: "이름변경" }[f.status] || f.status;
      list.appendChild(h("button", { type: "button", class: "chg-file" + (f.path === changeSel ? " sel" : ""), role: "option", "aria-selected": f.path === changeSel ? "true" : "false", title: f.path,
        onclick: function () { changeSel = f.path; renderChanges(r, per); } },
        h("span", { class: "cw-fk", text: kind }), h("span", { class: "p", text: "\u200E" + f.path }),
        f.binary ? h("span", { class: "sub", text: "바이너리" }) : h("span", {}, h("span", { class: "add", text: "+" + f.added }), " ", h("span", { class: "del", text: "−" + f.deleted }))));
    });
    box.appendChild(list);
    var chosen = files.filter(function (f) { return f.path === changeSel; })[0];
    var diff = h("div", { class: "chg-diff" });
    if (chosen && per[chosen.path]) diff.appendChild(diffView(per[chosen.path].join("\n")));
    else diff.appendChild(h("p", { class: "sub", text: chosen && chosen.binary ? "바이너리 파일은 내용을 보여 주지 않습니다." : "이 파일의 변경 내용을 불러오지 못했습니다(diff가 길어 일부만 보관됩니다)." }));
    box.appendChild(diff);
    if (r.patch_truncated) box.appendChild(h("p", { class: "sub", text: "(diff가 길어 앞부분만 보입니다. 적용하면 전체가 적용됩니다.)" }));
  }
  function closePops() { [].forEach.call(document.querySelectorAll(".cw-rowmenu"), function (m) { m.remove(); }); [].forEach.call(document.querySelectorAll(".cw-pop"), function (m) { m.hidden = true; }); [].forEach.call(document.querySelectorAll(".chip-btn"), function (b) { b.setAttribute("aria-expanded", "false"); }); }
  function togglePop(menu, btn) { var was = menu.hidden; closePops(); menu.hidden = !was; btn.setAttribute("aria-expanded", was ? "true" : "false"); }
  function updateProjectChip() {
    var lbl = $("code-project-label"), sel = $("code-project"), repo = $("code-repo"), chip = $("code-project-chip");
    if (!lbl || !sel || !repo) return;
    var text = "프로젝트 선택", full = "";
    if (sel.value) { var o = sel.selectedOptions[0]; text = o ? o.text.replace(/\s*\(.*$/, "") : text; full = o ? o.text : ""; }
    else if (repo.value.trim()) { full = repo.value.trim(); text = full.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || full; }
    lbl.textContent = text; if (chip) chip.title = full || "작업할 프로젝트 폴더를 고릅니다(그 폴더의 복사본에서만 작업합니다)";
  }
  function openProjectMenu(menu, btn) {
    var was = menu.hidden; closePops(); if (!was) return;
    menu.textContent = ""; btn.setAttribute("aria-expanded", "true");
    var sel = $("code-project"), repo = $("code-repo");
    var registered = [].filter.call(sel.options, function (o) { return o.value; });
    if (registered.length) menu.appendChild(h("div", { class: "mh", text: "내 프로젝트" }));
    registered.forEach(function (o) {
      menu.appendChild(h("button", { type: "button", role: "menuitem", class: o.value === sel.value ? "sel" : "", title: o.text,
        onclick: function () { sel.value = o.value; codeProjectChanged(); updateProjectChip(); closePops(); } }, (o.value === sel.value ? "✓ " : "") + o.text.replace(/\s*\(.*$/, "")));
    });
    if (registered.length) menu.appendChild(h("hr"));
    menu.appendChild(h("button", { type: "button", role: "menuitem", onclick: async function () {
      try { var r = await api("POST", "/projects/pick", {}); if (r.path) { sel.value = ""; repo.value = r.path; store("code-repo", r.path); codeProjectChanged(); updateProjectChip(); closePops(); } else toast("폴더를 고르지 않았습니다."); }
      catch (e) { toast(e.message); } } }, "＋ 폴더 선택…"));
    var typed = h("input", { type: "text", "aria-label": "프로젝트 폴더 경로", placeholder: "또는 경로를 붙여넣고 Enter", value: sel.value ? "" : repo.value });
    typed.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); if (typed.value.trim()) { sel.value = ""; repo.value = typed.value.trim(); store("code-repo", repo.value); codeProjectChanged(); updateProjectChip(); closePops(); } } });
    menu.appendChild(typed);
    if (sel.value) { menu.appendChild(h("hr")); menu.appendChild(h("button", { type: "button", role: "menuitem", onclick: function () { closePops(); renderProjectSettings(sel.value); } }, "⚙ 프로젝트 설정")); }
    menu.hidden = false;
  }
  function syncRunsToggle() { var root = $("code-root"), b = $("code-toggle-runs"); if (root && b) b.setAttribute("aria-expanded", root.classList.contains("runs-closed") ? "false" : "true"); }
  function toggleRuns() { var root = $("code-root"); if (!root) return; var closed = root.classList.toggle("runs-closed"); store("runs-open", closed ? "0" : "1"); syncRunsToggle(); }
  /* ---------- task list: search, views (active / archived / trash), row menu, bulk actions ---------- */
  var codeView = "active", codeQuery = "", codeLimit = 30, codeSel = {}, codeSelectMode = false, codeCounts = { active: 0, archived: 0, trash: 0 }, codeLast = [];
  var VIEW_NAMES = { active: "작업", archived: "보관", trash: "휴지통" };
  var codeFilter = store("code-filter") || "", codeSort = store("code-sort") || "recent", codeGroup = store("code-group") === "1", codeCollapsed = {};
  var FILTERS = { "": "모든 상태", running: "진행 중", review: "검토 대기", failed: "실패·취소", other: "완료·기타" };
  function filterOf(status) { return status === "running" ? "running" : status === "review" ? "review" : status === "failed" || status === "cancelled" ? "failed" : "other"; }
  function visibleRuns(runs) {
    var list = runs.filter(function (r) { return !codeFilter || filterOf(r.status) === codeFilter; });
    if (codeSort === "name") list = list.slice().sort(function (a, b) { return runTitle(a).localeCompare(runTitle(b), "ko"); });
    return list;
  }
  function runTitle(r) { return r.title || (r.prompt || "").split("\n")[0].slice(0, 80) || "(제목 없음)"; }
  function runFolder(r) { return (r.project_name || (r.repo || "").replace(/[\\/]+$/, "").split(/[\\/]/).pop() || ""); }
  function toastUndo(message, undo) {
    var t = $("toast"); t.textContent = ""; t.append(message + " ");
    t.appendChild(h("button", { type: "button", class: "btn mini", text: "되돌리기", onclick: function () { t.hidden = true; undo(); } }));
    t.hidden = false; clearTimeout(toast.timer); toast.timer = setTimeout(function () { t.hidden = true; }, 7000);
  }
  async function runAct(id, action, body) {
    try { var r = await api("POST", "/code/runs/" + id + "/" + action, body || {}); if (r && r.counts) codeCounts = r.counts; return true; }
    catch (e) { toast(e.message); return false; }
  }
  async function trashRun(r) {
    if (!(await runAct(r.id, "trash"))) return;
    toastUndo("휴지통으로 옮겼습니다.", async function () { if (await runAct(r.id, "restore")) renderCode(); });
    renderCode();
  }
  var PURGE_ASK = "이 작업의 기록(대화·변경 내용·보고서)을 영구 삭제합니다. 복구할 수 없습니다.\n내 프로젝트 폴더의 파일은 그대로 남습니다. 계속할까요?";
  async function purgeRun(r) { if (!window.confirm(PURGE_ASK)) return; if (await runAct(r.id, "purge", { confirm: true })) { toast("영구 삭제했습니다."); renderCode(); } }
  async function bulkAct(ids, action, confirmed) {
    if (!ids.length) return;
    try {
      var r = await api("POST", "/code/bulk", { ids: ids.slice(0, 100), action: action, confirm: confirmed === true });
      codeCounts = r.counts || codeCounts;
      var bad = r.results.filter(function (x) { return !x.ok; });
      toast(bad.length ? (r.results.length - bad.length) + "개 처리, " + bad.length + "개는 건너뜀: " + bad[0].error : r.results.length + "개 처리했습니다.");
    } catch (e) { toast(e.message); }
    codeSel = {}; renderCode();
  }
  function startRename(row, r) {
    var span = row.querySelector(".cw-run-t"); if (!span) return;
    var input = h("input", { type: "text", class: "cw-rename", maxlength: "80", value: r.title || runTitle(r), "aria-label": "작업 이름" });
    var done = false;
    async function save() { if (done) return; done = true; var text = input.value; input.classList.remove("cw-rename"); await runAct(r.id, "rename", { title: text }); renderCode(); }
    input.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); save(); } else if (e.key === "Escape") { done = true; input.classList.remove("cw-rename"); renderCode(); } });
    input.addEventListener("blur", save);
    span.replaceWith(input); input.focus(); input.select();
  }
  function openRowMenu(row, r, anchor) {
    closePops();
    var old = row.querySelector(".cw-rowmenu"); if (old) old.remove();
    var menu = h("div", { class: "cw-pop cw-rowmenu", role: "menu" });
    function item(label, fn, danger) { menu.appendChild(h("button", { type: "button", role: "menuitem", class: danger ? "danger" : "", text: label, onclick: function (e) { e.stopPropagation(); closePops(); fn(); } })); }
    if (codeView === "trash") {
      item("복원", async function () { if (await runAct(r.id, "restore")) renderCode(); });
      item("영구 삭제…", function () { purgeRun(r); }, true);
    } else {
      item("이름 변경", function () { startRename(row, r); });
      item("다시 실행(새 작업)", async function () { try { var n = await api("POST", "/code/runs/" + r.id + "/rerun", {}); codeOpen = n.id; codeStick = true; toast("같은 설정으로 새 작업을 시작했습니다."); } catch (e) { toast(e.message); } renderCode(); });
      if (codeView === "archived") item("보관 해제", async function () { if (await runAct(r.id, "unarchive")) renderCode(); });
      else item("보관", async function () { if (await runAct(r.id, "archive")) { toast("보관했습니다. 위의 '보관' 탭에서 볼 수 있습니다."); renderCode(); } });
      item("삭제(휴지통으로)", function () { trashRun(r); }, true);
    }
    row.appendChild(menu); anchor.setAttribute("aria-expanded", "true");
  }
  function buildRunTools() {
    var search = h("input", { type: "search", id: "code-search", class: "cw-search", placeholder: "작업 검색", "aria-label": "작업 검색", value: codeQuery });
    var timer = null;
    search.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(function () { codeQuery = search.value.trim(); codeLimit = 30; renderCode(); }, 250); });
    var tabs = h("div", { id: "code-tabs", class: "cw-tabs", role: "tablist" });
    var selBtn = h("button", { type: "button", class: "btn mini", id: "code-select-toggle", text: "선택", title: "여러 작업을 골라 한 번에 처리합니다",
      onclick: function () { codeSelectMode = !codeSelectMode; codeSel = {}; renderCode(); } });
    var bulk = h("div", { id: "code-bulk", class: "cw-bulk", hidden: true });
    var filt = h("select", { id: "code-filter", class: "cw-mini", "aria-label": "상태 필터", title: "상태로 거르기" }, Object.keys(FILTERS).map(function (k) { return h("option", { value: k, text: FILTERS[k] }); }));
    filt.value = codeFilter; filt.onchange = function () { codeFilter = filt.value; store("code-filter", codeFilter); renderRunList({ runs: codeLast }); };
    var sort = h("select", { id: "code-sort", class: "cw-mini", "aria-label": "정렬", title: "정렬" }, h("option", { value: "recent", text: "최근순" }), h("option", { value: "name", text: "이름순" }));
    sort.value = codeSort; sort.onchange = function () { codeSort = sort.value; store("code-sort", codeSort); renderRunList({ runs: codeLast }); };
    var grp = h("label", { class: "cw-mini check", title: "프로젝트(폴더)별로 묶어 접고 펼칩니다" }, h("input", { type: "checkbox", id: "code-group", checked: codeGroup }), h("span", { text: " 프로젝트별" }));
    grp.querySelector("input").onchange = function (e) { codeGroup = e.target.checked; store("code-group", codeGroup ? "1" : "0"); renderRunList({ runs: codeLast }); };
    var fbar = h("div", { class: "cw-filters" }, filt, sort, grp);
    var more = h("button", { type: "button", class: "btn mini", id: "code-more", text: "더 보기", hidden: true, onclick: function () { codeLimit += 30; renderCode(); } });
    return [h("div", { class: "cw-tools" }, search, selBtn), tabs, fbar, bulk, more];
  }
  function renderRunList(d) {
    if (document.querySelector(".cw-rowmenu") || document.querySelector(".cw-rename")) return;       // never rebuild a list while a menu or a name is being edited
    var box = $("code-runs"); box.textContent = "";
    codeLast = d.runs;
    var tabs = $("code-tabs"); tabs.textContent = "";
    ["active", "archived", "trash"].forEach(function (v) {
      tabs.appendChild(h("button", { type: "button", role: "tab", class: "cw-tab" + (v === codeView ? " on" : ""), "aria-selected": v === codeView ? "true" : "false",
        text: VIEW_NAMES[v] + " " + (codeCounts[v] || 0), onclick: function () { codeView = v; codeLimit = 30; codeSel = {}; renderCode(); } }));
    });
    $("code-select-toggle").setAttribute("aria-pressed", codeSelectMode ? "true" : "false");
    var shown = visibleRuns(d.runs);
    if (d.runs.length && !shown.length) box.appendChild(h("p", { class: "sub", text: "이 상태의 작업이 없습니다. 위의 상태 필터를 바꿔 보세요." }));
    if (!d.runs.length) box.appendChild(h("p", { class: "sub", text: codeQuery ? "검색 결과가 없습니다." : codeView === "trash" ? "휴지통이 비어 있습니다." : codeView === "archived" ? "보관한 작업이 없습니다." : "아직 작업이 없습니다." }));
    var folders = {}; shown.forEach(function (r) { folders[runFolder(r)] = (folders[runFolder(r)] || 0) + 1; });
    var grouped = codeGroup && Object.keys(folders).length > 1, lastFolder = null, hiddenFolder = false;
    shown.slice().sort(function (a, b) { return grouped ? runFolder(a).localeCompare(runFolder(b), "ko") : 0; }).forEach(function (r) {
      if (grouped && runFolder(r) !== lastFolder) {
        lastFolder = runFolder(r); hiddenFolder = !!codeCollapsed[lastFolder];
        box.appendChild(h("button", { type: "button", class: "cw-grp", "aria-expanded": hiddenFolder ? "false" : "true", onclick: (function (name) { return function () { codeCollapsed[name] = !codeCollapsed[name]; renderRunList({ runs: codeLast }); }; })(lastFolder) },
          (hiddenFolder ? "▸ " : "▾ ") + (lastFolder || "(폴더 없음)") + " ", h("span", { class: "sub", text: "(" + folders[lastFolder] + ")" })));
      }
      if (grouped && hiddenFolder) return;
      var row = h("div", { class: "cw-row" + (r.id === codeOpen ? " open" : "") });
      if (codeSelectMode) row.appendChild(h("input", { type: "checkbox", "aria-label": "선택", checked: !!codeSel[r.id], onchange: function (e) { if (e.target.checked) codeSel[r.id] = true; else delete codeSel[r.id]; renderBulk(); } }));
      row.appendChild(h("button", { type: "button", class: "cw-run" + (r.id === codeOpen ? " open" : ""), title: r.prompt, onclick: function () { codeOpen = r.id; codeStick = true; renderCode(); } },
        h("span", { class: "cw-run-t", text: runTitle(r) }), codeBadge(r.status),
        h("span", { class: "sub", text: [runFolder(r), r.stat || "", new Date((r.started || 0) * 1000).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "numeric", minute: "2-digit" })].filter(Boolean).join(" · ") })));
      var more = h("button", { type: "button", class: "icon cw-more", "aria-label": "작업 메뉴", "aria-haspopup": "true", "aria-expanded": "false", text: "⋯", title: "이름 변경·다시 실행·보관·삭제",
        onclick: function (e) { e.stopPropagation(); var had = row.querySelector(".cw-rowmenu"); closePops(); if (!had) openRowMenu(row, r, more); } });
      row.appendChild(more);
      box.appendChild(row);
    });
    $("code-more").hidden = d.runs.length < codeLimit;
    renderBulk();
  }
  function renderBulk() {
    var bar = $("code-bulk"); if (!bar) return;
    var ids = Object.keys(codeSel);
    bar.hidden = !(codeSelectMode || (codeView === "trash" && codeCounts.trash));
    bar.textContent = "";
    if (codeSelectMode) {
      bar.appendChild(h("span", { class: "sub", text: ids.length + "개 선택" }));
      bar.appendChild(h("button", { type: "button", class: "btn mini", text: "전체", onclick: function () { codeLast.forEach(function (r) { codeSel[r.id] = true; }); renderRunList({ runs: codeLast }); } }));
      if (codeView === "trash") {
        bar.appendChild(h("button", { type: "button", class: "btn mini", text: "복원", disabled: !ids.length, onclick: function () { bulkAct(Object.keys(codeSel), "restore"); } }));
        bar.appendChild(h("button", { type: "button", class: "btn mini danger", text: "영구 삭제", disabled: !ids.length, onclick: function () { if (window.confirm(PURGE_ASK)) bulkAct(Object.keys(codeSel), "purge", true); } }));
      } else {
        bar.appendChild(h("button", { type: "button", class: "btn mini", text: codeView === "archived" ? "보관 해제" : "보관", disabled: !ids.length, onclick: function () { bulkAct(Object.keys(codeSel), codeView === "archived" ? "unarchive" : "archive"); } }));
        bar.appendChild(h("button", { type: "button", class: "btn mini danger", text: "삭제", disabled: !ids.length, onclick: function () { bulkAct(Object.keys(codeSel), "trash"); } }));
      }
    } else if (codeView === "trash" && codeCounts.trash) {
      bar.appendChild(h("button", { type: "button", class: "btn mini danger", text: "휴지통 비우기…", onclick: async function () {
        if (!window.confirm("휴지통의 작업 " + codeCounts.trash + "개를 모두 영구 삭제합니다.\n" + PURGE_ASK)) return;
        try { var all = (await api("POST", "/code/list", { view: "trash", limit: 500 })).runs.map(function (r) { return r.id; }); for (var i = 0; i < all.length; i += 100) await bulkAct(all.slice(i, i + 100), "purge", true); } catch (e) { toast(e.message); }
      } }));
    }
  }
  /* ---------- system status popover (only values JetAI already reads; nothing is invented) ---------- */
  async function openSystemPop(btn, pop) {
    var was = pop.hidden; closePops(); if (!was) return;
    pop.textContent = ""; pop.appendChild(h("div", { class: "mh", text: "불러오는 중…" })); pop.hidden = false; btn.setAttribute("aria-expanded", "true");
    var env = null, eng = null;
    try { env = await api("GET", "/env"); } catch (e) { /* shown as unknown below */ }
    try { eng = await api("GET", "/engine"); } catch (e) { /* idem */ }
    pop.textContent = "";
    function row(label, value, bad) { pop.appendChild(h("div", { class: "cw-sys-row" }, h("span", { class: "sub", text: label }), h("b", { class: bad ? "bad" : "", text: value }))); }
    pop.appendChild(h("div", { class: "mh", text: "이 PC와 실행 환경" }));
    row("Ollama", env ? (env.ollama && env.ollama.running ? "실행 중 · 모델 " + (env.ollama.models || []).length + "개" : "꺼져 있음") : "알 수 없음", env && !(env.ollama && env.ollama.running));
    row("내장 엔진", eng ? (eng.running ? "실행 중" : eng.binary ? "설치됨(꺼져 있음)" : eng.supported === false ? "이 PC에서는 지원하지 않음" : "설치 안 됨") : "알 수 없음");
    row("OpenCode", env ? (env.opencode ? "있음" : "없음 (에이전트 방식에 필요, 직접 모드는 없어도 됨)") : "알 수 없음", env && !env.opencode);
    var hw = eng && eng.hardware;
    row("메모리", hw && hw.ram_gb ? hw.ram_gb + " GB" : "알 수 없음");
    row("그래픽", hw && hw.gpu ? hw.gpu + " · " + hw.vram_gb + " GB" + (hw.vram_free_gb ? " (지금 비어 있는 " + hw.vram_free_gb + " GB)" : "") : "그래픽 카드 없음(CPU로 실행)");
    pop.appendChild(h("div", { class: "mh", text: "실시간 사용률은 측정하지 않습니다. 위 값은 조회 시점의 값입니다." }));
  }
  async function renderCode() {
    codeBuildShell();
    var d; try { d = await api("GET", "/code"); } catch (e) { var s0 = $("code-stream"); if (s0) s0.textContent = e.message; return; }
    codeCounts = d.counts || codeCounts;
    if (codeView !== "active" || codeQuery || codeLimit > 30) {
      try { var lst = await api("POST", "/code/list", { view: codeView, q: codeQuery, limit: codeLimit }); d.runs = lst.runs; codeCounts = lst.counts || codeCounts; } catch (e) { toast(e.message); }
    }
    $("code-env").textContent = d.environment.opencode ? "" : "OpenCode를 찾지 못했습니다. 먼저 설치하세요(npm i -g opencode-ai).";
    var pr; try { pr = (await api("GET", "/projects")).projects; } catch (e) { pr = []; }
    codeProjects = pr;
    var psel = $("code-project"), pprev = psel.value || store("code-project") || ""; psel.textContent = "";
    pr.forEach(function (p) { psel.appendChild(h("option", { value: p.id, text: p.name })); });
    psel.appendChild(h("option", { value: "", text: "＋ 새 프로젝트(폴더 등록)…" }));
    psel.value = pr.some(function (p) { return p.id === pprev; }) ? pprev : (pr[0] ? pr[0].id : "");
    codeProjectChanged();
    var repos = $("code-repos"); repos.textContent = "";
    d.repos.forEach(function (r) { repos.appendChild(h("option", { value: r })); });
    if (!pr.length && !$("code-repo").value && d.repos[0]) $("code-repo").value = d.repos[0];
    var sel = $("code-model"), prev = sel.value; sel.textContent = "";
    sel.appendChild(h("option", { value: "auto", text: "자동 선택", title: "무료·로컬 모델 중 코드 작업에 충분한 모델을 고릅니다" }));
    d.models.forEach(function (m) { sel.appendChild(h("option", { value: m.ref, "data-warn": m.warn || "", "data-tier": m.tier, "data-ctx": m.ctx_limit || "", text: m.ref + (m.tier === "local" ? " (내 PC)" : m.tier === "free" ? " (무료)" : " (유료 가능)") + (m.warn ? " ⚠" : "") })); });
    d.environment.opencode && sel.appendChild(h("option", { value: "opencode/deepseek-v4.1-flash-free", "data-warn": "OpenCode의 무료 서비스입니다. 코드가 그 서비스로 전송됩니다.", "data-tier": "free", text: "opencode/deepseek-v4.1-flash-free (OpenCode 무료)" }));
    if (prev) sel.value = prev; codeModeChanged(); updateProjectChip();
    $("code-start").disabled = (!d.environment.opencode && $("code-mode").value === "agent") || codeBusy;
    renderRunList(d);
    if (codeOpen && !d.runs.some(function (r) { return r.id === codeOpen; }) && !document.querySelector(".cw-rowmenu, .cw-rename")) codeOpen = "";
    if (codeOpen) await renderCodeDetail(codeOpen); else { codeWelcome(); renderChanges(null, {}); }
    var running = d.runs.some(function (r) { return r.status === "running" || (r.checks && r.checks.state === "running") || (r.review && r.review.state === "running"); });
    if (running && !codeTimer) codeTimer = setInterval(function () { if ($("view-code").hidden) return; renderCode(); }, 1500);
    if (!running && codeTimer) { clearInterval(codeTimer); codeTimer = null; }
  }
  function codeWelcome() {
    var s = $("code-stream"); s.textContent = "";
    s.appendChild(h("div", { class: "cw-welcome" }, h("h2", { text: "무엇을 고칠까요?" }),
      h("p", { class: "sub", text: "아래에 프로젝트 폴더와 할 일을 적으면, 일회용 복사본(git worktree)에서 코딩 에이전트가 고치고 진행 과정을 여기에 보여 줍니다. 바뀐 내용을 보고 마음에 들 때만 내 폴더에 적용합니다. 커밋·푸시는 하지 않습니다." }),
      h("ul", { class: "sub" },
        h("li", { text: "예) calc.py의 add 함수 버그를 고치고 테스트가 통과하게 해줘" }),
        h("li", { text: "예) README에 설치 방법을 정리해 줘" }),
        h("li", { text: "/ 를 입력하면 적용·되돌리기·검사·리뷰 같은 명령이 나옵니다." }))));
  }
  /* (welcome end) */
  var codeProjects = [];
  function codeProjectChanged() {
    var isNew = !$("code-project").value;
    $("code-repo").hidden = !isNew;
    $("code-project-settings").hidden = isNew;
    updateProjectChip();
  }
  async function renderProjectSettings(id) {
    var p; try { p = await api("GET", "/projects/" + id); } catch (e) { toast(e.message); return; }
    var s = $("code-stream"); s.textContent = "";
    function field(label, input) { return h("label", { class: "field" }, h("span", { text: label }), input); }
    var name = h("input", { type: "text", value: p.name, maxlength: "60" });
    var branch = h("input", { type: "text", value: p.work_branch || "", placeholder: "예: jetai/work" });
    var dev = h("input", { type: "text", value: p.dev_url || "", placeholder: "예: http://localhost:5173" });
    var apps = h("input", { type: "text", value: (p.allowed_apps || []).join(", "), placeholder: "화면 조작을 허용할 앱 이름(쉼표로, 최대 10개)" });
    var cmds = {};
    ["build", "test", "run", "lint"].forEach(function (k) {
      var detected = (p.detected_commands || []).filter(function (c) { return c.kind === k; }).map(function (c) { return c.command; }).join(", ");
      cmds[k] = h("input", { type: "text", value: (p.commands || {})[k] || "", placeholder: detected ? "감지됨: " + detected : "감지되지 않음" });
    });
    var commit = h("select", {}, h("option", { value: "never", text: "커밋 안 함(기본)" }), h("option", { value: "ask", text: "승인을 받은 뒤 작업 브랜치에 커밋" }));
    commit.value = (p.policy || {}).commit || "never";
    var cu = h("select", {}, h("option", { value: "observe", text: "관찰만(기본)" }), h("option", { value: "approval", text: "승인 후 실행" }));
    cu.value = (p.policy || {}).computer_use || "observe";
    var card = h("div", { class: "cw-card" }, h("h3", { text: "프로젝트 설정: " + p.name }),
      h("p", { class: "sub", text: p.path + (p.remote ? " · " + p.remote : "") + " · 기본 브랜치 " + (p.default_branch || "-") + " · " + ((p.languages || []).join(", ") || "언어 미감지") }),
      h("p", { class: "sub", text: "지침 파일: " + ((p.instructions || []).join(", ") || "없음") }),
      field("이름", name), field("작업 브랜치 이름", branch), field("개발 서버 주소(이 PC만)", dev), field("허용 앱", apps),
      h("p", { class: "sub", text: "명령(비우면 감지된 명령을 씁니다. git push·삭제·sudo 같은 위험한 명령은 저장되지 않습니다)" }),
      field("빌드", cmds.build), field("테스트", cmds.test), field("실행", cmds.run), field("린트", cmds.lint),
      field("커밋 정책", commit), field("화면 조작 모드", cu),
      h("div", { class: "cw-actions" },
        h("button", { type: "button", class: "btn primary", text: "저장", onclick: async function () {
          try {
            await api("POST", "/projects/" + id, { name: name.value, work_branch: branch.value.trim(), dev_url: dev.value.trim(),
              allowed_apps: apps.value.split(",").map(function (a) { return a.trim(); }).filter(Boolean),
              commands: { build: cmds.build.value, test: cmds.test.value, run: cmds.run.value, lint: cmds.lint.value }, policy: { commit: commit.value, computer_use: cu.value } });
            toast("저장했습니다."); renderCode();
          } catch (e) { toast(e.message); } } }),
        h("button", { type: "button", class: "btn", text: "다시 감지", onclick: async function () { try { await api("POST", "/projects/" + id + "/refresh", {}); toast("다시 감지했습니다."); renderProjectSettings(id); } catch (e) { toast(e.message); } } }),
        h("button", { type: "button", class: "btn", text: "등록 해제", onclick: async function () {
          if (!confirm("이 프로젝트를 목록에서 뺍니다. 폴더와 작업 기록은 그대로 둡니다.")) return;
          try { await api("POST", "/projects/" + id + "/remove", {}); store("code-project", ""); toast("목록에서 뺐습니다."); codeOpen = ""; renderCode(); } catch (e) { toast(e.message); } } }),
        h("button", { type: "button", class: "btn", text: "닫기", onclick: function () { renderCode(); } })));
    s.appendChild(card);
  }
  function codeModeChanged() {
    var direct = $("code-mode").value === "direct" || $("code-mode").value === "loop";
    $("code-validate-row").hidden = !direct;
    $("code-attempts").hidden = !direct;
    $("code-mix-row").hidden = !direct || parseInt($("code-attempts").value, 10) < 2;
    [].forEach.call($("code-model").options, function (o) { o.disabled = direct && o.value.indexOf("opencode/") === 0; });
    if ($("code-model").selectedOptions[0] && $("code-model").selectedOptions[0].disabled) $("code-model").value = "auto";
    codeModelNote();
  }
  async function codeCtxCheck() {
    var b = $("code-ctx-check"); b.disabled = true;
    try {
      var r = await api("POST", "/code/context-probe", { model: $("code-model").value });
      toastLong(r.ok ? (r.truncated ? "입력이 약 " + r.limit_tokens + "토큰에서 잘립니다. " + r.advice : "입력이 잘리지 않습니다(큰 입력 " + r.big_tokens + "토큰 확인).") : r.error);
      renderCode();
    } catch (e) { toast(e.message); }
    b.disabled = false;
  }
  function codeModelNote() {
    var o = $("code-model").selectedOptions[0]; if (!o) return;
    $("code-model-note").textContent = (o.dataset.warn || "") + (o.dataset.ctx ? " ⚠ 이 모델은 입력이 약 " + o.dataset.ctx + "토큰에서 잘립니다(점검 결과)." : "");
    $("code-ctx-check").hidden = o.dataset.tier !== "local" || o.value === "auto";
    $("code-cost-row").hidden = o.dataset.tier !== "other";
  }
  function codeSlashMenu() {
    var input = $("code-prompt"), menu = $("code-slash"), text = input.value;
    if (text.charAt(0) !== "/" || text.indexOf("\n") >= 0) { menu.hidden = true; return; }
    var word = text.slice(1).toLowerCase();
    var hits = CODE_COMMANDS.filter(function (c) { return c.name.indexOf(word) === 0 || c.alias.indexOf(word) === 0; });
    menu.textContent = "";
    if (!hits.length) { menu.hidden = true; return; }
    hits.forEach(function (c) {
      menu.appendChild(h("button", { type: "button", role: "option", onclick: function () { input.value = "/" + c.name; menu.hidden = true; codeSubmit(); } },
        h("b", { text: "/" + c.name }), h("span", { class: "sub", text: "  " + c.alias + " — " + c.help })));
    });
    menu.hidden = false;
  }
  async function codeSubmit() {
    var input = $("code-prompt"), text = input.value.trim();
    if (!text || codeBusy) return;
    $("code-slash").hidden = true;
    if (text.charAt(0) === "/") {
      var word = text.slice(1).split(/\s+/)[0].toLowerCase();
      var cmd = CODE_COMMANDS.filter(function (c) { return c.name === word || c.alias === word; })[0];
      if (!cmd) { toast("알 수 없는 명령입니다. / 를 입력해 목록을 보세요."); return; }
      input.value = "";
      if (cmd.name === "new") { codeOpen = ""; renderCode(); return; }
      if (!codeOpen) { toast("먼저 왼쪽에서 작업을 고르세요."); return; }
      return codeAction(codeOpen, cmd.name);
    }
    codeBusy = true; $("code-start").disabled = true;
    try {
      store("code-repo", $("code-repo").value.trim()); store("code-project", $("code-project").value);
      if ($("code-model").value === "auto" && ($("code-mode").value === "direct" || $("code-mode").value === "loop") && state.settings && state.settings.code_prefer_big !== false) toast("9B가 설치돼 있으면 엔진을 켜느라 시작이 조금 늦을 수 있습니다.");
      var r = await api("POST", "/code/start", { project: $("code-project").value || undefined, repo: $("code-repo").value.trim(), prompt: text, model: $("code-model").value, confirm_cost: $("code-cost").checked, guidance: $("code-guidance").checked, mode: $("code-mode").value, validate: $("code-validate").checked, attempts: $("code-mode").value !== "agent" && $("code-validate").checked ? parseInt($("code-attempts").value, 10) : 1,
        models: $("code-mode").value !== "agent" && $("code-mix").checked && !$("code-mix-row").hidden ? ["auto"] : null });
      codeOpen = r.id; codeStick = true; input.value = ""; toast("작업을 시작했습니다.");
    } catch (e) { toast(e.message); }
    codeBusy = false; renderCode();
  }
  var CODE_ACTIONS = {
    apply: { path: "apply", body: { confirm: true }, ask: "위 변경을 내 프로젝트 폴더에 적용할까요?\n(커밋은 하지 않습니다. 적용 후 직접 확인하고 커밋하세요.)", done: "적용했습니다. 폴더에서 변경을 확인하세요." },
    undo: { path: "undo", body: { confirm: true }, ask: "적용했던 변경을 내 폴더에서 다시 빼낼까요?\n(적용 뒤에 같은 부분을 또 고쳤다면 되돌릴 수 없습니다.)", done: "되돌렸습니다." },
    check: { path: "check", body: {}, ask: "이 작업의 복사본에서 프로젝트의 빌드·테스트 명령을 실행합니다(에이전트가 쓴 코드도 실행됩니다). 계속할까요?", done: "실행합니다." },
    review: { path: "review", body: {}, ask: "수정한 모델과 다른 무료·로컬 모델이 변경 내용(diff)을 읽고 의견을 냅니다. 클라우드 서비스라면 diff가 그 서비스로 전송됩니다. 계속할까요?", done: "리뷰를 시작했습니다." },
    cancel: { path: "cancel", body: {}, ask: "", done: "멈추는 중입니다." },
    discard: { path: "discard", body: {}, ask: "이 작업의 복사본과 변경을 지울까요?\n(작업 기록은 목록에 남습니다. 기록을 없애려면 목록의 ⋯ → 삭제를 쓰세요. 내 프로젝트 폴더는 건드리지 않습니다.)", done: "복사본을 정리했습니다." }
  };
  async function codeAction(id, name) {
    var a = CODE_ACTIONS[name]; if (!a) return;
    if (a.ask && !confirm(a.ask)) return;
    try { await api("POST", "/code/runs/" + id + "/" + a.path, a.body); toast(a.done); } catch (e) { toast(e.message); }
    codeStick = true; renderCode();
  }
  async function codeDecide(id, item, decision, ask) {
    if (ask && !confirm(ask)) return;
    try { await api("POST", "/code/runs/" + id + "/command", { id: item.id, decision: decision }); toast(decision === "decline" ? "거절했습니다." : "허용하고 이어서 실행합니다."); } catch (e) { toast(e.message); }
    codeStick = true; renderCode();
  }
  function diffView(patch) {
    var out = h("div", { class: "diff" }), lines = String(patch || "").split("\n").slice(0, 3000);
    lines.forEach(function (l) {
      var kind = l.indexOf("diff --git") === 0 ? "file" : l.indexOf("@@") === 0 ? "hunk" : (l[0] === "+" && l.indexOf("+++") !== 0) ? "add" : (l[0] === "-" && l.indexOf("---") !== 0) ? "del" : "ctx";
      out.appendChild(h("div", { class: "dl dl-" + kind, text: l || " " }));
    });
    return out;
  }
  function splitPatch(patch) {
    var files = {}, current = null;
    String(patch || "").split("\n").forEach(function (l) {
      if (l.indexOf("diff --git ") === 0) { var m = / b\/(.+)$/.exec(l); current = m ? m[1] : l; files[current] = []; }
      if (current) files[current].push(l);
    });
    return files;
  }
  function toolLine(text) {
    var m = /^(✓|✗|…)\s+(\S+)\s*(.*)$/.exec(text);
    if (!m) return null;
    var icons = { search: "🔎", list: "📁", run: "▶️", finish: "🏁", plan: "🗒️", attempt: "🔁", localize: "🔎", create: "✏️", test: "🧪", repair: "🔧", read: "📄", edit: "✏️", write: "✏️", bash: "⌨️", grep: "🔎", glob: "🔎", list: "📁", patch: "✏️" };
    return h("div", { class: "cw-tool " + (m[1] === "✗" ? "bad" : m[1] === "…" ? "wait" : "ok") },
      h("span", { class: "cw-ico", text: icons[m[2]] || "🛠" }), h("b", { text: m[2] }), h("span", { class: "cw-target", text: m[3] }), h("span", { class: "cw-mark", text: m[1] }));
  }
  /* "다른 모델로 다시 시도…": the user picks one of the models the composer already offers; the old run stays. Paid models stay refused by the server. */
  function otherModelButton(r) {
    var wrap = h("span", { class: "cw-pop-wrap" });
    var menu = h("div", { class: "cw-pop", role: "menu", hidden: true });
    var btn = h("button", { type: "button", class: "btn", text: "다른 모델로 다시 시도…", "aria-haspopup": "true", "aria-expanded": "false",
      title: "같은 요청을 내가 고른 다른 모델로 새 복사본에서 다시 합니다(이 작업은 그대로 남습니다). 더 큰 모델은 보통 더 정확하지만 더 느립니다." });
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      var was = menu.hidden; closePops(); if (!was) return;
      menu.textContent = ""; menu.appendChild(h("div", { class: "mh", text: "이 모델로 새 작업을 시작합니다" }));
      var options = [].filter.call($("code-model").options, function (o) { return o.value && o.value !== "auto" && o.value !== r.model && !o.disabled && !((r.mode === "direct" || r.mode === "loop") && o.value.indexOf("opencode/") === 0); });
      if (!options.length) menu.appendChild(h("div", { class: "mh", text: "고를 수 있는 다른 모델이 없습니다. 설정에서 서비스를 연결하거나 내장 엔진을 설치하세요." }));
      options.forEach(function (o) {
        menu.appendChild(h("button", { type: "button", role: "menuitem", title: o.dataset.warn || "", text: o.text, onclick: async function () {
          closePops();
          try { var n = await api("POST", "/code/runs/" + r.id + "/rerun", { model: o.value }); codeOpen = n.id; codeStick = true; toast(o.value + "(으)로 새 작업을 시작했습니다."); }
          catch (err) { toast(err.message); }
          renderCode();
        } }));
      });
      menu.hidden = false; btn.setAttribute("aria-expanded", "true");
    });
    wrap.append(btn, menu);
    return wrap;
  }
  function codeActions(r) {
    var id = r.id, row = h("div", { class: "cw-actions" });
    function btn(label, name, primary) { row.appendChild(h("button", { type: "button", class: "btn" + (primary ? " primary" : ""), text: label, onclick: function () { codeAction(id, name); } })); }
    var checking = r.checks && r.checks.state === "running", reviewing = r.review && r.review.state === "running";
    if (r.status === "running") btn("취소", "cancel");
    if (r.status === "review") btn("변경을 내 폴더에 적용", "apply", true);
    if (["review", "nochange", "failed"].indexOf(r.status) >= 0 && !checking) btn(r.checks ? "빌드·테스트 다시" : "빌드·테스트", "check");
    if (r.files && r.files.length && r.status !== "running" && !reviewing) btn(r.review ? "코드 리뷰 다시" : "코드 리뷰", "review");
    if (r.status === "applied") btn("적용 되돌리기", "undo");
    if (["review", "nochange", "failed", "cancelled"].indexOf(r.status) >= 0 && !checking) row.appendChild(otherModelButton(r));
    if (["review", "nochange", "failed", "cancelled"].indexOf(r.status) >= 0 && !checking) row.appendChild(h("button", { type: "button", class: "btn", text: r.status === "failed" ? "다시 시도(새 작업)" : "다시 실행(새 작업)",
      title: "같은 요청과 설정으로 새 복사본에서 새 작업을 시작합니다(이 작업은 그대로 남습니다)",
      onclick: async function () { try { var n = await api("POST", "/code/runs/" + id + "/rerun", {}); codeOpen = n.id; codeStick = true; toast("새 작업을 시작했습니다."); } catch (e) { toast(e.message); } renderCode(); } }));
    if (r.status !== "running" && r.status !== "discarded") btn(r.status === "applied" ? "복사본 정리" : "복사본 정리(변경 버리기)", "discard");
    return row;
  }
  /* ---------- run detail: summary card and the plan / work / verify timeline (built only from data JetAI already records) ---------- */
  var STAGE_OF = { plan: 0, localize: 0, list: 0, search: 0, read: 0, glob: 0, grep: 0, attempt: 0, edit: 1, patch: 1, create: 1, write: 1, repair: 1, step: 1, run: 2, bash: 2, test: 2, check: 2, finish: 2 };
  var STAGE_NAMES = ["① 계획·탐색", "② 수정", "③ 실행·검증"];
  function fmtDur(sec) { sec = Math.max(0, Math.round(sec)); return sec >= 3600 ? Math.floor(sec / 3600) + "시간 " + Math.floor(sec % 3600 / 60) + "분" : sec >= 60 ? Math.floor(sec / 60) + "분 " + (sec % 60) + "초" : sec + "초"; }
  function runSummaryCard(r) {
    var s = r.stats || {}, modeName = { agent: "에이전트(OpenCode)", direct: "직접 모드", loop: "액션 루프(실험)" }[r.mode] || "에이전트(OpenCode)";
    var end = r.finished || (Date.now() / 1000), seconds = r.started ? end - r.started : 0;
    var tokens;
    if (s.prompt_tokens != null) tokens = "입력 " + fmt(s.prompt_tokens) + " · 출력 " + fmt(s.completion_tokens || 0) + " (서비스가 센 값)";
    else if (s.tokens_est) tokens = "약 " + fmt(s.tokens_est) + " (추정)";
    else tokens = "기록 없음" + (r.mode === "agent" || !r.mode ? " (에이전트 방식은 실행기가 알려 주지 않습니다)" : "");
    var rows = [["모델", r.model || "-"], ["방식", modeName + (r.attempts > 1 ? " · 최대 " + r.attempts + "회 시도" : "")], [r.status === "running" ? "경과" : "걸린 시간", r.started ? fmtDur(seconds) : "기록 없음"], ["토큰", tokens]];
    if (s.calls != null) rows.push(["모델 호출", s.calls + "회" + (s.steps != null ? " · 단계 " + s.steps : "")]);
    var ck = r.checks && r.checks.verdict ? { passed: "✅ 통과", failed: "❌ 실패", none: "검사할 명령 없음", unavailable: "실행 도구 없음", notests: "실행된 테스트 없음" }[r.checks.verdict] : "";
    if (ck) rows.push(["빌드·테스트", ck]);
    var card = h("div", { class: "cw-sum" });
    rows.forEach(function (row) { card.appendChild(h("div", { class: "cw-sum-i" }, h("span", { class: "sub", text: row[0] }), h("b", { text: row[1] }))); });
    if (r.escalated_from) card.appendChild(h("div", { class: "cw-sum-i" }, h("span", { class: "sub", text: "자동 재시도" }), h("b", { text: "이전 작업이 실패해 9B로 자동 시작됨" })));
    if (r.escalated_to) card.appendChild(h("div", { class: "cw-sum-i" }, h("span", { class: "sub", text: "자동 재시도" }), h("b", { text: "실패해서 9B로 새 작업이 자동 시작됨(작업 목록에서 찾아보세요)" })));
    if (s.cut_calls) card.appendChild(h("div", { class: "cw-sum-i warn" }, h("span", { class: "sub", text: "입력 잘림" }), h("b", { text: s.cut_calls + "회 (모델이 일부를 못 봤을 수 있음)" })));
    return card;
  }
  function stageOf(text) { var m = /^(✓|✗|…)\s+(\S+)/.exec(text); return m && m[2] !== "attempt" ? (STAGE_OF[m[2]] !== undefined ? STAGE_OF[m[2]] : 1) : -1; }   // an "attempt" marker stays with the step before it
  function buildTimeline(lines, running) {
    var wrap = h("div", { class: "cw-stages" }), groups = [];
    lines.forEach(function (l, at) {
      if (/^…\s/.test(l) && lines.slice(at + 1).some(function (x) { return x.slice(1).trim() === l.slice(1).trim(); })) return;   // finished later: show only the result
      var st = stageOf(l), last = groups[groups.length - 1];
      if (st < 0) st = last ? last.stage : 0;                                                          // a plain message stays with the step it belongs to
      if (!last || last.stage !== st) { last = { stage: st, items: [], bad: 0 }; groups.push(last); }
      var tool = toolLine(l);
      if (tool) { last.items.push(tool); if (/^✗/.test(l)) last.bad++; } else last.items.push(h("div", { class: "cw-text", text: l.replace(/^💬\s*/, "") }));
    });
    groups.forEach(function (g, i) {
      var det = h("details", { class: "cw-stage" + (g.bad ? " bad" : "") }, h("summary", {}, h("b", { text: STAGE_NAMES[g.stage] }), h("span", { class: "sub", text: "  " + g.items.length + "줄" + (g.bad ? " · 실패 " + g.bad : "") })));
      g.items.forEach(function (it) { det.appendChild(it); });
      det.open = g.bad > 0 || i === groups.length - 1 && (running || groups.length === 1);
      wrap.appendChild(det);
    });
    return wrap;
  }
  async function renderCodeDetail(id) {
    var box = $("code-stream"), r;
    try { r = await api("GET", "/code/runs/" + id); } catch (e) { box.textContent = e.message; return; }
    var keep = box.scrollTop;
    box.textContent = "";
    var you = h("div", { class: "cw-msg you" }, h("div", { class: "cw-who", text: "나" }), h("div", { class: "cw-text", text: r.prompt }),
      h("p", { class: "sub", text: (r.project_name ? r.project_name + " · " : "") + r.repo + " · " + r.model + " · 작업 지침: " + ({ off: "없음", short: "짧게", full: "전체" }[r.guidance] || "없음") + (r.repo_was_dirty ? " · ⚠ 폴더에 저장하지 않은 변경이 있었습니다(복사본에는 없음)" : "") }));
    if (r.project) you.appendChild(h("details", { class: "cw-fold" }, h("summary", { text: "JetAI가 미리 읽은 프로젝트 요약(에이전트에 함께 전달됨)" }), h("pre", { class: "sub", text: r.project })));
    box.appendChild(you);
    box.appendChild(runSummaryCard(r));
    var agent = h("div", { class: "cw-msg agent" }, h("div", { class: "cw-who" }, (r.mode === "direct" ? "직접 모드 " : r.mode === "loop" ? "액션 루프 " : "에이전트 "), codeBadge(r.status)));
    var lines = r.activity || [];
    if (lines.length) agent.appendChild(buildTimeline(lines, r.status === "running"));
    if (r.status === "running" && !(r.activity || []).length) agent.appendChild(h("div", { class: "sub", text: "시작하는 중…" }));
    if (r.status === "running") agent.appendChild(h("div", { class: "cw-typing", "aria-label": "작업 중" }, h("i"), h("i"), h("i")));
    if (r.stats && r.stats.cut_calls) box.appendChild(h("div", { class: "cw-card warn" }, h("p", { text: "⚠ 입력이 잘렸을 수 있습니다(" + r.stats.cut_calls + "회). 모델이 일부를 보지 못했을 수 있어요. 모델 옆 \"입력 한도 점검\"으로 확인하세요." })));
    if (r.error) agent.appendChild(h("p", { class: r.status === "failed" ? "err" : "sub", text: r.error }));
    box.appendChild(agent);
    (r.blocked || []).forEach(function (b) {
      var card = h("div", { class: "cw-card warn" }, h("p", {}, "🚫 막힌 명령: ", h("code", { text: b.command })),
        h("p", { class: "sub", text: b.state !== "open" ? ({ once: "이번만 허용함", always: "이 프로젝트에서 항상 허용함", declined: "거절함" }[b.state] || b.state) : (b.level === "never" ? "허용할 수 없는 명령입니다: " + b.note : b.level === "once" ? b.note : "이 명령을 허용하면 같은 복사본에서 이어서 작업합니다.") }));
      if (b.state === "open" && b.level !== "never" && r.status !== "running") {
        var acts = h("div", { class: "cw-actions" }, h("button", { type: "button", class: "btn", text: "이번만 허용", onclick: function () { codeDecide(id, b, "once", "이 명령을 이번 한 번만 허용하고 이어서 실행할까요?\n" + b.command); } }));
        if (b.level === "normal") acts.appendChild(h("button", { type: "button", class: "btn", text: "이 프로젝트에서 항상 허용", onclick: function () { codeDecide(id, b, "always", "이 프로젝트에서 이 명령을 항상 허용할까요?\n" + b.command); } }));
        acts.appendChild(h("button", { type: "button", class: "btn", text: "거절", onclick: function () { codeDecide(id, b, "decline"); } }));
        card.appendChild(acts);
      }
      box.appendChild(card);
    });
    var per = splitPatch(r.patch);
    renderChanges(r, per);
    if (r.files && r.files.length && window.innerWidth > 800) {
      box.appendChild(h("div", { class: "cw-card" }, h("p", {}, h("b", { text: "바뀐 파일 " + r.files.length + "개 " }), h("span", { class: "sub", text: r.stat })),
        h("button", { type: "button", class: "btn", text: "변경 사항 보기", onclick: function () { toggleChanges(true); } })));
    } else if (r.files && r.files.length) {
      var panel = h("div", { class: "cw-card" }, h("p", {}, h("b", { text: "바뀐 파일 " }), h("span", { class: "sub", text: r.stat })));
      r.files.forEach(function (f) {
        var kind = { A: "추가", M: "수정", D: "삭제", R: "이름변경" }[f.status] || f.status;
        var det = h("details", { class: "cw-file" }, h("summary", {}, h("span", { class: "cw-fk", text: kind }), " " + f.path, h("span", { class: "sub", text: f.binary ? " (바이너리)" : "  +" + f.added + " −" + f.deleted })));
        if (per[f.path]) det.appendChild(diffView(per[f.path].join("\n")));
        if (r.files.length <= 3) det.open = true;
        panel.appendChild(det);
      });
      if (r.patch_truncated) panel.appendChild(h("p", { class: "sub", text: "(diff가 길어 앞부분만 보입니다. 적용하면 전체가 적용됩니다.)" }));
      box.appendChild(panel);
    }
    var ck = r.checks;
    if (ck) {
      var verdictText = { passed: "✅ 통과", failed: "❌ 실패", none: "검사할 명령이 감지되지 않았습니다", unavailable: "실행 도구가 없어 못 했습니다", notests: "실행된 테스트가 없습니다(통과로 보지 않음)", "": "진행 중…" }[ck.verdict || ""] || ck.verdict;
      box.appendChild(h("div", { class: "cw-card" }, h("p", {}, "빌드·테스트(JetAI가 복사본에서 직접 실행): ", h("b", { text: ck.state === "running" ? "실행 중…" : verdictText })),
        (ck.items || []).map(function (c) { return h("details", { class: "cw-fold" }, h("summary", { text: ({ passed: "✓", failed: "✗", timeout: "⏱", unavailable: "—", notests: "∅" }[c.status] || "?") + " " + c.kind + " · " + c.command + " (" + c.seconds + "초)" }), h("pre", { class: "sub", text: c.output || "(출력 없음)" })); })));
    }
    var rv = r.review;
    if (rv) {
      var rvName = { ok: "✅ 문제 없어 보임", caution: "⚠ 주의", problem: "❌ 문제 있음", unknown: "판단 불가(답을 읽지 못함)" }[rv.verdict] || "";
      var rvBox = h("div", { class: "cw-card" }, h("p", {}, "코드 리뷰(" + rv.model + "): ", h("b", { text: rv.state === "running" ? "리뷰 중…" : rv.state === "failed" ? "실패: " + (rv.error || "") : rvName })));
      if (rv.state === "done") {
        if (rv.summary) rvBox.appendChild(h("p", { class: "sub", text: rv.summary }));
        (rv.issues || []).forEach(function (i) { rvBox.appendChild(h("p", { class: "sub", text: "[" + i.severity + "] " + i.where + ": " + i.text })); });
        (rv.missing || []).forEach(function (m) { rvBox.appendChild(h("p", { class: "sub", text: "빠진 것: " + m })); });
        rvBox.appendChild(h("p", { class: "sub", text: "리뷰는 참고용입니다. 자동으로 고치거나 막지 않습니다." }));
      }
      box.appendChild(rvBox);
    }
    if (r.status !== "running") box.appendChild(webCheckCard(r));
    if (r.report) box.appendChild(h("details", { class: "cw-fold" }, h("summary", { text: "작업 보고서(Markdown)" }), h("pre", { class: "sub", text: r.report })));
    box.appendChild(codeActions(r));
    box.scrollTop = codeStick ? box.scrollHeight : keep;
  }

  function webCheckCard(r) {
    var wc = r.webcheck || {};
    var url = h("input", { type: "text", "aria-label": "확인할 주소", placeholder: "http://localhost:5173  (이 PC의 개발 서버)", value: wc.url || "" });
    var btn = h("button", { type: "button", class: "btn", text: wc.state === "running" ? "확인 중…" : "화면 확인", disabled: wc.state === "running",
      title: "설치된 Chrome/Edge를 임시 프로필로 열어 화면·콘솔 오류를 읽습니다. 클릭·입력은 하지 않습니다.",
      onclick: async function () { btn.disabled = true; try { await api("POST", "/code/runs/" + r.id + "/webcheck", { url: url.value.trim() }); codeStick = true; renderCode(); } catch (e) { toast(e.message); btn.disabled = false; } } });
    var card = h("div", { class: "cw-card" }, h("p", {}, h("b", { text: "웹 화면 확인 " }), h("span", { class: "sub", text: "(읽기만 합니다: 클릭·입력 없음, localhost만)" })), h("div", { class: "cw-opts" }, url, btn));
    if (wc.state === "done") {
      if (!wc.ok) card.appendChild(h("p", { class: "err", text: wc.error || "확인하지 못했습니다." }));
      else {
        card.appendChild(h("p", {}, h("b", { text: wc.title || "(제목 없음)" }), h("span", { class: "sub", text: "  " + wc.url + " · 눌러 볼 요소 " + wc.elements + "개" })));
        card.appendChild(h("p", { class: wc.problems && wc.problems.length ? "err" : "sub", text: wc.problems && wc.problems.length ? "문제 " + wc.problems.length + "건" : "콘솔 오류·실패한 요청 없음" }));
        (wc.problems || []).forEach(function (p) { card.appendChild(h("p", { class: "sub", text: "• " + p })); });
        if (r.webcheck_image) card.appendChild(h("img", { class: "cw-shot", alt: "화면 캡처", src: "data:image/png;base64," + r.webcheck_image }));
        if (wc.text) card.appendChild(h("details", { class: "cw-fold" }, h("summary", { text: "화면에 보이는 글(페이지가 쓴 내용이라 지시로 취급하지 않습니다)" }), h("pre", { class: "sub", text: wc.text })));
      }
    }
    return card;
  }

  /* ---------- phone control (settings) ---------- */
  var remoteTimer = null;
  async function renderRemote() {
    var box = $("rem-body"); if (!box) return;
    var s; try { s = await api("GET", "/remote"); } catch (e) { box.textContent = e.message; return; }
    box.textContent = "";
    clearInterval(remoteTimer); remoteTimer = null;
    if (!s.configured) {
      var tok = h("input", { type: "password", id: "rem-token", maxlength: "100", autocomplete: "off", spellcheck: "false", placeholder: "BotFather가 준 토큰 붙여넣기 (예: 123456789:AA…)" });
      box.appendChild(h("ol", { class: "rem-steps" },
        h("li", { text: "폰의 텔레그램에서 @BotFather를 열고 /newbot 으로 봇을 만듭니다." }),
        h("li", { text: "받은 토큰을 아래에 붙여 넣고 연결합니다." }),
        h("li", { text: "표시되는 6자리 코드를 만든 봇에게 /start 코드 로 보냅니다. 처음 보낸 계정만 주인이 됩니다." })));
      box.appendChild(h("div", { class: "row" }, tok, h("button", { type: "button", class: "btn primary", id: "rem-connect", text: "연결", onclick: async function () {
        var b = this; b.disabled = true;
        try { var r = await api("POST", "/remote/telegram", { token: tok.value.trim() }); tok.value = ""; toast("봇 " + r.bot + " 연결됨. 코드를 봇에게 보내세요."); state.remoteCode = r.code; renderRemote(); }
        catch (e) { toast(e.message); b.disabled = false; } } })));
      return;
    }
    if (s.paired) {
      box.appendChild(h("p", { text: "연결됨: 봇 " + s.bot + " · 주인 " + (s.owner || "") + (s.running ? " · 실행 중" : " · 멈춤") + (s.last_error ? " · " + s.last_error : "") }));
      box.appendChild(h("p", { class: "sub", text: "폰에서 /help 를 보내 보세요. 글을 그냥 보내면 자동 선택으로 답합니다." }));
    } else {
      box.appendChild(h("p", { text: "봇 " + s.bot + " 에게 아래 코드를 보내 주세요:" }));
      box.appendChild(h("p", { class: "rem-code", text: s.pairing ? "/start " + (state.remoteCode || "(코드 다시 만들기를 누르세요)") : "코드가 만료되었습니다. 다시 만드세요." }));
      remoteTimer = setInterval(renderRemote, 3000);                // wait until the phone has paired
    }
    var actions = h("div", { class: "row" });
    if (!s.paired) actions.appendChild(h("button", { type: "button", class: "btn", text: "코드 다시 만들기", onclick: async function () {
      try { var r = await api("POST", "/remote/repair", {}); state.remoteCode = r.code; renderRemote(); } catch (e) { toast(e.message); } } }));
    actions.appendChild(h("button", { type: "button", class: "btn", text: "연결 해제(토큰 삭제)", onclick: async function () {
      if (!confirm("원격 제어를 끄고 봇 토큰을 지울까요?")) return;
      try { await api("POST", "/remote/disconnect", {}); state.remoteCode = ""; renderRemote(); } catch (e) { toast(e.message); } } }));
    box.appendChild(actions);
  }

  /* ---------- built-in local engine (settings) ---------- */
  var engineTimer = null;
  function fmtBytes(n) { return n >= 1e9 ? (n / 1e9).toFixed(2) + " GB" : (n / 1e6).toFixed(0) + " MB"; }
  async function renderEngine() {
    var box = $("eng-list"); if (!box) return;
    var s; try { s = await api("GET", "/engine"); } catch (e) { box.textContent = e.message; return; }
    var hw = s.hardware;
    $("eng-hw").textContent = "이 PC: 메모리 " + hw.ram_gb + "GB" + (hw.gpu ? " · 그래픽 " + hw.gpu + " " + hw.vram_gb + "GB" : " · 그래픽 카드 없음(CPU로 실행)") +
      (s.binary ? " · 엔진 설치됨(" + s.binary.tag + ", " + s.binary.variant + ")" : " · 엔진 미설치") +
      (s.running ? (s.ready ? " · 실행 중(포트 " + s.port + ")" : " · 시작하는 중…") : "");
    box.textContent = "";
    if (!s.supported) { box.textContent = "이 운영체제에서는 쓸 수 없습니다(Windows·Linux)."; return; }
    s.models.forEach(function (m) {
      var row = h("div", { class: "eng-row" }, h("b", { text: m.label }),
        h("span", { class: "sub", text: " " + m.fit_note + (m.recommended ? " · 추천" : "") + " · " + m.license }));
      if (!m.installed) row.appendChild(h("button", { type: "button", class: "btn mini", text: "설치…", disabled: m.fit === "no" ? "disabled" : false, onclick: function () { engineAsk(m.id); } }));
      else {
        var on = s.active === m.id && s.running;
        row.appendChild(h("button", { type: "button", class: "btn mini" + (on ? " on" : ""), text: on ? (s.ready ? "사용 중" : "시작 중…") : "이 모델로 시작", disabled: on ? "disabled" : false,
          onclick: async function () { try { await api("POST", "/engine/start", { model: m.id }); toast("시작합니다. 잠시 걸릴 수 있습니다."); setTimeout(renderEngine, 1500); engineWatchReady(); } catch (e) { toast(e.message); } } }));
        row.appendChild(h("button", { type: "button", class: "btn mini", text: "삭제", onclick: async function () {
          if (!confirm(m.label + " 파일을 지울까요? (" + fmtBytes(m.bytes || 0) + ")")) return;
          try { await api("POST", "/engine/remove", { what: m.id }); await loadState(); renderEngine(); } catch (e) { toast(e.message); } } }));
      }
      box.appendChild(row);
    });
    $("eng-stop").hidden = !s.running; $("eng-clear").hidden = !s.binary && !s.models.some(function (m) { return m.installed; });
    $("eng-stop").onclick = async function () { try { await api("POST", "/engine/stop", {}); await loadState(); renderEngine(); } catch (e) { toast(e.message); } };
    $("eng-clear").onclick = async function () {
      if (!confirm("내장 엔진과 내려받은 모든 모델을 지울까요?")) return;
      try { await api("POST", "/engine/remove", { what: "all" }); await loadState(); renderEngine(); } catch (e) { toast(e.message); } };
    var auto = $("eng-auto"); auto.checked = !state.settings || state.settings.engine_autostart !== false;
    auto.onchange = async function () { try { state.settings = (await api("POST", "/settings", { engine_autostart: auto.checked })).settings; } catch (e) { toast(e.message); auto.checked = !auto.checked; } };
    paintEngineJob(s.job);
    if (s.job && s.job.running && !engineTimer) engineTimer = setInterval(engineTick, 1000);
  }
  function paintEngineJob(job) {
    var box = $("eng-job"); if (!box) return;
    if (!job || (!job.running && !job.finished) || (job.finished && !job.error && !job.running && !box.dataset.show)) { box.hidden = true; return; }
    box.hidden = false; box.textContent = "";
    var pct = job.total ? Math.min(100, Math.round(100 * job.done / job.total)) : 0;
    box.appendChild(h("div", {}, h("b", { text: job.error ? "실패: " + job.error : job.finished ? "설치 완료" : job.step }),
      h("span", { class: "sub", text: job.error || job.finished ? "" : "  " + fmtBytes(job.done) + " / " + fmtBytes(job.total) + " (" + pct + "%)" })));
    if (!job.finished) {
      box.appendChild(h("div", { class: "bar eng-bar" }, h("i", { style: "width:" + pct + "%" })));
      box.appendChild(h("button", { type: "button", class: "btn mini", text: "취소", onclick: function () { api("POST", "/engine/cancel", {}); } }));
    }
  }
  async function engineTick() {
    try {
      var s = await api("GET", "/engine"); paintEngineJob(s.job);
      if (!s.job.running) {
        clearInterval(engineTimer); engineTimer = null; $("eng-job").dataset.show = "1"; paintEngineJob(s.job);
        if (!s.job.error) toast("설치가 끝났습니다. '이 모델로 시작'을 눌러 주세요.");
        renderEngine();
      }
    } catch (e) { clearInterval(engineTimer); engineTimer = null; }
  }
  function engineWatchReady() {
    var n = 0, t = setInterval(async function () {
      n += 1;
      try { var s = await api("GET", "/engine"); if (s.ready || !s.running || n > 120) { clearInterval(t); await loadState(); renderEngine(); if (s.ready) toast("엔진이 준비되었습니다. 이제 자동 선택이 이 모델도 씁니다."); } } catch (e) { clearInterval(t); }
    }, 1500);
  }
  async function engineAsk(modelId) {
    var plan; try { plan = await api("POST", "/engine/plan", { model: modelId }); } catch (e) { toast(e.message); return; }
    if (plan.already_installed) { toast("이미 설치되어 있습니다."); return; }
    var box = $("helpbox"); box.textContent = ""; box.hidden = false;
    var card = h("div", { class: "card help-card", role: "dialog", "aria-modal": "true", "aria-label": "내려받기 확인" },
      h("header", {}, h("h2", { text: "인터넷에서 파일을 내려받습니다" }), h("button", { class: "icon", "aria-label": "닫기", text: "\u2715", onclick: closeHelp })));
    plan.items.forEach(function (it) {
      card.appendChild(h("div", { class: "help-row" }, h("b", { text: (it.kind === "binary" ? "실행 엔진(llama.cpp) " : "모델 파일 ") + fmtBytes(it.bytes) }),
        h("p", { text: it.name + " — 출처: " + it.source + (it.kind === "model" ? " · 라이선스 " + plan.license : "") })));
    });
    card.appendChild(h("div", { class: "help-row" }, h("b", { text: "합계 " + fmtBytes(plan.total_bytes) }),
      h("p", { text: "디스크 여유 " + fmtBytes(plan.disk_free_bytes) + (plan.enough_disk ? "" : " — 공간이 부족해 받을 수 없습니다") + ". 받은 파일은 SHA-256으로 검증하고, 이 PC의 JetAI 폴더(~/.agent-workshop/engine)에만 저장합니다. 엔진은 이 PC 안(127.0.0.1)에서만 실행됩니다." })));
    var go = h("button", { type: "button", class: "btn primary", text: "받고 설치 (" + fmtBytes(plan.total_bytes) + ")", disabled: plan.enough_disk ? false : "disabled", onclick: async function () {
      go.disabled = true;
      try { await api("POST", "/engine/install", { model: modelId, confirm_bytes: plan.total_bytes }); closeHelp(); renderEngine(); if (!engineTimer) engineTimer = setInterval(engineTick, 1000); }
      catch (e) { toast(e.message); go.disabled = false; } } });
    card.appendChild(h("div", { class: "foot" }, h("button", { type: "button", class: "btn", text: "취소", onclick: closeHelp }), go));
    box.appendChild(card);
  }

  /* ---------- settings view (native; replaces the old framed setup page) ---------- */
  /* ---------- free-quota guard (services whose free allowance cannot be read from an API) ---------- */
  async function renderQuota() {
    var box = $("quota-body"); if (!box) return;
    var d; try { d = await api("GET", "/quota"); } catch (e) { box.textContent = e.message; return; }
    box.textContent = "";
    function when(t) { return t ? new Date(t * 1000).toLocaleString() : "—"; }
    function post(body, done) { return async function () { try { await api("POST", "/quota", body()); if (done) toast(done); } catch (e) { toast(e.message); } renderQuota(); }; }
    d.guards.forEach(function (g) {
      var s = g.settings, card = h("div", { class: "cw-card" });
      var state = g.open ? "✅ 사용 가능" : g.enabled ? "⛔ 차단됨" : "⏸ 꺼짐";
      card.appendChild(h("p", {}, h("b", { text: g.label + " — " + state })));
      if (!g.open) card.appendChild(h("p", { class: "sub", text: "이유: " + g.reason }));
      card.appendChild(h("p", { class: "sub", text: g.registered.length ? "연결됨: " + g.registered.join(", ") + " · 모델 mercury-3" : "아직 연결되지 않았습니다. 서비스 연결에서 'Mercury 3'를 고르고 키를 넣으세요." }));
      var rows = [
        ["확인된 무료 잔액", g.balance_label + (g.balance_checked_at ? " · 마지막 확인 " + when(g.balance_checked_at) : "")],
        ["누적 사용(JetAI 기준)", g.total_tokens + " 토큰 · 이번 세션 " + g.session_tokens],
        ["오늘 / 이번 달", g.day_tokens + " / " + s.daily_soft_limit_tokens + " · " + g.month_tokens + " / " + s.monthly_soft_limit_tokens + " (내부 한도)"],
        ["소진 예상", g.balance_known ? (g.may_run_out_today ? "오늘 내부 한도의 80%를 넘었습니다" : "여유 있음") : "확인 불가"],
        ["크레딧 기한", g.valid_until ? when(g.valid_until) : "입력 안 함"]];
      rows.forEach(function (r) { card.appendChild(h("p", { class: "sub" }, h("b", { text: r[0] + ": " }), r[1])); });
      if (g.last_error) card.appendChild(h("p", { class: "sub", text: "마지막 오류: " + g.last_error }));
      card.appendChild(h("p", { class: "sub", text: "JetAI가 세는 사용량은 공급자의 실제 잔액이 아닙니다. 같은 키를 다른 곳에서 쓰면 달라지므로, 잔액은 정기적으로 공급자 화면에서 다시 확인해 입력하세요(만료되면 차단)." }));
      var attest = h("input", { type: "checkbox", id: "q-attest-" + g.id, checked: !!g.attested_no_payment_at });
      attest.onchange = post(function () { return { guard: g.id, action: "attest", confirmed: attest.checked }; });
      card.appendChild(h("label", { class: "check" }, attest, h("span", { text: " 공급자 계정에 결제수단(카드)이 등록되어 있지 않음을 직접 확인했습니다" + (g.attested_no_payment_at ? " (" + when(g.attested_no_payment_at) + ")" : "") })));
      var bal = h("input", { type: "number", min: "0", step: "1", id: "q-bal-" + g.id, placeholder: "공급자 화면의 남은 무료 토큰 수" });
      var until = h("input", { type: "date", id: "q-until-" + g.id, "aria-label": "무료 크레딧 사용 기한(알면)" });
      card.appendChild(h("div", { class: "row" }, bal, until, h("button", { type: "button", class: "btn", text: "잔액 저장", onclick: post(function () {
        return { guard: g.id, action: "balance", tokens: parseInt(bal.value, 10), valid_until: until.value ? Math.floor(new Date(until.value + "T23:59:59").getTime() / 1000) : 0 };
      }, "잔액을 저장했습니다.") })));
      card.appendChild(h("div", { class: "row" }, h("button", { type: "button", class: "btn" + (g.enabled ? "" : " primary"), text: g.enabled ? "끄기" : "켜기",
        onclick: post(function () { return { guard: g.id, action: "enable", enabled: !g.enabled }; }, g.enabled ? "껐습니다." : "켰습니다.") })));
      var names = [["max_concurrent_requests", "동시 요청"], ["max_request_output_tokens", "요청당 최대 출력"], ["daily_soft_limit_tokens", "일일 내부 한도"], ["monthly_soft_limit_tokens", "월간 내부 한도"], ["safety_margin_percent", "여유분 %"], ["timeout_seconds", "제한 시간(초)"]];
      var inputs = names.map(function (n) { return h("label", { class: "field" }, h("span", { text: n[1] }), h("input", { type: "number", id: "q-" + n[0] + "-" + g.id, value: s[n[0]] })); });
      card.appendChild(h("details", { class: "cw-fold" }, h("summary", { text: "내부 한도 설정" }), inputs, h("button", { type: "button", class: "btn", text: "저장", onclick: post(function () {
        var values = {}; names.forEach(function (n) { values[n[0]] = parseInt($("q-" + n[0] + "-" + g.id).value, 10); }); return { guard: g.id, action: "limits", values: values };
      }, "저장했습니다.") })));
      box.appendChild(card);
    });
  }
  async function renderSettings() {
    var list = $("set-list");
    renderEngine(); renderRemote(); renderQuota();
    var auto = $("set-auto");
    if (auto) {
      auto.checked = !state.settings || state.settings.auto_add_models !== false;
      auto.onchange = async function () {
        try { state.settings = (await api("POST", "/settings", { auto_add_models: auto.checked })).settings; toast(auto.checked ? "새 모델을 자동으로 추가합니다" : "새 모델 자동 추가를 껐습니다"); }
        catch (e) { toast(e.message); auto.checked = !auto.checked; }
      };
      $("set-check").onclick = async function () {
        var btn = $("set-check"), msg = $("set-check-msg"); btn.disabled = true; msg.textContent = "확인하는 중…";
        try {
          var r = await api("POST", "/models/check", {});
          var feed = await api("POST", "/catalog/refresh", { force: true }).catch(function () { return null; });
          if (feed && feed.catalog) state.catalog = feed.catalog;
          var added = []; Object.keys(r.added || {}).forEach(function (k) { r.added[k].forEach(function (m) { added.push(k + "/" + m); }); });
          var fresh = 0; Object.keys(r.new || {}).forEach(function (k) { fresh += r.new[k]; });
          msg.textContent = r.skipped ? "방금 확인했습니다." : (added.length ? "추가: " + added.join(", ") : (fresh ? "새 모델 " + fresh + "개가 있지만 무료·채팅용이 아니거나 이미 있어 추가하지 않았습니다." : "새 모델이 없습니다.")) + " (서비스 " + r.checked + "곳 확인)" + (feed && feed.added && feed.added.length ? " · 새 무료 서비스 " + feed.added.length + "개 발견" : "");
          if (added.length) { await loadState(); renderSettings(); }
        } catch (e) { msg.textContent = e.message; }
        btn.disabled = false;
      };
    }
    loadEnv().then(function (env) {
      var node = $("set-env-text");
      if (!node) return;
      if (!env) { node.textContent = "확인하지 못했습니다."; return; }
      var parts = [env.os + " · Python " + env.python,
        env.ollama.running ? "Ollama 실행 중(모델 " + env.ollama.models.length + "개)" : "Ollama 없음(선택 사항)"];
      parts.push(env.opencode ? "OpenCode 있음" : "OpenCode 없음(선택 사항)");
      if (env.wsl.applicable) parts.push(env.wsl.available ? "WSL 사용 가능" : "WSL 없음(터미널·코드 작업에만 필요)");
      node.textContent = parts.join(" · ");
    });
    try { await loadState(); } catch (e) { /* keep what we have */ }
    list.textContent = "";
    if (!state.providers.length) { list.appendChild(h("p", { class: "sub", text: "아직 연결된 서비스가 없습니다. 위의 \"서비스 추가\"로 시작하세요." })); return; }
    state.providers.forEach(function (p) {
      var local = p.policy === "local";
      var badge = local ? "내 PC" : (p.consented ? "클라우드 · 전송 동의함" : "클라우드 · 동의 필요");
      var addModel = h("input", { type: "text", placeholder: "모델 이름 추가", "aria-label": "모델 이름 추가", maxlength: "200" });
      var msg = h("span", { class: "sub" });
      var card = h("div", { class: "svc-card", "data-provider": p.name },
        h("div", { class: "row" }, h("b", { text: p.label }), h("span", { class: "pill", text: badge })),
        h("div", { class: "sub", text: p.base_url + " · 토큰 " + p.token }),
        h("div", { class: "sub", text: "모델 " + p.models.length + "개" + (p.models.length ? ": " + p.models.slice(0, 6).join(", ") + (p.models.length > 6 ? " …" : "") : "") }),
        h("div", { class: "row" }, addModel,
          h("button", { type: "button", class: "btn", text: "추가", onclick: async function () {
            var m = addModel.value.trim(); if (!m) return;
            try { await api("POST", "/providers/models", { name: p.name, model: m }); await renderSettings(); } catch (e) { msg.textContent = e.message; } } })),
        h("div", { class: "row" },
          local ? h("span") : h("button", { type: "button", class: "btn", text: "계정(키) 관리", onclick: function () { openAccounts(); } }),
          h("button", { type: "button", class: "btn danger", "data-remove": p.name, text: "연결 해제", onclick: async function () {
            if (!window.confirm(p.label + " 연결을 해제할까요? 저장된 토큰도 함께 삭제됩니다.")) return;
            try { await api("POST", "/providers/remove", { name: p.name }); await loadState(); pickDefaultModel(); renderModelBtn(); await renderSettings(); } catch (e) { msg.textContent = e.message; } } })),
        msg);
      list.appendChild(card);
    });
  }
  $("set-add").addEventListener("click", function () { openWizard(); });

  /* ---------- accounts: several keys of one service, used in turn ---------- */
  function openAccounts() {
    var root = $("wizard"), msg = h("p", { class: "note", id: "acc-msg", text: "" });
    root.hidden = false;
    function paint() {
      root.textContent = "";
      var card = h("div", { class: "card", role: "dialog", "aria-modal": "true", "aria-label": "계정 관리" },
        h("header", {}, h("div", {}, h("h2", { text: "서비스별 계정(키) 관리" }),
          h("p", { class: "sub", text: "한 서비스에 키를 여러 개 두면, 한도(429)가 차거나 분당 요청 수를 넘을 때 다음 계정으로 넘어갑니다." })),
          h("button", { class: "icon", "aria-label": "닫기", text: "✕", onclick: function () { root.hidden = true; root.textContent = ""; } })));
      card.appendChild(h("p", { class: "note", text: "본인이 직접 발급받았고 서비스 약관이 허용하는 계정만 등록하세요(예: 개인 계정과 회사·팀 계정). 한 사람이 무료 한도를 늘리려고 계정을 여러 개 만드는 것은 대부분의 서비스가 금지하며 계정이 정지될 수 있습니다." }));
      var cloud = state.providers.filter(function (p) { return p.policy !== "local"; });
      if (!cloud.length) card.appendChild(h("p", { class: "note", text: "먼저 클라우드 서비스를 하나 연결하세요." }));
      cloud.forEach(function (p) {
        var box = h("section", { class: "acc-box", "data-provider": p.name }, h("b", { text: p.label }));
        (p.accounts || []).forEach(function (a) {
          box.appendChild(h("div", { class: "acc-row" }, h("span", { text: a.label + " · " + a.token + (a.rpm ? " · 분당 " + a.rpm + "회" : "") }),
            a.primary ? h("i", { text: "기본" }) : h("button", { type: "button", class: "btn", text: "삭제", onclick: async function () {
              try { await api("POST", "/providers/accounts/remove", { name: p.name, label: a.label }); await loadState(); paint(); } catch (e) { msg.textContent = e.message; } } })));
        });
        var label = h("input", { type: "text", maxlength: "20", placeholder: "별칭(예: 회사)", "aria-label": "별칭" });
        var key = h("input", { type: "password", autocomplete: "off", spellcheck: "false", maxlength: "512", placeholder: "토큰", "aria-label": "토큰" });
        var rpm = h("input", { type: "number", min: "0", max: "100000", placeholder: "분당 한도(0=제한 없음)", "aria-label": "분당 한도" });
        var own = h("input", { type: "checkbox", "aria-label": "본인 계정 확인" });
        box.appendChild(h("div", { class: "acc-add" }, label, key, rpm,
          h("label", { class: "opt" }, own, h("span", { text: "본인이 발급받았고 약관이 허용하는 계정입니다" })),
          h("button", { type: "button", class: "btn", "data-add": p.name, text: "계정 추가", onclick: async function () {
            try {
              await api("POST", "/providers/accounts", { name: p.name, label: label.value.trim(), key: key.value.trim(), rpm: Number(rpm.value) || 0, confirm_own: own.checked });
              await loadState(); paint(); $("acc-msg").textContent = "추가했습니다. 채팅에서 한도가 차면 이 계정으로 넘어갑니다.";
            } catch (e) { $("acc-msg").textContent = e.message; }
          } })));
        card.appendChild(box);
      });
      card.appendChild(msg);
      root.appendChild(h("div", { class: "card-wrap", style: "display:contents" }, card));
    }
    paint();
  }
  async function renderMoreModels(p) {
    var menu = $("modelmenu");
    menu.textContent = "";
    menu.appendChild(h("div", { class: "mh" }, h("button", { type: "button", text: "\u2190 " + p.label, onclick: function (e) { e.stopPropagation(); renderMenu(); } })));
    var status = h("div", { class: "mh", text: "모델 목록을 불러오는 중…" });
    var box = h("div", { id: "more-list" });
    var filter = h("input", { type: "search", id: "more-filter", placeholder: "모델 검색", "aria-label": "모델 검색", style: "width:calc(100% - 12px);margin:4px 6px;padding:8px 10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--fg);font:inherit" });
    menu.append(filter, status, box);
    var all = [];
    function paint() {
      box.textContent = "";
      var f = filter.value.trim().toLowerCase();
      all.filter(function (m) { return !f || m.toLowerCase().indexOf(f) >= 0; }).slice(0, 60).forEach(function (m) {
        box.appendChild(h("button", { type: "button", "data-pick": m, onclick: async function (e) {
          e.stopPropagation();
          try { if (p.models.indexOf(m) < 0) await api("POST", "/providers/models", { name: p.name, model: m }); await loadState(); await chooseModel(p.name + "/" + m); }
          catch (er) { toast(er.message); }
        } }, h("span", { text: m }), isFree(m) ? h("span", { class: "tag free", text: "무료" }) : null));
      });
    }
    filter.addEventListener("input", paint); filter.addEventListener("click", function (e) { e.stopPropagation(); });
    try { all = (await api("POST", "/onboard/models", { service: p.name })).models; status.textContent = all.length + "개 모델 · 이름이 -free/:free로 끝나면 무료"; paint(); filter.focus(); }
    catch (e) { status.textContent = "불러오지 못했습니다: " + e.message; }
  }
  $("modelbtn").addEventListener("click", function (e) {
    e.stopPropagation();
    if (!$("modelmenu").hidden) { closeMenu(); return; }
    renderMenu();
    $("modelmenu").hidden = false; $("modelbtn").setAttribute("aria-expanded", "true");
  });
  document.addEventListener("click", function (e) { if (!e.target.closest || !e.target.closest(".model-wrap")) closeMenu(); });

  /* ---------- onboarding wizard ---------- */
  function openWizard() {
    state.wiz = { step: 1, service: null, customName: "", customUrl: "", key: "", showKey: false, consent: false,
                  models: [], modelsError: "", model: "", filter: "", busy: false, error: "", result: null };
    $("wizard").hidden = false; renderWizard();
  }
  function closeWizard() { $("wizard").hidden = true; state.wiz = null; $("wizard").textContent = ""; }
  function svc() {
    var w = state.wiz;
    if (w.service === "custom") {
      var local = /^http:\/\/(127\.0\.0\.1|localhost)([:\/]|$)/.test(w.customUrl);
      return { id: "custom", label: "직접 입력 (OpenAI 호환)", policy: local ? "local" : "cloud-may-train", needs_key: !local, key_url: "",
               note: "OpenAI 호환 주소(…/v1)를 입력하세요. 이 PC(127.0.0.1)의 서버라면 토큰이 필요 없습니다." };
    }
    return state.catalog.filter(function (c) { return c.id === w.service; })[0];
  }
  function wizPayload(extra) {
    var w = state.wiz, p = { service: w.service };
    if (w.service === "custom") { p.custom_name = w.customName.trim(); p.custom_url = w.customUrl.trim(); }
    if (w.key) p.key = w.key.trim();
    if (w.consent) p.consent = "on";
    if (w.freeConfirmed) p.free_confirmed = "on";
    return Object.assign(p, extra || {});
  }
  function renderWizard() {
    var w = state.wiz, root = $("wizard");
    if (!w) return;
    root.textContent = "";
    var title = ["서비스 선택", "연결 정보", "모델 선택과 테스트"][w.step - 1];
    var card = h("div", { class: "card", role: "dialog", "aria-modal": "true", "aria-label": "서비스 연결" },
      h("header", {}, h("div", {}, h("h2", { text: "서비스 연결 · " + title }), h("p", { class: "sub", text: "토큰은 이 PC에만 저장되며 화면에 다시 나타나지 않습니다." })),
        h("button", { class: "icon", "aria-label": "닫기", text: "✕", onclick: function () { if (!w.busy) closeWizard(); } })),
      h("div", { class: "steps" }, [1, 2, 3].map(function (n) { return h("i", { class: n <= w.step ? "on" : "" }); })));
    if (w.step === 1) wizStep1(card, w); else if (w.step === 2) wizStep2(card, w); else wizStep3(card, w);
    root.appendChild(h("div", { class: "card-wrap", style: "display:contents" }, card));
  }
  async function loadEnv() { try { return await api("GET", "/env"); } catch (e) { return null; } }
  var GROUPS = [["local", "내 PC에서 (토큰 불필요)"], ["top", "무료 한도가 넉넉하고 잘 알려진 서비스"], ["more", "그 밖의 무료 서비스·게이트웨이 (한도가 작거나 자주 바뀜)"], ["new", "새로 발견된 서비스 (공개 목록에서 자동 수집 · 미검증)"]];
  function svcButton(c, w) {
    return h("button", { class: "svc", type: "button", "data-service": c.id, "data-search": (c.label + " " + c.id + " " + (c.limits || "") + " " + (c.note || "")).toLowerCase(),
      onclick: function () { w.service = c.id; w.step = 2; w.error = ""; renderWizard(); } },
      h("b", { text: c.label }), h("span", { text: c.policy === "local" ? "토큰 불필요 · 내 PC에서 실행" : "클라우드 · 토큰 필요" }),
      c.is_new ? h("span", { class: "tag free", text: "NEW" }) : null,
      c.limits ? h("span", { class: "lim", text: "무료 한도: " + c.limits }) : null,
      reachNote(c));
  }
  function reachNote(c) {
    var st = (state.catalog_status && state.catalog_status.services || {})[c.id];
    if (c.policy === "local") return null;
    if (!st) return h("span", { class: "lim", text: "응답 확인: 아직 안 함" });
    var day = new Date(state.catalog_status.checked_at * 1000).toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" });
    return h("span", { class: "lim", text: "응답 확인 " + day + ": " + (st.ok ? "응답함" : "응답 없음·주소 확인 필요") + " (무료 한도는 이 확인으로 알 수 없음)" });
  }
  function wizStep1(card, w) {
    var boxes = [];
    GROUPS.forEach(function (g) {
      var list = state.catalog.filter(function (c) { return (c.group || "more") === g[0]; });
      if (!list.length) return;
      boxes.push(h("div", { class: "svc-group", "data-group": g[0] }, h("h3", { text: g[1] + " (" + list.length + ")" }), h("div", { class: "grid" }, list.map(function (c) { return svcButton(c, w); }))));
    });
    boxes.push(h("div", { class: "svc-group", "data-group": "custom" }, h("h3", { text: "직접 입력" }), h("div", { class: "grid" },
      h("button", { class: "svc", type: "button", "data-service": "custom", "data-search": "직접 입력 custom openai", onclick: function () { w.service = "custom"; w.step = 2; w.error = ""; renderWizard(); } },
        h("b", { text: "직접 입력" }), h("span", { text: "OpenAI 호환 주소를 직접 지정" })))));
    var search = h("input", { type: "search", id: "w-search", placeholder: "서비스 검색 (예: groq, 무료, gemini)", "aria-label": "서비스 검색", oninput: function (e) {
      var q = e.target.value.trim().toLowerCase();
      Array.prototype.forEach.call(document.querySelectorAll("#wizard .svc"), function (b) { b.hidden = !!q && b.dataset.search.indexOf(q) < 0; });
      Array.prototype.forEach.call(document.querySelectorAll("#wizard .svc-group"), function (g) { g.hidden = !g.querySelector(".svc:not([hidden])"); });
    } });
    card.appendChild(h("div", { class: "row", style: "align-items:center;gap:8px" }, search,
      h("button", { type: "button", class: "btn mini", id: "w-reach", text: "응답 확인", title: "서비스마다 키 없이 주소 하나(/models)만 익명으로 요청해 지금 응답하는지 봅니다. 무료 한도는 알 수 없고, 아무것도 저장되지 않는 내용은 보내지 않습니다.",
        onclick: async function () {
          var b = this; b.disabled = true; b.textContent = "확인 중…";
          try {
            await api("POST", "/catalog/check", {});
            for (var i = 0; i < 40; i++) {                                           // the check runs in the background (a slow service can take a while)
              await new Promise(function (r) { setTimeout(r, 3000); });
              await loadState();
              if (!(state.catalog_status && state.catalog_status.running)) break;
            }
            toast("응답 확인을 마쳤습니다.");
          } catch (e) { toast(e.message); }
          renderWizard();
        } }),
      h("button", { type: "button", class: "btn mini", id: "w-refresh", text: "목록 새로 고침", title: "공개 목록에서 새로 생긴 무료 서비스를 찾아 목록에 더합니다", onclick: async function () {
        var b = this; b.disabled = true; b.textContent = "찾는 중…";
        try {
          var r = await api("POST", "/catalog/refresh", { force: true });
          state.catalog = r.catalog;
          toast(r.skipped === "offline" ? "인터넷에 연결되지 않아 목록을 갱신하지 못했습니다." : (r.added.length ? "새 무료 서비스 " + r.added.length + "개를 찾았습니다: " + r.added.slice(0, 3).join(", ") : "새로 생긴 서비스는 없습니다 (발견된 서비스 " + r.total + "개)"));
          renderWizard();
        } catch (e) { toast(e.message); b.disabled = false; b.textContent = "목록 새로 고침"; }
      } })));
    var envBox = h("p", { class: "note", id: "w-env", text: "이 PC를 확인하는 중…" });
    card.appendChild(envBox);
    loadEnv().then(function (env) {
      var node = $("w-env");
      if (!node) return;
      if (!env) { node.textContent = ""; return; }
      var parts = [];
      parts.push(env.ollama.running ? "Ollama 실행 중 (모델 " + env.ollama.models.length + "개) — 아래에서 Ollama를 고르면 토큰 없이 바로 쓸 수 있습니다."
                                    : "Ollama는 감지되지 않았습니다(선택 사항). 클라우드 무료 서비스로 먼저 시작해도 됩니다.");
      if (env.wsl.applicable) parts.push(env.wsl.available ? "WSL 사용 가능(터미널·코드 작업에 필요)." : "WSL 없음 — 채팅·압축·사용량은 WSL 없이 쓸 수 있습니다.");
      node.textContent = parts.join(" ");
      if (env.ollama.running) Array.prototype.forEach.call(document.querySelectorAll('.svc[data-service="ollama"]'), function (b) { b.classList.add("rec"); });
    });
    card.appendChild(h("div", { class: "svc-list" }, boxes));
    card.appendChild(h("div", { class: "foot" }, h("button", { class: "btn", type: "button", id: "w-later", text: "나중에 할게요", onclick: function () { closeWizard(); } })));
    card.appendChild(h("p", { class: "note", text: "처음이라면 무료 티어가 있는 서비스나 Ollama(내 PC)를 추천합니다. 무료 서비스는 입력이 학습에 쓰일 수 있고 한도가 자주 바뀝니다. 서비스 목록은 2026-10-21에 공개 목록을 모아 주소 응답까지 확인한 것입니다." }));
  }
  function wizStep2(card, w) {
    var meta = svc();
    var cloud = meta.policy !== "local";
    card.appendChild(h("p", { class: "note", text: meta.label + " — " + meta.note }));
    if (meta.discovered) {
      card.appendChild(h("label", { class: "check", id: "w-free-row" },
        h("input", { type: "checkbox", id: "w-free", checked: w.freeConfirmed ? "checked" : false, onchange: function (e) { w.freeConfirmed = e.target.checked; } }),
        h("span", { text: "이 서비스의 키에는 결제 수단(카드)을 연결하지 않았습니다. 무료 한도 안에서만 쓰는 것으로 취급합니다. (체크하지 않으면 이름이 -free/:free인 모델만 무료로 봅니다)" })));
    }
    if (w.service === "custom") {
      card.appendChild(h("label", { class: "field" }, h("span", { text: "이름 (영문 소문자·숫자·-·_)" }),
        h("input", { type: "text", maxlength: "32", value: w.customName, placeholder: "예: myservice", id: "w-name", oninput: function (e) { w.customName = e.target.value; } })));
      card.appendChild(h("label", { class: "field" }, h("span", { text: "주소" }),
        h("input", { type: "text", maxlength: "300", value: w.customUrl, placeholder: "https://…/v1  또는  http://127.0.0.1:포트/v1", id: "w-url",
          oninput: function (e) { w.customUrl = e.target.value; var c = svc().policy !== "local"; $("w-consent-row").hidden = !c; $("w-token-row").hidden = !svc().needs_key && !w.key; } })));
    }
    var keyInput = h("input", { type: w.showKey ? "text" : "password", id: "w-key", maxlength: "512", autocomplete: "off", spellcheck: "false", value: w.key,
      placeholder: meta.needs_key ? "토큰 / API 키 붙여넣기" : "필요하면 입력 (이 서비스는 보통 불필요)", oninput: function (e) { w.key = e.target.value; } });
    card.appendChild(h("label", { class: "field", id: "w-token-row", hidden: w.service !== "custom" && !meta.needs_key && !w.key },
      h("span", { text: "토큰 / API 키" }),
      h("div", { class: "row" }, keyInput, h("button", { class: "btn", type: "button", text: w.showKey ? "숨기기" : "보기", onclick: function () { w.showKey = !w.showKey; keyInput.type = w.showKey ? "text" : "password"; this.textContent = w.showKey ? "숨기기" : "보기"; } }))));
    if (meta.key_url) card.appendChild(h("p", { class: "sub" }, "토큰 발급: ", h("a", { href: meta.key_url, target: "_blank", rel: "noopener noreferrer", text: meta.key_url })));
    card.appendChild(h("label", { class: "check", id: "w-consent-row", hidden: !cloud },
      h("input", { type: "checkbox", id: "w-consent", checked: w.consent ? "checked" : false, onchange: function (e) { w.consent = e.target.checked; sync(); } }),
      h("span", { text: "토큰과 대화 내용이 이 서비스로 전송되고, 무료 티어는 입력이 학습에 쓰일 수 있으며, 모델에 따라 요금이 부과될 수 있음을 이해했습니다." })));
    var err = h("p", { class: "err", id: "w-err", text: w.error });
    var next = h("button", { class: "btn primary", type: "button", id: "w-next", text: w.busy ? "확인 중…" : "연결 확인", onclick: async function () {
      w.busy = true; w.error = ""; sync();
      try {
        var listing = await api("POST", "/onboard/models", wizPayload());
        w.models = listing.models; w.info = listing.info || {}; w.recommended = listing.recommended || []; w.hidden = listing.hidden || 0; w.total = listing.total || w.models.length; w.modelsError = "";
      } catch (e) { w.models = []; w.recommended = []; w.hidden = 0; w.total = 0; w.modelsError = e.message; }
      w.picked = (w.recommended || []).slice();
      w.busy = false; w.step = 3; w.model = (w.recommended && w.recommended[0]) || w.models[0] || ""; renderWizard();
    } });
    card.appendChild(err);
    card.appendChild(h("div", { class: "foot" }, h("button", { class: "btn", type: "button", text: "뒤로", onclick: function () { w.step = 1; w.error = ""; renderWizard(); } }), next));
    function sync() {
      var meta2 = svc();
      next.disabled = w.busy || (meta2.policy !== "local" && !w.consent) || (meta2.needs_key && !w.key.trim()) || (w.service === "custom" && (!w.customName.trim() || !w.customUrl.trim()));
      next.textContent = w.busy ? "확인 중…" : "연결 확인";
    }
    card.addEventListener("input", sync); sync();
  }
  function wizStep3(card, w) {
    if (w.result && w.result.ok) {
      card.appendChild(h("p", { class: "ok", text: "연결에 성공했고 저장했습니다 (" + w.result.seconds + "초)" }));
      card.appendChild(h("div", { class: "reply", text: w.result.reply }));
      card.appendChild(h("div", { class: "foot" }, h("span"), h("button", { class: "btn primary", type: "button", id: "w-start", text: "대화 시작", onclick: async function () {
        var ref = w.result.provider + "/" + w.result.model;
        await loadState();
        var prov = state.providers.filter(function (p) { return p.name === w.result.provider; })[0];
        var cheap = prov && (prov.policy === "local" || isFreeIn(prov, w.result.model));
        state.model = (cheap && hasAutoModel()) ? AUTO : ref;     // free/local connections just work through 'auto'
        renderModelBtn();   // update first, then close: no flash of the "connect a service" screen
        closeWizard(); newChat();
      } })));
      return;
    }
    if (w.modelsError) card.appendChild(h("p", { class: "note", text: "모델 목록을 불러오지 못했습니다 (" + w.modelsError + "). 모델 이름을 직접 입력해도 됩니다." }));
    var meta = svc(), freeInfo = catalogFree(meta);
    var rec = (w.recommended || []).filter(function (m) { return w.models.indexOf(m) >= 0; });
    var picked = w.picked = (w.picked || rec).filter(function (m) { return w.models.indexOf(m) >= 0; });
    if (rec.length) {
      card.appendChild(h("p", { class: "note", id: "w-rec-note" }, h("button", { type: "button", class: "help", "data-help": "auto", "aria-label": "자동 선택 설명", text: "?" }), " 추천 모델입니다. 체크한 모델은 함께 저장되어, 자동 선택이 질문에 맞게 골라 씁니다. 하나만 먼저 테스트합니다."));
      var recBox = h("div", { class: "recs", id: "w-recs" });
      rec.forEach(function (m) {
        recBox.appendChild(h("label", { class: "rec-row" },
          h("input", { type: "checkbox", "data-model": m, checked: picked.indexOf(m) >= 0 ? "checked" : false, onchange: function (e) {
            w.picked = w.picked.filter(function (x) { return x !== m; }); if (e.target.checked) w.picked.push(m); } }),
          h("span", { class: "rec-name", text: m }),
          h("span", { class: "rec-cap", title: (w.info && w.info[m] ? w.info[m].desc + " (출처: " + w.info[m].source + ")" : ""),
            text: w.info && w.info[m] && w.info[m].tags.length ? w.info[m].tags.join(" · ") : capText(m) }),
          isFreeIn(freeInfo, m) ? h("span", { class: "tag free", text: "무료" }) : null,
          h("button", { type: "button", class: "btn mini", text: m === w.model ? "테스트 대상" : "이걸로 테스트", onclick: function () { w.model = m; renderWizard(); } })));
      });
      card.appendChild(recBox);
    }
    var listBox = h("div", { class: "models", id: "w-models" });
    function paintList() {
      listBox.textContent = "";
      var f = w.filter.trim().toLowerCase();
      var shown = w.models.filter(function (m) { return !f || m.toLowerCase().indexOf(f) >= 0; }).slice(0, 80);
      shown.forEach(function (m) {
        listBox.appendChild(h("button", { type: "button", class: m === w.model ? "sel" : "", "data-model": m, onclick: function () { w.model = m; manual.value = m; paintList(); } },
          h("span", { text: m }), isFreeIn(freeInfo, m) ? h("span", { class: "tag free", text: "무료" }) : null));
      });
      listBox.hidden = !shown.length;
    }
    var manual = h("input", { type: "text", id: "w-model", maxlength: "200", value: w.model, placeholder: "모델 이름", oninput: function (e) { w.model = e.target.value; paintList(); sync(); } });
    if (w.models.length) {
      var all = h("details", { class: "all-models", id: "w-all", open: rec.length ? false : "open" },
        h("summary", { text: "전체 모델 보기 (" + w.models.length + "개" + (w.hidden ? " · 음성·이미지·임베딩 등 채팅이 안 되는 " + w.hidden + "개는 숨김" : "") + ")" }),
        h("label", { class: "field" }, h("span", { text: "모델 검색" }),
          h("input", { type: "search", id: "w-filter", value: w.filter, placeholder: "이름으로 찾기", oninput: function (e) { w.filter = e.target.value; paintList(); } })),
        listBox);
      card.appendChild(all);
    }
    card.appendChild(h("label", { class: "field" }, h("span", { text: "테스트할 모델 (직접 입력 가능)" }), manual));
    card.appendChild(h("p", { class: "note", text: freeInfo.free_all
      ? "이 서비스는 결제 수단을 연결하지 않은 무료 키라면 모든 모델이 무료 한도 안에서 쓰입니다(한도를 넘으면 잠시 막힘). 결제를 연결한 키는 요금이 나올 수 있습니다."
      : "이름이 -free 또는 :free로 끝나는 모델이 무료입니다. 그 밖의 모델은 요금이 부과될 수 있으니 서비스의 요금 안내를 확인하세요." }));
    var err = h("p", { class: "err", text: w.error });
    var go = h("button", { class: "btn primary", type: "button", id: "w-test", text: w.busy ? "테스트 중…" : "테스트하고 저장", onclick: async function () {
      w.busy = true; w.error = ""; renderWizard();
      try {
        var r = await api("POST", "/onboard/test", wizPayload({ model: w.model.trim(), prompt: "한 문장으로 짧게 인사해줘.", save: "on", extra_models: (w.picked || []).filter(function (m) { return m !== w.model.trim(); }).join(",") }));
        if (r.ok) { w.result = r; w.key = ""; } else w.error = r.error;
      } catch (e) { w.error = e.message; }
      w.busy = false; renderWizard();
    } });
    function sync() { go.disabled = w.busy || !w.model.trim(); }
    card.appendChild(err);
    card.appendChild(h("div", { class: "foot" }, h("button", { class: "btn", type: "button", text: "뒤로", onclick: function () { w.step = 2; w.error = ""; renderWizard(); } }), go));
    paintList(); sync();
  }
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && state.wiz && !state.wiz.busy) closeWizard(); });

  /* ---------- terminal ---------- */
  var term = null, fit = null, socket = null;
  function termSend(obj) { if (socket && socket.readyState === 1) socket.send(JSON.stringify(obj)); }
  function termConnect() {
    var status = $("term-status"), reconnect = $("term-reconnect");
    socket = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/terminal/ws");
    socket.binaryType = "arraybuffer";
    status.textContent = "연결 중…"; reconnect.hidden = true;
    socket.onopen = function () {
      fit.fit();
      termSend({ t: "auth", token: body.dataset.termToken, cols: term.cols, rows: term.rows });
      status.textContent = "연결됨 (WSL 셸)"; term.focus();
    };
    socket.onmessage = function (event) { term.write(event.data instanceof ArrayBuffer ? new Uint8Array(event.data) : event.data); };
    socket.onclose = function () { status.textContent = "연결 종료됨"; reconnect.hidden = false; };
  }
  function startTerminal() {
    if (TERM !== "ready") return;
    if (term) { fit.fit(); term.focus(); return; }
    term = new Terminal({ cursorBlink: true, fontSize: 14, scrollback: 5000, fontFamily: "Consolas, 'Cascadia Mono', monospace", theme: { background: "#000000" } });
    fit = new FitAddon.FitAddon();
    term.loadAddon(fit); term.open($("term")); fit.fit();
    term.onData(function (data) { termSend({ t: "i", d: data }); });
    term.onResize(function (size) { termSend({ t: "r", c: size.cols, r: size.rows }); });
    new ResizeObserver(function () { if (!$("view-terminal").hidden) fit.fit(); }).observe($("term"));
    $("term-reconnect").addEventListener("click", function () { term.reset(); termConnect(); });
    termConnect();
  }

  /* ---------- command palette (Ctrl+K): jump to a screen, a chat or a code run, or run a common action ---------- */
  var paletteEl = null;
  function paletteMatches(query, items) {
    var q = query.trim().toLowerCase();
    if (!q) return items.slice(0, 40);
    return items.map(function (it) {
      var hay = (it.label + " " + (it.hint || "")).toLowerCase(), at = hay.indexOf(q);
      return { it: it, rank: at < 0 ? -1 : (it.label.toLowerCase().indexOf(q) === 0 ? 0 : at === 0 ? 1 : 2) };
    }).filter(function (x) { return x.rank >= 0; }).sort(function (a, b) { return a.rank - b.rank; }).map(function (x) { return x.it; }).slice(0, 40);
  }
  async function paletteItems() {
    var go = function (view) { return function () { showView(view); }; };
    var items = [
      { label: "새 채팅", hint: "chat new", group: "동작", run: newChat },
      { label: "채팅 화면", hint: "chat", group: "이동", run: go("chat") },
      { label: "코드 작업 화면", hint: "code 코딩", group: "이동", run: go("code") },
      { label: "설정", hint: "settings 서비스 모델 연결", group: "이동", run: go("settings") },
      { label: "작업(대기열)", hint: "tasks queue", group: "이동", run: go("tasks") },
      { label: "보드", hint: "boards 여러 모델", group: "이동", run: go("boards") },
      { label: "사용량", hint: "usage 토큰 절약", group: "이동", run: go("usage") },
      { label: "긴 글 압축", hint: "digest 요약", group: "이동", run: go("digest") },
      { label: "발견(새 모델·서비스 추천)", hint: "discovery", group: "이동", run: go("discovery") },
      { label: "테마 전환(밝게/어둡게)", hint: "theme dark light", group: "동작", run: function () { $("theme").click(); } },
      { label: "사이드바 숨기기/표시", hint: "sidebar Ctrl+B", group: "동작", run: function () { setHidden(!body.classList.contains("side-hidden")); } },
      { label: "새 코드 작업", hint: "new task", group: "동작", run: function () { showView("code"); codeOpen = ""; renderCode(); setTimeout(function () { var p = $("code-prompt"); p && p.focus(); }, 300); } }
    ];
    (state.chats || []).slice(0, 30).forEach(function (c) { items.push({ label: c.title || "새 채팅", hint: "대화", group: "최근 대화", run: function () { showView("chat"); openChat(c.id); } }); });
    try {
      var d = await api("GET", "/code");
      d.runs.slice(0, 20).forEach(function (r) { items.push({ label: runTitle(r), hint: "코드 작업 " + (r.project_name || "") + " " + (CODE_STATUS[r.status] || ""), group: "최근 코드 작업", run: function () { showView("code"); codeOpen = r.id; codeStick = true; renderCode(); } }); });
    } catch (e) { /* the palette still works without the code runs */ }
    var order = ["동작", "이동", "최근 대화", "최근 코드 작업"];
    return items.map(function (it, i) { return { it: it, i: i }; }).sort(function (a, b) { return (order.indexOf(a.it.group) - order.indexOf(b.it.group)) || (a.i - b.i); }).map(function (x) { return x.it; });
  }
  async function openPalette() {
    if (paletteEl) { closePalette(); return; }
    var items = await paletteItems(), sel = 0, shown = items;
    var input = h("input", { type: "text", class: "pal-input", placeholder: "이동하거나 실행할 것을 입력하세요 (화면, 대화, 코드 작업, 동작)", "aria-label": "명령 검색", autocomplete: "off", spellcheck: "false" });
    var list = h("div", { class: "pal-list", role: "listbox" });
    function draw() {
      list.textContent = ""; var lastGroup = null;
      if (!shown.length) list.appendChild(h("p", { class: "sub pal-empty", text: "일치하는 항목이 없습니다." }));
      shown.forEach(function (it, i) {
        if (!input.value.trim() && it.group !== lastGroup) { lastGroup = it.group; list.appendChild(h("div", { class: "pal-group", text: it.group })); }
        list.appendChild(h("button", { type: "button", class: "pal-item" + (i === sel ? " on" : ""), role: "option", "aria-selected": i === sel ? "true" : "false",
          onclick: function () { choose(it); }, onmousemove: function () { if (sel !== i) { sel = i; draw(); } } },
          h("span", { text: it.label }), input.value.trim() ? h("span", { class: "sub", text: it.group }) : null));
      });
      var on = list.querySelector(".pal-item.on"); if (on && on.scrollIntoView) on.scrollIntoView({ block: "nearest" });
    }
    function choose(it) { closePalette(); setTimeout(it.run, 0); }
    input.addEventListener("input", function () { shown = paletteMatches(input.value, items); sel = 0; draw(); });
    input.addEventListener("keydown", function (e) {
      if (e.isComposing || e.keyCode === 229) return;                       // Korean input: Enter that ends a syllable must not pick an item
      if (e.key === "ArrowDown") { e.preventDefault(); sel = Math.min(shown.length - 1, sel + 1); draw(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); sel = Math.max(0, sel - 1); draw(); }
      else if (e.key === "Enter") { e.preventDefault(); if (shown[sel]) choose(shown[sel]); }
      else if (e.key === "Escape") { e.preventDefault(); closePalette(); }
    });
    paletteEl = h("div", { class: "pal-wrap", role: "dialog", "aria-modal": "true", "aria-label": "명령 팔레트", onclick: function (e) { if (e.target === paletteEl) closePalette(); } },
      h("div", { class: "pal-box" }, input, list));
    document.body.appendChild(paletteEl); draw(); input.focus();
  }
  function closePalette() { if (paletteEl) { paletteEl.remove(); paletteEl = null; } }
  document.addEventListener("keydown", function (e) {
    if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); }
  });

  /* ---------- start ---------- */
  (async function init() {
    updateSend();
    try { await loadState(); await loadChats(); loadUsage(); } catch (e) { toast("상태를 불러오지 못했습니다: " + e.message); }
    renderWelcome(); showView("chat");
    pollDiscovery(); setInterval(pollDiscovery, 300000);                  // the unread dot on the 발견 menu
    if (!state.onboarded) openWizard();
    $("input").focus();
  })();
})();
