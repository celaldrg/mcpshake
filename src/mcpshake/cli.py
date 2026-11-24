"""Command line interface: mcpshake [options] -- <server command...>"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from typing import List, Optional

from . import __version__
from .client import StdioServer
from .runner import RULES, SEVERITY, Options, RunReport, Runner, ToolReport, short_json

USAGE = "mcpshake [options] -- <server command> [args...]"

EPILOG = """examples:
  mcpshake -- python3 server.py
  mcpshake --cases 100 --seed 7 -- npx -y @modelcontextprotocol/server-everything
  mcpshake --tools 'search_*' --timeout 3 --json -- uv run my-server
  mcpshake --rules            list every finding mcpshake can report

Tools annotated destructiveHint: true are skipped unless you pass
--include-destructive. Run mcpshake against a sandbox, not production data.
"""


class Style:
    def __init__(self, on: bool):
        self.on = on

    def _w(self, code: str, s: str) -> str:
        return f"\x1b[{code}m{s}\x1b[0m" if self.on else s

    def b(self, s): return self._w("1", s)
    def dim(self, s): return self._w("2", s)
    def red(self, s): return self._w("31", s)
    def yellow(self, s): return self._w("33", s)
    def green(self, s): return self._w("32", s)
    def cyan(self, s): return self._w("36", s)
    def blue(self, s): return self._w("34", s)

    def sev(self, sev: str, s: str) -> str:
        return {"error": self.red, "warn": self.yellow, "info": self.blue}[sev](s)


ICON = {"error": "✗", "warn": "!", "info": "i"}


def parse(argv: List[str]):
    if "--" in argv:
        i = argv.index("--")
        own, cmd = argv[:i], argv[i + 1 :]
    else:
        own, cmd = argv, []
    p = argparse.ArgumentParser(prog="mcpshake", usage=USAGE, epilog=EPILOG,
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                description="Property-based tests for MCP servers: call every tool with "
                                "schema-generated valid and invalid arguments and report what breaks.")
    p.add_argument("--cases", type=int, default=30, metavar="N", help="valid cases per tool (default 30; invalid cases are added on top)")
    p.add_argument("--seed", type=int, default=1, help="random seed; the same seed replays the same cases (default 1)")
    p.add_argument("--timeout", type=float, default=10.0, metavar="SEC", help="per-call timeout in seconds (default 10)")
    p.add_argument("--slow", type=float, default=1000.0, metavar="MS", help="report tools whose p95 latency is above this (default 1000)")
    p.add_argument("--tools", action="append", default=[], metavar="GLOB", help="only these tools (comma list or repeat)")
    p.add_argument("--skip", action="append", default=[], metavar="GLOB", help="skip these tools (comma list or repeat)")
    p.add_argument("--include-destructive", action="store_true", help="also call tools annotated destructiveHint: true")
    p.add_argument("--env", action="append", default=[], metavar="KEY=VALUE", help="extra environment variable for the server")
    p.add_argument("--cwd", help="working directory for the server")
    p.add_argument("--fail-on", choices=["error", "warn", "info", "never"], default="error", help="exit 1 at this severity or above (default error)")
    p.add_argument("--json", action="store_true", help="machine-readable report on stdout")
    p.add_argument("--github", action="store_true", help="also print GitHub Actions annotations")
    p.add_argument("--quiet", "-q", action="store_true", help="only print tools with findings and the summary")
    p.add_argument("--no-color", action="store_true", help="disable colours (NO_COLOR is honoured too)")
    p.add_argument("--rules", action="store_true", help="list the findings mcpshake reports and exit")
    p.add_argument("--version", action="version", version=f"mcpshake {__version__}")
    args = p.parse_args(own)
    if len(cmd) == 1 and " " in cmd[0]:
        cmd = shlex.split(cmd[0])
    return p, args, cmd


def _globs(vals: List[str]) -> List[str]:
    return [g.strip() for v in vals for g in v.split(",") if g.strip()]


def print_rules(st: Style) -> None:
    for rule, (sev, desc) in RULES.items():
        print(f"  {st.sev(sev, f'{sev:<5}')}  {rule:<18} {desc}")


def tool_block(tr: ToolReport, st: Style, quiet: bool, width: int = 18) -> List[str]:
    lines: List[str] = []
    if tr.skipped:
        if not quiet and tr.skipped != "not selected":
            lines.append(f"  {st.dim('-')} {st.b(tr.name.ljust(width))} {st.dim('skipped: ' + tr.skipped)}")
        return lines
    sev_counts = {"error": 0, "warn": 0, "info": 0}
    for f in tr.findings:
        sev_counts[f.severity] += 1
    p95 = tr.p95()
    stats = f"{tr.calls} calls" + (f" · p95 {p95:.0f}ms" if p95 is not None else "")
    if sev_counts["error"]:
        head = st.red("✗")
    elif sev_counts["warn"]:
        head = st.yellow("!")
    else:
        head = st.green("✓")
    if quiet and not tr.findings:
        return lines
    name = tr.name.ljust(width)
    lines.append(f"  {head} {st.b(name)} {st.dim(stats)}" + (f"  {st.yellow(tr.stopped)}" if tr.stopped else ""))
    order = sorted(tr.findings, key=lambda f: -SEVERITY[f.severity])
    for f in order:
        times = f" ({f.count}×)" if f.count > 1 else ""
        lines.append(f"      {st.sev(f.severity, ICON[f.severity] + ' ' + f.rule.ljust(17))} {f.message}{st.dim(times)}")
        if f.rule in ("invalid-accepted", "unhandled-invalid") and f.kinds:
            more = f" (+{len(f.kinds) - 2} more)" if len(f.kinds) > 2 else ""
            lines.append(f"          {st.dim('case')} {'; '.join(f.kinds[:2])}{st.dim(more)}")
        if f.args is not None:
            lines.append(f"          {st.dim('args')} {st.cyan(short_json(f.args, 84))}")
        if f.detail:
            lines.append(f"          {st.dim('got ')} {f.detail}")
    return lines


def human(rep: RunReport, cmd: List[str], opts: Options, st: Style, quiet: bool) -> str:
    out: List[str] = []
    out.append(f"{st.b('mcpshake')} {__version__} {st.dim('·')} {' '.join(cmd)}")
    if rep.server_name or rep.protocol:
        tested = sum(1 for t in rep.tools if not t.skipped)
        out.append(st.dim(f"server {rep.server_name} {rep.server_version} · protocol {rep.protocol or '?'} · "
                          f"{len(rep.tools)} tools ({tested} tested) · seed {opts.seed} · {opts.cases} valid cases/tool"))
    out.append("")
    width = min(30, max([len(t.name) for t in rep.tools] + [12]))
    for tr in rep.tools:
        out += tool_block(tr, st, quiet, width)
    if rep.fatal:
        out.append("")
        out.append(st.red("fatal: " + rep.fatal))
    c = rep.counts()
    calls = sum(t.calls for t in rep.tools)
    parts = [
        st.red(f"{c['error']} errors") if c["error"] else st.green("0 errors"),
        st.yellow(f"{c['warn']} warnings") if c["warn"] else "0 warnings",
        f"{c['info']} info",
    ]
    out.append("")
    out.append(f"{len(rep.tools)} tools · {calls} calls · " + " · ".join(parts)
               + f" · {rep.restarts} restarts · {rep.elapsed:.1f}s")
    return "\n".join(out)


def as_json(rep: RunReport, cmd: List[str], opts: Options) -> str:
    return json.dumps({
        "version": __version__,
        "command": cmd,
        "seed": opts.seed,
        "server": {"name": rep.server_name, "version": rep.server_version, "protocolVersion": rep.protocol},
        "summary": {**rep.counts(), "calls": sum(t.calls for t in rep.tools), "restarts": rep.restarts,
                    "elapsedSeconds": round(rep.elapsed, 3)},
        "fatal": rep.fatal or None,
        "tools": [{
            "name": t.name,
            "skipped": t.skipped or None,
            "stopped": t.stopped or None,
            "calls": t.calls,
            "validCalls": t.valid_calls,
            "invalidCalls": t.invalid_calls,
            "p95ms": None if t.p95() is None else round(t.p95(), 1),
            "findings": [f.to_dict() for f in t.findings],
        } for t in rep.tools],
    }, indent=2, ensure_ascii=False)


def github(rep: RunReport) -> str:
    lvl = {"error": "error", "warn": "warning", "info": "notice"}
    lines = []
    for f in rep.findings():
        msg = f"{f.message} args={short_json(f.args, 200)}".replace("%", "%25").replace("\n", "%0A")
        lines.append(f"::{lvl[f.severity]} title=mcpshake {f.rule} ({f.tool})::{msg}")
    if rep.fatal:
        lines.append(f"::error title=mcpshake::{rep.fatal}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    p, a, cmd = parse(argv)
    color = not a.no_color and not os.environ.get("NO_COLOR") and (sys.stdout.isatty() or os.environ.get("FORCE_COLOR", "0") not in ("", "0"))
    st = Style(color and not a.json)
    if a.rules:
        print_rules(st)
        return 0
    if not cmd:
        p.print_usage(sys.stderr)
        print("mcpshake: error: give the server command after --", file=sys.stderr)
        return 2
    env = {}
    for kv in a.env:
        if "=" not in kv:
            print(f"mcpshake: error: --env expects KEY=VALUE, got {kv!r}", file=sys.stderr)
            return 2
        k, v = kv.split("=", 1)
        env[k] = v
    opts = Options(cases=max(1, a.cases), seed=a.seed, timeout=a.timeout, slow_ms=a.slow,
                   include=_globs(a.tools), exclude=_globs(a.skip), include_destructive=a.include_destructive)
    server = StdioServer(cmd, env=env, cwd=a.cwd)
    rep = Runner(server, opts).run()
    if a.json:
        print(as_json(rep, cmd, opts))
    else:
        print(human(rep, cmd, opts, st, a.quiet))
    if a.github:
        gh = github(rep)
        if gh:
            print(gh)
    if rep.fatal and not rep.tools:
        return 2
    if a.fail_on == "never":
        return 1 if rep.fatal else 0
    worst = max((SEVERITY[f.severity] for f in rep.findings()), default=0)
    return 1 if worst >= SEVERITY[a.fail_on] or rep.fatal else 0


if __name__ == "__main__":
    sys.exit(main())
