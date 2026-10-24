"""Built-in local engine: JetAI downloads llama.cpp's server and a model file itself, so Ollama is not required.

What it does (only after the user confirmed the exact download size in the app):
  1. looks up the newest llama.cpp release that has a build for this PC (Vulkan build when a GPU is present,
     CPU build otherwise) and the chosen model file on Hugging Face,
  2. downloads both over HTTPS from github.com / huggingface.co only (redirects to their CDNs are checked),
     resumable, and verifies SHA-256 against the hash those sites publish,
  3. unpacks the server into ~/.agent-workshop/engine/ (paths checked, nothing is written outside),
  4. runs `llama-server` bound to 127.0.0.1 (never reachable from the network, never as administrator) and registers
     it as the local service "jetai-engine", so the auto mode can use it like any other local model.
One model is served at a time (switching restarts the server). Nothing is downloaded unless the user asked; removing
files is a button too. This module never talks to a paid service.
"""
import atexit
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlparse

SERVICE_NAME = "jetai-engine"
RELEASES_URL = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=12"
HF_API = "https://huggingface.co/api/models/%s?blobs=true"
HF_FILE = "https://huggingface.co/%s/resolve/main/%s"
ALLOWED_HOST_SUFFIXES = ("github.com", "githubusercontent.com", "huggingface.co", "hf.co")
MAX_UNPACKED_BYTES = 2_500_000_000
CHUNK = 1024 * 1024
DEFAULT_PORT = 11500
READY_TIMEOUT = 180

# Curated models (all Apache-2.0, ungated). Sizes and hashes are read from Hugging Face at plan time, not trusted
# from this table. `vram_gb` is the memory needed to run it fully on a GPU with a modest context.
MODELS = [
    {"id": "qwen2.5-0.5b", "label": "Qwen2.5 0.5B (아주 가벼움, 시험용)", "repo": "bartowski/Qwen2.5-0.5B-Instruct-GGUF",
     "file": "Qwen2.5-0.5B-Instruct-Q4_K_M.gguf", "alias": "qwen2.5-0.5b-instruct", "tags": ["가벼움"], "vram_gb": 1, "license": "Apache-2.0"},
    {"id": "qwen2.5-1.5b", "label": "Qwen2.5 1.5B (가벼운 일상 질문)", "repo": "bartowski/Qwen2.5-1.5B-Instruct-GGUF",
     "file": "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf", "alias": "qwen2.5-1.5b-instruct", "tags": ["일상", "빠름"], "vram_gb": 2, "license": "Apache-2.0"},
    {"id": "qwen2.5-7b", "label": "Qwen2.5 7B (일상·글쓰기 균형)", "repo": "bartowski/Qwen2.5-7B-Instruct-GGUF",
     "file": "Qwen2.5-7B-Instruct-Q4_K_M.gguf", "alias": "qwen2.5-7b-instruct", "tags": ["일상", "균형"], "vram_gb": 6, "license": "Apache-2.0"},
    {"id": "qwen2.5-coder-7b", "label": "Qwen2.5 Coder 7B (코드)", "repo": "bartowski/Qwen2.5-Coder-7B-Instruct-GGUF",
     "file": "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf", "alias": "qwen2.5-coder-7b-instruct", "tags": ["코드"], "vram_gb": 6, "license": "Apache-2.0"},
    {"id": "qwen2.5-coder-14b", "label": "Qwen2.5 Coder 14B (코드, 고성능)", "repo": "bartowski/Qwen2.5-Coder-14B-Instruct-GGUF",
     "file": "Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf", "alias": "qwen2.5-coder-14b-instruct", "tags": ["코드", "고성능"], "vram_gb": 10, "license": "Apache-2.0"},
    {"id": "qwen3.5-9b", "label": "Qwen3.5 9B (새 세대, 측정 전)", "repo": "unsloth/Qwen3.5-9B-GGUF",
     "file": "Qwen3.5-9B-Q4_K_M.gguf", "alias": "qwen3.5-9b", "tags": ["새 모델", "측정 전"], "vram_gb": 8, "license": "Apache-2.0"},
]
# Memory facts per model (weights = the Q4_K_M file; KV = bytes per token of an fp16 KV cache, 2 x layers x kv_heads x head_dim x 2,
# read from each model's config.json on Hugging Face on 2026-10-22). Hybrid/sliding-attention models need less KV than this: upper bound.
FACTS = {"qwen2.5-0.5b": (0.37, 12288), "qwen2.5-1.5b": (0.92, 28672), "qwen2.5-7b": (4.36, 57344),
         "qwen2.5-coder-7b": (4.36, 57344), "qwen2.5-coder-14b": (8.37, 196608), "qwen3.5-9b": (5.3, 131072)}
