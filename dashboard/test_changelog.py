#!/usr/bin/env python3
"""Tests for the What's new tab: the changelog parser, the endpoint, and the
real CHANGELOG.md.

Usage:  python3 dashboard/test_changelog.py
"""
import json
import sys
import threading
import unittest
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import changelog  # noqa: E402

SAMPLE = """# Changelog

Intro with a [repo link](docs/VERSIONING.md) and a [web link](https://example.com).

## [Unreleased]

<!-- source: commits after v0.2.0 -->

### Added
- Something new <script>alert(1)</script>

## [0.2.0] - 2026-09-11

<!-- source: tag v0.2.0 -->

### Fixed
- A fix, see [the doc](docs/x.md) and [a section](#fixed).

```
## not a heading inside a fence
```

## v0.1.0 - 2026-08-23

### Added
- First release, with a bullet
  that wraps onto a second line.

## 2026-08-14

### Breaking
- Old dated entry.
"""

class ParseTest(unittest.TestCase):
    def setUp(self):
        self.p = changelog.parse(SAMPLE)
        self.s = self.p["sections"]

    def test_sections_in_order(self):
        self.assertEqual([x["title"] for x in self.s],
                         ["[Unreleased]", "[0.2.0] - 2026-09-11",
                          "v0.1.0 - 2026-08-23", "2026-08-14"])

    def test_versions_and_dates(self):
        self.assertTrue(self.s[0]["unreleased"])
        self.assertIsNone(self.s[0]["version"])
        self.assertEqual((self.s[1]["version"], self.s[1]["date"]), ("0.2.0", "2026-09-11"))
        self.assertEqual((self.s[2]["version"], self.s[2]["date"]), ("0.1.0", "2026-08-23"))
        self.assertEqual((self.s[3]["version"], self.s[3]["date"]), (None, "2026-08-14"))
        self.assertEqual(changelog.latest_release(self.p)["version"], "0.2.0")

    def test_comments_dropped(self):
        for x in self.s:
            self.assertNotIn("<!--", x["body"])
            self.assertNotIn("source:", x["body"])

    def test_repo_links_flattened_web_links_kept(self):
        self.assertIn("see the doc and [a section](#fixed)", self.s[1]["body"])
        self.assertIn("[web link](https://example.com)", self.p["intro"])
        self.assertIn("a repo link and", self.p["intro"])
        self.assertNotIn("# Changelog", self.p["intro"])

    def test_fenced_heading_is_not_a_section(self):
        self.assertIn("## not a heading inside a fence", self.s[1]["body"])

    def test_raw_html_passes_through_as_text(self):
        # Escaping is the browser's job (U.mdToHtml escapes before formatting);
        # the parser must not silently drop or alter it either.
        self.assertIn("<script>alert(1)</script>", self.s[0]["body"])

    def test_wrapped_bullet_joined(self):
        self.assertIn("- First release, with a bullet that wraps onto a second line.",
                      self.s[2]["body"])

    def test_missing_file(self):
        d = changelog.load(HERE / "no-such-changelog.md")
        self.assertTrue(d["missing"])
        self.assertEqual(d["sections"], [])

class RealChangelogTest(unittest.TestCase):
    """The shipped CHANGELOG.md keeps the shape the tab and the release
    notes depend on."""

    def setUp(self):
        self.d = changelog.load(HERE.parent / "CHANGELOG.md")

    def test_unreleased_on_top_then_releases_newest_first(self):
        s = self.d["sections"]
        self.assertTrue(s[0]["unreleased"])
        versions = [tuple(map(int, x["version"].split("."))) for x in s if x["version"]]
        self.assertGreaterEqual(len(versions), 8)   # 0.1.0, 0.2.0, 0.2.1, 0.3.0 .. 0.7.0
        self.assertEqual(versions, sorted(versions, reverse=True))

    def test_every_backfilled_release_cites_a_source(self):
        raw = (HERE.parent / "CHANGELOG.md").read_text(encoding="utf-8")
        for v in ("0.2.0", "0.2.1", "0.3.0", "0.4.0", "0.5.0", "0.6.0", "0.7.0"):
            head = f"## [{v}] - "
            self.assertIn(head, raw)
            after = raw.split(head, 1)[1][:400]
            self.assertIn("<!-- source:", after, v)

    def test_no_em_dash(self):
        raw = (HERE.parent / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertNotIn(chr(0x2014), raw)   # em-dash

class EndpointTest(unittest.TestCase):
    """Start the real handler on a free port and read /api/changelog."""

    @classmethod
    def setUpClass(cls):
        import server
        from http.server import ThreadingHTTPServer
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.DashboardHandler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_api_changelog(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/changelog", timeout=10) as r:
            self.assertEqual(r.status, 200)
            d = json.loads(r.read().decode("utf-8"))
        self.assertTrue(d["sections"][0]["unreleased"])
        self.assertRegex(d["latest"], r"^\d+\.\d+\.\d+$")
        self.assertNotIn("<!--", json.dumps(d))

    def test_tab_assets_served(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/tab-whatsnew.js", timeout=10) as r:
            js = r.read().decode("utf-8")
        self.assertIn("window.Tabs.whatsnew", js)
        self.assertIn("U.mdToHtml", js)
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=10) as r:
            html = r.read().decode("utf-8")
        self.assertIn('data-tab="whatsnew"', html)
        self.assertIn('id="tab-whatsnew"', html)
        self.assertIn('src="tab-whatsnew.js"', html)

if __name__ == "__main__":
    unittest.main(verbosity=2)
