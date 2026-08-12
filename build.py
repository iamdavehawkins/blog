import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import sys
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser


SITE_URL = "https://blog.iamdavehawkins.com"
FEED_URL = "https://buttondown.com/iamdavehawkins/rss"
AUTHOR = "Dave Hawkins"
SITE_TITLE = "Letters from Dave Hawkins"
SITE_TAGLINE = "some letters and stories i've written"
SITE_DESC = (
    "The full archive of Dave Hawkins' newsletter: letters on songwriting, "
    "home recording, and life between art and work. From Denver, Colorado."
)
DEFAULT_OG = SITE_URL + "/og-default.png"
BUTTONDOWN_FORM = "https://buttondown.com/api/emails/embed-subscribe/iamdavehawkins"
UMAMI_ID = "99efd3af-3c2e-490e-adc0-aa7a12c5f68a"

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, ".cache")
WORDS_PER_MINUTE = 220


VOID = {"br", "hr", "img", "input", "meta", "link", "source", "col", "wbr", "area"}
DROP_WHOLE = {"script", "style", "head", "title"}
UNWRAP = {"html", "body", "font", "center"}
DROP_ATTRS = {"draggable", "class", "style", "id", "width", "height",
              "data-start", "data-end", "data-pm-slice", "srcset", "sizes"}
DEMOTE = {"h1": "h3", "h2": "h3", "h3": "h4", "h4": "h5", "h5": "h6", "h6": "h6"}


def strip_utm(url):
    if "?" not in url:
        return url
    base, _, query = url.partition("?")
    frag = ""
    if "#" in query:
        query, _, frag = query.partition("#")
    kept = [p for p in query.split("&")
            if p and not p.split("=")[0].lower().startswith("utm_")]
    out = base + ("?" + "&".join(kept) if kept else "")
    return out + ("#" + frag if frag else "")


class Cleaner(HTMLParser):

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out = []
        self.skip_depth = 0
        self.first_image = None
        self.in_figcaption = False

    def handle_starttag(self, tag, attrs):
        if self.skip_depth:
            return
        if tag in DROP_WHOLE:
            self.skip_depth = 1
            return
        if tag in UNWRAP:
            return

        attrs = [(k.lower(), v) for k, v in attrs if k.lower() not in DROP_ATTRS]
        d = dict(attrs)

        if tag == "a":
            href = strip_utm(d.get("href", "") or "")
            if not href:
                return
            d = {"href": href}
            if href.startswith(("http://", "https://")) and "iamdavehawkins.com" not in href:
                d["target"] = "_blank"
                d["rel"] = "noopener"
        elif tag == "img":
            src = d.get("src", "")
            if not src:
                return
            if self.first_image is None:
                self.first_image = src
            d = {"src": src, "alt": d.get("alt", "") or "", "loading": "lazy",
                 "decoding": "async"}
        elif tag == "iframe":
            src = d.get("src", "")
            if not src:
                return
            self.out.append('<div class="embed">')
            d = {"src": src, "loading": "lazy", "allowfullscreen": None,
                 "title": d.get("title", "Embedded video") or "Embedded video"}
        elif tag == "figcaption":
            self.in_figcaption = True

        tag = DEMOTE.get(tag, tag)
        bits = []
        for k, v in d.items():
            bits.append(k if v is None else '%s="%s"' % (k, html.escape(v, quote=True)))
        self.out.append("<%s%s>" % (tag, (" " + " ".join(bits)) if bits else ""))

    def handle_endtag(self, tag):
        if self.skip_depth:
            if tag in DROP_WHOLE:
                self.skip_depth = 0
            return
        if tag in UNWRAP or tag in VOID:
            return
        if tag == "figcaption":
            self.in_figcaption = False
        self.out.append("</%s>" % DEMOTE.get(tag, tag))
        if tag == "iframe":
            self.out.append("</div>")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        if not self.skip_depth:
            self.out.append(html.escape(data, quote=False))

    def handle_entityref(self, name):
        if not self.skip_depth:
            self.out.append("&%s;" % name)

    def handle_charref(self, name):
        if not self.skip_depth:
            self.out.append("&#%s;" % name)

    def result(self):
        body = "".join(self.out)
        body = re.sub(r"(?:<p>\s*</p>\s*)+", "", body)
        body = re.sub(r"(?:<br\s*/?>\s*){3,}", "<br><br>", body)
        return body.strip()


class TextExtractor(HTMLParser):

    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6",
             "figure", "figcaption", "blockquote", "tr", "hr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in DROP_WHOLE:
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in DROP_WHOLE and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

    def text(self):
        return re.sub(r"\s+", " ", "".join(self.parts)).strip()


def to_text(markup):
    p = TextExtractor()
    p.feed(markup)
    p.close()
    return p.text()


