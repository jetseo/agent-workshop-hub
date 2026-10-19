import unittest
from unittest.mock import patch, MagicMock
from workshop.planner import validate_plan, plan_local

class PlannerTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(len(validate_plan({"tasks": [
            {"title": "Implement", "kind": "code", "depends_on": []},
            {"title": "Verify", "kind": "qa", "depends_on": [0]}
        ]})["tasks"]), 2)

    def test_invalid_dependencies(self):
        with self.assertRaises(ValueError):
            validate_plan({"tasks": [{"title": "Bad", "kind": "code", "depends_on": [0]}]})

    def test_reject_remote(self):
        with self.assertRaises(ValueError):
            plan_local("Build a game", model="test", base_url="http://example.com")

    def test_reject_excess(self):
        with self.assertRaises(ValueError):
            validate_plan({"tasks": [{"title": "a", "kind": "code", "depends_on": []}] * 5})

    @patch("workshop.planner.urllib.request.urlopen")
    def test_local_mock(self, mock_open):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"message":{"content":"{\\"tasks\\":[{\\"title\\":\\"Review\\",\\"kind\\":\\"plan\\",\\"depends_on\\":[]}]}"}}'
        mock_open.return_value = response
        self.assertEqual(plan_local("Review code", model="local")["tasks"][0]["title"], "Review")

if __name__ == "__main__":
    unittest.main()
