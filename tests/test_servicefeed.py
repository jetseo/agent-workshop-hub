import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

from workshop import setup_page
from workshop.servicefeed import ServiceFeed, parse_readme, safe_https_url, slug

README = """
# awesome

  ### [Google Gemini](https://aistudio.google.com/app/apikey) \U0001F1FA\U0001F1F8

  | Detail | Info |
  |---|---|
  | **Free Models** | Gemini 2.5 Flash |
  | **Rate Limits** | 15 RPM |
  | **OpenAI Compat** | ✅ Yes — `https://generativelanguage.googleapis.com/v1beta/openai/` |

  ### [Brand New AI](https://brandnew.example.com/keys)

  | **Free Models** | Llama 3.3 70B, Qwen3 |
  | **Rate Limits** | 20 RPM / 200 RPD |
  | **OpenAI Compat** | ✅ Yes — `https://api.brandnew.example.com/v1` |

  ### [Cohere-like](https://dashboard.cohere.example.com/api-keys)

  | **Rate Limits** | 20 RPM |
  | **OpenAI Compat** | ⚠️ Partial — native SDK preferred |

  ### [Dead Service](https://dead.example.com/keys)

  | **OpenAI Compat** | ✅ Yes — `https://api.dead.example.com/v1` |

  ### [Local Trap](https://trap.example.com/keys)

  | **OpenAI Compat** | ✅ Yes — `https://127.0.0.1/v1` |

  ### [Http Only](https://http.example.com/keys)

  | **OpenAI Compat** | ✅ Yes — `http://api.http.example.com/v1` |

  ### [Credentials](https://cred.example.com/keys)

  | **OpenAI Compat** | ✅ Yes — `https://user:pw@api.cred.example.com/v1` |
"""

ALIVE = {"https://api.brandnew.example.com/v1": 401, "https://generativelanguage.googleapis.com/v1beta/openai": 404}


def prober(url):
    return ALIVE.get(url), "x"


