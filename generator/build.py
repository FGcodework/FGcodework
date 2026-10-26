#!/usr/bin/env python3
"""FG profile builder.

Reads profile.json and generates README.md plus the SVG assets in assets/.

    python generator/build.py          build everything (writes files)
    python generator/build.py check    validate only, write nothing, fail on warnings

Edit profile.json, never the generated files. Logos of extensions are fetched
from each repository (assets/logo.webp, falling back to assets/logo.png).
"""
from __future__ import annotations

import base64
import html
import io
import json
import math
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape as _xesc

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "profile.json"
ASSETS = ROOT / "assets"

SANS = "'Segoe UI',Helvetica,Arial,sans-serif"
MONO = "SFMono-Regular,Consolas,'Liberation Mono',monospace"
PAGE_W = 900          # width of every generated row, in SVG units
GAP = 5.0             # gap between boxes that share a row
MAX_COLUMNS = 4

DEFAULT_THEME = {
    "panel": "#0d172b",         # flat panel background
    "grad_from": "#14264a",     # gradient start (cards, principle)
    "grad_to": "#0b1426",       # gradient end
    "border": "#2a4276",
    "text": "#f2f6ff",
    "text_soft": "#b8c7e6",
    "text_muted": "#7f93bb",
    "pill_text": "#dbe7ff",
    "accent": "#4cc3ff",
    "accent2": "#ff7a4d",
    "accent2_text": "#ffd5c4",
    "accent2_muted": "#a8725f",
}

DEFAULT_SECTIONS = ["hero", "about", "featured", "stack", "principle",
                    "stats", "contacts", "footer", "views"]

WARNINGS: list[str] = []


class ConfigError(Exception):
    pass


def warn(msg: str) -> None:
    WARNINGS.append(msg)


# ----------------------------------------------------------------- helpers
def esc(s) -> str:
    return _xesc(str(s))


def attr(s) -> str:
    return _xesc(str(s), {'"': "&quot;"})


def num(x: float) -> str:
    return f"{x:.3f}".rstrip("0").rstrip(".") or "0"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")


def req(d: dict, key: str, where: str):
    if not isinstance(d, dict) or key not in d:
        raise ConfigError(f'profile.json: missing "{key}" in {where}')
    return d[key]


def balanced_rows(items: list, columns: int) -> list[list]:
    """Split items into rows of at most `columns`, as evenly as possible."""
    if not items:
        return []
    rows = math.ceil(len(items) / columns)
    base, extra = divmod(len(items), rows)
    out, pos = [], 0
    for r in range(rows):
        size = base + (1 if r < extra else 0)
        out.append(items[pos:pos + size])
        pos += size
    return out


def grid(n: int) -> tuple[float, float, list[float]]:
    """Geometry for n boxes sharing one full-width row.

    Each box is its own image sized 100/n % of the page. Returns the viewBox
    width of one image, the box width and the x offset of every box inside
    its image, so that the row spans exactly the full width with equal gaps.
    """
    w = PAGE_W / n
    cw = (PAGE_W - (n - 1) * GAP) / n
    return w, cw, [i * (cw + GAP - w) for i in range(n)]


def pct(n: int) -> str:
    return num(100 / n) + "%"


def wrap(text: str, max_chars: int) -> list[str]:
    out: list[str] = []
    for para in str(text).split("\n"):
        cur = ""
        for word in para.split():
            if cur and len(cur) + 1 + len(word) > max_chars:
                out.append(cur)
                cur = word
            else:
                cur = f"{cur} {word}".strip()
        out.append(cur)
    return out


# ------------------------------------------------------------------ logos
_img_cache: dict[str, Image.Image] = {}


def http_get(url: str) -> bytes:
    req_ = urllib.request.Request(url, headers={"User-Agent": "fg-profile-builder"})
    with urllib.request.urlopen(req_, timeout=30) as r:
        return r.read()


