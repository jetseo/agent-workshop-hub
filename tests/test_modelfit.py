import subprocess
import unittest
from unittest import mock

from workshop import engine

M7 = engine.BY_ID["qwen2.5-coder-7b"]
M14 = engine.BY_ID["qwen2.5-coder-14b"]


def hw(vram=12.0, free=12.0, ram=64.0):
    return {"ram_gb": ram, "gpu": "RTX" if vram else None, "vram_gb": vram, "vram_free_gb": free, "system": "Windows"}


class EstimateTests(unittest.TestCase):
    def test_weights_plus_kv_cache_plus_buffers_with_a_margin(self):
        est = engine.estimate_memory(M7, 4096)
        self.assertEqual(est["weights_gib"], 4.36)
        self.assertAlmostEqual(est["kv_gib"], 4096 * 57344 / 2**30, places=2)
        self.assertAlmostEqual(est["total_gib"], (4.36 + 0.22 + engine.OVERHEAD_GIB) * engine.SAFETY, delta=0.02)
        self.assertGreater(engine.estimate_memory(M7, 32768)["total_gib"], est["total_gib"])      # a longer context costs memory
        self.assertGreater(engine.estimate_memory(M14, 4096)["kv_gib"], est["kv_gib"])           # the 14B has a bigger KV cache per token

    def test_unknown_kv_is_said_not_invented(self):
        glm = [c for c in engine.CANDIDATES if c["id"] == "glm-4.7-flash"][0]
        est = engine.estimate_memory(glm, 8192)
        self.assertFalse(est["kv_known"])
        self.assertIsNone(est["kv_gib"])
        self.assertIsNone(engine.fit_with_context(glm, hw())[3])                                   # no max context without KV facts


class FitTests(unittest.TestCase):
    def test_fits_the_free_vram_not_the_total(self):
        self.assertEqual(engine.fit_with_context(M7, hw(free=12.0))[0], "gpu")
        kind, note, _, _ = engine.fit_with_context(M7, hw(free=3.0))                                # something else is using the GPU
        self.assertEqual(kind, "partial")
        self.assertIn("RAM", note)

    def test_cpu_only_and_not_enough_memory(self):
        self.assertEqual(engine.fit_with_context(M7, hw(vram=0, free=0, ram=32))[0], "cpu")
        self.assertEqual(engine.fit_with_context(M14, hw(vram=0, free=0, ram=8))[0], "no")

    def test_ram_is_never_added_to_vram(self):
        self.assertNotEqual(engine.fit_with_context(M14, hw(vram=8, free=8, ram=64), 8192)[0], "gpu")   # 64 GB of RAM does not make 8 GB of VRAM enough

    def test_max_context_grows_with_free_vram_and_matches_the_fit(self):
        small = engine.fit_with_context(M7, hw(free=6.0))[3]
        large = engine.fit_with_context(M7, hw(free=12.0))[3]
        self.assertTrue(0 < small < large)
        self.assertEqual(engine.fit_with_context(M7, hw(free=12.0), large)[0], "gpu")              # that context really fits
        self.assertNotEqual(engine.fit_with_context(M7, hw(free=12.0), large * 2)[0], "gpu")
        self.assertIsNone(engine.fit_with_context(M14, hw(free=2.0))[3])                           # nothing fits at all

    def test_old_fit_is_unchanged(self):
        self.assertEqual(engine.fit(M7, hw())[0], "gpu")


class HardwareAndCatalogTests(unittest.TestCase):
    def test_detect_hardware_reads_free_vram(self):
        done = subprocess.CompletedProcess([], 0, stdout="NVIDIA GeForce RTX 3060, 12288, 6298\n")
        with mock.patch.object(engine.shutil, "which", return_value="nvidia-smi"), mock.patch.object(engine.subprocess, "run", return_value=done):
            info = engine.detect_hardware()
        self.assertEqual((info["gpu"], info["vram_gb"], info["vram_free_gb"]), ("NVIDIA GeForce RTX 3060", 12.0, 6.2))

    def test_every_installable_model_has_memory_facts(self):
        for model in engine.MODELS:
            self.assertGreater(model["weights_gib"], 0, model["id"])
            self.assertGreater(model["kv_bytes_per_token"], 0, model["id"])

    def test_candidates_are_metadata_only(self):
        for candidate in engine.CANDIDATES:
            self.assertNotIn(candidate["id"], engine.BY_ID)                                         # not installable, nothing to download
            self.assertIsNone(candidate["tools"], candidate["id"])                                   # tool calling stays unverified
            self.assertTrue(candidate["license"] and candidate["repo"] and candidate["weights_gib"] > 0)

    def test_status_carries_the_context_aware_fit(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            from workshop.providers import Providers
            status = engine.Engine(Path(tmp) / "engine", Providers(Path(tmp) / "p.json"), hardware_fn=lambda: hw(free=12.0)).status()
        entry = [m for m in status["models"] if m["id"] == "qwen2.5-coder-7b"][0]
        self.assertEqual(entry["fit_ctx"]["fit"], "gpu")
        self.assertGreater(entry["fit_ctx"]["max_ctx"], 8192)
        self.assertTrue(entry["memory"]["kv_known"])


if __name__ == "__main__":
    unittest.main()
