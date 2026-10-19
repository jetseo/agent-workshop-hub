"""Local-only task dashboard. No remote exposure or automatic agent execution."""
import argparse
import html
import json
import os
import re
import secrets
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, quote
from .queue import Queue

PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent Workshop Hub</title>
<style>
:root{font-family:system-ui,sans-serif;color-scheme:dark}
body{max-width:1000px;margin:32px auto;padding:0 18px;background:#111827;color:#e5e7eb}*{box-sizing:border-box}
h1{font-size:1.8rem}p{color:#9ca3af}
section,.job{background:#1f2937;border:1px solid #374151;border-radius:12px;padding:18px;margin:14px 0}
input,select,button{padding:10px;border-radius:8px;border:1px solid #4b5563;background:#111827;color:#fff}
input{width:min(440px,85%)}button{cursor:pointer;background:#2563eb;border:0}
.job{display:flex;justify-content:space-between;gap:14px;align-items:center;flex-wrap:wrap}.job>div:first-child{min-width:0;overflow-wrap:anywhere}.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:10px;width:100%;margin-top:12px}.gallery a{display:block;min-width:0}.gallery img{width:100%;height:auto;aspect-ratio:1;object-fit:contain;margin:0;background:#111827}.job form{max-width:100%}@media(max-width:600px){body{margin:12px auto;padding:0 12px}section,.job{padding:12px;margin:10px 0}input,select,button{max-width:100%;width:100%;min-height:44px;margin:4px 0}.job{display:block}.gallery{grid-template-columns:repeat(2,minmax(0,1fr))}}
small{color:#9ca3af} .tag{color:#93c5fd}
</style></head><body>
<h1>Agent Workshop Hub</h1>
<p>Local task dashboard · A separately enabled worker executes queued tasks.<br>로컬 작업 대시보드 · 작업은 별도로 허용한 워커만 실행합니다.</p>
<section><h2>Add task / 작업 추가</h2><form method="post" action="/add">
<input type="hidden" name="token" value="__TOKEN__">
<select name="kind"><option>plan</option><option>code</option><option>image</option><option>qa</option></select>
<input name="title" placeholder="Describe a task" required maxlength="500">
<input name="resource" placeholder="Code: absolute worktree path / Image: workflow JSON path" maxlength="2000">
<label>Image output directory (optional) <input name="output_dir" placeholder="Absolute directory for generated images" maxlength="2000"></label>
<button>Add / 추가</button></form></section>
<h2>Tasks / 작업 목록</h2><p><a href="/boards" style="color:#93c5fd">Boards / 멀티 모델 보드 →</a></p>__JOBS__
</body></html>"""

STATE_KO = {"pending_approval": " (실행 승인 대기)", "queued": " (대기 중)",
            "running": " (실행 중)", "review": " (결과 검토 대기)",
            "done": " (완료)", "failed": " (실패 · 부수효과 확인 후 다시 추가)"}

def gallery_files(job):
    if not job or job["kind"] != "image" or job["state"] not in ("review", "done"):
        return []
    try:
        payload = json.loads(job.get("payload") or "{}")
        output_dir = payload.get("output_dir")
        if not isinstance(output_dir, str) or not Path(output_dir).is_absolute():
            return []
        root = Path(output_dir).resolve()
        files = json.loads(job.get("result") or "{}").get("files", [])
        if not isinstance(files, list):
            return []
        valid = []
        for item in files[:24]:
            if not isinstance(item, dict):
                continue
            path, digest = item.get("path"), item.get("sha256")
            if (isinstance(path, str) and Path(path).is_absolute()
                    and Path(path).suffix.lower() in (".png", ".jpg", ".webp")
                    and Path(path).resolve().parent == root
                    and Path(path).name.startswith("image-")
                    and isinstance(digest, str) and len(digest) == 64
                    and all(c in "0123456789abcdefABCDEF" for c in digest)):
                valid.append((Path(path), digest))
        return valid
    except (ValueError, TypeError, AttributeError):
        return []


def gallery_signature(token, job_id, index):
    return hmac.new(token.encode(), f"{job_id}:{index}".encode(), hashlib.sha256).hexdigest()


def make_handler(queue, token, boards_root=None):
    boards_root = boards_root or str(Path.home() / ".agent-workshop" / "boards")
    class Handler(BaseHTTPRequestHandler):
        def _trusted_host(self):
            host = self.headers.get("Host", "")
            return host in (
                "127.0.0.1:" + str(self.server.server_port),
                "localhost:" + str(self.server.server_port),
            )

        def _send(self, code, body, content_type="text/html; charset=utf-8"):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; form-action 'self'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not self._trusted_host():
                self._send(403, "Invalid Host", "text/plain")
                return
            if urlsplit(self.path).path == "/image":
                params = parse_qs(urlsplit(self.path).query)
                job_id = params.get("id", [""])[0]
                index_text = params.get("index", [""])[0]
                signature = params.get("sig", [""])[0]
                if not index_text.isdecimal() or len(index_text) > 3:
                    self._send(404, "Not found", "text/plain")
                    return
                index = int(index_text)
                if not hmac.compare_digest(signature, gallery_signature(token, job_id, index)):
                    self._send(403, "Forbidden", "text/plain")
                    return
                job = next((j for j in queue.list() if j["id"] == job_id), None)
                images = gallery_files(job)
                if index >= len(images):
                    self._send(404, "Not found", "text/plain")
                    return
                image_path, digest = images[index]
                try:
                    if not image_path.is_file() or image_path.stat().st_size > 64 * 1024 * 1024:
                        raise ValueError("Invalid image")
                    data = image_path.read_bytes()
                    if hashlib.sha256(data).hexdigest().lower() != digest.lower():
                        raise ValueError("Checksum mismatch")
                    ext = image_path.suffix.lower()
                    signatures = {".png": data.startswith(bytes.fromhex("89504e470d0a1a0a")),
                                  ".jpg": data.startswith(bytes.fromhex("ffd8ff")),
                                  ".webp": data[:4] == b"RIFF" and data[8:12] == b"WEBP"}
                    if not signatures[ext]:
                        raise ValueError("Invalid image signature")
                except (OSError, ValueError):
                    self._send(404, "Image unavailable", "text/plain")
                    return
                self.send_response(200)
                self.send_header("Content-Type", {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}[ext])
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(data)
                return
            if urlsplit(self.path).path == "/api/jobs":
                # Never expose prompts, workspace paths or execution logs without auth.
                public_jobs = [
                    {"id": job["id"], "kind": job["kind"], "title": job["title"],
                     "state": job["state"]} for job in queue.list()
                ]
                self._send(200, json.dumps(public_jobs), "application/json; charset=utf-8")
                return
            route = urlsplit(self.path).path
            if route in ("/boards", "/board"):
                # Read-only view of shared boards; running models stays in the CLI/worker.
                root = Path(boards_root)
                if route == "/boards":
                    ids = sorted((d.name for d in root.glob("*") if (d / "board.md").is_file()), reverse=True) if root.is_dir() else []
                    items = "".join('<li><a href="/board?id=' + quote(i) + '">' + html.escape(i) + '</a></li>' for i in ids)
                    self._send(200, '<!doctype html><html lang="ko"><meta charset="utf-8"><body style="font-family:sans-serif;background:#111827;color:#e5e7eb;max-width:900px;margin:30px auto">'
                               '<h1>Boards / 보드</h1><a href="/" style="color:#93c5fd">← Tasks</a><ul>' + (items or "<li>No boards yet. / 보드가 없습니다.</li>") + '</ul></body></html>')
                    return
                board_id = parse_qs(urlsplit(self.path).query).get("id", [""])[0]
                target = root / board_id / "board.md"
                if not re.fullmatch(r"[0-9a-f-]{36}", board_id) or not target.is_file():
                    self._send(404, "Not found", "text/plain")
                    return
                self._send(200, '<!doctype html><html lang="ko"><meta charset="utf-8"><body style="font-family:sans-serif;background:#111827;color:#e5e7eb;max-width:900px;margin:30px auto">'
                           '<a href="/boards" style="color:#93c5fd">← Boards</a><pre style="white-space:pre-wrap;overflow-wrap:anywhere">' +
                           html.escape(target.read_text(encoding="utf-8")[:200000]) + '</pre></body></html>')
                return
            if route != "/":
                self._send(404, "Not found", "text/plain")
                return
            jobs = queue.list()
            cards = []
            for job in reversed(jobs):
                name = html.escape(job["title"])
                kind = html.escape(job["kind"])
                state = html.escape(job["state"])
                approve = ""
                if job["state"] == "pending_approval":
                    approve = ('<form method="post" action="/authorize">'
                               '<input type="hidden" name="token" value="' + token + '">'
                               '<input type="hidden" name="id" value="' + html.escape(job["id"]) + '">'
                               '<button>Authorize execution / 실행 승인</button></form>')
                if job["state"] == "review":
                    approve = ('<form method="post" action="/approve">'
                               '<input type="hidden" name="token" value="' + token + '">'
                               '<input type="hidden" name="id" value="' + html.escape(job["id"]) + '">'
                               '<button>Approve / 결과 승인</button></form>')
                detail = ""
                if job["kind"] == "image" and job["state"] in ("review", "done", "failed"):
                    try:
                        result = json.loads(job.get("result") or "{}")
                        files = result.get("files") or []
                        if isinstance(files, list) and files:
                            detail = "<small> · Generated files: " + str(len(files)) + "</small>"
                        if result.get("report_path"):
                            detail += "<small> · Report saved locally</small>"
                    except (ValueError, TypeError, AttributeError):
                        detail = ""
                gallery = ""
                for i, _ in enumerate(gallery_files(job)):
                    url = ("/image?id=" + quote(job["id"]) + "&index=" + str(i) +
                           "&sig=" + gallery_signature(token, job["id"], i))
                    gallery += '<a href="' + url + '"><img loading="lazy" alt="Generated image" src="' + url + '"></a>'
                if gallery:
                    detail += '<div class="gallery">' + gallery + '</div>'
                cards.append('<div class="job"><div><strong>' + name + '</strong><br>'
                             '<small>' + kind + ' · <span class="tag">' + state + STATE_KO.get(job["state"], "") + '</span></small></div>'
                             + detail + approve + '</div>')
            page = PAGE.replace("__TOKEN__", token).replace("__JOBS__", "".join(cards) or "<p>No tasks yet. / 아직 작업이 없습니다.</p>")
            self._send(200, page)

        def do_POST(self):
            if not self._trusted_host():
                self._send(403, "Invalid Host", "text/plain")
                return
            path = urlsplit(self.path).path
            if path not in ("/add", "/approve", "/authorize"):
                self._send(404, "Not found", "text/plain")
                return
            try:
                origin = self.headers.get("Origin")
                if origin is not None:
                    allowed_origins = (
                        "http://127.0.0.1:" + str(self.server.server_port),
                        "http://localhost:" + str(self.server.server_port),
                    )
                    if origin not in allowed_origins:
                        self._send(403, "Invalid Origin", "text/plain")
                        return
                size = int(self.headers.get("Content-Length", "0"))
                if size <= 0 or size > 8192:
                    raise ValueError("Invalid request size")
                form = parse_qs(self.rfile.read(size).decode("utf-8"), keep_blank_values=True)
                if not secrets.compare_digest(form.get("token", [""])[0], token):
                    self._send(403, "Forbidden", "text/plain")
                    return
                if path == "/add":
                    kind = form.get("kind", [""])[0]
                    title = form.get("title", [""])[0][:500]
                    resource = form.get("resource", [""])[0].strip()
                    if kind in ("code", "image") and not resource:
                        raise ValueError("Code and image tasks require a workspace or workflow path")
                    payload = ({"workspace": resource} if kind == "code" else
                               {"workflow": resource} if kind == "image" else {})
                    output_dir = form.get("output_dir", [""])[0].strip()
                    if output_dir and kind != "image":
                        raise ValueError("Output directory is only valid for image tasks")
                    if output_dir:
                        if len(output_dir) > 2000 or not Path(output_dir).is_absolute():
                            raise ValueError("Image output directory must be an absolute path")
                        payload.update({"save_outputs": True, "output_dir": output_dir})
                    queue.add(kind, title, payload, require_approval=kind in ("code", "image"))
                elif path == "/authorize":
                    queue.authorize(form.get("id", [""])[0])
                else:
                    queue.approve(form.get("id", [""])[0])
            except (ValueError, UnicodeDecodeError, IndexError) as error:
                self._send(400, str(error), "text/plain")
                return
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
    return Handler

def main():
    parser = argparse.ArgumentParser(description="Local-only Agent Workshop dashboard")
    parser.add_argument("--db", default=str(Path.home() / ".agent-workshop" / "jobs.sqlite3"))
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    token = secrets.token_urlsafe(32)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(Queue(args.db), token))
    print(f"Dashboard: http://127.0.0.1:{args.port}/", flush=True)
    print("Loopback only. Stop with Ctrl+C.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == "__main__":
    main()
