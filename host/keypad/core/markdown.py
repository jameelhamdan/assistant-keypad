"""Claude's Markdown as the keypad draws it.

The keypad has a fixed-width font and three styles, switched by marker
characters in the text: BOLD, CODE and DIM. This module parses real CommonMark
(markdown-it-py, with tables) and lays it out the way Claude Code's terminal
does, to a given width:

  headings        bold, a blank line before
  **bold**        bold                 *italic*  dim
  `code`, fences  code (a fence keeps its indentation; the language tag goes)
  - lists         "- " and "1. ", nested ones indented, wrapped with a hanging indent
  > quotes        "> " and dim
  tables          aligned columns, or "Header: value" rows when they do not fit
  [text](url)     text (host/path)
  ---             a dim rule

Styles are closed at the end of every line, so a line can be cut or wrapped
without breaking the ones around it. Text the keypad's font cannot draw is
mapped to something readable (see device_text).
"""

from __future__ import annotations

import re
import unicodedata

from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode

BOLD, CODE, DIM = "\x01", "\x02", "\x03"
_B, _C, _D = 1, 2, 4  # style bits
_MARKS = {_B: BOLD, _C: CODE, _D: DIM}
WIDTH = 50  # cells of the keypad's transcript (firmware LOG_CELLS)

_MD = MarkdownIt("commonmark").enable("table")

Seg = tuple[str, int]  # text, style bits
Line = list[Seg]


# ---- text the keypad's font cannot draw ----------------------------------------

_MAP = {
    "‘": "'", "’": "'", "‚": "'", "′": "'", "“": '"', "”": '"', "„": '"', "″": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-", "…": "...",
    "•": "·", "‣": "·", "◦": "·", "▪": "·", "●": "·", "○": "o",
    "■": "#", "□": "#", "→": "->", "←": "<-", "↑": "^", "↓": "v", "⇒": "=>",
    "⇐": "<=", "↔": "<->", "✓": "v", "✔": "v", "✗": "x", "✘": "x", "✕": "x",
    "✖": "x", "≤": "<=", "≥": ">=", "≠": "!=", "≈": "~",
    "─": "-", "━": "-", "│": "|", "┃": "|", "┌": "+", "┐": "+", "└": "+",
    "┘": "+", "├": "+", "┤": "+", "┬": "+", "┴": "+", "┼": "+", "═": "=",
    "║": "|", "╔": "+", "╗": "+", "╚": "+", "╝": "+", "╠": "+", "╣": "+",
    "╦": "+", "╩": "+", "╬": "+", "▶": ">", "◀": "<", "▲": "^", "▼": "v",
    " ": " ", " ": " ", " ": " ",   # thin spaces
    "​": "", "‌": "", "‍": "", "﻿": "", "️": "",   # zero-width characters, emoji selector
}


def _drawable(ch: str) -> bool:
    o = ord(ch)
    return 0x20 <= o < 0x7F or 0xA0 <= o <= 0xFF or ch in "\n" + BOLD + CODE + DIM


def device_text(s: str) -> str:
    """s with every character the keypad's font cannot draw replaced by an
    ASCII look-alike, or (emoji, accents on their own) dropped. A run of other
    characters (CJK, Arabic, ...) becomes one "?" instead of a string of them."""
    out: list[str] = []
    unknown = False
    for ch in s:
        ch = _MAP.get(ch, ch)
        for c in ch:
            if _drawable(c):
                out.append(c)
                unknown = False
            elif c == "\t":
                out.append(" ")
                unknown = False
            elif unicodedata.category(c) == "Cc" or (
                    unicodedata.category(c) in ("Mn", "Me", "Cf", "So", "Sk") and ord(c) > 0x2000):
                continue  # control characters, combining marks, format characters, emoji and symbols
            elif not unknown:
                out.append("?")
                unknown = True
    return "".join(out)


# ---- inline ---------------------------------------------------------------------


def _width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _short_url(href: str) -> str:
    u = re.sub(r"^https?://(www\.)?", "", href).rstrip("/")
    return u if len(u) <= 32 else u[:31] + "…"


