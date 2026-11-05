#!/usr/bin/env python3
"""Fetch upstream RSS, clean junk, write static XML for GitHub Pages."""
from __future__ import annotations
import html as html_lib
import json, re, sys, urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "feeds"
UA = "EpochFeeds/1.0 (+https://github.com/rediang/epoch-feeds)"
TS_RE = re.compile(
    r"^(?:\s|<(?:div|p|span)[^>]*>)*"
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
    r"\s*(?:</(?:div|p|span)>)?",
    re.I,
)
PROMO_RE = re.compile(
    r"<(?:p|div)[^>]*>[^<]*(?:获取更多RSS|feedx\.net|feedx\.site)[\s\S]*?</(?:p|div)>",
    re.I,
)
# Zaobao / similar related-reading cards (thumbnails look like screenshots)
RELATED_RE = re.compile(
    r"<section\b[^>]*>\s*<h[1-6][^>]*>\s*(?:延伸阅读|相关阅读|推荐阅读|你可能喜欢|热门文章)[\s\S]*?</section>",
    re.I,
)
RELATED_DIV_RE = re.compile(
    r"<(?:div|aside)\b[^>]*>\s*<(?:h[1-6]|strong|b)[^>]*>\s*(?:延伸阅读|相关阅读|推荐阅读)[\s\S]*?</(?:div|aside)>",
    re.I,
)
AD_RE = re.compile(r"<astro-island\b[^>]*>[\s\S]*?</astro-island>", re.I)
AD_EMPTY_RE = re.compile(r"<astro-island\b[^>]*/?>", re.I)
LAZY_ATTRS = ("data-src", "data-original", "data-lazy-src", "data-actualsrc")


def fetch(url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        },
    )
    with urllib.request.urlopen(req, timeout=45) as r:
        return r.read()


def clean_html(html: str) -> str:
    for _ in range(5):
        n = TS_RE.sub("", html, count=1)
        if n == html:
            break
        html = n
    html = PROMO_RE.sub("", html)
    html = RELATED_RE.sub("", html)
    html = RELATED_DIV_RE.sub("", html)
    html = AD_RE.sub("", html)
    html = AD_EMPTY_RE.sub("", html)
    # Drop leftover ISO timestamp-only divs anywhere
    html = re.sub(
        r"<(?:div|p|span)[^>]*>\s*\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[^<]*</(?:div|p|span)>",
        "",
        html,
        flags=re.I,
    )
    # Unwrap <article id="article-body"> … keep inner
    html = re.sub(
        r"<article\b[^>]*id=[\"']article-body[\"'][^>]*>([\s\S]*?)</article>",
        r"\1",
        html,
        flags=re.I,
    )

    def fix_img(m: re.Match) -> str:
        tag = m.group(0)
        real = None
        for a in LAZY_ATTRS:
            mm = re.search(rf'{a}=["\'](https?://[^"\']+)["\']', tag, re.I)
            if mm:
                real = mm.group(1)
                break
        if not real:
            return tag
        tag = re.sub(r'(^|\s)src=["\'][^"\']*["\']', f' src="{real}"', tag, count=1, flags=re.I)
        if "src=" not in tag.lower():
            tag = tag.replace("<img", f'<img src="{real}"', 1)
        for a in LAZY_ATTRS:
            tag = re.sub(rf'\s*{a}=["\'][^"\']*["\']', "", tag, flags=re.I)
        return tag

    html = re.sub(r"<img\b[^>]*>", fix_img, html, flags=re.I)
    return html.strip()


def clean_feed_xml(raw: str) -> str:
    raw = re.sub(r"获取更多全文RSS[：:].*?(?=</)", "", raw)
    raw = re.sub(r"https?://feedx\.(?:net|site)/?", "", raw)

    def clean_tag(m: re.Match) -> str:
        open_t, body, close_t = m.group(1), m.group(2), m.group(3)
        if "<![CDATA[" in body:
            inner = body[9:-3] if body.endswith("]]>") else body.replace("<![CDATA[", "").replace("]]>", "")
            return f"{open_t}<![CDATA[{clean_html(inner)}]]>{close_t}"
        # Many feeds escape HTML entities inside <description>
        decoded = html_lib.unescape(body)
        if "<" in decoded and ("&lt;" in body or "&#" in body):
            cleaned = clean_html(decoded)
            # Keep as CDATA so tags survive in RSS
            return f"{open_t}<![CDATA[{cleaned}]]>{close_t}"
        return f"{open_t}{clean_html(body)}{close_t}"

    raw = re.sub(
        r"(<(?:description|content:encoded)[^>]*>)([\s\S]*?)(</(?:description|content:encoded)>)",
        clean_tag,
        raw,
        flags=re.I,
    )
    return raw


def index_html(feeds: list[dict], ok: list[str]) -> str:
    rows = []
    for f in feeds:
        status = "ok" if f["id"] in ok else "fail"
        href = f"feeds/{f['id']}.xml"
        rows.append(
            f'<li><a href="{href}">{escape(f["title"])}</a> '
            f'<code>{escape(f["id"])}.xml</code> [{status}]</li>'
        )
    return (
        "<!doctype html><html lang=zh-CN><meta charset=utf-8>"
        "<title>纪元 RSS</title><body>"
        "<h1>纪元自建 RSS（GitHub Actions）</h1>"
        "<p>每小时更新。把下面链接填进 WordPress「纪元RSS」订阅源即可。</p>"
        f"<ul>{''.join(rows)}</ul>"
        "</body></html>"
    )


def main() -> int:
    cfg = json.loads((ROOT / "feeds.json").read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    ok = []
    for f in cfg:
        path = OUT / f"{f['id']}.xml"
        try:
            data = fetch(f["url"]).decode("utf-8", "replace")
            data = clean_feed_xml(data)
            if "<rss" not in data.lower() and "<feed" not in data.lower():
                raise RuntimeError("not an RSS/Atom document")
            path.write_text(data, encoding="utf-8")
            ok.append(f["id"])
            print("OK", f["id"], len(data))
        except Exception as e:
            print("FAIL", f["id"], e, file=sys.stderr)
            if not path.exists():
                path.write_text(
                    f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
                    f'<title>{escape(f["title"])}</title><description>fetch failed</description>'
                    f"</channel></rss>",
                    encoding="utf-8",
                )
    (ROOT / "index.html").write_text(index_html(cfg, ok), encoding="utf-8")
    print(f"done {len(ok)}/{len(cfg)}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
