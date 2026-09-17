"""Convert Brightspace rich-text HTML into readable Markdown.

D2L module descriptions use a small, predictable tag set (headings,
paragraphs, bold/italic, lists, links and images).  This handles exactly that
and ignores styling wrappers such as ``span`` and ``div`` attributes, so the
result is clean Markdown with no dependency on a converter library.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_BLOCKS = {"p", "div", "blockquote"}


class _Markdown(HTMLParser):
    def __init__(self, base_url: str = ""):
        super().__init__(convert_charrefs=True)
        self.base = base_url.rstrip("/")
        self.parts: list[str] = []
        self._hrefs: list[str] = []
        self._lists: list[str] = []

    def _nl(self, count: int = 1) -> None:
        self.parts.append("\n" * count)

    def _url(self, url: str) -> str:
        if not url or url.startswith(("http://", "https://", "mailto:", "#")):
            return url
        if url.startswith("/") and self.base:
            return self.base + url
        return url

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in ("strong", "b"):
            self.parts.append("**")
        elif tag in ("em", "i"):
            self.parts.append("*")
        elif tag in _HEADINGS:
            self._nl(2)
            self.parts.append("#" * int(tag[1]) + " ")
        elif tag == "br":
            self._nl(1)
        elif tag in _BLOCKS:
            self._nl(2)
            if tag == "blockquote":
                self.parts.append("> ")
        elif tag in ("ul", "ol"):
            self._nl(2)
            self._lists.append(tag)
        elif tag == "li":
            self._nl(1)
            depth = max(0, len(self._lists) - 1)
            ordered = self._lists and self._lists[-1] == "ol"
            self.parts.append("  " * depth + ("1. " if ordered else "- "))
        elif tag == "a":
            self._hrefs.append(self._url(attributes.get("href", "")))
            self.parts.append("[")
        elif tag == "img":
            src = self._url(attributes.get("src", ""))
            alt = attributes.get("alt", "")
            if src:
                self.parts.append(f"![{alt}]({src})")

    def handle_endtag(self, tag):
        if tag in ("strong", "b"):
            self.parts.append("**")
        elif tag in ("em", "i"):
            self.parts.append("*")
        elif tag in _HEADINGS or tag in _BLOCKS:
            self._nl(2)
        elif tag in ("ul", "ol"):
            if self._lists:
                self._lists.pop()
            self._nl(2)
        elif tag == "li":
            self._nl(1)
        elif tag == "a":
            href = self._hrefs.pop() if self._hrefs else ""
            self.parts.append(f"]({href})")

    def handle_data(self, data):
        self.parts.append(data)


def to_markdown(html: str | None, base_url: str = "") -> str:
    """Convert a description's HTML into Markdown."""
    parser = _Markdown(base_url)
    parser.feed(html or "")
    parser.close()
    text = "".join(parser.parts).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.replace("****", "")
    return text.strip() + "\n"
