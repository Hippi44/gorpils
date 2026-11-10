import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.build_github_pages import PROJECT_ROOT, Rewriter, build_site, validate_site


class PagesTests(unittest.TestCase):
    def setUp(self):
        self.rewriter = Rewriter(PROJECT_ROOT / "site", "/gorpils", "https://hippi44.github.io/gorpils")

    def test_root_relative_urls_and_external_urls(self):
        self.assertEqual(self.rewriter.url("/"), "/gorpils/")
        self.assertEqual(self.rewriter.url("/instruction/#dosage"), "/gorpils/instruction/#dosage")
        self.assertEqual(self.rewriter.url("//cdn.example.com/a.png"), "//cdn.example.com/a.png")
        self.assertEqual(self.rewriter.url("https://example.com/a"), "https://example.com/a")
        self.assertEqual(self.rewriter.url("#buy"), "#buy")
        self.assertEqual(self.rewriter.url("/gorpils/images/share.png"), "/gorpils/images/share.png")

    def test_image_srcset(self):
        self.assertEqual(self.rewriter.srcset("/images/a.avif 400w, /images/b.avif 800w"),
                         "/gorpils/images/a.avif 400w, /gorpils/images/b.avif 800w")

    def test_css_fonts_and_data_urls(self):
        css = 'a{src:url(/_astro/font.woff2)}b{background:url("/images/share.png")}c{background:url(data:image/png;base64,AA)}'
        self.assertEqual(self.rewriter.css(css),
                         'a{src:url(/gorpils/_astro/font.woff2)}b{background:url("/gorpils/images/share.png")}c{background:url(data:image/png;base64,AA)}')

    def test_self_closing_tags_entities_and_scripts_are_preserved(self):
        source = '<!DOCTYPE html><html><head><meta charset="UTF-8"/><link href="/favicon.svg"/></head><body><a href="/instruction/?a=1&amp;b=2">A &amp; B</a><script>const parts = value.split("/"); const image = "/images/share.png";</script></body></html>'
        rewritten = self.rewriter.text(source, ".html", Path("index.html"))
        self.assertIn('<meta charset="UTF-8"/>', rewritten)
        self.assertIn('<link href="/gorpils/favicon.svg"/>', rewritten)
        self.assertIn('href="/gorpils/instruction/?a=1&amp;b=2"', rewritten)
        self.assertIn('A &amp; B', rewritten)
        self.assertIn('value.split("/")', rewritten)
        self.assertIn('"/gorpils/images/share.png"', rewritten)

    def test_old_origin_and_metadata_are_replaced(self):
        original = '<link rel="canonical" href="https://gorpils-client-preview-20261105.vercel.app/instruction/">'
        rewritten = self.rewriter.text(original, ".html", Path("instruction/index.html"))
        self.assertIn('href="https://hippi44.github.io/gorpils/instruction/"', rewritten)
        sitemap = '<urlset><url><loc>https://gorpils-client-preview-20261105.vercel.app/instruction/</loc></url></urlset>'
        self.assertIn("https://hippi44.github.io/gorpils/instruction/",
                      self.rewriter.text(sitemap, ".xml", Path("sitemap.xml")))

    def test_full_site_build_and_unchanged_source(self):
        source = PROJECT_ROOT / "site"
        before = {file.relative_to(source): hashlib.sha256(file.read_bytes()).hexdigest()
                  for file in source.rglob("*") if file.is_file()}
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "pages"
            files, references = build_site(source, output)
            self.assertEqual(files, 71)
            self.assertGreater(references, 100)
            self.assertTrue((output / ".nojekyll").exists())
            for relative in before:
                if relative.suffix not in {".html", ".css", ".js", ".json", ".xml", ".txt", ".svg", ".map"}:
                    self.assertEqual((output / relative).read_bytes(), (source / relative).read_bytes())
            for file in output.rglob("*.html"):
                text = file.read_text(encoding="utf-8")
                self.assertNotIn("https://gorpils-client-preview-20261105.vercel.app", text)
            # The exported noindex preview intentionally has an empty sitemap.
            original_sitemap = (source / "sitemap.xml").read_text(encoding="utf-8")
            expected_sitemap = original_sitemap.replace("https://gorpils-client-preview-20261105.vercel.app",
                                                       "https://hippi44.github.io/gorpils")
            self.assertEqual((output / "sitemap.xml").read_text(encoding="utf-8"), expected_sitemap)
        after = {file.relative_to(source): hashlib.sha256(file.read_bytes()).hexdigest()
                 for file in source.rglob("*") if file.is_file()}
        self.assertEqual(before, after)

    def test_original_vercel_checksums_still_match(self):
        manifest = json.loads((PROJECT_ROOT / "vercel-export/manifest.json").read_text(encoding="utf-8"))
        prefix = "src/.vercel/output/static/"
        for entry in manifest["files"]:
            file = PROJECT_ROOT / "site" / entry["path"][len(prefix):] if entry["path"].startswith(prefix) else PROJECT_ROOT / "vercel-export/config.json"
            self.assertEqual(hashlib.sha256(file.read_bytes()).hexdigest(), entry["sha256"], entry["path"])

    def test_missing_or_unprefixed_local_assets_fail_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            (output / "index.html").write_text('<img src="/images/missing.png">', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "outside project subpath"):
                validate_site(output, "/gorpils", "https://hippi44.github.io/gorpils")
            (output / "index.html").write_text('<img src="/gorpils/images/missing.png">', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing local target"):
                validate_site(output, "/gorpils", "https://hippi44.github.io/gorpils")

    def test_output_cannot_overwrite_source(self):
        with self.assertRaisesRegex(ValueError, "separate directories"):
            build_site(PROJECT_ROOT / "site", PROJECT_ROOT / "site")


if __name__ == "__main__":
    unittest.main()