def _inline(node: SyntaxTreeNode, base: int = 0) -> Line:
    """The segments of an inline node; a "\\n" segment is a line break."""
    segs: Line = []
    for ch in node.children:
        t = ch.type
        if t == "text":
            segs.append((ch.content, base))
        elif t == "code_inline":
            segs.append((ch.content, base | _C))
        elif t in ("softbreak", "hardbreak"):
            segs.append(("\n", base))
        elif t == "strong":
            segs += _inline(ch, base | _B)
        elif t == "em":
            segs += _inline(ch, base | _D)
        elif t == "link":
            inner = _inline(ch, base)
            segs += inner
            href, shown = str(ch.attrs.get("href", "")), "".join(s for s, _ in inner)
            if re.match(r"https?://", href) and _short_url(href) not in shown and href.rstrip("/") != shown.rstrip("/"):
                segs.append((" (" + _short_url(href) + ")", _D))
        elif t == "image":
            segs.append((ch.content or "[image]", base))
        elif t in ("html_inline", "html_block"):
            segs.append((ch.content, base))
        elif ch.children:  # anything else that nests (strikethrough, ...): its text
            segs += _inline(ch, base)
    return segs


# ---- layout ---------------------------------------------------------------------


def _split_lines(segs: Line) -> list[Line]:
    lines: list[Line] = [[]]
    for text, st in segs:
        parts = text.split("\n")
        for i, p in enumerate(parts):
            if i:
                lines.append([])
            if p:
                lines[-1].append((p, st))
    return lines


def _wrap(line: Line, width: int) -> list[Line]:
    """Greedy word wrap of one styled line; a word longer than a row is split."""
    rows: list[Line] = [[]]
    used = 0

    def newrow() -> None:
        nonlocal used
        while rows[-1] and not rows[-1][-1][0].strip():
            rows[-1].pop()
        rows.append([])
        used = 0

    for text, st in line:
        for w in re.findall(r"\s+|\S+", text):
            if w.isspace():
                if used:  # leading spaces of a row are dropped
                    rows[-1].append((w, st))
                    used += _width(w)
                continue
            n = _width(w)
            if used + n <= width:
                rows[-1].append((w, st))
                used += n
                continue
            if used and n <= width:
                newrow()
                rows[-1].append((w, st))
                used = n
                continue
            while w:  # longer than a row: fill rows up to the edge
                if used >= width or (used and width - used < 8):
                    newrow()
                take, got = 0, 0
                while take < len(w) and got + _width(w[take]) <= width - used:
                    got += _width(w[take])
                    take += 1
                take = max(take, 1)
                rows[-1].append((w[:take], st))
                used += _width(w[:take])
                w = w[take:]
    while rows[-1] and not rows[-1][-1][0].strip():
        rows[-1].pop()
    return rows


def _prefixed(rows: list[Line], first: str, rest: str, st: int = 0) -> list[Line]:
    out = []
    for i, r in enumerate(rows):
        p = first if i == 0 else rest
        out.append(([(p, st)] if p else []) + r)
    return out


def _text_block(node: SyntaxTreeNode, width: int, base: int = 0) -> list[Line]:
    rows: list[Line] = []
    for ln in _split_lines(_inline(node, base)):
        rows.extend(_wrap(ln, width) if ln else [[]])
    return rows


def _plain(node: SyntaxTreeNode) -> str:
    return "".join(s for s, _ in _inline(node)).replace("\n", " ").strip()


