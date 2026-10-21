(function () {
  "use strict";
  var body = document.body, CSRF = body.dataset.csrf, TERM = body.dataset.terminal;
  var state = { providers: [], catalog: [], onboarded: false, chats: [], chat: null, model: "",
                view: "chat", streaming: null, wiz: null, fallback: store("fallback") !== "0" };
  var VIEWS = ["chat", "settings", "tasks", "boards", "usage", "digest", "terminal"];
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
  function setSide(open) {
    $("side").classList.toggle("collapsed", !open);
    body.classList.toggle("side-closed", !open);
    $("scrim").hidden = !(open && window.innerWidth <= 760);
    store("side", open ? "1" : "0");
  }
  setSide(window.innerWidth > 760 && store("side") !== "0");
  var wasNarrow = window.innerWidth <= 760;
  window.addEventListener("resize", function () {          // shrinking the window must not leave the sidebar covering the chat
    var narrow = window.innerWidth <= 760;
    if (narrow && !wasNarrow) setSide(false);
    else if (!narrow && wasNarrow) setSide(store("side") !== "0");
    wasNarrow = narrow;
  });
  $("side-close").addEventListener("click", function () { setSide(false); });
  $("side-open").addEventListener("click", function () { setSide(true); });
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
    state.providers = s.providers; state.catalog = s.catalog; state.onboarded = s.onboarded;
    pickDefaultModel(); renderModelBtn(); renderWelcome();
  }
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
  /* ---------- settings view (native; replaces the old framed setup page) ---------- */
  async function renderSettings() {
    var list = $("set-list");
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
  var GROUPS = [["local", "내 PC에서 (토큰 불필요)"], ["top", "무료 한도가 넉넉하고 잘 알려진 서비스"], ["more", "그 밖의 무료 서비스·게이트웨이 (한도가 작거나 자주 바뀜)"]];
  function svcButton(c, w) {
    return h("button", { class: "svc", type: "button", "data-service": c.id, "data-search": (c.label + " " + c.id + " " + (c.limits || "") + " " + (c.note || "")).toLowerCase(),
      onclick: function () { w.service = c.id; w.step = 2; w.error = ""; renderWizard(); } },
      h("b", { text: c.label }), h("span", { text: c.policy === "local" ? "토큰 불필요 · 내 PC에서 실행" : "클라우드 · 토큰 필요" }),
      c.limits ? h("span", { class: "lim", text: "무료 한도: " + c.limits }) : null);
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
    card.appendChild(search);
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
        w.models = listing.models; w.recommended = listing.recommended || []; w.hidden = listing.hidden || 0; w.total = listing.total || w.models.length; w.modelsError = "";
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
          h("span", { class: "rec-name", text: m }), h("span", { class: "rec-cap", text: capText(m) }),
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

  /* ---------- start ---------- */
  (async function init() {
    updateSend();
    try { await loadState(); await loadChats(); loadUsage(); } catch (e) { toast("상태를 불러오지 못했습니다: " + e.message); }
    renderWelcome(); showView("chat");
    if (!state.onboarded) openWizard();
    $("input").focus();
  })();
})();
