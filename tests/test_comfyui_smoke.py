import unittest
from unittest.mock import patch
from scripts.comfyui_smoke import wait_for_images


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
