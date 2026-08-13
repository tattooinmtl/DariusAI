"""Regression guard for the chat-panel welcome text.

A previous version of `index.html` had three hint lines whose closing
double-quote (" — U+201D) was corrupted to a Unicode replacement
character (U+FFFD, rendered as `?`) plus a stray ASCII question mark.
The result was that the chat welcome text showed a `?` at the end of
each hint, e.g.:

    "add a health endpoint and a test for it??"

The same root cause had also corrupted the editor-tab dirty
indicator (a non-ASCII bullet that was meant to mark unsaved
files). Both have been fixed by replacing the corrupted run with the
intended UTF-8 character. This test pins the contract so the same
file-editing mistake cannot silently reintroduce either.

The test is deliberately textual — it asserts the source HTML
contains the proper UTF-8 glyphs and not the replacement-character
artifact. If a future commit reintroduces the corruption, this test
fails before the user sees `?` in the UI.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

INDEX = Path(__file__).resolve().parents[1] / "src" / "dariusai" / "viz" / "static" / "index.html"

# The Unicode characters we expect to see in the page.
OPEN_QUOTE = "\u201c"   # " (left double quotation mark)
CLOSE_QUOTE = "\u201d"  # " (right double quotation mark)
DIRTY_BULLET = "\u25cf"  # ● (black circle)
REPLACEMENT = "\ufffd"   # (the malformed U+FFFD we want to detect)


def _read() -> str:
    return INDEX.read_text(encoding="utf-8")


def test_no_replacement_characters_in_index_html():
    """`index.html` is saved as UTF-8 and decoded as UTF-8 by the
    browser. Any U+FFFD in the source means a previous edit encoded
    a non-ASCII character as a different encoding (typically cp1252)
    and the byte got round-tripped through UTF-8. Catch them all
    here, not just the ones we know about."""
    assert REPLACEMENT not in _read(), (
        f"index.html contains a U+FFFD replacement character. "
        f"This usually means a non-ASCII string was edited "
        f"with the wrong source encoding — replace the literal "
        f"\\uFFFD with the intended glyph."
    )


def test_chat_welcome_hints_have_curly_closing_quotes():
    """The three welcome hints are wrapped in curly quotes. The
    closing quote has been broken in the past (rendered as `?`); the
    test pins the actual UTF-8 character so the fix stays fixed.

    Each hint ends with a question mark (it's a question) followed by
    the closing curly quote (U+201D). The previous corruption was a
    U+FFFD ('?') run between the question mark and the closing quote,
    which is what the user saw as a stray `?` at the end of each
    hint in the chat panel welcome text."""
    html = _read()
    # "<hint>?<close>" — the full literal sequence — is what the
    # browser renders. The previous corruption produced a U+FFFD
    # between the `?` and the closing quote, so the test asserts
    # both characters are present and no U+FFFD between them.
    lines_we_care_about = [
        "add a health endpoint and a test for it?" + CLOSE_QUOTE,
        "why is the settings panel closing?" + CLOSE_QUOTE,
        "what do you already know about SQLite?" + CLOSE_QUOTE,
    ]
    for needle in lines_we_care_about:
        assert needle in html, f"chat welcome text missing: {needle!r}"


def test_chat_welcome_hints_have_curly_opening_quotes():
    """Pairs with the closing-quote test — the opening curly quote
    on each hint should also be U+201C, not the ASCII " or a
    replacement char."""
    html = _read()
    assert html.count(OPEN_QUOTE) >= 3, (
        "expected at least 3 U+201C opening curly quotes in the chat "
        "welcome hints (one per hint line)"
    )


def test_editor_tab_dirty_indicator_is_a_clean_utf8_character():
    """The editor tab title suffix for unsaved files used to be a
    UTF-8 bullet corrupted to U+FFFD + `?`. The fix is U+25CF (black
    circle) — the same indicator VS Code uses. Pin the character so
    a future edit can't reintroduce the corruption."""
    html = _read()
    # The dirty indicator appears in the tab-rendering code. Look
    # for the whole pattern — the inner ternary returns an empty
    # string or " ●" — so the bullet appears in the source.
    assert DIRTY_BULLET in html, (
        f"editor tab dirty indicator is missing the U+25CF (black "
        f"circle) bullet. The original was a UTF-8 character that "
        f"got misencoded; this test guards against the same "
        f"regression."
    )
