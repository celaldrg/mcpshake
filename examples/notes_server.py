"""A small notes MCP server with five realistic bugs, for trying mcpshake.

    mcpshake --timeout 2 -- python3 examples/notes_server.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from minimcp import serve, text  # noqa: E402

NOTES = ["buy milk", "ship v0.1.0", "call the dentist", "café opening hours", "rotate the logs"]

TOOLS = [
    {
        "name": "search_notes",
        "description": "Search notes by text.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Text to look for"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["query"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "get_note",
        "description": "Fetch one note by its numeric id.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "integer", "minimum": 0}},
            "required": ["id"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "word_stats",
        "description": "Count words and characters in a piece of text.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {"words": {"type": "integer"}, "chars": {"type": "integer"}},
            "required": ["words", "chars"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "summarize",
        "description": "Return every n-th word of a text as a crude summary.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "every": {"type": "integer", "minimum": 0, "maximum": 10},
            },
            "required": ["text", "every"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "delete_note",
        "description": "Delete a note forever.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
        "annotations": {"destructiveHint": True},
    },
]


def search_notes(query, limit=10):
    # bug: query[0] on an empty string raises IndexError -> -32603 with a traceback
    first = query[0].lower()
    hits = [n for n in NOTES if query.lower() in n.lower() or n.lower().startswith(first)]
    # bug: limit is never validated; "42", 1.5 and 0 all slip through int()
    return text("\n".join(hits[: int(limit)]) or "no matches")


def get_note(id):
    if id >= 2**31 - 1:
        # bug: id + 1 overflows a 32-bit int in the storage layer and the process dies
        os._exit(3)
    if id >= len(NOTES):
        raise ValueError(f"note {id} not found")
    return text(NOTES[id])


def word_stats(text_):
    words = len(text_.split())
    if len(text_) > 500:
        print(f"debug: counting a long text ({len(text_)} chars)")  # bug: stdout is the protocol channel
    # bug: chars comes back as a string when the text has non-ASCII characters
    chars = len(text_) if text_.isascii() else str(len(text_))
    return text(f"{words} words, {chars} chars", {"words": words, "chars": chars})


def summarize(text_, every):
    words, out, i = text_.split(), [], 0
    while i < len(words):  # bug: every=0 never advances
        out.append(words[i])
        i += every
    return text(" ".join(out))


def delete_note(id):
    raise ValueError("read-only demo")


HANDLERS = {
    "search_notes": search_notes,
    "get_note": get_note,
    "word_stats": lambda text: word_stats(text),
    "summarize": lambda text, every: summarize(text, every),
    "delete_note": delete_note,
}

if __name__ == "__main__":
    serve("notes-demo", "0.3.0", TOOLS, HANDLERS)