def clean(markup):
    c = Cleaner()
    c.feed(markup)
    c.close()
    return c.result(), c.first_image


def md_inline(s):
    s = html.escape(s, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)",
               r'<img src="\2" alt="\1" loading="lazy" decoding="async">', s)
    s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![*\w])\*([^*\n]+)\*(?!\w)", r"<em>\1</em>", s)
    return s


def md_to_html(src):
    out, lines, i = [], src.replace("\r\n", "\n").split("\n"), 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
        elif line.startswith("```"):
            i += 1
            buf = []
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(html.escape(lines[i], quote=False))
                i += 1
            i += 1
            out.append("<pre><code>%s</code></pre>" % "\n".join(buf))
        elif re.match(r"^(-{3,}|\*{3,})$", line.strip()):
            out.append("<hr>")
            i += 1
        elif re.match(r"^#{1,6}\s", line):
            n = len(line) - len(line.lstrip("#"))
            out.append("<h%d>%s</h%d>" % (n, md_inline(line[n:].strip()), n))
            i += 1
        elif re.match(r"^\s*[-*+]\s+|^\s*\d+[.)]\s+", line):
            ordered = bool(re.match(r"^\s*\d+[.)]\s+", line))
            items = []
            while i < len(lines) and re.match(r"^\s*[-*+]\s+|^\s*\d+[.)]\s+", lines[i]):
                items.append(md_inline(re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", lines[i])))
                i += 1
            tag = "ol" if ordered else "ul"
            out.append("<%s>%s</%s>" % (tag, "".join("<li>%s</li>" % t for t in items), tag))
        elif line.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].startswith(">"):
                buf.append(lines[i].lstrip("> ").rstrip())
                i += 1
            out.append("<blockquote><p>%s</p></blockquote>" % md_inline(" ".join(buf)))
        else:
            buf = []
            while i < len(lines) and lines[i].strip() and not re.match(
                    r"^(#{1,6}\s|```|>|\s*[-*+]\s+|\s*\d+[.)]\s+)", lines[i]):
                buf.append(lines[i].rstrip())
                i += 1
            out.append("<p>%s</p>" % md_inline("<br>".join(buf)).replace("&lt;br&gt;", "<br>"))
    return "\n".join(out)


def load_markdown_posts():
    folder = os.path.join(HERE, "posts")
    found = []
    if not os.path.isdir(folder):
        return found
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".md"):
            continue
        raw = open(os.path.join(folder, name), encoding="utf-8").read()
        meta, body = {}, raw
        if raw.startswith("---"):
            _, _, rest = raw.partition("---\n")
            block, _, body = rest.partition("---\n")
            for line in block.splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip().lower()] = v.strip().strip('"\'')
        title = meta.get("title") or os.path.splitext(name)[0].replace("-", " ")
        date_raw = meta.get("date", "")
        try:
            date = dt.datetime.strptime(date_raw[:10], "%Y-%m-%d").replace(
                tzinfo=dt.timezone.utc)
        except ValueError:
            sys.stderr.write("  ! posts/%s: bad or missing date, skipped\n" % name)
            continue
        markup, first_img = clean(md_to_html(body))
        found.append(make_post(
            title=title,
            slug=meta.get("slug") or slugify(os.path.splitext(name)[0]),
            date=date,
            markup=markup,
            image=meta.get("image") or first_img,
            source="post",
            origin="",
        ))
    return found


def slugify(s):
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s or "untitled"


def make_post(title, slug, date, markup, image, source, origin):
    text = to_text(markup)
    words = len(text.split())
    summary = text[:300].rsplit(" ", 1)[0] if len(text) > 300 else text
    return {
        "title": title,
        "slug": slug,
        "date": date,
        "html": markup,
        "text": text,
        "words": words,
        "minutes": max(1, round(words / WORDS_PER_MINUTE)),
        "summary": summary + ("…" if len(text) > 300 else ""),
        "image": image or "",
        "source": source,
        "origin": origin,
    }


