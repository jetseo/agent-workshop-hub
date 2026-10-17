import unittest
from unittest.mock import patch
from scripts.comfyui_smoke import wait_for_images, download_images, save_report
import tempfile
from pathlib import Path


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, *args):
        import json
        return json.dumps(self.payload).encode("utf-8")


class SmokeTests(unittest.TestCase):
    @patch("scripts.comfyui_smoke.urllib.request.urlopen")
    def test_download_png_and_report(self, urlopen):
        class BinaryResponse:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, *args):
                return bytes.fromhex("89504e470d0a1a0a") + b"test"
        urlopen.return_value = BinaryResponse()
        with tempfile.TemporaryDirectory() as temp:
            files = download_images([{"filename": "test.png", "type": "output"}],
                                    base_url="http://127.0.0.1:8188", output_dir=temp)
            self.assertEqual(len(files), 1)
            self.assertTrue(Path(files[0]["path"]).exists())
            report = save_report({"images": files}, Path(temp) / "report.json")
            self.assertTrue(report.exists())
            with self.assertRaises(FileExistsError):
                save_report({"images": files}, report)

    @patch("scripts.comfyui_smoke.urllib.request.urlopen")
    def test_image_metadata(self, urlopen):
        urlopen.return_value = FakeResponse({
            "abc": {"status": {"completed": True}, "outputs": {
                "9": {"images": [{"filename": "test.png", "type": "output"}]}}}
        })
        self.assertEqual(wait_for_images("abc", timeout=1)[0]["filename"], "test.png")

    @patch("scripts.comfyui_smoke.urllib.request.urlopen")
    def test_completed_without_images_fails(self, urlopen):
        urlopen.return_value = FakeResponse({"abc": {"status": {"completed": True}, "outputs": {}}})
        with self.assertRaises(RuntimeError):
            wait_for_images("abc", timeout=1)


if __name__ == "__main__":
    unittest.main()
