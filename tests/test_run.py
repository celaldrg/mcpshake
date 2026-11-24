import json
import os
import sys

import pytest

from mcpshake.cli import main
from mcpshake.client import StdioServer
from mcpshake.runner import Options, Runner

HERE = os.path.dirname(os.path.abspath(__file__))
EX = os.path.join(HERE, "..", "examples")
BUGGY = [sys.executable, os.path.join(EX, "notes_server.py")]
FIXED = [sys.executable, os.path.join(EX, "notes_server_fixed.py")]


def run(cmd, **kw):
    opts = Options(timeout=1.0, **kw)
    return Runner(StdioServer(cmd), opts).run()


@pytest.fixture(scope="module")
def buggy():
    return run(BUGGY)


def rules(rep, tool):
    t = next(t for t in rep.tools if t.name == tool)
    return {f.rule for f in t.findings}


def test_finds_every_planted_bug(buggy):
    assert buggy.server_name == "notes-demo"
    assert buggy.protocol == "2025-11-25"
    assert {"internal-error", "leaks-trace", "invalid-accepted", "unhandled-invalid"} <= rules(buggy, "search_notes")
    assert "crash" in rules(buggy, "get_note")
    assert {"stdout-noise", "output-schema"} <= rules(buggy, "word_stats")
    assert "timeout" in rules(buggy, "summarize")
    assert buggy.restarts >= 2


def test_destructive_tools_are_skipped(buggy):
    t = next(t for t in buggy.tools if t.name == "delete_note")
    assert t.skipped.startswith("destructiveHint") and t.calls == 0


def test_fixed_server_is_clean():
    rep = run(FIXED)
    assert not rep.fatal
    assert rep.findings() == []
    assert sum(t.calls for t in rep.tools) > 100


def test_tool_filters():
    rep = run(FIXED, include=["search_*"])
    tested = [t.name for t in rep.tools if not t.skipped]
    assert tested == ["search_notes"]


def test_cli_exit_codes_and_json(capsys):
    assert main(["--timeout", "1", "--json", "--tools", "word_stats", "--"] + BUGGY) == 1
    data = json.loads(capsys.readouterr().out)
    ws = next(t for t in data["tools"] if t["name"] == "word_stats")
    assert any(f["rule"] == "output-schema" and f["severity"] == "error" for f in ws["findings"])
    assert main(["--timeout", "1", "--"] + FIXED) == 0
    capsys.readouterr()
    assert main(["--timeout", "1", "--fail-on", "never", "--tools", "word_stats", "--"] + BUGGY) == 0


def test_server_that_cannot_start(capsys):
    code = main(["--timeout", "1", "--", sys.executable, "-c", "import sys; sys.exit(4)"])
    assert code == 2
    assert "could not start the server" in capsys.readouterr().out


def test_usage_without_command(capsys):
    assert main([]) == 2
    assert main(["--rules"]) == 0
    assert "stdout-noise" in capsys.readouterr().out


def test_task_required_tools_are_skipped():
    from mcpshake.runner import needs_task

    assert needs_task({"name": "t", "execution": {"taskSupport": "required"}})
    assert not needs_task({"name": "t", "execution": {"taskSupport": "optional"}})
    assert not needs_task({"name": "t"})
