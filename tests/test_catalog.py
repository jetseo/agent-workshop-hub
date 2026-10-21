import unittest
import urllib.error
from urllib.parse import urlparse

from workshop.catalog_check import check_catalog, probe
from workshop.providers import NAME
from workshop.setup_page import BY_ID, CATALOG, public_catalog


class CatalogTests(unittest.TestCase):
    def test_entries_are_well_formed_and_unique(self):
        ids = [c["id"] for c in CATALOG]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(CATALOG), 15)
        for c in CATALOG:
            self.assertRegex(c["id"], NAME.pattern)
            self.assertIn(c["group"], ("local", "top", "more"))
            self.assertTrue(c["limits"] and c["note"] and c["label"])
            if c["policy"] == "local":
                self.assertEqual(urlparse(c["base_url"]).hostname, "127.0.0.1")
                self.assertFalse(c["needs_key"])
            else:
                self.assertEqual(urlparse(c["base_url"]).scheme, "https")       # tokens never travel over plain http
                self.assertTrue(c["needs_key"], c["id"])                          # keyless claims were wrong when tried
                self.assertEqual(c["policy"], "cloud-may-train")                  # free tiers may train on inputs
                self.assertTrue(c["key_url"].startswith("https://"), c["id"])

    def test_only_ollama_is_local_and_it_comes_first(self):
        self.assertEqual([c["id"] for c in CATALOG if c["policy"] == "local"], ["ollama"])
        self.assertEqual(CATALOG[0]["id"], "ollama")

    def test_public_catalog_exposes_group_and_limits_but_no_secrets(self):
        rows = public_catalog()
        self.assertEqual(len(rows), len(CATALOG))
        self.assertTrue(all("group" in r and "limits" in r for r in rows))
        self.assertEqual(set(rows[0]), {"id", "label", "base_url", "policy", "key_url", "model", "needs_key", "note", "group", "limits"})

    def test_every_catalog_id_resolves(self):
        for c in CATALOG:
            self.assertIs(BY_ID[c["id"]], c)


class CatalogCheckTests(unittest.TestCase):
    def test_statuses_are_classified(self):
        answers = {"https://a.test/v1": (401, ""), "https://b.test/v1": (200, ""), "https://c.test/v1": (404, ""),
                   "https://d.test/v1": (None, ""), "https://e.test/v1": (404, "")}
        catalog = [{"id": n, "label": n, "policy": "cloud-may-train", "base_url": "https://%s.test/v1" % n}
                   for n in "abcde"]
        catalog[4]["id"] = "gemini"                                   # anonymous 404 is normal for Gemini
        rows = {r["id"]: r for r in check_catalog(catalog, prober=lambda url: answers[url])}
        self.assertTrue(rows["a"]["ok"] and rows["b"]["ok"] and rows["gemini"]["ok"])
        self.assertFalse(rows["c"]["ok"])
        self.assertFalse(rows["d"]["ok"])

    def test_local_services_are_not_probed(self):
        called = []
        check_catalog([{"id": "ollama", "label": "o", "policy": "local", "base_url": "http://127.0.0.1:11434/v1"}],
                      prober=lambda url: called.append(url))
        self.assertEqual(called, [])

    def test_probe_sends_no_credentials_and_maps_errors(self):
        seen = []

        def opener(request, timeout):
            seen.append(request)
            raise urllib.error.HTTPError(request.full_url, 401, "no", {}, None)
        self.assertEqual(probe("https://x.test/v1/", opener=opener)[0], 401)
        self.assertEqual(seen[0].full_url, "https://x.test/v1/models")
        self.assertIsNone(seen[0].get_header("Authorization"))

        def dead(request, timeout):
            raise urllib.error.URLError("dns")
        self.assertIsNone(probe("https://x.test/v1", opener=dead)[0])


if __name__ == "__main__":
    unittest.main()
