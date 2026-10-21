"""Standalone OpenAI-compatible mock for the browser e2e run: python mock_openai.py PORT"""
import json, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MD = ("## 요약\n\n**빠른 시작**은 세 단계입니다:\n\n1. 설치\n2. 실행\n3. 설정\n\n- 항목 *하나*\n- `inline` 코드\n\n"
      "```python\nprint('hello')\nfor i in range(3):\n    print(i)\n```\n\n"
      "<img src=x onerror=\"window.__pwned=1\"> [나쁜 링크](javascript:window.__pwned=2) [좋은 링크](https://example.com/a?b=1&c=2)\n\n> 인용문")

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _json(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        if self.path == "/v1/models":
            return self._json(200, {"data": [{"id": m} for m in ("demo-model", "demo-free:free", "slow", "md", "other-model", "ratelimit")]})
        self._json(404, {})
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        last = body["messages"][-1]["content"]; model = body["model"]
        if model.startswith("ratelimit"):
            return self._json(429, {"error": {"message": "slow down"}})
        if not body.get("stream"):
            return self._json(200, {"choices": [{"message": {"content": "안녕하세요! 반갑습니다."}}]})
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        if model == "md": pieces, delay = [MD[i:i+12] for i in range(0, len(MD), 12)], 0.02
        elif model == "slow": pieces, delay = ["느린 응답 %d " % i for i in range(80)], 0.12
        else: pieces, delay = [w + " " for w in ("Echo: " + last).split(" ")], 0.05
        try:
            for p in pieces:
                self.wfile.write(("data: " + json.dumps({"choices": [{"delta": {"content": p}}]}) + "\n\n").encode()); self.wfile.flush(); time.sleep(delay)
            if (body.get("stream_options") or {}).get("include_usage"):
                self.wfile.write(("data: " + json.dumps({"choices": [], "usage": {"prompt_tokens": 120, "completion_tokens": 80}}) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")
        except (BrokenPipeError, ConnectionResetError): pass

ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
