import hashlib
import io
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from workshop import engine as eng
from workshop.engine import Engine, EngineError
from workshop.providers import Providers

HW_GPU = {"ram_gb": 64.0, "gpu": "RTX 3060", "vram_gb": 12.0, "system": "Windows"}
HW_CPU = {"ram_gb": 16.0, "gpu": None, "vram_gb": 0.0, "system": "Windows"}
MODEL = eng.BY_ID["qwen2.5-0.5b"]
MODEL_BYTES = b"GGUF" + bytes(range(256)) * 40
TAG = "b999"


def build_zip(with_server=True, evil=False):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        if with_server:
            z.writestr("llama-server.exe", b"fake server")
        z.writestr("readme.txt", b"hi")
        if evil:
            z.writestr("../../escaped.txt", b"nope")
    return buffer.getvalue()


class Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class FakeNet:
    """Stands in for github.com and huggingface.co: JSON listings and file bodies (with Range support)."""

    def __init__(self, zip_bytes=None, model_bytes=MODEL_BYTES, gated=False, drop_hash=False, asset_hash=None, model_hash=None):
        self.zip = zip_bytes if zip_bytes is not None else build_zip()
        self.model = model_bytes
        self.gated, self.drop_hash = gated, drop_hash
        self.asset_hash = asset_hash or hashlib.sha256(self.zip).hexdigest()
        self.model_hash = model_hash or hashlib.sha256(self.model).hexdigest()
        self.requests = []
        self.stop_after = None

    def releases(self):
        asset = {"name": "llama-%s-bin-win-vulkan-x64.zip" % TAG, "size": len(self.zip), "digest": "sha256:" + self.asset_hash,
                 "browser_download_url": "https://github.com/ggml-org/llama.cpp/releases/download/%s/llama-%s-bin-win-vulkan-x64.zip" % (TAG, TAG)}
        cpu = dict(asset, name="llama-%s-bin-win-cpu-x64.zip" % TAG, browser_download_url=asset["browser_download_url"].replace("vulkan", "cpu"))
        return [{"tag_name": "v0.7.0", "assets": []}, {"tag_name": TAG, "assets": [asset, cpu]}]

    def hf(self):
        sib = {"rfilename": MODEL["file"], "size": len(self.model), "lfs": {} if self.drop_hash else {"sha256": self.model_hash, "size": len(self.model)}}
        return {"gated": self.gated, "siblings": [sib]}

    def open(self, request, timeout=0):
        url = request.full_url
        self.requests.append((url, dict(request.header_items())))
        if url == eng.RELEASES_URL:
            return Resp(json.dumps(self.releases()).encode())
        if url == eng.HF_API % MODEL["repo"]:
            return Resp(json.dumps(self.hf()).encode())
        body = self.zip if url.endswith(".zip") else self.model if url.endswith(".gguf") else None
        if body is None:
            raise urllib.error.URLError("404")
        start = 0
        rng = request.get_header("Range")
        if rng:
            start = int(rng.split("=")[1].rstrip("-"))
        data = body[start:]
        if self.stop_after is not None and not rng:
            data = data[:self.stop_after]                       # a connection that dies half way
        resp = Resp(data)
        resp.status = 206 if rng else 200
        return resp


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.providers = Providers(self.dir / "providers.json")
        self.net = FakeNet()
        self.make()

    def make(self, hw=HW_GPU, **kw):
        self.engine = Engine(self.dir / "engine", self.providers, opener=self.net, hardware_fn=lambda: hw, **kw)
        self.addCleanup(self.engine.stop)

    def finish(self):
        self.engine._thread.join(30)
        self.assertFalse(self.engine.job["running"])
        return self.engine.job


