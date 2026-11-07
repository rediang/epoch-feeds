#!/usr/bin/env python3
"""Fetch upstream RSS, clean junk, write static XML for GitHub Pages.

Also mirrors YouTube channel Atom feeds (browser UA + retries) into
feeds/yt-<channel_id>.xml, dropping YouTube Shorts entries.
"""
from __future__ import annotations
import html as html_lib
import json, re, sys, time, urllib.error, urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "feeds"
UA = "EpochFeeds/1.0 (+https://github.com/rediang/epoch-feeds)"
CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
YT_RETRIES = 15
YT_RETRY_DELAY = 1.5
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


def fetch(url: str, *, ua: str = UA, timeout: int = 45) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": ua,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_youtube(channel_id: str) -> bytes:
    """YouTube channel Atom feeds often 404; retry with a browser UA."""
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    last_err: Exception | None = None
    for attempt in range(1, YT_RETRIES + 1):
        try:
            data = fetch(url, ua=CHROME_UA)
            text = data.decode("utf-8", "replace")
            if "<feed" not in text.lower() or "<entry" not in text.lower():
                raise RuntimeError("not a YouTube Atom feed with entries")
            return data
        except Exception as e:
            last_err = e
            print(f"  yt retry {attempt}/{YT_RETRIES}: {e}", file=sys.stderr)
            if attempt < YT_RETRIES:
                time.sleep(YT_RETRY_DELAY)
    raise RuntimeError(f"YouTube fetch failed after {YT_RETRIES} tries: {last_err}")


ENTRY_RE = re.compile(rb"<entry\b[\s\S]*?</entry>\s*", re.I)
VID_RE = re.compile(rb"<yt:videoId>([\w-]{11})</yt:videoId>")


def shorts_ids(channel_id: str) -> set[bytes]:
    """IDs in the channel's Shorts playlist (UUSH…). Empty set if unavailable."""
    if not channel_id.startswith("UC"):
        return set()
    url = f"https://www.youtube.com/feeds/videos.xml?playlist_id=UUSH{channel_id[2:]}"
    for _ in range(3):
        try:
            return set(VID_RE.findall(fetch(url, ua=CHROME_UA)))
        except urllib.error.HTTPError as e:
            if e.code == 404:  # channel has no Shorts
                return set()
        except Exception:
            pass
        time.sleep(YT_RETRY_DELAY)
    return set()


def drop_shorts(data: bytes, channel_id: str) -> tuple[bytes, int]:
    """Remove YouTube Shorts entries: /shorts/ links, or IDs in the Shorts playlist."""
    sids = shorts_ids(channel_id)
    dropped = 0

    def keep(m: re.Match) -> bytes:
        nonlocal dropped
        e = m.group(0)
        v = VID_RE.search(e)
        if b"/shorts/" in e or (v and v.group(1) in sids):
            dropped += 1
            return b""
        return e

    return ENTRY_RE.sub(keep, data), dropped


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


def index_html(feeds: list[dict], ok: list[str], yt: list[dict], yt_ok: list[str]) -> str:
    rows = []
    for f in feeds:
        status = "ok" if f["id"] in ok else "fail"
        href = f"feeds/{f['id']}.xml"
        rows.append(
            f'<li><a href="{href}">{escape(f["title"])}</a> '
            f'<code>{escape(f["id"])}.xml</code> [{status}]</li>'
        )
    for y in yt:
        cid = y["channel_id"]
        status = "ok" if cid in yt_ok else "fail"
        href = f"feeds/yt-{cid}.xml"
        rows.append(
            f'<li><a href="{href}">{escape(y["title"])} (YouTube)</a> '
            f'<code>yt-{escape(cid)}.xml</code> [{status}]</li>'
        )
    return (
        "<!doctype html><html lang=zh-CN><meta charset=utf-8>"
        "<title>纪元 RSS</title><body>"
        "<h1>纪元自建 RSS（GitHub Actions）</h1>"
        "<p>每小时更新。把下面链接填进 WordPress「纪元RSS」订阅源即可。</p>"
        f"<ul>{''.join(rows)}</ul>"
        "</body></html>"
    )


def load_youtube() -> list[dict]:
    path = ROOT / "youtube.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def build_rss(cfg: list[dict]) -> list[str]:
    ok: list[str] = []
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
    return ok


def build_youtube(yt: list[dict]) -> list[str]:
    """Mirror YouTube Atom XML unchanged. On total failure keep previous file."""
    ok: list[str] = []
    for y in yt:
        cid = y["channel_id"]
        path = OUT / f"yt-{cid}.xml"
        try:
            data = fetch_youtube(cid)
            # Drop Shorts; otherwise keep the XML unchanged so yt:videoId / media:* stay intact
            data, shorts = drop_shorts(data, cid)
            path.write_bytes(data)
            ok.append(cid)
            entries = data.count(b"<entry")
            print("OK yt", cid, f"bytes={len(data)} entries={entries} shorts_dropped={shorts}")
        except Exception as e:
            print("FAIL yt", cid, e, file=sys.stderr)
            if path.exists():
                print(f"  kept previous {path.name}", file=sys.stderr)
            else:
                print(f"  no previous file for {path.name}", file=sys.stderr)
    return ok


def main() -> int:
    cfg = json.loads((ROOT / "feeds.json").read_text(encoding="utf-8"))
    yt = load_youtube()
    OUT.mkdir(parents=True, exist_ok=True)
    ok = build_rss(cfg)
    yt_ok = build_youtube(yt)
    (ROOT / "index.html").write_text(index_html(cfg, ok, yt, yt_ok), encoding="utf-8")
    print(f"done rss {len(ok)}/{len(cfg)} yt {len(yt_ok)}/{len(yt)}")
    # Succeed if any feed built, or YouTube kept a previous file for every channel
    if ok or yt_ok:
        return 0
    if yt and all((OUT / f"yt-{y['channel_id']}.xml").exists() for y in yt):
        return 0
    return 1 if cfg or yt else 0


if __name__ == "__main__":
    raise SystemExit(main())
