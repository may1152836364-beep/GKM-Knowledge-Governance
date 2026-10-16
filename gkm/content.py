"""Opt-in, reversible HTML-to-readable text. Never execute or fetch resources."""

from html.parser import HTMLParser


class ReadableHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in (
            "p",
            "div",
            "section",
            "article",
            "br",
            "tr",
            "h1",
            "h2",
            "h3",
            "h4",
            "ul",
            "ol",
            "pre",
        ):
            self.output.append("\n")
        if tag == "li":
            self.output.append("\n• ")
        if tag in ("td", "th"):
            self.output.append(" | ")
        if tag == "a":
            self.links.append(attributes.get("href", ""))
        if tag == "img":
            self.output.append(
                "[图片："
                + (
                    attributes.get("alt")
                    or attributes.get("title")
                    or "无描述，信息不足需核实原图"
                )
                + "]"
            )
            if attributes.get("src"):
                self.output.append("[图片地址：" + attributes["src"] + "]")
        for name, value in attrs:
            if value and (name.startswith("data-") or name in ("aria-label", "title")):
                self.output.append("[" + name + "：" + value + "]")

    def handle_endtag(self, tag):
        if tag == "a" and self.links:
            link = self.links.pop()
            if link:
                self.output.append("[链接：" + link + "]")
        if tag in ("p", "div", "li", "tr", "section", "article", "pre"):
            self.output.append("\n")

    def handle_data(self, data):
        self.output.append(data)

    def handle_comment(self, data):
        # Preserve hidden text as explicit source data, not executable markup.
        self.output.append("[原文注释：" + data + "]")


def readable_html(raw):
    parser = ReadableHTML()
    parser.feed(raw)
    parser.close()
    return "".join(parser.output).strip()