for _m in MODELS:
    _m["weights_gib"], _m["kv_bytes_per_token"] = FACTS[_m["id"]]
BY_ID = {m["id"]: m for m in MODELS}

# Candidate models checked against Hugging Face (existence, licence, GGUF size) but NOT installable and NOT measured: shown as "측정 전".
# Tool-calling support is unverified for all of them (JetAI's direct mode does not need it).
CANDIDATES = [
    {"id": "gpt-oss-20b", "label": "GPT-OSS 20B", "repo": "unsloth/gpt-oss-20b-GGUF", "file": "gpt-oss-20b-Q4_K_M.gguf", "license": "Apache-2.0",
     "params_b": 20.9, "moe": True, "vision": False, "weights_gib": 10.8, "kv_bytes_per_token": 49152, "tools": None, "reasoning": True},
    {"id": "qwen3-coder-30b-a3b", "label": "Qwen3 Coder 30B-A3B", "repo": "unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF", "file": "Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf",
     "license": "Apache-2.0", "params_b": 30.5, "moe": True, "vision": False, "weights_gib": 17.3, "kv_bytes_per_token": 98304, "tools": None, "reasoning": None},
    {"id": "glm-4.7-flash", "label": "GLM-4.7-Flash", "repo": "unsloth/GLM-4.7-Flash-GGUF", "file": "GLM-4.7-Flash-Q4_K_M.gguf", "license": "MIT",
     "params_b": 31.2, "moe": True, "vision": False, "weights_gib": 17.1, "kv_bytes_per_token": None, "tools": None, "reasoning": None},
]
EXT_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,60}/[A-Za-z0-9][A-Za-z0-9._-]{0,100}$")
EXT_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,120}Q4_K_M\.gguf$")        # one Q4_K_M file: never a split or a free-form name
OVERHEAD_GIB = 0.6        # runtime buffers (rough); the estimate is a planning aid, not a promise
SAFETY = 1.08


class EngineError(Exception):
    """A problem to show to the user as is (Korean message)."""


# ---- network helpers ------------------------------------------------------------------------------------------

def host_allowed(url):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(host == s or host.endswith("." + s) for s in ALLOWED_HOST_SUFFIXES)


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not host_allowed(newurl):
            raise urllib.error.URLError("허용되지 않은 주소로 넘어가려 해서 중단했습니다: " + (urlparse(newurl).hostname or "?"))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def default_opener():
    return urllib.request.build_opener(_CheckedRedirects)


def _get_json(url, opener, timeout=30):
    if not host_allowed(url):
        raise EngineError("허용되지 않은 주소입니다.")
    request = urllib.request.Request(url, headers={"User-Agent": "jetai-engine", "Accept": "application/json"})
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read(5_000_000).decode("utf-8"))


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url, dest, *, sha256, size, opener, progress=None, cancel=None):
    """Resumable download to `dest`, verified against sha256. Raises EngineError on any mismatch (the bad file is removed)."""
    if not host_allowed(url):
        raise EngineError("허용되지 않은 주소입니다.")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    have = part.stat().st_size if part.exists() else 0
    if have > size:
        part.unlink()
        have = 0
    if have < size:
        headers = {"User-Agent": "jetai-engine"}
        if have:
            headers["Range"] = "bytes=%d-" % have
        try:
            with opener.open(urllib.request.Request(url, headers=headers), timeout=60) as response:
                if have and getattr(response, "status", 206) != 206:
                    have = 0                                           # the server ignored Range: start over
                with open(part, "ab" if have else "wb") as out:
                    while True:
                        if cancel is not None and cancel.is_set():
                            raise EngineError("취소했습니다.")
                        block = response.read(CHUNK)
                        if not block:
                            break
                        out.write(block)
                        have += len(block)
                        if progress:
                            progress(have, size)
                        if have > size:
                            raise EngineError("받은 크기가 예상보다 큽니다.")
        except urllib.error.URLError as error:
            raise EngineError("다운로드에 실패했습니다: " + str(getattr(error, "reason", error))[:200]) from None
    if part.stat().st_size != size:
        raise EngineError("받은 크기가 맞지 않습니다(%d / %d). 다시 시도하면 이어서 받습니다." % (part.stat().st_size, size))
    if sha256_of(part) != sha256.lower():
        part.unlink()
        raise EngineError("파일 검증(SHA-256)이 맞지 않아 지웠습니다. 다시 시도해 주세요.")
    os.replace(part, dest)
    return dest