class SafetyHelpersTests(unittest.TestCase):
    def test_only_https_github_and_huggingface_hosts_are_allowed(self):
        ok = ["https://github.com/a/b", "https://huggingface.co/x", "https://us.aws.cdn.hf.co/f", "https://release-assets.githubusercontent.com/z",
              "https://api.github.com/repos/x"]
        bad = ["http://github.com/a", "https://evil.example.com/", "https://github.com.evil.com/", "https://notgithub.com/", "file:///c:/x", "https://hf.co.evil.io/"]
        self.assertTrue(all(eng.host_allowed(u) for u in ok))
        self.assertFalse(any(eng.host_allowed(u) for u in bad))

    def test_redirects_to_other_hosts_are_refused(self):
        handler = eng._CheckedRedirects()
        req = urllib.request.Request("https://huggingface.co/x")
        with self.assertRaises(urllib.error.URLError):
            handler.redirect_request(req, None, 302, "Found", {}, "https://evil.example.com/steal")
        self.assertIsNotNone(handler.redirect_request(req, None, 302, "Found", {}, "https://us.aws.cdn.hf.co/file"))

    def test_zip_slip_and_zip_bombs_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            archive = Path(d) / "evil.zip"
            archive.write_bytes(build_zip(evil=True))
            with self.assertRaises(EngineError):
                eng.safe_extract(archive, Path(d) / "out")
            self.assertFalse((Path(d) / "escaped.txt").exists())
            ok = Path(d) / "ok.zip"
            ok.write_bytes(build_zip())
            with self.assertRaises(EngineError):
                eng.safe_extract(ok, Path(d) / "out2", limit=5)
            eng.safe_extract(ok, Path(d) / "out3")
            self.assertTrue((Path(d) / "out3" / "llama-server.exe").exists())

    def test_asset_choice_and_fit(self):
        assets = [{"name": n} for n in ("llama-b1-bin-win-cpu-x64.zip", "llama-b1-bin-win-vulkan-x64.zip", "cudart-llama-bin-win-cuda-12.4-x64.zip",
                                         "llama-b1-bin-ubuntu-x64.tar.gz", "llama-b1-bin-ubuntu-vulkan-x64.tar.gz")]
        self.assertTrue(eng.pick_asset(assets, "Windows", True)["name"].endswith("win-vulkan-x64.zip"))
        self.assertTrue(eng.pick_asset(assets, "Windows", False)["name"].endswith("win-cpu-x64.zip"))
        self.assertTrue(eng.pick_asset(assets, "Linux", False)["name"].endswith("ubuntu-x64.tar.gz"))
        self.assertIsNone(eng.pick_asset(assets, "Darwin", True))
        self.assertIsNone(eng.pick_asset([{"name": "cudart-llama-bin-win-cuda-12.4-x64.zip"}], "Windows", True))
        big = eng.BY_ID["qwen2.5-coder-14b"]
        self.assertEqual(eng.fit(big, HW_GPU)[0], "gpu")
        self.assertEqual(eng.fit(big, HW_CPU)[0], "no")
        self.assertEqual(eng.fit(eng.BY_ID["qwen2.5-1.5b"], HW_CPU)[0], "cpu")


class PlanTests(Base):
    def test_plan_lists_exact_sizes_sources_and_totals(self):
        plan = self.engine.plan("qwen2.5-0.5b")
        kinds = {i["kind"]: i for i in plan["items"]}
        self.assertEqual(set(kinds), {"binary", "model"})
        self.assertTrue(kinds["binary"]["name"].endswith("vulkan-x64.zip"))            # GPU present -> Vulkan build
        self.assertEqual(kinds["binary"]["tag"], TAG)                                   # skipped the release without builds
        self.assertEqual(plan["total_bytes"], len(self.net.zip) + len(MODEL_BYTES))
        self.assertIn("huggingface.co", kinds["model"]["source"])
        self.assertTrue(plan["enough_disk"])
        self.assertFalse(plan["already_installed"])
        self.assertEqual(self.net.requests[0][0], eng.RELEASES_URL)                     # only listings were read, no file body

    def test_cpu_pc_gets_the_cpu_build(self):
        self.make(HW_CPU)
        self.assertTrue([i for i in self.engine.plan("qwen2.5-0.5b")["items"] if i["kind"] == "binary"][0]["name"].endswith("cpu-x64.zip"))

    def test_gated_or_unverifiable_models_are_not_offered(self):
        self.net.gated = True
        with self.assertRaises(EngineError):
            self.engine.plan("qwen2.5-0.5b")
        self.net.gated, self.net.drop_hash = False, True
        with self.assertRaises(EngineError):
            self.engine.plan("qwen2.5-0.5b")
        with self.assertRaises(EngineError):
            self.engine.plan("not-a-model")

    def test_unsupported_system(self):
        self.make({"ram_gb": 8, "gpu": None, "vram_gb": 0, "system": "Darwin"})
        with self.assertRaises(EngineError):
            self.engine.plan("qwen2.5-0.5b")


