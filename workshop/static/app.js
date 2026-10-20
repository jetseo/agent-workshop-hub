(function () {
  "use strict";
  var body = document.body;
  var state = body.dataset.terminal;            // off | locked | ready
  var tabs = Array.prototype.slice.call(document.querySelectorAll("nav [data-tab]"));
  var panes = {
    setup: document.getElementById("pane-setup"), tasks: document.getElementById("pane-tasks"),
    boards: document.getElementById("pane-boards"), terminal: document.getElementById("pane-terminal")
  };
  var term = null, fit = null, socket = null;
  var status = document.getElementById("term-status");
  var reconnect = document.getElementById("term-reconnect");

  function show(name) {
    tabs.forEach(function (b) { b.setAttribute("aria-selected", String(b.dataset.tab === name)); });
    Object.keys(panes).forEach(function (k) { if (panes[k]) panes[k].hidden = (k !== name); });
    if (name === "terminal" && state === "ready") startTerminal();
  }

  function send(obj) { if (socket && socket.readyState === 1) socket.send(JSON.stringify(obj)); }

  function connect() {
    var scheme = location.protocol === "https:" ? "wss://" : "ws://";
    socket = new WebSocket(scheme + location.host + "/terminal/ws");
    socket.binaryType = "arraybuffer";
    status.textContent = "연결 중…";
    reconnect.hidden = true;
    socket.onopen = function () {
      fit.fit();
      send({ t: "auth", token: body.dataset.termToken, cols: term.cols, rows: term.rows });
      status.textContent = "연결됨 (WSL 셸)";
      term.focus();
    };
    socket.onmessage = function (event) {
      term.write(event.data instanceof ArrayBuffer ? new Uint8Array(event.data) : event.data);
    };
    socket.onclose = function () {
      status.textContent = "연결 종료됨";
      reconnect.hidden = false;
    };
  }

  function startTerminal() {
    if (term) { fit.fit(); term.focus(); return; }
    term = new Terminal({ cursorBlink: true, fontSize: 14, scrollback: 5000,
                          fontFamily: "Consolas, 'Cascadia Mono', monospace",
                          theme: { background: "#000000" } });
    fit = new FitAddon.FitAddon();
    term.loadAddon(fit);
    term.open(document.getElementById("term"));
    fit.fit();
    term.onData(function (data) { send({ t: "i", d: data }); });
    term.onResize(function (size) { send({ t: "r", c: size.cols, r: size.rows }); });
    new ResizeObserver(function () { if (!panes.terminal.hidden) fit.fit(); })
      .observe(document.getElementById("term"));
    reconnect.addEventListener("click", function () { term.reset(); connect(); });
    connect();
  }

  tabs.forEach(function (b) { b.addEventListener("click", function () { show(b.dataset.tab); }); });
  show(location.hash === "#terminal" && state !== "off" ? "terminal" : "setup");
})();
