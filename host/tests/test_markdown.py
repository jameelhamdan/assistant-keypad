from keypad.core.markdown import BOLD as B
from keypad.core.markdown import CODE as C
from keypad.core.markdown import DIM as D
from keypad.core.markdown import device_text, render


def lines(md: str, **kw) -> list[str]:
    return render(md, **kw).split("\n")


def test_inline_styles():
    assert render("a **b** *c* `d` e") == f"a {B}b{B} {D}c{D} {C}d{C} e"
    assert render("### **Bold** heading `x`") == f"{B}Bold heading {C}x{C}{B}"


def test_heading_bold_with_code_inside():
    assert render("### Fix `x` now") == f"{B}Fix {C}x{C} now{B}"  # bold stays on while code toggles


def test_links_keep_their_address():
    assert render("[docs](https://example.com/a/b)") == f"docs{D} (example.com/a/b){D}"
    assert render("[https://x.io](https://x.io)") == "https://x.io"  # the text already is the address
    assert render("[see](#anchor)") == "see"


def test_lists_wrap_with_a_hanging_indent():
    md = "1. " + "word " * 14 + "end\n2. two\n   - nested *x*\n- bullet"
    out = lines(md, width=30)
    assert out[0].startswith("1. word")
    assert out[1].startswith("   word")  # continuation under the text, not the number
    assert all(len(ln.replace(B, "").replace(C, "").replace(D, "")) <= 30 for ln in out)
    assert "2. two" in out and f"   - nested {D}x{D}" in out and "- bullet" in out


def test_numbered_list_keeps_its_start():
    assert lines("3. a\n4. b") == ["3. a", "4. b"]


def test_blockquote_is_dim():
    assert lines("> one\n> two") == [f"{D}> one{D}", f"{D}> two{D}"]


def test_table_aligned():
    out = lines("| Name | Qty |\n|---|---|\n| apples | 3 |\n| pears | 12 |")
    assert [ln.replace(B, "").replace(D, "") for ln in out] == [
        "Name   | Qty", "-------+----", "apples | 3", "pears  | 12"]


def test_wide_table_becomes_key_value_rows():
    md = "| Name | Description |\n|---|---|\n| a | " + "long " * 12 + "|\n| b | short |"
    out = render(md, width=30)
    assert f"{B}Description: {B}" in out and "short" in out and "|" not in out


def test_code_fence_keeps_indent_and_drops_language():
    out = lines("```python\ndef f():\n    return 1\n```")
    assert out == [f"{C}def f():{C}", f"{C}    return 1{C}"]


def test_unclosed_fence_and_stray_markers_do_not_break():
    assert render("```\nx") == f"{C}x{C}"
    assert render("a `b") == "a `b"
    assert render("\x01raw\x02\x03") == "raw"


def test_rule_and_paragraph_spacing():
    out = lines("one\n\n\n\ntwo\n\n---\n\nthree")
    assert out == ["one", "", "two", "", f"{D}{'-' * 24}{D}", "", "three"]


def test_every_line_closes_its_styles():
    out = render("**bold\nacross** and *dim\nlines*\n\n- **a**\n  *b*")
    for ln in out.split("\n"):
        for m in (B, C, D):
            assert ln.count(m) % 2 == 0, ln


def test_unreadable_characters_become_something_readable():
    assert device_text("“quoted” — a… → b ✓") == '"quoted" - a... -> b v'
    assert device_text("emoji \U0001f600 here") == "emoji  here"
    assert device_text("日本語 text") == "? text"  # a run of unsupported characters is one "?"
    assert device_text("┌─┐ │") == "+-+ |"
    assert device_text("café") == "café" and device_text("a\x1b[0mb") == "a[0mb"


def test_long_message_is_cut_on_a_line_boundary():
    md = "\n\n".join(f"Paragraph number {i} with some words." for i in range(200))
    out = render(md, max_bytes=500)
    assert len(out.encode()) <= 500 and out.endswith("\n...")
    assert out.split("\n")[-2].startswith("Paragraph") or out.split("\n")[-2] == ""


def test_real_claude_replies_render_cleanly():
    """Replies Claude actually wrote (headings, tables, nested lists, code, bold): no raw Markdown left, styles balanced,
    nothing the keypad cannot draw, nothing wider than the screen outside code blocks."""
    import json
    import re
    from pathlib import Path

    from keypad.core.markdown import WIDTH

    replies = json.loads((Path(__file__).parent / "data" / "real_replies.json").read_text(encoding="utf-8"))
    assert len(replies) >= 3
    for md in replies:
        out = render(md)
        assert out, md[:60]
        for ln in out.split("\n"):
            plain = re.sub("[\x01\x02\x03]", "", ln)
            for m in (B, C, D):
                assert ln.count(m) % 2 == 0, ln
            assert all(ch in "\n" or 0x20 <= ord(ch) < 0x7F or 0xA0 <= ord(ch) <= 0xFF for ch in plain), ln
            assert not plain.lstrip().startswith(("#", "|--", "```")) and "**" not in plain, ln
            if C not in ln:
                assert len(plain) <= WIDTH, plain