def load_logo(user: str, spec) -> Image.Image:
    """spec: repo name, or {repo, owner?, branch?, logo?}. `logo` overrides."""
    if isinstance(spec, str):
        spec = {"repo": spec}
    key = json.dumps(spec, sort_keys=True)
    if key in _img_cache:
        return _img_cache[key]

    img = None
    if spec.get("logo"):
        src = spec["logo"]
        data = http_get(src) if src.startswith("http") else (ROOT / src).read_bytes()
        img = Image.open(io.BytesIO(data))
    else:
        owner = spec.get("owner", user)
        repo = req(spec, "repo", "a logo reference")
        branches = spec.get("branch") or ["master", "main"]
        if isinstance(branches, str):
            branches = [branches]
        tried = []
        for branch in branches:
            for name in ("logo.webp", "logo.png"):
                url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/assets/{name}"
                try:
                    data = http_get(url)
                except urllib.error.HTTPError as e:
                    if e.code == 404:
                        tried.append(url)
                        continue
                    raise
                img = Image.open(io.BytesIO(data))
                break
            if img is not None:
                break
        if img is None:
            raise ConfigError("no logo found for %s/%s, tried:\n  %s"
                              % (owner, repo, "\n  ".join(tried)))

    img = img.convert("RGBA")
    _img_cache[key] = img
    return img