def fetch_feed(offline):
    path = os.path.join(CACHE, "feed.xml")
    if offline:
        if not os.path.exists(path):
            sys.exit("No cached feed at %s. Run once without --offline." % path)
        print("Reading cached feed")
        return open(path, "rb").read()
    print("Fetching %s" % FEED_URL)
    req = urllib.request.Request(
        FEED_URL, headers={"User-Agent": "iamdavehawkins-blog-build/1.0"})
    data = urllib.request.urlopen(req, timeout=45).read()
    os.makedirs(CACHE, exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return data


def parse_feed(raw):
    root = ET.fromstring(raw)
    posts = []
    for item in root.iter("item"):
        def get(tag):
            el = item.find(tag)
            return (el.text or "") if el is not None else ""

        link = get("link").strip()
        title = get("title").strip() or "Untitled"
        try:
            date = dt.datetime.strptime(get("pubDate").strip(),
                                        "%a, %d %b %Y %H:%M:%S %z")
        except ValueError:
            date = dt.datetime.now(dt.timezone.utc)
        slug = slugify(link.rstrip("/").rsplit("/", 1)[-1]) if link else slugify(title)
        markup, first_img = clean(get("description"))
        posts.append(make_post(title, slug, date, markup, first_img, "letter", link))
    return posts


def e(s):
    return html.escape(s or "", quote=True)


def head(title, desc, url, image, extra="", og_type="website", published=None):
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<meta name="description" content="{desc}">
<meta name="author" content="{author}">
<link rel="canonical" href="{url}">
<link rel="icon" href="/favicon.ico" type="image/x-icon">
<link rel="alternate" type="application/rss+xml" title="{site}" href="{site_url}/feed.xml">

<meta property="og:type" content="{og_type}">
<meta property="og:site_name" content="{site}">
<meta property="og:url" content="{url}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{desc}">
<meta property="og:image" content="{image}">
{article}<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{desc}">
<meta name="twitter:image" content="{image}">

<style>{css}</style>
<script defer src="https://cloud.umami.is/script.js" data-website-id="{umami}"></script>
{extra}
</head>
<body>
<div class="wrap">
""".format(title=e(title), desc=e(desc), url=e(url), image=e(image),
           author=e(AUTHOR), site=e(SITE_TITLE), site_url=SITE_URL,
           og_type=og_type, css=CSS, umami=UMAMI_ID, extra=extra,
           article=('<meta property="article:published_time" content="%s">\n'
                    '<meta property="article:author" content="%s">\n'
                    % (e(published), e(AUTHOR))) if published else "")


SIGNUP = """
<div class="panel panel-hero">
  <p class="panel-title">get the next letter in your inbox</p>
  <p class="panel-subtitle">{blurb}</p>
  <form action="{action}" method="post" target="popupwindow"
        onsubmit="window.open('https://newsletter.iamdavehawkins.com', 'popupwindow')">
    <label class="visually-hidden" for="{fid}">Email address</label>
    <input type="email" name="email" id="{fid}" placeholder="your@email.com" required>
    <input type="submit" value="join the list">
  </form>
  <p class="panel-fine">No spam, no algorithm. Unsubscribe whenever you like.</p>
</div>
"""


INDEX_SIGNUP = """<div class="panel panel-hero">
  <p class="panel-title">It's a different kind of "internet sharing" than what most of us are used to.</p>
  <p>just old-fashioned, hand-typed emails</p>
  <form action="{action}" method="post" target="popupwindow"
        onsubmit="window.open('https://newsletter.iamdavehawkins.com', 'popupwindow')">
    <label class="visually-hidden" for="email-top">Email address</label>
    <input type="email" name="email" id="email-top" placeholder="your@email.com" required>
    <input type="submit" value="join the list">
  </form>
  <p>This is my "blog", I guess? It's a record of all the emails I've sent. About songs I've written, records I've been into, other random musings and stories. This is a place where it's all archived so you can read without having to sign up for anything if you don't want to. And if you do, you can always unsubscribe at any time.</p>
  <p>Latest: <a href="/p/{lslug}/">{ltitle}</a>, {ldate}.</p>
</div>
"""


def signup(blurb, fid):
    return SIGNUP.format(blurb=blurb, action=BUTTONDOWN_FORM, fid=fid)


FOOTER = """
<hr>
<div class="footer">
  <a href="https://iamdavehawkins.com">iamdavehawkins.com</a> &middot;
  <a href="https://music.iamdavehawkins.com">the archive: all my music, free</a> &middot;
  <a href="https://iamdavehawkins.bandcamp.com" target="_blank" rel="noopener">bandcamp</a> &middot;
  <a href="/feed.xml">rss</a>
  <br>
  made with love in denver, co
</div>
</div>
</body>
</html>
"""


def render_index(posts):
    rows, year = [], None
    for p in posts:
        if p["date"].year != year:
            year = p["date"].year
            rows.append('<li class="year-rule" aria-hidden="true"><span>%d</span></li>' % year)
        rows.append(
            '<li class="row" data-slug="{slug}">'
            '<a class="row-link" href="/p/{slug}/">'
            '<span class="row-date"><span class="vis-year">{year} </span>{date}</span>'
            '<span class="row-title">{title}</span>'
            '<span class="row-len">{mins} min</span>'
            '</a><p class="row-snippet" hidden></p></li>'.format(
                slug=p["slug"], date=p["date"].strftime("%b %d").replace(" 0", "  "),
                year=p["date"].year, title=e(p["title"]), mins=p["minutes"]))
        rows.append("")

    latest = posts[0]
    body = """
<header class="masthead">
  <h1><a href="/">dave hawkins</a></h1>
  <p class="tagline">{tagline}</p>
</header>

{signup}

<section class="archive" aria-labelledby="archive-h">
  <div class="archive-head">
    <h2 id="archive-h">the archive</h2>
    <p class="count" id="count">{n} letters, {first}&ndash;{last}</p>
  </div>

  <div class="search" hidden>
    <label class="visually-hidden" for="q">Search the letters</label>
    <input type="search" id="q" placeholder="search the letters&hellip;"
           autocomplete="off" spellcheck="false">
    <button type="button" id="clear" hidden>clear</button>
  </div>
  <p class="search-status" id="status" role="status" hidden></p>

  <ol class="rows" id="rows">
{rows}
  </ol>
  <p class="empty" id="empty" hidden>Nothing matches that. Try a different word.</p>
</section>
""".format(tagline=html.escape(SITE_TAGLINE, quote=False),
        signup=INDEX_SIGNUP.format(
        action=BUTTONDOWN_FORM, lslug=latest["slug"], ltitle=e(latest["title"]),
        ldate=latest["date"].strftime("%B %-d, %Y")),
        n=len(posts), first=posts[-1]["date"].year, last=posts[0]["date"].year,
        rows="\n".join(rows))

    ld = json.dumps({
        "@context": "https://schema.org",
        "@type": "Blog",
        "name": SITE_TITLE,
        "url": SITE_URL + "/",
        "description": SITE_DESC,
        "inLanguage": "en-US",
        "author": {"@type": "Person", "name": AUTHOR,
                   "url": "https://iamdavehawkins.com/"},
        "blogPost": [{
            "@type": "BlogPosting",
            "headline": p["title"],
            "url": "%s/p/%s/" % (SITE_URL, p["slug"]),
            "datePublished": p["date"].isoformat(),
        } for p in posts[:12]],
    }, ensure_ascii=False)

    extra = '<script type="application/ld+json">%s</script>' % ld
    return (head(SITE_TITLE + " | newsletter archive", SITE_DESC,
                 SITE_URL + "/", DEFAULT_OG, extra)
            + body + FOOTER.replace("</body>", SEARCH_JS + "\n</body>"))


def render_post(p, newer, older):
    url = "%s/p/%s/" % (SITE_URL, p["slug"])
    image = p["image"] or DEFAULT_OG
    ld = json.dumps({
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        "headline": p["title"],
        "description": p["summary"],
        "datePublished": p["date"].isoformat(),
        "dateModified": p["date"].isoformat(),
        "url": url,
        "mainEntityOfPage": {"@type": "WebPage", "@id": url},
        "image": image,
        "wordCount": p["words"],
        "inLanguage": "en-US",
        "author": {"@type": "Person", "name": AUTHOR,
                   "url": "https://iamdavehawkins.com/"},
        "publisher": {"@type": "Person", "name": AUTHOR},
        "isPartOf": {"@type": "Blog", "name": SITE_TITLE, "url": SITE_URL + "/"},
    }, ensure_ascii=False)
    extra = '<script type="application/ld+json">%s</script>' % ld

    def nav(post, label):
        if not post:
            return '<span class="pn-none"></span>'
        return ('<a class="pn" href="/p/{slug}/"><span class="pn-label">{label}</span>'
                '<span class="pn-title">{title}</span></a>').format(
            slug=post["slug"], label=label, title=e(post["title"]))

    origin = ""
    if p["origin"]:
        origin = ('<p class="origin">Originally sent by email. '
                  '<a href="%s" target="_blank" rel="noopener nofollow">'
                  'View the original</a>.</p>' % e(p["origin"]))

    body = """
<header class="masthead masthead-sm">
  <p class="crumb">
    <a class="crumb-home" href="/">dave hawkins</a>
    <span class="crumb-sep">/</span>
    <a href="/">all letters</a>
  </p>
</header>

<article class="letter">
  <div class="headers">
    <div class="hdr"><span class="hdr-k">From</span><span class="hdr-v">{author}</span></div>
    <div class="hdr"><span class="hdr-k">Date</span><span class="hdr-v">
      <time datetime="{iso}">{date}</time></span></div>
    <div class="hdr hdr-subject"><span class="hdr-k">Subject</span>
      <h1 class="hdr-v">{title}</h1></div>
  </div>

  <div class="prose">
{content}
  </div>
</article>

{origin}

{signup}

<nav class="postnav" aria-label="More letters">
  {newer}
  {older}
</nav>
""".format(author=e(AUTHOR), iso=p["date"].isoformat(),
           date=p["date"].strftime("%a, %b %-d, %Y"), title=e(p["title"]),
           content=p["html"], origin=origin,
           signup=signup("Liked this one? The next one goes out by email first.",
                         "email-post"),
           newer=nav(newer, "newer"), older=nav(older, "older"))

    return (head(p["title"] + " | " + AUTHOR, p["summary"], url, image, extra,
                 og_type="article", published=p["date"].isoformat())
            + body + FOOTER)


def render_feed(posts):
    now = dt.datetime.now(dt.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    items = []
    for p in posts:
        items.append("""  <item>
    <title>{title}</title>
    <link>{url}</link>
    <guid isPermaLink="true">{url}</guid>
    <pubDate>{date}</pubDate>
    <description>{body}</description>
  </item>""".format(
            title=html.escape(p["title"]), url="%s/p/%s/" % (SITE_URL, p["slug"]),
            date=p["date"].strftime("%a, %d %b %Y %H:%M:%S +0000"),
            body=html.escape(p["html"])))
    return """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
  <title>{title}</title>
  <link>{url}/</link>
  <description>{desc}</description>
  <language>en-us</language>
  <lastBuildDate>{now}</lastBuildDate>
  <atom:link href="{url}/feed.xml" rel="self" type="application/rss+xml"/>
{items}
</channel>
</rss>
""".format(title=html.escape(SITE_TITLE), url=SITE_URL,
           desc=html.escape(SITE_DESC), now=now, items="\n".join(items))


def render_sitemap(posts):
    urls = ['  <url><loc>%s/</loc><changefreq>weekly</changefreq>'
            '<priority>1.0</priority></url>' % SITE_URL]
    for p in posts:
        urls.append('  <url><loc>%s/p/%s/</loc><lastmod>%s</lastmod>'
                    '<priority>0.8</priority></url>'
                    % (SITE_URL, p["slug"], p["date"].strftime("%Y-%m-%d")))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(urls) + "\n</urlset>\n")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true",
                    help="build from the cached feed instead of fetching")
    args = ap.parse_args()

    posts = parse_feed(fetch_feed(args.offline))
    print("  %d letters from Buttondown" % len(posts))

    local = load_markdown_posts()
    if local:
        print("  %d local markdown post(s)" % len(local))
        seen = {p["slug"] for p in posts}
        for p in local:
            while p["slug"] in seen:
                p["slug"] += "-2"
            seen.add(p["slug"])
        posts += local

    posts.sort(key=lambda p: p["date"], reverse=True)
    if not posts:
        sys.exit("No posts found; refusing to write an empty site.")

    out = os.path.join(HERE, "p")
    if os.path.isdir(out):
        shutil.rmtree(out)

    for i, p in enumerate(posts):
        newer = posts[i - 1] if i > 0 else None
        older = posts[i + 1] if i + 1 < len(posts) else None
        write(os.path.join(out, p["slug"], "index.html"),
              render_post(p, newer, older))

    write(os.path.join(HERE, "index.html"), render_index(posts))
    write(os.path.join(HERE, "feed.xml"), render_feed(posts))
    write(os.path.join(HERE, "sitemap.xml"), render_sitemap(posts))
    write(os.path.join(HERE, "robots.txt"),
          "User-agent: *\nAllow: /\n\nSitemap: %s/sitemap.xml\n" % SITE_URL)
    write(os.path.join(HERE, "404.html"), render_404())

    write(os.path.join(HERE, "posts.json"), json.dumps(
        [{"s": p["slug"], "t": p["title"], "d": p["date"].strftime("%Y-%m-%d"),
          "m": p["minutes"], "x": p["text"]} for p in posts],
        ensure_ascii=False, separators=(",", ":")))

    print("Built %d pages + index, feed, sitemap, search index." % len(posts))
    print("Preview: python3 -m http.server  ->  http://localhost:8000")


def render_404():
    body = """
<header class="masthead">
  <h1><a href="/">dave hawkins</a></h1>
  <p class="tagline">{tagline}</p>
</header>

<div class="panel">
  <p class="panel-title">no letter at that address</p>
  <p class="panel-subtitle">The link may be old, or the letter may have moved.</p>
  <p><a href="/">&larr; browse all the letters</a></p>
</div>
""".format(tagline=html.escape(SITE_TAGLINE, quote=False))
    return (head("Not found | " + SITE_TITLE,
                 "That page doesn't exist. Browse the full newsletter archive instead.",
                 SITE_URL + "/404.html", DEFAULT_OG)
            + body + FOOTER)


CSS = r"""
:root{
  --bg:#f6f6f6; --panel:#fff; --hero:#fffef5;
  --ink:#000; --muted:#666; --faint:#999;
  --rule:#ccc; --rule-strong:#333;
  --link:#00c; --visited:#551a8b; --brand:#9B4A34;
  --mono:"Courier New",Courier,ui-monospace,monospace;
}
*{box-sizing:border-box}
[hidden]{display:none !important}
html{-webkit-text-size-adjust:100%}
body{
  font-family:Arial,Helvetica,sans-serif; font-size:14px; line-height:1.4;
  margin:0; background:var(--bg); color:var(--ink);
}
.wrap{max-width:600px; margin:0 auto; padding:10px 10px 24px}
a{color:var(--link)}
a:visited{color:var(--visited)}
a:focus-visible,input:focus-visible,button:focus-visible,summary:focus-visible{
  outline:2px solid var(--brand); outline-offset:2px;
}
hr{border:none; border-top:1px solid var(--rule); margin:18px 0}
img{max-width:100%; height:auto}
.visually-hidden{
  position:absolute; width:1px; height:1px; padding:0; margin:-1px;
  overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; border:0;
}

.masthead{margin:10px 0 12px}
.masthead h1{font-size:16px; margin:0 0 3px}
.masthead h1 a{color:var(--ink); text-decoration:none}
.masthead h1 a:visited{color:var(--ink)}
.masthead h1 a:hover{text-decoration:underline}
.tagline{font-size:13px; font-weight:normal; color:var(--muted); margin:0; font-style:italic}
.masthead-sm{margin:6px 0 12px; padding-bottom:8px; border-bottom:1px solid var(--rule)}
.crumb{margin:0; font-size:12px}
.crumb-home{font-weight:bold; color:var(--ink); text-decoration:none}
.crumb-home:visited{color:var(--ink)}
.crumb-home:hover{text-decoration:underline}
.crumb-sep{color:var(--rule); margin:0 5px}

.panel{border:1px solid var(--rule-strong); padding:12px; margin:12px 0; background:var(--panel)}
.panel-hero{border-width:2px; background:var(--hero)}
.panel-title{font-weight:bold; font-size:13px; margin:0 0 6px}
.panel-subtitle{font-size:11px; color:var(--muted); margin:0 0 10px; font-style:italic}
.panel-fine{font-size:10px; color:var(--faint); margin:8px 0 0}
.panel form{margin:0; display:flex; flex-wrap:wrap; gap:6px}
input[type=email]{
  padding:5px 6px; font-size:13px; font-family:inherit; flex:1 1 160px; min-width:0;
  border:1px solid var(--rule-strong); background:#fff; color:var(--ink);
}
input[type=submit]{
  padding:5px 14px; font-size:12px; font-family:inherit; cursor:pointer;
  border:1px solid #7a3a29; background:var(--brand); color:#fff; font-weight:bold;
}
input[type=submit]:hover{background:#7a3a29}

.archive-head{
  display:flex; align-items:baseline; justify-content:space-between;
  gap:10px; flex-wrap:wrap; margin:20px 0 8px;
}
.archive-head h2{
  font-size:12px; margin:0; background:#eee; padding:3px 5px;
  border:1px solid var(--rule); font-weight:bold;
}
.count{font-size:11px; color:var(--muted); margin:0; font-family:var(--mono)}

.search{display:flex; gap:6px; margin:0 0 4px}
input[type=search]{
  flex:1 1 auto; min-width:0; padding:6px 8px; font-size:13px; font-family:inherit;
  border:1px solid var(--rule-strong); background:#fff; color:var(--ink);
  -webkit-appearance:none; appearance:none; border-radius:0;
}
#clear{
  padding:6px 10px; font-size:11px; font-family:inherit; cursor:pointer;
  border:1px solid var(--rule); background:#eee; color:var(--muted);
}
#clear:hover{color:var(--ink); border-color:var(--rule-strong)}
.search-status{font-size:11px; color:var(--muted); margin:6px 0 0; font-family:var(--mono)}
.empty{font-size:12px; color:var(--muted); font-style:italic; padding:14px 2px}

.rows{list-style:none; margin:8px 0 0; padding:0; border-top:1px solid var(--rule)}
.row{border-bottom:1px solid var(--rule); background:var(--panel)}
.row-link{
  display:grid; grid-template-columns:4.6em 1fr auto; gap:10px;
  align-items:baseline; padding:7px 6px; text-decoration:none; color:var(--link);
}
.row-link:visited{color:var(--visited)}
.row-link:hover{background:#eef1f8}
.row-link:hover .row-title{text-decoration:underline}
.row-date{font-family:var(--mono); font-size:11px; color:var(--muted); white-space:nowrap}
.vis-year{display:none}
.row-title{color:inherit; min-width:0; overflow-wrap:break-word}
.row-len{font-family:var(--mono); font-size:10px; color:var(--faint); white-space:nowrap}
.row-snippet{
  margin:0; padding:0 6px 9px; font-size:12px; color:var(--muted); line-height:1.5;
}
.row-snippet mark, .row-title mark{background:#ffe9a8; color:inherit; padding:0 1px}

.year-rule{
  display:flex; align-items:center; gap:8px; padding:14px 6px 5px;
  font-family:var(--mono); font-size:11px; color:var(--faint); letter-spacing:.08em;
}
.year-rule::after{content:""; flex:1; border-top:1px solid var(--rule)}
.rows .year-rule:first-child{padding-top:8px}

.headers{
  border:2px solid var(--rule-strong); background:var(--panel);
  padding:9px 11px; margin:0 0 18px; font-family:var(--mono); font-size:12px;
}
.hdr{display:flex; gap:8px; padding:1px 0; align-items:baseline}
.hdr-k{
  flex:0 0 4.6em; color:var(--faint); text-transform:uppercase;
  font-size:10px; letter-spacing:.06em;
}
.hdr-k::after{content:":"}
.hdr-v{margin:0; font-size:12px; font-weight:normal; color:var(--ink)}
.hdr-subject{border-top:1px solid var(--rule); margin-top:5px; padding-top:6px}
.hdr-subject .hdr-v{font-weight:bold; font-size:15px; line-height:1.3}

.prose{font-size:15px; line-height:1.7; overflow-wrap:break-word}
.prose > *:first-child{margin-top:0}
.prose p{margin:0 0 1em}
.prose h3{font-size:15px; margin:1.6em 0 .4em; font-weight:bold}
.prose h4,.prose h5,.prose h6{font-size:13px; margin:1.4em 0 .4em; font-weight:bold}
.prose a{text-decoration:underline}
.prose ul,.prose ol{margin:0 0 1em; padding-left:1.4em}
.prose li{margin:.25em 0}
.prose blockquote{
  margin:1.2em 0; padding:2px 0 2px 12px; border-left:2px solid var(--brand);
  color:var(--muted); font-style:italic;
}
.prose hr{margin:1.6em 0}
.prose figure{margin:1.4em 0}
.prose img{display:block; border:1px solid var(--rule); background:#fff}
.prose figcaption{
  font-size:11px; color:var(--muted); font-style:italic; margin-top:5px; line-height:1.45;
}
.prose code{font-family:var(--mono); font-size:.92em; background:#eee; padding:1px 3px}
.prose pre{
  font-family:var(--mono); font-size:12px; background:var(--panel);
  border:1px solid var(--rule); padding:10px; overflow-x:auto; line-height:1.45;
}
.prose pre code{background:none; padding:0}
.prose table{border-collapse:collapse; font-size:13px; width:100%}
.prose td,.prose th{border:1px solid var(--rule); padding:5px 7px; text-align:left}
.embed{position:relative; padding-bottom:56.25%; height:0; margin:1.4em 0; background:#000}
.embed iframe{position:absolute; inset:0; width:100%; height:100%; border:0}

.origin{font-size:11px; color:var(--faint); margin:16px 0 0; font-style:italic}

.postnav{display:grid; grid-template-columns:1fr 1fr; gap:8px; margin:14px 0 0}
.pn{
  display:block; border:1px solid var(--rule); background:var(--panel);
  padding:8px 10px; text-decoration:none; min-width:0;
}
.pn:hover{border-color:var(--rule-strong); background:#eef1f8}
.pn-label{
  display:block; font-family:var(--mono); font-size:10px; color:var(--faint);
  text-transform:uppercase; letter-spacing:.06em; margin-bottom:2px;
}
.pn-title{display:block; font-size:12px; line-height:1.35}
.postnav .pn:last-child{text-align:right}
.pn-none{display:block}

.about{font-size:12px; color:var(--muted); margin:14px 0}
.about summary{cursor:pointer; color:var(--link)}
.about p{margin:8px 0; line-height:1.55}
.footer{font-size:10px; color:var(--muted); margin-top:16px; line-height:1.7}

@media (max-width:420px){
  .row-link{grid-template-columns:1fr auto; gap:4px 8px}
  .row-date{grid-column:1; font-size:10px}
  .row-len{grid-column:2; text-align:right}
  .row-title{grid-column:1/-1}
  .vis-year{display:inline}
  .prose{font-size:15px}
  .postnav{grid-template-columns:1fr}
  .postnav .pn:last-child{text-align:left}
  .pn-none{display:none}
}
@media (prefers-reduced-motion:reduce){
  *{animation:none !important; transition:none !important; scroll-behavior:auto !important}
}
"""

SEARCH_JS = r"""
<script>
(function () {
  var q = document.getElementById('q'),
      rows = document.getElementById('rows'),
      status = document.getElementById('status'),
      empty = document.getElementById('empty'),
      clear = document.getElementById('clear'),
      items = [].slice.call(rows.querySelectorAll('.row')),
      years = [].slice.call(rows.querySelectorAll('.year-rule')),
      bodies = null, state = 'idle', pending = null, timer;

  var meta = {};
  items.forEach(function (li) {
    var el = li.querySelector('.row-title');
    meta[li.dataset.slug] = {
      el: el, text: el.textContent,
      snip: li.querySelector('.row-snippet')
    };
  });

  function load() {
    if (state !== 'idle') return;
    state = 'loading';
    fetch('posts.json').then(function (r) {
      if (!r.ok) throw new Error(r.status);
      return r.json();
    }).then(function (data) {
      bodies = {};
      data.forEach(function (p) { bodies[p.s] = p.x; });
      state = 'ready';
      var term = pending !== null ? pending : q.value;
      pending = null;
      if (term.trim()) run(term);
    }).catch(function () {
      state = 'failed';
      if (pending !== null) { var t = pending; pending = null; run(t); }
    });
  }

  function esc(s) {
    return s.replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  function mark(text, at, len) {
    return esc(text.slice(0, at)) + '<mark>' + esc(text.slice(at, at + len)) +
           '</mark>' + esc(text.slice(at + len));
  }

  function snippet(body, at, len) {
    var start = Math.max(0, at - 70),
        end = Math.min(body.length, at + len + 90),
        s = body.slice(start, end),
        shift = start;
    if (start > 0) {
      var cut = s.search(/\s/);
      if (cut > -1 && cut < 25) { s = '…' + s.slice(cut + 1); shift = start + cut + 1 - 1; }
    }
    if (end < body.length) s = s.replace(/\s\S*$/, '…');
    return mark(s, at - shift, len);
  }

  function reset() {
    items.forEach(function (li) {
      li.hidden = false;
      var m = meta[li.dataset.slug];
      m.el.textContent = m.text;
      m.snip.hidden = true;
      m.snip.textContent = '';
    });
    years.forEach(function (y) { y.hidden = false; });
    status.hidden = true;
    empty.hidden = true;
    clear.hidden = true;
  }

  function run(value) {
    var term = value.trim().toLowerCase();
    if (!term) { reset(); return; }
    clear.hidden = false;
    if (state === 'idle') { pending = value; load(); }
    else if (state === 'loading') { pending = value; }

    var hits = 0;
    items.forEach(function (li) {
      var m = meta[li.dataset.slug],
          tAt = m.text.toLowerCase().indexOf(term),
          body = bodies ? (bodies[li.dataset.slug] || '') : '',
          bAt = body ? body.toLowerCase().indexOf(term) : -1;

      li.hidden = tAt < 0 && bAt < 0;
      if (li.hidden) { m.snip.hidden = true; return; }
      hits++;
      m.el.innerHTML = tAt >= 0 ? mark(m.text, tAt, term.length) : esc(m.text);

      if (tAt < 0 && bAt >= 0) {
        m.snip.innerHTML = snippet(body, bAt, term.length);
        m.snip.hidden = false;
      } else {
        m.snip.hidden = true;
      }
    });

    years.forEach(function (y) {
      var n = y.nextElementSibling, any = false;
      while (n && !n.classList.contains('year-rule')) {
        if (n.classList.contains('row') && !n.hidden) { any = true; break; }
        n = n.nextElementSibling;
      }
      y.hidden = !any;
    });

    empty.hidden = hits > 0 || state === 'loading';
    status.hidden = false;
    status.textContent = hits + (hits === 1 ? ' letter' : ' letters') +
      ' matching “' + value.trim() + '”' +
      (state === 'loading' ? ', searching titles, loading the rest…' :
       state === 'failed' ? ', titles only' : '');
  }

  q.addEventListener('focus', load);
  q.addEventListener('input', function () {
    clearTimeout(timer);
    var v = q.value;
    timer = setTimeout(function () { run(v); }, 120);
  });
  clear.addEventListener('click', function () { q.value = ''; reset(); q.focus(); });

  document.addEventListener('keydown', function (ev) {
    var tag = document.activeElement && document.activeElement.tagName;
    if (ev.key === '/' && document.activeElement !== q &&
        !/^(INPUT|TEXTAREA|SELECT)$/.test(tag || '')) {
      ev.preventDefault();
      q.focus();
    } else if (ev.key === 'Escape' && document.activeElement === q) {
      q.value = '';
      reset();
    }
  });

  q.parentNode.hidden = false;
})();
</script>
"""


if __name__ == "__main__":
    main()
