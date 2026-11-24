"""The same notes server with the bugs fixed. mcpshake reports nothing here.

    mcpshake -- python3 examples/notes_server_fixed.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from minimcp import serve, text  # noqa: E402
from notes_server import NOTES, TOOLS  # noqa: E402


def _check(cond, msg):
    if not cond:
        raise ValueError(msg)


def search_notes(query=None, limit=10, **extra):
    _check(isinstance(query, str), "query must be a string")
    _check(isinstance(limit, int) and not isinstance(limit, bool) and 1 <= limit <= 50, "limit must be an integer from 1 to 50")
    _check(not extra, "unknown arguments: " + ", ".join(extra))
    q = query.lower()
    hits = [n for n in NOTES if q in n.lower()]
    return text("\n".join(hits[:limit]) or "no matches")


def get_note(id=None, **extra):
    _check(isinstance(id, int) and not isinstance(id, bool) and id >= 0, "id must be a non-negative integer")
    _check(not extra, "unknown arguments")
    if id >= len(NOTES):
        return text(f"note {id} not found")
    return text(NOTES[id])


def word_stats(text_=None, **extra):
    _check(isinstance(text_, str), "text must be a string")
    words, chars = len(text_.split()), len(text_)
    print(f"debug: {chars} chars", file=sys.stderr)  # logs go to stderr
    return text(f"{words} words, {chars} chars", {"words": words, "chars": chars})


def summarize(text_=None, every=None, **extra):
    _check(isinstance(text_, str), "text must be a string")
    _check(isinstance(every, int) and not isinstance(every, bool) and 0 <= every <= 10, "every must be an integer from 0 to 10")
    words = text_.split()
    return text(" ".join(words[:: max(every, 1)]))


HANDLERS = {
    "search_notes": search_notes,
    "get_note": get_note,
    "word_stats": lambda text=None, **kw: word_stats(text, **kw),
    "summarize": lambda text=None, every=None, **kw: summarize(text, every, **kw),
    "delete_note": lambda **kw: text("read-only demo"),
}

if __name__ == "__main__":
    serve("notes-demo", "0.3.1", TOOLS, HANDLERS, leak_traces=False)