class InstallTests(Base):
    def install(self, confirm=None):
        plan = self.engine.plan("qwen2.5-0.5b")
        self.engine.install("qwen2.5-0.5b", plan["total_bytes"] if confirm is None else confirm)
        return self.finish()

    def test_install_needs_the_exact_confirmed_size(self):
        plan = self.engine.plan("qwen2.5-0.5b")
        for wrong in (0, plan["total_bytes"] - 1, None):
            with self.assertRaises(EngineError):
                self.engine.install("qwen2.5-0.5b", wrong)
        self.assertFalse((self.dir / "engine" / "models").exists())                      # nothing was downloaded

    def test_install_downloads_verifies_unpacks_and_records(self):
        job = self.install()
        self.assertEqual((job["error"], job["finished"], job["done"]), ("", True, job["total"]))
        state = self.engine._state()
        self.assertTrue(Path(state["binary"]["exe"]).exists())
        self.assertEqual(state["binary"]["variant"], "vulkan")
        self.assertEqual((self.dir / "engine" / "models" / MODEL["file"]).read_bytes(), MODEL_BYTES)
        self.assertIn("qwen2.5-0.5b", state["models"])
        self.assertTrue(self.engine.status()["models"][0]["installed"])
        self.assertTrue(self.engine.plan("qwen2.5-0.5b")["already_installed"])           # a second plan downloads nothing

    def test_a_wrong_hash_deletes_the_file_and_reports_it(self):
        self.net.model_hash = "0" * 64
        job = self.install()
        self.assertIn("SHA-256", job["error"])
        self.assertFalse((self.dir / "engine" / "models" / MODEL["file"]).exists())
        self.assertFalse(list((self.dir / "engine" / "models").glob("*.part")) if (self.dir / "engine" / "models").exists() else [])
        self.assertNotIn("qwen2.5-0.5b", self.engine._state()["models"])

    def test_interrupted_download_resumes_from_the_part_file(self):
        self.net.stop_after = 300                                                         # the connection dies after 300 bytes
        job = self.install()
        self.assertTrue(job["error"])
        part = self.dir / "engine" / "models" / (MODEL["file"] + ".part")
        self.assertEqual(part.stat().st_size, 300)
        self.net.stop_after = None
        job = self.install()
        self.assertEqual(job["error"], "")
        ranges = [h.get("Range") for url, h in self.net.requests if url.endswith(".gguf")]
        self.assertEqual(ranges[-1], "bytes=300-")

    def test_unsafe_archive_aborts_the_install(self):
        self.net = FakeNet(zip_bytes=build_zip(evil=True))
        self.make()
        self.assertTrue(self.install()["error"])
        self.assertFalse(self.engine._state()["binary"])

    def test_archive_without_a_server_binary_is_reported(self):
        self.net = FakeNet(zip_bytes=build_zip(with_server=False))
        self.make()
        self.assertIn("llama-server", self.install()["error"])

    def test_cancel_stops_the_download(self):
        plan = self.engine.plan("qwen2.5-0.5b")
        self.engine.cancel()
        self.engine._cancel.set()
        # run the worker directly with the flag already set (install() would clear it first)
        self.engine.job = {"running": True, "step": "", "done": 0, "total": plan["total_bytes"], "error": "", "finished": False}
        self.engine._run_install("qwen2.5-0.5b", plan)
        self.assertIn("취소", self.engine.job["error"])

    def test_not_enough_disk_blocks_the_install(self):
        plan = self.engine.plan("qwen2.5-0.5b")
        self.engine.plan = lambda model_id: dict(plan, enough_disk=False)
        with self.assertRaises(EngineError):
            self.engine.install("qwen2.5-0.5b", plan["total_bytes"])


FAKE_SERVER = """
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
port = int(sys.argv[sys.argv.index("--port") + 1])
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        body = json.dumps({"status": "ok"}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
"""


