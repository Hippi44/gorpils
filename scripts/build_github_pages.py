"""Prepare the unchanged Vercel export for a GitHub Pages project subpath."""

import argparse
import html
from html.parser import HTMLParser
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ORIGINS = (
    "https://gorpils-client-preview-20261105.vercel.app",
    "https://gorpils-landing-20261110.vercel.app",
)
URL_ATTRIBUTES = {"href", "src", "poster", "action", "formaction", "data-src"}
ATTRIBUTES = re.compile(r"(?P<name>[\w:-]+)(?P<equals>\s*=\s*)(?P<quote>[\"'])(?P<value>.*?)(?P=quote)", re.DOTALL)
CSS_URLS = re.compile(r"url\(\s*(?P<quote>[\"']?)(?P<url>[^\s\)\"']+)(?P=quote)\s*\)", re.IGNORECASE)
QUOTED_PATHS = re.compile(r"(?P<quote>[\"'`])(?P<url>/(?!/)[^\s\"'`<>]*)(?P=quote)")
SRCSET_URLS = re.compile(r"(?P<separator>^\s*|,\s*)(?P<url>[^\s,]+)")


class Rewriter:
    def __init__(self, source, base_path, site_url):
        self.source = Path(source)
        self.base_path = base_path.rstrip("/")
        self.site_url = site_url.rstrip("/")

    def url(self, value):
        if not value.startswith("/") or value.startswith("//"):
            return value
        if self.base_path and (value == self.base_path or value.startswith(self.base_path + "/")):
            return value
        return self.base_path + value

    def srcset(self, value):
        return SRCSET_URLS.sub(lambda match: match["separator"] + self.url(match["url"]), value)

    def css(self, value):
        def replace(match):
            return "url(" + match["quote"] + self.url(match["url"]) + match["quote"] + ")"
        return CSS_URLS.sub(replace, value)

    def javascript(self, value):
        def replace(match):
            url = match["url"]
            # A bare slash can be a separator in split('/'), not a site URL.
            if url == "/":
                return match[0]
            parsed = urlsplit(url)
            target = self.source / unquote(parsed.path).lstrip("/")
            if not (target.is_file() or (target / "index.html").is_file()):
                return match[0]
            return match["quote"] + self.url(url) + match["quote"]
        return QUOTED_PATHS.sub(replace, value)

    def tag(self, value):
        def replace(match):
            name = match["name"].lower()
            original = match["value"]
            if name in URL_ATTRIBUTES:
                rewritten = self.url(original)
            elif name == "srcset":
                rewritten = self.srcset(original)
            elif name == "style":
                rewritten = self.css(original)
            else:
                rewritten = original
            return match["name"] + match["equals"] + match["quote"] + rewritten + match["quote"]
        return ATTRIBUTES.sub(replace, value)

    def text(self, value, suffix, relative):
        for origin in SOURCE_ORIGINS:
            if relative.as_posix() == "404.html":
                value = value.replace(origin + "/404/", self.site_url + "/404.html")
            value = value.replace(origin, self.site_url)
        if suffix == ".html":
            parser = HtmlRewriter(self)
            parser.feed(value)
            parser.close()
            return "".join(parser.output)
        if suffix == ".css":
            return self.css(value)
        if suffix in {".js", ".json"}:
            return self.javascript(value)
        if relative.as_posix() == "robots.txt":
            value = re.sub(r"(?m)^Disallow:\s*/\s*$", "Disallow: " + self.base_path + "/", value)
        return value


class HtmlRewriter(HTMLParser):
    def __init__(self, rewriter):
        super().__init__(convert_charrefs=False)
        self.rewriter = rewriter
        self.output = []
        self.script = False

    def handle_starttag(self, tag, attrs):
        self.output.append(self.rewriter.tag(self.get_starttag_text()))
        self.script = tag == "script"

    def handle_startendtag(self, tag, attrs):
        self.output.append(self.rewriter.tag(self.get_starttag_text()))

    def handle_endtag(self, tag):
        self.output.append("</" + tag + ">")
        if tag == "script":
            self.script = False

    def handle_data(self, value):
        self.output.append(self.rewriter.javascript(value) if self.script else value)

    def handle_entityref(self, name):
        self.output.append("&" + name + ";")

    def handle_charref(self, name):
        self.output.append("&#" + name + ";")

    def handle_comment(self, value):
        self.output.append("<!--" + value + "-->")

    def handle_decl(self, value):
        self.output.append("<!" + value + ">")

    def handle_pi(self, value):
        self.output.append("<?" + value + ">")


class LinkCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.references = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        for name in URL_ATTRIBUTES:
            if attrs.get(name):
                self.references.append(attrs[name])
        if attrs.get("srcset"):
            self.references.extend(match["url"] for match in SRCSET_URLS.finditer(attrs["srcset"]))
        if attrs.get("style"):
            self.references.extend(match["url"] for match in CSS_URLS.finditer(attrs["style"]))
        if tag == "meta" and attrs.get("property") in {"og:url", "og:image"}:
            self.references.append(attrs.get("content", ""))

    handle_startendtag = handle_starttag


def validate_site(output, base_path, site_url):
    output = Path(output).resolve()
    origin = urlsplit(site_url).netloc
    errors = []
    references = 0
    for file in sorted(output.rglob("*")):
        if not file.is_file():
            continue
        if file.suffix == ".html":
            collector = LinkCollector()
            collector.feed(file.read_text(encoding="utf-8"))
            urls = collector.references
        elif file.suffix == ".css":
            urls = [match["url"] for match in CSS_URLS.finditer(file.read_text(encoding="utf-8"))]
        else:
            continue
        for value in urls:
            parsed = urlsplit(html.unescape(value))
            if (parsed.scheme and parsed.scheme not in {"http", "https"}) or (parsed.netloc and parsed.netloc != origin):
                continue
            path = unquote(parsed.path)
            if path.startswith("/"):
                if base_path and not (path == base_path or path.startswith(base_path + "/")):
                    errors.append(str(file.relative_to(output)) + ": URL outside project subpath: " + value)
                    continue
                target = output / path[len(base_path):].lstrip("/")
            elif path:
                target = file.parent / path
            else:
                target = file
            target = target.resolve()
            if not target.is_relative_to(output):
                errors.append(str(file.relative_to(output)) + ": URL escapes output: " + value)
            elif not (target.is_file() or (target / "index.html").is_file()):
                errors.append(str(file.relative_to(output)) + ": missing local target: " + value)
            references += 1
    if errors:
        raise ValueError("Invalid GitHub Pages references:\n" + "\n".join(sorted(set(errors))))
    return references


def build_site(source, output, base_path="/gorpils", site_url="https://hippi44.github.io/gorpils"):
    source, output = Path(source).resolve(), Path(output).resolve()
    base_path = base_path.rstrip("/")
    if base_path and (not base_path.startswith("/") or base_path.startswith("//") or ".." in base_path.split("/")):
        raise ValueError("Expected a safe root-relative base path")
    parsed = urlsplit(site_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path.rstrip("/") != base_path:
        raise ValueError("Site URL must use HTTP(S) and match the configured base path")
    if output == source or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("Source and generated output must be separate directories")
    rewriter = Rewriter(source, base_path, site_url)
    text_suffixes = {".html", ".css", ".js", ".json", ".xml", ".txt", ".svg", ".map"}
    files = 0
    for file in sorted(source.rglob("*")):
        if file.is_symlink():
            raise ValueError("Symbolic links are not supported in a Pages artifact")
        if not file.is_file():
            continue
        relative = file.relative_to(source)
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if file.suffix in text_suffixes:
            transformed = rewriter.text(file.read_text(encoding="utf-8"), file.suffix, relative)
            destination.write_bytes(transformed.encode("utf-8"))
        else:
            destination.write_bytes(file.read_bytes())
        files += 1
    (output / ".nojekyll").write_bytes(b"")
    references = validate_site(output, base_path, site_url)
    return files, references


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=PROJECT_ROOT / "site")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "_pages")
    parser.add_argument("--base-path", default="/gorpils")
    parser.add_argument("--site-url", default="https://hippi44.github.io/gorpils")
    options = parser.parse_args()
    files, references = build_site(options.source, options.output, options.base_path, options.site_url)
    print("Built " + str(files) + " site files and .nojekyll; validated " + str(references) + " local references.")
    print("GitHub Pages URL: " + options.site_url.rstrip("/") + "/")


if __name__ == "__main__":
    main()