def _table(node: SyntaxTreeNode, width: int) -> list[Line]:
    rows: list[list[str]] = []
    n_head = 0
    for part in node.children:  # thead, tbody
        for tr in part.children:
            rows.append([_plain(c.children[0]) if c.children else "" for c in tr.children])
        if part.type == "thead":
            n_head = len(rows)
    cols = max((len(r) for r in rows), default=0)
    rows = [r + [""] * (cols - len(r)) for r in rows]
    widths = [max((_width(r[i]) for r in rows), default=0) for i in range(cols)]
    if cols and sum(widths) + 3 * (cols - 1) <= width:
        def row(r: list[str], st: int) -> Line:
            line: Line = []
            for i, cell in enumerate(r):
                if i:
                    line.append((" | ", _D))
                line.append((cell + " " * (widths[i] - _width(cell)), st))
            return line

        out = [row(r, _B) for r in rows[:n_head]]
        out.append([("-+-".join("-" * w for w in widths), _D)])
        return out + [row(r, 0) for r in rows[n_head:]]
    out = []  # too wide: one "Header: value" group per row
    heads = rows[0] if n_head else [""] * cols
    for r in rows[n_head:]:
        if out:
            out.append([])
        for i, cell in enumerate(r):
            if cell:
                out.extend(_wrap([((heads[i] + ": ") if heads[i] else "", _B), (cell, 0)], width))
    return out


def _blocks(nodes: list[SyntaxTreeNode], width: int, tight: bool = False) -> list[Line]:
    out: list[Line] = []
    for n in nodes:
        part = _block(n, width)
        if out and part and not (tight and n.type in ("bullet_list", "ordered_list")):
            out.append([])  # a blank line between blocks
        out.extend(part)
    return out


def _block(n: SyntaxTreeNode, width: int) -> list[Line]:
    t = n.type
    if t == "heading":
        return _text_block(n.children[0], width, _B) if n.children else []
    if t == "paragraph":
        return _text_block(n.children[0], width) if n.children else []
    if t in ("bullet_list", "ordered_list"):
        out: list[Line] = []
        start = int(n.attrs.get("start", 1)) if t == "ordered_list" else 0
        for i, item in enumerate(n.children):
            mark = f"{start + i}. " if t == "ordered_list" else "- "
            body = _blocks(item.children, width - len(mark), tight=True)
            out.extend(_prefixed(body or [[]], mark, " " * len(mark)))
        return out
    if t == "blockquote":
        body = [[(t, st | _D) for t, st in ln] for ln in _blocks(n.children, width - 2)]
        return _prefixed(body or [[]], "> ", "> ", _D) if n.children else []
    if t in ("fence", "code_block"):
        code = n.content.rstrip("\n")
        return [[(ln, _C)] if ln.strip() else [] for ln in code.split("\n")]
    if t == "hr":
        return [[("-" * 24, _D)]]
    if t == "table":
        return _table(n, width)
    if t == "html_block":
        return [[(ln, 0)] if ln.strip() else [] for ln in n.content.strip("\n").split("\n")]
    return []


def _line_text(line: Line) -> str:
    """One line with its styles as markers, every style closed at the end."""
    line = list(line)
    while line and not line[-1][0].strip():  # no trailing spaces (table padding)
        line.pop()
    if line:
        line[-1] = (line[-1][0].rstrip(), line[-1][1])
    out: list[str] = []
    cur = 0
    for text, st in line:
        if not text:
            continue
        if st != cur:
            for bit, mark in reversed(_MARKS.items()):  # close what ends, then open what starts
                if (cur & ~st) & bit:
                    out.append(mark)
            for bit, mark in _MARKS.items():
                if (st & ~cur) & bit:
                    out.append(mark)
            cur = st
        out.append(text)
    for bit, mark in reversed(_MARKS.items()):
        if cur & bit:
            out.append(mark)
    return "".join(out)


def render(text: str, width: int = WIDTH, max_bytes: int = 0) -> str:
    """Markdown as styled keypad text. With max_bytes, whole lines are dropped
    from the end (and a "..." line added) until it fits."""
    text = re.sub(f"[{BOLD}{CODE}{DIM}\r]", "", text)
    root = SyntaxTreeNode(_MD.parse(text))
    lines = [_line_text(ln) for ln in _blocks(root.children, width)]
    while lines and not lines[-1].strip():
        lines.pop()
    out = device_text("\n".join(lines))
    out = re.sub(r"\n{3,}", "\n\n", out).strip("\n")
    if max_bytes and len(out.encode()) > max_bytes:
        keep = out.split("\n")
        while keep and len("\n".join(keep + ["..."]).encode()) > max_bytes:
            keep.pop()
        out = "\n".join(keep + ["..."])
    return out