class RunTests(Base):
    def setUp(self):
        super().setUp()
        script = self.dir / "fake_llama.py"
        script.write_text(FAKE_SERVER, encoding="utf-8")
        self.make(argv_fn=lambda exe, model, port, alias, hw: [sys.executable, str(script), "--port", str(port)])
        engine_dir = self.dir / "engine"
        (engine_dir / "models").mkdir(parents=True)
        (engine_dir / "models" / MODEL["file"]).write_bytes(MODEL_BYTES)
        exe = engine_dir / "bin" / TAG / "llama-server.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"x")
        self.engine._save({"binary": {"tag": TAG, "variant": "vulkan", "exe": str(exe)},
                           "models": {"qwen2.5-0.5b": {"file": MODEL["file"], "bytes": len(MODEL_BYTES), "sha256": "x"}}, "active": None})

    def wait_ready(self):
        deadline = time.time() + 20
        while time.time() < deadline and not self.engine._ready:
            time.sleep(0.1)
        self.assertTrue(self.engine._ready)

    def test_start_serves_on_loopback_registers_the_local_service_and_stop_ends_it(self):
        self.engine.start("qwen2.5-0.5b")
        self.wait_ready()
        status = self.engine.status()
        self.assertTrue(status["running"] and status["ready"])
        entry = self.providers.get(eng.SERVICE_NAME)
        self.assertEqual(entry["data_policy"], "local")                                   # the auto mode treats it as a local model
        self.assertEqual(entry["models"], [MODEL["alias"]])
        self.assertTrue(entry["base_url"].startswith("http://127.0.0.1:"))
        proc = self.engine._proc
        self.engine.stop()
        self.assertIsNotNone(proc.poll())
        self.assertFalse(self.engine.status()["running"])

    def test_default_command_is_loopback_only_and_uses_the_gpu_when_there_is_one(self):
        argv = Engine._default_argv(self.engine, "srv", "m.gguf", 11500, "alias", HW_GPU)
        self.assertEqual(argv[argv.index("--host") + 1], "127.0.0.1")
        self.assertIn("-ngl", argv)
        self.assertNotIn("-ngl", Engine._default_argv(self.engine, "srv", "m.gguf", 11500, "alias", HW_CPU))
        self.assertNotIn("0.0.0.0", argv)

    def test_start_without_install_and_remove(self):
        with self.assertRaises(EngineError):
            self.engine.start("qwen2.5-coder-7b")
        self.engine.start("qwen2.5-0.5b")
        self.wait_ready()
        self.engine.remove("qwen2.5-0.5b")
        self.assertFalse(self.engine.status()["running"])
        self.assertFalse((self.dir / "engine" / "models" / MODEL["file"]).exists())
        self.assertIsNone(self.providers.get(eng.SERVICE_NAME))
        with self.assertRaises(EngineError):
            self.engine.remove("nonsense")
        self.engine.remove("all")
        self.assertIsNone(self.engine._state()["binary"])

    def test_autostart_brings_back_the_last_model_only_if_installed(self):
        state = self.engine._state()
        state["active"] = "qwen2.5-0.5b"
        self.engine._save(state)
        self.engine.autostart()
        self.wait_ready()
        self.engine.stop()
        self.engine.remove("all")
        self.engine.autostart()                                                           # nothing installed: quietly nothing
        self.assertFalse(self.engine.status()["running"])


class EngineApiTests(unittest.TestCase):
    def setUp(self):
        from tests.test_chat_api import ApiBase
        self.base = ApiBase("run")
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        net = FakeNet()
        self.base.ctx.engine.opener = net
        self.base.ctx.engine.hardware_fn = lambda: HW_GPU

    def test_status_plan_and_install_need_valid_input(self):
        status, body = self.base.jcall("GET", "/engine")
        self.assertEqual(status, 200)
        self.assertTrue(body["supported"])
        self.assertEqual(body["models"][0]["id"], "qwen2.5-0.5b")
        status, plan = self.base.jcall("POST", "/engine/plan", {"model": "qwen2.5-0.5b"})
        self.assertEqual(status, 200)
        self.assertGreater(plan["total_bytes"], 0)
        self.assertEqual(self.base.jcall("POST", "/engine/install", {"model": "qwen2.5-0.5b"})[0], 400)                       # no size confirmed
        self.assertEqual(self.base.jcall("POST", "/engine/install", {"model": "qwen2.5-0.5b", "confirm_bytes": "1"})[0], 400)
        self.assertEqual(self.base.jcall("POST", "/engine/install", {"model": "qwen2.5-0.5b", "confirm_bytes": 5})[0], 400)     # wrong size
        self.assertEqual(self.base.jcall("POST", "/engine/plan", {"model": "zzz"})[0], 400)
        self.assertEqual(self.base.jcall("POST", "/engine/stop", {})[0], 200)
        self.assertEqual(self.base.jcall("POST", "/engine/remove", {"what": "zzz"})[0], 400)
        self.assertEqual(self.base.jcall("POST", "/engine/nope", {})[0], 404)

    def test_autostart_setting(self):
        status, body = self.base.jcall("POST", "/settings", {"engine_autostart": False})
        self.assertEqual((status, body["settings"]["engine_autostart"]), (200, False))
        self.assertTrue(body["settings"]["auto_add_models"])


if __name__ == "__main__":
    unittest.main()