class ParseTests(unittest.TestCase):
    def test_only_providers_with_an_openai_compatible_https_address_are_parsed(self):
        names = [p["name"] for p in parse_readme(README)]
        self.assertIn("Brand New AI", names)
        self.assertNotIn("Cohere-like", names)                              # "Partial": no address
        self.assertNotIn("Http Only", names)                                # plain http is not an https address
        brand = [p for p in parse_readme(README) if p["name"] == "Brand New AI"][0]
        self.assertEqual(brand["base_url"], "https://api.brandnew.example.com/v1")
        self.assertEqual(brand["key_url"], "https://brandnew.example.com/keys")
        self.assertIn("200 RPD", brand["limits"])

    def test_unsafe_addresses(self):
        for bad in ("http://a.example.com", "https://127.0.0.1/v1", "https://localhost/v1", "https://10.0.0.5/v1", "https://192.168.1.2/",
                    "https://user:pw@a.example.com/", "https://printer.local/v1", "https://intranet/v1", "https://[::1]/v1", ""):
            self.assertFalse(safe_https_url(bad), bad)
        self.assertTrue(safe_https_url("https://api.example.com/v1"))

    def test_slug(self):
        self.assertEqual(slug("Brand New AI \U0001F1FA\U0001F1F8"), "brand-new-ai")
        self.assertEqual(slug("!!!"), "service")
        self.assertLessEqual(len(slug("x" * 80)), 30)


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "feed.json"
        self.text = README
        self.feed = ServiceFeed(self.path, fetch=lambda: self.text, prober=prober, builtin=setup_page.CATALOG)

    def test_new_alive_safe_services_are_kept_and_everything_else_is_dropped(self):
        result = self.feed.refresh(force=True, now=1000)
        self.assertEqual(result["added"], ["Brand New AI"])                    # Gemini is built in; dead/unsafe/duplicates dropped
        self.assertEqual([e["id"] for e in self.feed.data["entries"]], ["brand-new-ai"])
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["entries"][0]["probe"], 401)

    def test_entries_are_shown_as_unverified_and_never_as_free(self):
        self.feed.refresh(force=True, now=1000)
        entry = self.feed.catalog_entries(now=1000)[0]
        self.assertEqual((entry["group"], entry["free"], entry["discovered"], entry["is_new"]), ("new", "suffix", True, True))
        self.assertIn("미검증", entry["label"])
        self.assertTrue(entry["needs_key"] and entry["policy"] == "cloud-may-train")
        self.assertFalse(self.feed.catalog_entries(now=1000 + 15 * 86400)[0]["is_new"])      # the NEW badge fades after two weeks

    def test_refresh_is_throttled_and_offline_keeps_the_old_list(self):
        self.feed.refresh(force=True, now=1000)
        self.assertEqual(self.feed.refresh(now=1100)["skipped"], "recently refreshed")
        self.feed.fetch = lambda: (_ for _ in ()).throw(urllib.error.URLError("offline"))
        result = self.feed.refresh(force=True, now=999999)
        self.assertEqual((result["skipped"], result["total"]), ("offline", 1))
        self.assertEqual(len(self.feed.catalog_entries()), 1)

    def test_known_services_keep_their_discovery_time_and_vanished_ones_disappear(self):
        self.feed.refresh(force=True, now=1000)
        again = self.feed.refresh(force=True, now=5000)
        self.assertEqual(again["added"], [])
        self.assertEqual(self.feed.data["entries"][0]["discovered_at"], 1000)
        self.feed.fetch = lambda: ""                                           # the list no longer mentions it
        self.feed.refresh(force=True, now=9000)
        self.assertEqual(self.feed.data["entries"], [])

    def test_a_service_whose_address_stopped_answering_is_dropped(self):
        ALIVE["https://api.brandnew.example.com/v1"] = None
        self.addCleanup(ALIVE.__setitem__, "https://api.brandnew.example.com/v1", 401)
        self.assertEqual(self.feed.refresh(force=True, now=1000)["added"], [])

    def test_cache_survives_a_restart(self):
        self.feed.refresh(force=True, now=1000)
        reloaded = ServiceFeed(self.path, fetch=lambda: 1 / 0, prober=prober, builtin=setup_page.CATALOG)
        self.assertEqual([e["id"] for e in reloaded.data["entries"]], ["brand-new-ai"])
        junk = Path(self.tmp.name) / "junk.json"
        junk.write_text("not json", encoding="utf-8")
        self.assertEqual(ServiceFeed(junk).data["entries"], [])


class WizardIntegrationTests(unittest.TestCase):
    def setUp(self):
        feed = ServiceFeed(Path(tempfile.mkdtemp()) / "f.json", fetch=lambda: README, prober=prober, builtin=setup_page.CATALOG)
        feed.refresh(force=True, now=1000)
        setup_page.set_extra(feed.catalog_entries(now=1000))
        self.addCleanup(setup_page.set_extra, [])

    def test_discovered_services_appear_in_the_public_catalog_after_the_built_in_ones(self):
        rows = setup_page.public_catalog()
        self.assertEqual(rows[-1]["id"], "brand-new-ai")
        self.assertTrue(rows[-1]["discovered"] and not rows[0]["discovered"])
        self.assertEqual(rows[0]["id"], "ollama")

    def test_a_discovered_service_is_free_only_when_the_user_says_so(self):
        _name, entry = setup_page.resolve_service({"service": ["brand-new-ai"]})
        self.assertFalse(entry["free_all"])
        _name, entry = setup_page.resolve_service({"service": ["brand-new-ai"], "free_confirmed": ["on"]})
        self.assertTrue(entry["free_all"])
        self.assertEqual(entry["base_url"], "https://api.brandnew.example.com/v1")

    def test_built_in_ids_cannot_be_replaced_by_the_internet(self):
        setup_page.set_extra([{"id": "groq", "label": "evil", "base_url": "https://evil.example.com/v1", "policy": "cloud-may-train", "key_url": "https://e.example.com",
                               "model": "", "needs_key": True, "group": "new", "free": "suffix", "free_match": [], "limits": "", "note": "", "discovered": True}])
        self.assertEqual(setup_page.find_service("groq")["base_url"], "https://api.groq.com/openai/v1")
        self.assertNotIn("evil", [r["label"] for r in setup_page.public_catalog()])


if __name__ == "__main__":
    unittest.main()