def safe_extract(archive, target, limit=MAX_UNPACKED_BYTES):
    """Unpack a zip / tar.gz into `target`; any member that would land outside it (or too much data) aborts."""
    target = Path(target).resolve()
    target.mkdir(parents=True, exist_ok=True)
    total = 0

    def check(name, size):
        nonlocal total
        dest = (target / name).resolve()
        if target != dest and target not in dest.parents:
            raise EngineError("압축 파일에 안전하지 않은 경로가 있어 중단했습니다: " + name)
        total += size
        if total > limit:
            raise EngineError("압축을 풀면 너무 큽니다.")
        return dest

    name = str(archive)
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                check(info.filename, info.file_size)
            z.extractall(target)
    else:
        with tarfile.open(archive) as t:
            for member in t.getmembers():
                if member.issym() or member.islnk() or member.isdev():
                    if not member.issym():
                        raise EngineError("압축 파일에 허용되지 않은 항목이 있습니다: " + member.name)
                check(member.name, member.size)
            t.extractall(target)


# ---- this PC ---------------------------------------------------------------------------------------------------

def detect_hardware():
    """{"ram_gb", "gpu", "vram_gb", "system"}: what the recommendations are based on (read only)."""
    info = {"ram_gb": 0.0, "gpu": None, "vram_gb": 0.0, "system": platform.system()}
    try:
        if os.name == "nt":
            import ctypes

            class Mem(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("avail", ctypes.c_ulonglong), ("pt", ctypes.c_ulonglong), ("pa", ctypes.c_ulonglong),
                            ("vt", ctypes.c_ulonglong), ("va", ctypes.c_ulonglong), ("ve", ctypes.c_ulonglong)]
            mem = Mem(); mem.length = ctypes.sizeof(Mem)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
            info["ram_gb"] = round(mem.total / 2**30, 1)
        else:
            info["ram_gb"] = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1)
    except (OSError, ValueError, AttributeError):
        pass
    exe = shutil.which("nvidia-smi")
    if exe:
        try:
            out = subprocess.run([exe, "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=8).stdout.strip().splitlines()[0]
            name, mib, free = [p.strip() for p in out.split(",")[:3]]
            info["gpu"], info["vram_gb"] = name, round(float(mib) / 1024, 1)
            info["vram_free_gb"] = round(float(free) / 1024, 1)
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            pass
    return info


def fit(model, hardware):
    """('gpu'|'cpu'|'no', Korean note) for one catalog model on this PC."""
    need = model["vram_gb"]
    if hardware["vram_gb"] and hardware["vram_gb"] >= need:
        return "gpu", "이 PC의 그래픽 카드에서 빠르게 실행됩니다"
    if hardware["ram_gb"] >= need * 2:
        return "cpu", "그래픽 메모리가 부족해 CPU로 실행됩니다(느림)" if hardware["vram_gb"] else "CPU로 실행됩니다(느림)"
    return "no", "메모리가 부족해 권장하지 않습니다"


def estimate_memory(model, ctx_tokens):
    """{"weights_gib", "kv_gib", "total_gib", "kv_known"}: weights + KV cache for `ctx_tokens` + runtime buffers, with a safety margin."""
    weights, per_token = model.get("weights_gib"), model.get("kv_bytes_per_token")
    kv = ctx_tokens * per_token / 2**30 if per_token else None
    total = ((weights or 0) + (kv or 0) + OVERHEAD_GIB) * SAFETY
    return {"weights_gib": weights, "kv_gib": round(kv, 2) if kv is not None else None, "total_gib": round(total, 2), "kv_known": kv is not None}


def fit_with_context(model, hardware, ctx_tokens=8192):
    """Context-aware fit: ("gpu"|"partial"|"cpu"|"no", note, memory, max_ctx).

    "gpu": weights + KV cache + buffers fit in the VRAM that is FREE now. "partial": the model fits in RAM and part of it on the GPU
    (slower). "cpu": only RAM. `max_ctx`: the longest context that still fits fully on the GPU (None if unknown or nothing fits).
    GPU memory and system RAM are never treated as one pool; the integrated GPU is not counted."""
    memory = estimate_memory(model, ctx_tokens)
    free = hardware.get("vram_free_gb") or hardware.get("vram_gb") or 0
    ram, total, weights = hardware.get("ram_gb") or 0, memory["total_gib"], memory["weights_gib"] or 0
    max_ctx = None
    if model.get("kv_bytes_per_token") and weights:
        room = free / SAFETY - weights - OVERHEAD_GIB
        max_ctx = int(room * 2**30 / model["kv_bytes_per_token"]) if room > 0 else None
    if free and total <= free:
        return "gpu", "그래픽 메모리에 모두 올라갑니다(예상 %.1f GiB, 지금 비어 있는 %.1f GiB)" % (total, free), memory, max_ctx
    if ram and ram >= total * 1.5:
        if free and weights and free > OVERHEAD_GIB * 2:
            return "partial", "일부만 그래픽 메모리에 올라가고 나머지는 RAM으로 돌아 느립니다(예상 %.1f GiB, 비어 있는 VRAM %.1f GiB)" % (total, free), memory, max_ctx
        return "cpu", "CPU/RAM으로만 돌아 느립니다(예상 %.1f GiB)" % total, memory, max_ctx
    return "no", "메모리가 부족해 권장하지 않습니다(예상 %.1f GiB)" % total, memory, max_ctx


def pick_asset(assets, system, has_gpu):
    """The llama.cpp release asset for this OS: Vulkan build with a GPU (works on NVIDIA/AMD/Intel without extra
    runtimes), CPU build otherwise. None when the release has no build for this system."""
    if system == "Windows":
        want = "bin-win-vulkan-x64.zip" if has_gpu else "bin-win-cpu-x64.zip"
    elif system == "Linux":
        want = "bin-ubuntu-vulkan-x64.tar.gz" if has_gpu else "bin-ubuntu-x64.tar.gz"
    else:
        return None
    for asset in assets:
        if asset["name"].startswith("llama-") and asset["name"].endswith(want):
            return asset
    return None


def free_port(preferred=DEFAULT_PORT):
    for port in [preferred] + list(range(preferred + 1, preferred + 40)):
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise EngineError("사용할 수 있는 포트를 찾지 못했습니다.")


# ---- the engine ------------------------------------------------------------------------------------------------

class Engine:
    def __init__(self, home, providers, *, opener=None, hardware_fn=detect_hardware, argv_fn=None, popen=subprocess.Popen,
                 health_fn=None):
        self.home = Path(home)
        self.providers = providers
        self.opener = opener or default_opener()
        self.hardware_fn = hardware_fn
        self.argv_fn = argv_fn or self._default_argv
        self.popen = popen
        self.health_fn = health_fn or self._health
        self.state_path = self.home / "state.json"
        self._lock = threading.RLock()
        self._proc = None
        self._port = None
        self._active = None
        self._ready = False
        self.job = {"running": False, "step": "", "done": 0, "total": 0, "error": "", "finished": False}
        self._cancel = threading.Event()
        self._thread = None
        atexit.register(self.stop)

    # -- state --------------------------------------------------------------------------------------------------
    def _state(self):
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"binary": None, "models": {}, "active": None}

    def _model(self, model_id):
        """A built-in model, or one registered from a discovery card (`register_external`, kept in the state file)."""
        return BY_ID.get(model_id) or (self._state().get("extra") or {}).get(model_id)

    def register_external(self, repo, file):
        """Make a Hugging Face GGUF installable through the SAME confirmation flow as the built-in models (exact size and hash are read
        from huggingface.co at plan time, nothing is downloaded here). The caller must have checked that the repo came from a discovery card."""
        if not isinstance(repo, str) or not EXT_REPO.fullmatch(repo) or not isinstance(file, str) or not EXT_FILE.fullmatch(file):
            raise EngineError("받을 수 없는 모델 이름입니다.")
        try:
            info = _get_json(HF_API % repo, self.opener)
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise EngineError("모델 정보를 가져오지 못했습니다: " + str(getattr(error, "reason", error))[:150]) from None
        if info.get("gated") or info.get("private"):
            raise EngineError("이 모델은 로그인·동의가 필요해 자동으로 받을 수 없습니다.")
        sibling = next((s for s in info.get("siblings", []) if s.get("rfilename") == file), None)
        lfs = (sibling or {}).get("lfs") or {}
        size = int((sibling or {}).get("size") or lfs.get("size") or 0)
        if not sibling or not lfs.get("sha256") or size <= 0:
            raise EngineError("모델 파일의 크기·해시를 확인하지 못해 받지 않습니다.")
        name = repo.split("/", 1)[1]
        model_id = "ext-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]
        license_tag = next((t[8:] for t in info.get("tags", []) if isinstance(t, str) and t.startswith("license:")), "확인 필요")
        weights = round(size / 2**30, 2)
        entry = {"id": model_id, "label": name.replace("-GGUF", "") + " (발견·측정 전)", "repo": repo, "file": file, "alias": model_id,
                 "tags": ["발견", "측정 전"], "vram_gb": max(2, int(weights + 1.5)), "license": license_tag, "weights_gib": weights,
                 "kv_bytes_per_token": None, "external": True}
        with self._lock:
            state = self._state()
            state.setdefault("extra", {})[model_id] = entry
            self._save(state)
        return model_id

    def _save(self, state):
        self.home.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def status(self):
        state, hardware = self._state(), self.hardware_fn()
        models = []
        for m in list(MODELS) + list((state.get("extra") or {}).values()):
            kind, note = fit(m, hardware)
            installed = state["models"].get(m["id"])
            ctx_fit = fit_with_context(m, hardware)
            models.append({"id": m["id"], "label": m["label"], "tags": m["tags"], "license": m["license"], "fit": kind, "fit_note": note,
                           "memory": ctx_fit[2], "fit_ctx": {"fit": ctx_fit[0], "note": ctx_fit[1], "ctx_tokens": 8192, "max_ctx": ctx_fit[3]},
                           "installed": bool(installed and (self.home / "models" / installed["file"]).exists()),
                           "bytes": installed["bytes"] if installed else None, "recommended": kind == "gpu"})
        return {"supported": platform.system() in ("Windows", "Linux"), "hardware": hardware,
                "binary": state["binary"] and {"tag": state["binary"]["tag"], "variant": state["binary"]["variant"]},
                "models": models, "active": self._active, "running": self._proc is not None and self._proc.poll() is None,
                "ready": self._ready, "port": self._port, "job": dict(self.job)}

    # -- plan (nothing is downloaded) ---------------------------------------------------------------------------
    def plan(self, model_id):
        """What installing `model_id` would download, with exact sizes, so the user can confirm."""
        model = self._model(model_id)
        if model is None:
            raise EngineError("알 수 없는 모델입니다.")
        hardware = self.hardware_fn()
        if hardware["system"] not in ("Windows", "Linux"):
            raise EngineError("내장 엔진은 Windows와 Linux에서만 쓸 수 있습니다.")
        state, items = self._state(), []
        if not state["binary"] or not Path(state["binary"]["exe"]).exists():
            try:
                releases = _get_json(RELEASES_URL, self.opener)
            except (urllib.error.URLError, OSError, ValueError) as error:
                raise EngineError("llama.cpp 릴리스 정보를 가져오지 못했습니다: " + str(getattr(error, "reason", error))[:150]) from None
            asset = release = None
            for candidate in releases:
                asset = pick_asset(candidate.get("assets", []), hardware["system"], bool(hardware["gpu"]))
                if asset and asset.get("digest", "").startswith("sha256:"):
                    release = candidate
                    break
            if not asset or release is None:
                raise EngineError("이 PC용 llama.cpp 빌드를 찾지 못했습니다.")
            items.append({"kind": "binary", "name": asset["name"], "bytes": asset["size"], "sha256": asset["digest"][7:],
                          "url": asset["browser_download_url"], "tag": release["tag_name"], "source": "github.com/ggml-org/llama.cpp",
                          "variant": "vulkan" if "vulkan" in asset["name"] else "cpu"})
        installed = state["models"].get(model_id)
        if not (installed and (self.home / "models" / installed["file"]).exists()):
            try:
                info = _get_json(HF_API % model["repo"], self.opener)
            except (urllib.error.URLError, OSError, ValueError) as error:
                raise EngineError("모델 정보를 가져오지 못했습니다: " + str(getattr(error, "reason", error))[:150]) from None
            if info.get("gated") or info.get("private"):
                raise EngineError("이 모델은 로그인·동의가 필요해 자동으로 받을 수 없습니다.")
            sibling = next((s for s in info.get("siblings", []) if s.get("rfilename") == model["file"]), None)
            lfs = (sibling or {}).get("lfs") or {}
            if not sibling or not lfs.get("sha256") or not (sibling.get("size") or lfs.get("size")):
                raise EngineError("모델 파일의 크기·해시를 확인하지 못해 받지 않습니다.")
            items.append({"kind": "model", "name": model["file"], "bytes": int(sibling.get("size") or lfs["size"]), "sha256": lfs["sha256"],
                          "url": HF_FILE % (model["repo"], model["file"]), "source": "huggingface.co/" + model["repo"]})
        total = sum(i["bytes"] for i in items)
        try:
            free = shutil.disk_usage(self.home if self.home.exists() else self.home.parent).free
        except OSError:
            free = 0
        return {"model": model_id, "label": model["label"], "license": model["license"], "items": items, "total_bytes": total,
                "disk_free_bytes": free, "enough_disk": free >= total * 1.15 + 200_000_000, "already_installed": not items}

    # -- install ------------------------------------------------------------------------------------------------
    def install(self, model_id, confirm_bytes):
        """Start the download in the background. `confirm_bytes` must equal the planned total the user was shown."""
        with self._lock:
            if self.job["running"]:
                raise EngineError("이미 설치 중입니다.")
            plan = self.plan(model_id)
            if confirm_bytes != plan["total_bytes"]:
                raise EngineError("받을 크기가 바뀌었습니다(%d → %d바이트). 다시 확인한 뒤 진행해 주세요." % (confirm_bytes or 0, plan["total_bytes"]))
            if not plan["enough_disk"]:
                raise EngineError("디스크 여유 공간이 부족합니다.")
            self._cancel.clear()
            self.job = {"running": True, "step": "준비", "done": 0, "total": plan["total_bytes"], "error": "", "finished": False}
            self._thread = threading.Thread(target=self._run_install, args=(model_id, plan), name="engine-install", daemon=True)
            self._thread.start()
            return plan

    def cancel(self):
        self._cancel.set()

    def _run_install(self, model_id, plan):
        base = {"done": 0}
        try:
            for item in plan["items"]:
                self.job["step"] = ("엔진 받는 중 · " if item["kind"] == "binary" else "모델 받는 중 · ") + item["name"]
                offset = base["done"]

                def progress(have, size, offset=offset):
                    self.job["done"] = offset + have
                if item["kind"] == "binary":
                    archive = download(item["url"], self.home / "downloads" / item["name"], sha256=item["sha256"], size=item["bytes"],
                                       opener=self.opener, progress=progress, cancel=self._cancel)
                    self.job["step"] = "엔진 압축 푸는 중"
                    target = self.home / "bin" / item["tag"]
                    shutil.rmtree(target, ignore_errors=True)
                    safe_extract(archive, target)
                    exe = self._find_server(target)
                    if exe is None:
                        raise EngineError("압축 안에서 llama-server를 찾지 못했습니다.")
                    if os.name != "nt":
                        os.chmod(exe, 0o755)
                    archive.unlink(missing_ok=True)
                    state = self._state()
                    state["binary"] = {"tag": item["tag"], "variant": item["variant"], "exe": str(exe)}
                    self._save(state)
                else:
                    download(item["url"], self.home / "models" / item["name"], sha256=item["sha256"], size=item["bytes"],
                             opener=self.opener, progress=progress, cancel=self._cancel)
                    state = self._state()
                    state["models"][model_id] = {"file": item["name"], "bytes": item["bytes"], "sha256": item["sha256"]}
                    self._save(state)
                base["done"] += item["bytes"]
            self.job.update(step="설치 완료", done=self.job["total"], finished=True)
        except EngineError as error:
            self.job.update(error=str(error), finished=True)
        except Exception as error:                          # never leave the UI waiting on a crashed thread
            self.job.update(error="설치 중 오류: " + str(error)[:200], finished=True)
        finally:
            self.job["running"] = False

    @staticmethod
    def _find_server(root):
        names = ("llama-server.exe", "llama-server")
        for path in sorted(Path(root).rglob("*"), key=lambda p: len(p.parts)):
            if path.name in names and path.is_file():
                return path
        return None

    # -- run ----------------------------------------------------------------------------------------------------
    def _default_argv(self, exe, model_path, port, alias, hardware):
        argv = [str(exe), "-m", str(model_path), "--host", "127.0.0.1", "--port", str(port), "-c", "8192", "--alias", alias]
        if hardware.get("gpu"):
            argv += ["-ngl", "99"]
        return argv

    @staticmethod
    def _health(port):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/health" % port, timeout=2) as response:
                return response.status == 200
        except (OSError, ValueError):
            return False

    def start(self, model_id):
        """Run the server for an installed model on 127.0.0.1 and register it as the local service."""
        with self._lock:
            state = self._state()
            entry, binary = state["models"].get(model_id), state["binary"]
            if not entry or not binary or not Path(binary["exe"]).exists() or not (self.home / "models" / entry["file"]).exists():
                raise EngineError("먼저 설치해 주세요.")
            self.stop()
            model = self._model(model_id)
            self._port = free_port()
            argv = self.argv_fn(binary["exe"], self.home / "models" / entry["file"], self._port, model["alias"], self.hardware_fn())
            log = open(self.home / "llama-server.log", "ab")
            kwargs = {"stdout": log, "stderr": log, "stdin": subprocess.DEVNULL}
            if os.name == "nt":
                kwargs["creationflags"] = 0x08000000                    # CREATE_NO_WINDOW
            try:
                self._proc = self.popen(argv, **kwargs)
            except OSError as error:
                raise EngineError("엔진을 실행하지 못했습니다: " + str(error)[:160]) from None
            finally:
                log.close()
            self._active, self._ready = model_id, False
            state["active"] = model_id
            self._save(state)
            threading.Thread(target=self._wait_ready, args=(self._proc, self._port, model), name="engine-ready", daemon=True).start()

    def _wait_ready(self, proc, port, model):
        deadline = time.time() + READY_TIMEOUT
        while time.time() < deadline and proc.poll() is None:
            if self.health_fn(port):
                if self._proc is proc:
                    self._register(port, model)          # first make it a usable service ...
                    self._ready = True                   # ... then say it is ready (never "ready" without the service)
                return
            time.sleep(0.5)

    def _register(self, port, model):
        """Make the running server a local service: the auto mode can pick it, the chat can use it."""
        base_url = "http://127.0.0.1:%d/v1" % port
        self.providers.add(SERVICE_NAME, base_url=base_url, data_policy="local", models=[model["alias"]])

    def stop(self):
        with self._lock:
            proc, self._proc, self._ready = self._proc, None, False
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except Exception:
                    proc.kill()

    def autostart(self):
        """At app start: bring the last used model back, if the user left it installed."""
        state = self._state()
        active = state.get("active")
        if active in state["models"] and state["binary"]:
            try:
                self.start(active)
            except EngineError:
                pass

    # -- remove -------------------------------------------------------------------------------------------------
    def remove(self, what):
        """what: a model id (deletes that file) or "all" (engine, models and downloads). Stops the server first."""
        with self._lock:
            if self.job["running"]:
                raise EngineError("설치 중에는 지울 수 없습니다.")
            self.stop()
            state = self._state()
            if what == "all":
                shutil.rmtree(self.home / "bin", ignore_errors=True)
                shutil.rmtree(self.home / "models", ignore_errors=True)
                shutil.rmtree(self.home / "downloads", ignore_errors=True)
                state = {"binary": None, "models": {}, "active": None}
            elif what in state["models"]:
                (self.home / "models" / state["models"][what]["file"]).unlink(missing_ok=True)
                del state["models"][what]
                if state.get("active") == what:
                    state["active"] = None
            else:
                raise EngineError("알 수 없는 항목입니다.")
            self._save(state)
            self._active = state.get("active")
            try:
                self.providers.remove(SERVICE_NAME)
            except (ValueError, KeyError, OSError):
                pass