def png_b64(img: Image.Image, size: int) -> str:
    im = img.copy()
    im.thumbnail((size, size), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(im, ((size - im.width) // 2, (size - im.height) // 2), im)
    buf = io.BytesIO()
    canvas.save(buf, "PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


# -------------------------------------------------------------- svg pieces
def svg_open(w: float, h: float, label: str) -> str:
    return ('<svg xmlns="http://www.w3.org/2000/svg" '
            'xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'viewBox="0 0 {num(w)} {num(h)}" width="{num(w)}" height="{num(h)}" '
            f'role="img" aria-label="{attr(label)}">')


def grad(id_: str, c1: str, c2: str, x2: int = 1, y2: int = 0) -> str:
    return (f'<linearGradient id="{id_}" x1="0" y1="0" x2="{x2}" y2="{y2}">'
            f'<stop offset="0" stop-color="{c1}"/><stop offset="1" stop-color="{c2}"/>'
            '</linearGradient>')


def norm_pills(raw) -> list[tuple[str, str]]:
    out = []
    for p in raw or []:
        if isinstance(p, str):
            out.append((p, "accent"))
        else:
            out.append((req(p, "text", "a chip/badge"), p.get("style", "accent")))
    return out


def pill(x: float, y: float, w: float, h: float, text: str, style: str,
         fs: float, T: dict, fo: float = 0.12, so: float = 0.5) -> str:
    if style == "accent2":
        col, tcol, so = T["accent2"], T["accent2_text"], so + 0.05
    else:
        col, tcol = T["accent"], T["pill_text"]
    ty = h / 2 + fs * 0.34
    return (f'<g transform="translate({num(x)} {num(y)})">'
            f'<rect width="{num(w)}" height="{num(h)}" rx="{num(h / 2)}" fill="{col}" '
            f'fill-opacity="{fo}" stroke="{col}" stroke-opacity="{so}"/>'
            f'<text x="{num(w / 2)}" y="{num(ty)}" text-anchor="middle" '
            f'font-family="{MONO}" font-size="{fs}" fill="{tcol}">{esc(text)}</text></g>')


def pill_w(text: str, fs: float, pad: float) -> float:
    return len(text) * fs * 0.6 + pad


# -------------------------------------------------------------------- hero
def hero_svg(cfg: dict, T: dict) -> str:
    h = req(cfg, "hero", "root")
    user = cfg["user"]
    title = req(h, "title", "hero")
    tagline = h.get("tagline", "")
    sub = h.get("subtitle", "")
    W, H = 900, 260
    side = h.get("side_logos", [])

    # horizontal budget before the logo fan (about x=640)
    limit = 640 if side else 860
    if len(tagline) * 10 > limit - 232:
        warn(f'hero.tagline is probably too long ({len(tagline)} chars)')
    if len(sub) * 8 > limit - 232:
        warn(f'hero.subtitle is probably too long ({len(sub)} chars)')

    tile = png_b64(Image.open(ROOT / req(h, "logo", "hero")).convert("RGBA"), 300)
    o = [svg_open(W, H, f"{title} — {tagline}".strip(" —")), "<defs>",
         grad("bg", T["grad_to"], T["grad_from"], 1, 1),
         grad("accent", T["accent"], T["accent2"]),
         '<pattern id="grid" width="30" height="30" patternUnits="userSpaceOnUse">'
         f'<path d="M30 0H0V30" fill="none" stroke="{T["accent"]}" stroke-opacity="0.07" '
         'stroke-width="1"/></pattern>', "</defs>",
         f'<rect width="{W}" height="{H}" rx="22" fill="url(#bg)"/>',
         f'<rect width="{W}" height="{H}" rx="22" fill="url(#grid)"/>',
         f'<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="21.5" fill="none" '
         f'stroke="{T["border"]}" stroke-opacity="0.8"/>',
         f'<image x="54" y="55" width="150" height="150" xlink:href="data:image/png;base64,{tile}"/>',
         f'<text x="232" y="106" font-family="{SANS}" font-size="46" font-weight="800" '
         f'fill="{T["text"]}" letter-spacing="-1">{esc(title)}</text>',
         f'<rect x="234" y="120" width="86" height="4" rx="2" fill="url(#accent)"/>']
    if tagline:
        o.append(f'<text x="232" y="156" font-family="{SANS}" font-size="20" '
                 f'fill="{T["text_soft"]}">{esc(tagline)}</text>')
    if sub:
        o.append(f'<text x="232" y="184" font-family="{SANS}" font-size="16" '
                 f'fill="{T["text_muted"]}">{esc(sub)}</text>')

    x = 232.0
    for text, style in norm_pills(h.get("chips")):
        w = pill_w(text, 12, 22)
        o.append(pill(x, 204, w, 26, text, style, 12, T))
        x += w + 10

    # fan of extension logos on the right, last one on top
    sizes, ys, rots = [88, 100, 92, 84], [100, 60, 108, 64], [-8, 4, 10, -6]
    k = len(side)
    if k > 4:
        warn("hero.side_logos: only the first 4 are shown")
        side, k = side[:4], 4
    if k:
        step = 63
        x0 = 864 - (step * (k - 1) + sizes[k - 1])
        o.append("<g>")
        for i, spec in enumerate(side):
            b = png_b64(load_logo(user, spec), 208)
            s = sizes[i]
            xi, yi = x0 + step * i, ys[i]
            o.append(f'<image x="{num(xi)}" y="{yi}" width="{s}" height="{s}" '
                     f'transform="rotate({rots[i]} {num(xi + s / 2)} {num(yi + s / 2)})" '
                     f'xlink:href="data:image/png;base64,{b}"/>')
        o.append("</g>")
    o.append("</svg>")
    return "\n".join(o) + "\n"


# ------------------------------------------------------------------- about
def about_svg(cfg: dict, T: dict) -> str:
    a = req(cfg, "about", "root")
    name, text = req(a, "name", "about"), a.get("text", "")
    side = a.get("side", [])[:2]
    W, H = 900, 150
    limit = 700 if side else 860
    if len(name) * 28 * 0.56 > limit - 40:
        warn(f'about.name is probably too long ({len(name)} chars)')
    if len(text) * 15 * 0.5 > limit - 40:
        warn(f'about.text is probably too long ({len(text)} chars)')
    o = [svg_open(W, H, f"{name}. {text}"), "<defs>",
         grad("accent", T["accent"], T["accent2"]),
         f'<clipPath id="clip"><rect width="{W}" height="{H}" rx="18"/></clipPath></defs>',
         f'<rect width="{W}" height="{H}" rx="18" fill="{T["panel"]}"/>',
         f'<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="17.5" fill="none" '
         f'stroke="{T["border"]}" stroke-opacity="0.8"/>',
         '<rect x="0" y="26" width="5" height="98" fill="url(#accent)" clip-path="url(#clip)"/>']
    if a.get("label"):
        o.append(f'<text x="40" y="52" font-family="{MONO}" font-size="12" '
                 f'fill="{T["text_muted"]}" letter-spacing="3">{esc(a["label"])}</text>')
    o.append(f'<text x="40" y="88" font-family="{SANS}" font-size="28" font-weight="700" '
             f'fill="{T["text"]}">{esc(name)}</text>')
    if text:
        o.append(f'<text x="40" y="118" font-family="{SANS}" font-size="15" '
                 f'fill="{T["text_soft"]}">{esc(text)}</text>')
    if side:
        o.append(f'<g font-family="{MONO}" font-size="12">')
        for i, s in enumerate(side):
            y = 52 + 48 * i
            o.append(f'<text x="736" y="{y}" fill="{T["text_muted"]}" letter-spacing="2">'
                     f'{esc(req(s, "label", "about.side"))}</text>')
            o.append(f'<text x="736" y="{y + 20}" fill="{T["pill_text"]}">'
                     f'{esc(req(s, "value", "about.side"))}</text>')
        o.append("</g>")
    o.append("</svg>")
    return "\n".join(o) + "\n"


# ---------------------------------------------------------------- featured
def card_lines(item: dict, cw: float) -> list[str]:
    d = item.get("description", "")
    if isinstance(d, list):
        return [str(x) for x in d]
    return wrap(d, int((cw - 40) / 7.4))


def card_svg(item: dict, n: int, i: int, height: float, lines: list[str],
             logo_b64: str, T: dict) -> str:
    W, CW, offs = grid(n)
    title = req(item, "title", "a featured item")
    label = item.get("alt") or f"FG {title}"
    if len(title) * 19 * 0.56 > CW - 94 - 16:
        warn(f'featured "{title}": title is probably too long for {n} column(s)')
    o = [svg_open(W, height, f"{label} — {' '.join(lines)}"), "<defs>",
         grad("bg", T["grad_from"], T["grad_to"], 1, 1),
         grad("accent", T["accent"], T["accent2"]), "</defs>",
         f'<g transform="translate({num(offs[i])} 0)">',
         f'<rect x="0.5" y="0.5" width="{num(CW - 1)}" height="{num(height - 1)}" rx="17.5" '
         f'fill="url(#bg)" stroke="{T["border"]}"/>',
         f'<image x="18" y="18" width="64" height="64" xlink:href="data:image/png;base64,{logo_b64}"/>']
    if item.get("kind"):
        o.append(f'<text x="94" y="42" font-family="{SANS}" font-size="11" '
                 f'fill="{T["text_muted"]}" letter-spacing="2">{esc(item["kind"].upper())}</text>')
    o.append(f'<text x="94" y="66" font-family="{SANS}" font-size="19" font-weight="700" '
             f'fill="{T["text"]}">{esc(title)}</text>')
    o.append('<rect x="20" y="98" width="36" height="3" rx="1.5" fill="url(#accent)"/>')
    for j, line in enumerate(lines):
        o.append(f'<text x="20" y="{130 + 19 * j}" font-family="{SANS}" font-size="13.5" '
                 f'fill="{T["text_soft"]}">{esc(line)}</text>')
    x = 20.0
    for text, style in norm_pills(item.get("badges")):
        w = pill_w(text, 11, 20)
        o.append(pill(x, height - 44, w, 24, text, style, 11, T))
        x += w + 10
        if x > CW - 10:
            warn(f'featured "{title}": badges do not fit in the card')
    o += ["</g>", "</svg>"]
    return "\n".join(o) + "\n"


def build_featured(cfg: dict, T: dict, files: dict) -> str:
    f = cfg.get("featured") or {}
    items = f.get("items", [])
    if not items:
        return ""
    user = cfg["user"]
    columns = int(f.get("columns", 3))
    if not 1 <= columns <= MAX_COLUMNS:
        raise ConfigError(f"featured.columns must be 1..{MAX_COLUMNS}")
    html_rows = []
    for row in balanced_rows(items, columns):
        n = len(row)
        _, cw, _ = grid(n)
        all_lines = [card_lines(it, cw) for it in row]
        longest = max(len(x) for x in all_lines)
        if longest > 5:
            warn(f"featured row {[r['title'] for r in row]}: a description has {longest} lines")
        height = 175 + 19 * max(longest, 2)   # 3 lines -> 232
        imgs = []
        for i, (it, lines) in enumerate(zip(row, all_lines)):
            title = req(it, "title", "a featured item")
            repo = req(it, "repo", f'featured item "{title}"')
            logo = png_b64(load_logo(user, it), 160)
            name = f"card-{slug(it.get('id') or title)}.svg"
            files[f"assets/{name}"] = card_svg(it, n, i, height, lines, logo, T)
            url = it.get("url") or f"https://github.com/{it.get('owner', user)}/{repo}"
            alt = it.get("alt") or f"FG {title}"
            imgs.append(f'<a href="{attr(url)}"><img src="assets/{name}" alt="{attr(alt)}" '
                        f'width="{pct(n)}"></a>')
        html_rows.append('<p align="center">' + "".join(imgs) + "</p>")
    head = ""
    if f.get("title"):
        head = f'<h3 align="center">{html.escape(f["title"])}</h3>\n\n'
    return head + "\n\n".join(html_rows)


# ------------------------------------------------------------------- stack
def stack_svg(cfg: dict, T: dict) -> str:
    s = req(cfg, "stack", "root")
    rows = req(s, "rows", "stack")
    W, X0, XMAX, ROW_H = 900, 120, 880, 40
    lines = []   # (label or None, [(x, text)])
    for row in rows:
        label = req(row, "label", "a stack row")
        if len(label) > 7:
            warn(f'stack row "{label}": label longer than 7 characters will collide')
        x, cur, first = float(X0), [], True
        for text in row.get("items", []):
            w = pill_w(text, 13, 32)
            if cur and x + w > XMAX:
                lines.append((label if first else None, cur))
                first, cur, x = False, [], float(X0)
            cur.append((x, text, w))
            x += w + 10
        lines.append((label if first else None, cur))
    H = 70 + ROW_H * len(lines)
    o = [svg_open(W, H, "Stack — " + "; ".join(
            f"{r['label'].lower()}: {', '.join(r.get('items', []))}" for r in rows)),
         "<defs>", grad("accent", T["accent"], T["accent2"]), "</defs>",
         f'<rect width="{W}" height="{H}" rx="18" fill="{T["panel"]}"/>',
         f'<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="17.5" fill="none" '
         f'stroke="{T["border"]}" stroke-opacity="0.8"/>']
    if s.get("decor", True):
        o.append(f'<text x="640" y="{H - 40}" font-family="{MONO}" font-size="120" '
                 f'font-weight="700" fill="{T["accent"]}" fill-opacity="0.08">&lt;/&gt;</text>')
    head = s.get("title", "STACK")
    if head:
        o.append(f'<text x="36" y="40" font-family="{MONO}" font-size="12" font-weight="600" '
                 f'fill="{T["text_muted"]}" letter-spacing="3">{esc(head)}</text>')
        o.append('<rect x="36" y="48" width="40" height="3" rx="1.5" fill="url(#accent)"/>')
    for r, (label, pills) in enumerate(lines):
        y = 64 + ROW_H * r
        if label:
            o.append(f'<text x="36" y="{y + 18}" font-family="{MONO}" font-size="12" '
                     f'font-weight="600" fill="{T["accent2"]}" letter-spacing="3">{esc(label)}</text>')
        for x, text, w in pills:
            o.append(pill(x, y, w, 26, text, "accent", 13, T, fo=0.1, so=0.4))
    o.append("</svg>")
    return "\n".join(o) + "\n"


# --------------------------------------------------------------- principle
def principle_svg(cfg: dict, T: dict) -> str:
    p = req(cfg, "principle", "root")
    title, text = req(p, "title", "principle"), p.get("text", "")
    W, H = 900, 110
    if len(title) * 26 * 0.56 > W - 72:
        warn("principle.title is probably too long")
    if len(text) * 14 * 0.5 > W - 72:
        warn("principle.text is probably too long")
    o = [svg_open(W, H, f"{title} {text}".strip()), "<defs>",
         grad("accent", T["accent"], T["accent2"]),
         grad("bg", T["grad_from"], T["grad_to"]), "</defs>",
         f'<rect width="{W}" height="{H}" rx="18" fill="url(#bg)"/>',
         f'<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="17.5" fill="none" '
         f'stroke="{T["border"]}" stroke-opacity="0.8"/>',
         f'<text x="36" y="52" font-family="{SANS}" font-size="26" font-weight="700" '
         f'fill="{T["text"]}">{esc(title)}</text>',
         '<rect x="36" y="64" width="64" height="4" rx="2" fill="url(#accent)"/>']
    if text:
        o.append(f'<text x="36" y="90" font-family="{SANS}" font-size="14" '
                 f'fill="#9fb2d8">{esc(text)}</text>')
    o.append("</svg>")
    return "\n".join(o) + "\n"


# ---------------------------------------------------------------- contacts
def icon_svg(icon: str, col: str) -> tuple[str, int]:
    """Returns (svg markup, x of the text column)."""
    if icon == "github":
        return (f'<circle cx="30" cy="28" r="13" fill="none" stroke="{col}" stroke-width="2"/>'
                '<path d="M25 34c0-2 1-3 2-4-2-.5-3-2-3-4 0-1.5.5-2.5 1.3-3.3-.3-1 0-2.2.4-2.7 '
                '1 0 2 .5 2.7 1.1 1.2-.3 2.4-.3 3.6 0 .7-.6 1.7-1.1 2.7-1.1.4.5.7 1.7.4 2.7.8.8 '
                '1.3 1.8 1.3 3.3 0 2-1 3.5-3 4 1 1 2 2 2 4" fill="none" '
                f'stroke="{col}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>', 56)
    if icon == "kofi":
        return ('<path d="M18 22h22v10c0 5-4 9-9 9h-4c-5 0-9-4-9-9z" transform="translate(2 -3)" '
                f'fill="none" stroke="{col}" stroke-width="2" stroke-linejoin="round"/>'
                f'<path d="M42 24h3a4 4 0 0 1 0 8h-3" fill="none" stroke="{col}" stroke-width="2" '
                'stroke-linecap="round"/>'
                '<path d="M31 32c-2-1.5-3.5-2.8-3.5-4.3a2 2 0 0 1 3.5-1.3a2 2 0 0 1 3.5 1.3c0 '
                f'1.5-1.5 2.8-3.5 4.3z" fill="{col}"/>', 62)
    if icon == "link":
        return (f'<circle cx="30" cy="28" r="13" fill="none" stroke="{col}" stroke-width="2"/>'
                f'<path d="M25 33L35 23M27 23h8v8" fill="none" stroke="{col}" stroke-width="1.8" '
                'stroke-linecap="round" stroke-linejoin="round"/>', 56)
    # default: globe
    return (f'<circle cx="30" cy="28" r="13" fill="none" stroke="{col}" stroke-width="2"/>'
            f'<ellipse cx="30" cy="28" rx="5.5" ry="13" fill="none" stroke="{col}" stroke-width="1.6"/>'
            f'<path d="M17 28h26" stroke="{col}" stroke-width="1.6"/>', 56)


def contact_svg(item: dict, n: int, i: int, T: dict) -> str:
    W, CW, offs = grid(n)
    label = req(item, "label", "a contact")
    text = req(item, "text", f'contact "{label}"')
    accent2 = item.get("style") == "accent2"
    col = T["accent2"] if accent2 else T["accent"]
    icon, tx = icon_svg(item.get("icon", "web"), col)
    if len(text) * 16 * 0.56 > CW - tx - 14:
        warn(f'contact "{label}": text is probably too long for {n} button(s)')
    return "\n".join([
        svg_open(W, 56, f"{label}: {text}"),
        f'<g transform="translate({num(offs[i])} 0)">',
        f'<rect width="{num(CW)}" height="56" rx="14" fill="{T["panel"]}"/>',
        f'<rect x="0.5" y="0.5" width="{num(CW - 1)}" height="55" rx="13.5" fill="none" '
        f'stroke="{col}" stroke-opacity="{0.55 if accent2 else 0.5}"/>',
        icon,
        f'<text x="{tx}" y="23" font-family="{MONO}" font-size="10" '
        f'fill="{T["accent2_muted"] if accent2 else T["text_muted"]}" letter-spacing="2">'
        f'{esc(label.upper())}</text>',
        f'<text x="{tx}" y="42" font-family="{SANS}" font-size="16" font-weight="600" '
        f'fill="{T["accent2_text"] if accent2 else T["text"]}">{esc(text)}</text>',
        "</g>", "</svg>", ""])


def build_contacts(cfg: dict, T: dict, files: dict) -> str:
    c = cfg.get("contacts") or {}
    items = c.get("items", [])
    if not items:
        return ""
    columns = int(c.get("columns", MAX_COLUMNS))
    rows_html = []
    for row in balanced_rows(items, min(columns, MAX_COLUMNS)):
        n = len(row)
        imgs = []
        for i, it in enumerate(row):
            name = f"contact-{slug(it.get('id') or it.get('icon') or it.get('label', 'link'))}.svg"
            files[f"assets/{name}"] = contact_svg(it, n, i, T)
            alt = f"{it['label']}: {it['text']}"
            imgs.append(f'<a href="{attr(req(it, "url", "a contact"))}"><img src="assets/{name}" '
                        f'alt="{attr(alt)}" width="{pct(n)}"></a>')
        rows_html.append('<p align="center">' + "".join(imgs) + "</p>")
    return "\n\n".join(rows_html)


# ------------------------------------------------------------------- stats
def stat_url(kind: str, user: str, T: dict, s: dict) -> tuple[str, str, str]:
    c = {k: v.lstrip("#") for k, v in T.items()}
    if kind == "stats":
        u = (f"https://github-readme-stats.vercel.app/api?username={user}&show_icons=true"
             f"&card_width=495&line_height=20&border_radius=20&bg_color={c['panel']}"
             f"&border_color={c['border']}&title_color={c['accent']}&text_color={c['text_soft']}"
             f"&icon_color={c['accent2']}&ring_color={c['accent']}")
        if s.get("hide_rank"):
            u += "&hide_rank=true"
        return u, f"GitHub statistics for {user}", f"https://github.com/{user}?tab=repositories"
    if kind == "langs":
        u = (f"https://github-readme-stats.vercel.app/api/top-langs/?username={user}"
             f"&layout=compact&card_width=495&langs_count={int(s.get('langs_count', 8))}"
             f"&border_radius=20&bg_color={c['panel']}&border_color={c['border']}"
             f"&title_color={c['accent']}&text_color={c['text_soft']}")
        return u, "Most used languages", f"https://github.com/{user}"
    if kind == "streak":
        h = int(s.get("streak_height", 140))
        u = (f"https://streak-stats.demolab.com/?user={user}&background={c['panel'].upper()}"
             f"&border={c['border'].upper()}&border_radius=18&stroke={c['border'].upper()}"
             f"&ring={c['accent'].upper()}&fire={c['accent2'].upper()}"
             f"&currStreakNum={c['text'].upper()}&currStreakLabel={c['accent'].upper()}"
             f"&sideNums={c['text'].upper()}&sideLabels={c['text_soft'].upper()}"
             f"&dates={c['text_muted'].upper()}&card_width=900&card_height={h}&width=900&height={h}")
        return u, "Contribution streak", f"https://github.com/{user}"
    raise ConfigError(f'stats: unknown widget "{kind}" (use stats, langs, streak)')


def build_stats(cfg: dict, T: dict) -> str:
    s = cfg.get("stats") or {}
    rows = s.get("rows", [])
    user = cfg["user"]
    out = []
    for row in rows:
        n = len(row)
        width = "100%" if n == 1 else num(100 / n - 0.4) + "%"
        imgs = []
        for kind in row:
            url, alt, link = stat_url(kind, user, T, s)
            imgs.append(f'<a href="{link}"><img src="{url}" alt="{attr(alt)}" width="{width}"></a>')
        out.append('<p align="center">' + " ".join(imgs) + "</p>")
    return "\n\n".join(out)


# ------------------------------------------------------------------ readme
def build_all(cfg: dict) -> dict[str, str]:
    for key in ("user",):
        req(cfg, key, "root")
    T = dict(DEFAULT_THEME)
    T.update(cfg.get("theme", {}))
    sections = cfg.get("sections", DEFAULT_SECTIONS)
    files: dict[str, str] = {}
    parts: list[str] = []
    user = cfg["user"]

    for sec in sections:
        if sec == "hero":
            files["assets/hero.svg"] = hero_svg(cfg, T)
            h = cfg["hero"]
            alt = f"{h['title']} — {h.get('tagline', '')}".strip(" —")
            img = f'<img src="assets/hero.svg" alt="{attr(alt)}" width="100%">'
            if h.get("link"):
                img = f'<a href="{attr(h["link"])}">{img}</a>'
            parts.append(f'<p align="center">{img}</p>')
        elif sec == "about":
            files["assets/profile.svg"] = about_svg(cfg, T)
            a = cfg["about"]
            parts.append('<p align="center"><img src="assets/profile.svg" alt="'
                         f'{attr(a["name"] + ". " + a.get("text", ""))}" width="100%"></p>')
        elif sec == "featured":
            parts.append(build_featured(cfg, T, files))
        elif sec == "stack":
            files["assets/stack.svg"] = stack_svg(cfg, T)
            parts.append('<p align="center"><img src="assets/stack.svg" alt="Stack" width="100%"></p>')
        elif sec == "principle":
            files["assets/principle.svg"] = principle_svg(cfg, T)
            p = cfg["principle"]
            parts.append('<p align="center"><img src="assets/principle.svg" alt="'
                         f'{attr(p["title"])}" width="100%"></p>')
        elif sec == "stats":
            parts.append(build_stats(cfg, T))
        elif sec == "contacts":
            parts.append(build_contacts(cfg, T, files))
        elif sec == "footer":
            if cfg.get("footer"):
                parts.append(f'<p align="center"><sub>{html.escape(cfg["footer"])}</sub></p>')
        elif sec == "views":
            v = cfg.get("views") or {}
            if v.get("enabled", True):
                label = urllib.parse.quote_plus(v.get("label", "profile views"))
                url = (f"https://komarev.com/ghpvc/?username={user}&style=flat-square"
                       f"&label={label}&color={T['accent2'].lstrip('#')}"
                       f"&labelColor={T['panel'].lstrip('#')}")
                if v.get("base"):
                    url += f"&base={int(v['base'])}"
                parts.append(f'<p align="center"><img src="{url}" alt="Profile views"></p>')
        else:
            raise ConfigError(f'sections: unknown section "{sec}"')

    banner = ("<!-- Generated by generator/build.py from profile.json. "
              "Edit profile.json, not this file. -->")
    files["README.md"] = banner + "\n\n" + "\n\n".join(p for p in parts if p) + "\n"
    return files


def load_config() -> dict:
    try:
        raw = json.loads(CONFIG.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"{CONFIG} not found")
    except json.JSONDecodeError as e:
        raise ConfigError(f"profile.json is not valid JSON: {e}")
    return {k: v for k, v in raw.items() if not k.startswith("$")}


def write_outputs(files: dict[str, str]) -> None:
    changed = 0
    for rel, content in files.items():
        path = ROOT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8", newline="\n")
            changed += 1
            print(f"  wrote {rel}")
    # remove generated cards/contacts that are no longer in the config
    for pattern in ("card-*.svg", "contact-*.svg"):
        for old in ASSETS.glob(pattern):
            if f"assets/{old.name}" not in files:
                old.unlink()
                changed += 1
                print(f"  removed assets/{old.name}")
    print(f"{changed} file(s) changed, {len(files)} generated.")


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "build"
    if cmd not in ("build", "check"):
        print(__doc__)
        return 2
    try:
        files = build_all(load_config())
    except ConfigError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    for w in WARNINGS:
        print(f"WARNING: {w}", file=sys.stderr)
    if cmd == "check":
        if WARNINGS:
            return 1
        print(f"OK: {len(files)} file(s) would be generated.")
        return 0
    write_outputs(files)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
