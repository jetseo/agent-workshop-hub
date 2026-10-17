import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from workshop.comfyui import submit_workflow

class FakeResponse:
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, *args):
        return b'{"prompt_id":"abc-123"}'

class ComfyTests(unittest.TestCase):
    def test_opt_in_required(self):
        with self.assertRaises(PermissionError):
            submit_workflow("unused.json")

    def test_reject_remote_url(self):
        with self.assertRaises(ValueError):
            submit_workflow("unused.json", enabled=True, base_url="https://example.com")

    def test_mocked_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "workflow.json"
            workflow.write_text(json.dumps({"1":{"class_type":"EmptyLatentImage","inputs":{"width":512,"height":512,"batch_size":1}}}))
            with patch("workshop.comfyui.urllib.request.urlopen", return_value=FakeResponse()) as mocked:
                result = submit_workflow(workflow, enabled=True)
            self.assertEqual(result["prompt_id"], "abc-123")
            request = mocked.call_args.args[0]
            self.assertEqual(request.full_url, "http://127.0.0.1:8188/prompt")
            self.assertEqual(request.get_method(), "POST")

if __name__ == "__main__":
    unittest.main()
