"""Run generated cases against every tool and turn behaviour into findings."""

from __future__ import annotations

import fnmatch
import json
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .client import CallTimeout, RpcError, ServerExited, StdioServer
from .gen import Case, build_cases
from .schema import validate

SEVERITY = {"error": 3, "warn": 2, "info": 1}

RULES = {
    "crash": ("error", "server process exited during the call"),
    "timeout": ("error", "no response within the timeout"),
    "stdout-noise": ("error", "wrote non-JSON-RPC output to stdout"),
    "internal-error": ("error", "schema-valid input raised a JSON-RPC error"),
    "bad-result": ("error", "result does not match the MCP CallToolResult shape"),
    "output-schema": ("error", "structuredContent breaks the tool's outputSchema"),
    "unhandled-invalid": ("warn", "invalid input crashed the handler instead of being rejected"),
    "invalid-accepted": ("warn", "invalid input was accepted as a success"),
    "valid-rejected": ("warn", "schema-valid input was rejected as invalid params"),
    "leaks-trace": ("warn", "error text exposes a stack trace"),
    "valid-is-error": ("info", "schema-valid input returned isError: true"),
    "slow": ("info", "p95 latency above the --slow threshold"),
}

TRACE_RE = re.compile(
    r"Traceback \(most recent call last\)|^\s+at .+\(.+:\d+:\d+\)|^\s+at [\w.$<>]+ \(|"
    r"File \".+\", line \d+|goroutine \d+ \[|panicked at|\.java:\d+\)",
    re.M,
)
CONTENT_TYPES = {"text", "image", "audio", "resource_link", "resource"}


@dataclass
class Finding:
    tool: str
    rule: str
    message: str
    args: Any = None
    detail: str = ""
    case: str = ""
    count: int = 1
    kinds: List[str] = field(default_factory=list)

    @property
    def severity(self) -> str:
        return RULES[self.rule][0]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool,
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
            "count": self.count,
            "case": self.case,
            "kinds": self.kinds,
            "args": self.args,
            "detail": self.detail,
        }


@dataclass
class ToolReport:
    name: str
    calls: int = 0
    valid_calls: int = 0
    invalid_calls: int = 0
    skipped: str = ""
    stopped: str = ""
    latencies: List[float] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)

    def p95(self) -> Optional[float]:
        if not self.latencies:
            return None
        xs = sorted(self.latencies)
        return xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))]

    def add(self, f: Finding) -> None:
        for g in self.findings:
            if g.rule == f.rule and g.message == f.message:
                g.count += 1
                if f.case and f.case not in g.kinds:
                    g.kinds.append(f.case)
                return
        if f.case:
            f.kinds = [f.case]
        self.findings.append(f)


@dataclass
class Options:
    cases: int = 30
    seed: int = 1
    timeout: float = 10.0
    slow_ms: float = 1000.0
    include: List[str] = field(default_factory=list)
    exclude: List[str] = field(default_factory=list)
    include_destructive: bool = False
    max_timeouts: int = 2
    max_crashes: int = 5


@dataclass
class RunReport:
    server_name: str = ""
    server_version: str = ""
    protocol: str = ""
    tools: List[ToolReport] = field(default_factory=list)
    restarts: int = 0
    elapsed: float = 0.0
    fatal: str = ""

    def findings(self) -> List[Finding]:
        return [f for t in self.tools for f in t.findings]

    def counts(self) -> Dict[str, int]:
        c = {"error": 0, "warn": 0, "info": 0}
        for f in self.findings():
            c[f.severity] += 1
        return c


def short_json(v: Any, limit: int = 110) -> str:
    s = json.dumps(v, ensure_ascii=False)
    s = s.replace("\n", "\\n")
    if len(s) > limit:
        s = s[: limit - 3] + "..."
    return s


def _first_line(s: Any, limit: int = 100) -> str:
    s = str(s or "")
    lines = [l for l in s.splitlines() if l.strip()]
    # prefer the exception line at the bottom of a Python traceback
    if lines and lines[0].startswith("Traceback"):
        line = lines[-1].strip()
    else:
        line = lines[0].strip() if lines else ""
    return line if len(line) <= limit else line[: limit - 3] + "..."


def _text_of(result: Dict[str, Any]) -> str:
    out = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            out.append(item["text"])
    return "\n".join(out)


def check_result_shape(result: Any) -> List[str]:
    if not isinstance(result, dict):
        return [f"result is {type(result).__name__}, not an object"]
    problems = []
    content = result.get("content")
    if not isinstance(content, list):
        problems.append("missing 'content' array")
    else:
        for i, item in enumerate(content):
            if not isinstance(item, dict):
                problems.append(f"content[{i}] is not an object")
                continue
            t = item.get("type")
            if t not in CONTENT_TYPES:
                problems.append(f"content[{i}] has unknown type {t!r}")
            elif t == "text" and not isinstance(item.get("text"), str):
                problems.append(f"content[{i}].text is not a string")
            elif t in ("image", "audio") and not (isinstance(item.get("data"), str) and isinstance(item.get("mimeType"), str)):
                problems.append(f"content[{i}] ({t}) needs string data and mimeType")
    if "isError" in result and not isinstance(result["isError"], bool):
        problems.append("'isError' is not a boolean")
    if "structuredContent" in result and not isinstance(result["structuredContent"], dict):
        problems.append("'structuredContent' is not an object")
    return problems


def is_destructive(tool: Dict[str, Any]) -> bool:
    ann = tool.get("annotations") if isinstance(tool.get("annotations"), dict) else {}
    return ann.get("destructiveHint") is True and ann.get("readOnlyHint") is not True


def needs_task(tool: Dict[str, Any]) -> bool:
    ex = tool.get("execution") if isinstance(tool.get("execution"), dict) else {}
    return ex.get("taskSupport") == "required"


def selected(name: str, opts: Options) -> bool:
    if opts.include and not any(fnmatch.fnmatchcase(name, p) for p in opts.include):
        return False
    return not any(fnmatch.fnmatchcase(name, p) for p in opts.exclude)


class Runner:
    def __init__(self, server: StdioServer, opts: Options, on_tool: Optional[Callable[[ToolReport], None]] = None):
        self.server = server
        self.opts = opts
        self.on_tool = on_tool
        self.report = RunReport()

    def _boot(self) -> None:
        self.server.start()
        info = self.server.initialize(timeout=max(self.opts.timeout, 15.0))
        si = info.get("serverInfo") if isinstance(info.get("serverInfo"), dict) else {}
        self.report.server_name = str(si.get("name", ""))
        self.report.server_version = str(si.get("version", ""))
        self.report.protocol = str(info.get("protocolVersion", ""))

    def _restart(self) -> bool:
        self.server.kill()
        self.report.restarts += 1
        try:
            self._boot()
            return True
        except Exception:
            return False

    def run(self) -> RunReport:
        t0 = time.monotonic()
        try:
            self._boot()
            tools = self.server.list_tools(timeout=max(self.opts.timeout, 15.0))
        except Exception as e:  # noqa: BLE001
            tail = " | ".join(list(self.server.stderr_tail)[-3:])
            self.report.fatal = f"could not start the server: {e}" + (f" (stderr: {tail})" if tail else "")
            self.server.kill()
            self.report.elapsed = time.monotonic() - t0
            return self.report
        rng = random.Random(self.opts.seed)
        for tool in tools:
            name = str(tool.get("name", "?"))
            tr = ToolReport(name)
            self.report.tools.append(tr)
            if not selected(name, self.opts):
                tr.skipped = "not selected"
            elif needs_task(tool):
                tr.skipped = "needs task augmentation (execution.taskSupport: required)"
            elif is_destructive(tool) and not self.opts.include_destructive:
                tr.skipped = "destructiveHint: true (use --include-destructive)"
            else:
                tool_rng = random.Random(f"{self.opts.seed}:{name}")
                try:
                    self._fuzz(tool, tr, tool_rng)
                except _Abort as e:
                    tr.stopped = str(e)
                    self.report.fatal = f"server could not be restarted after {name}"
            if self.on_tool:
                self.on_tool(tr)
            if self.report.fatal:
                break
        del rng
        self.server.stop()
        self.report.elapsed = time.monotonic() - t0
        return self.report

    def _fuzz(self, tool: Dict[str, Any], tr: ToolReport, rng: random.Random) -> None:
        o = self.opts
        schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {"type": "object"}
        out_schema = tool.get("outputSchema") if isinstance(tool.get("outputSchema"), dict) else None
        cases = build_cases(schema, o.cases, rng)
        timeouts = crashes = 0
        for case in cases:
            if not self.server.alive() and not self._restart():
                raise _Abort("server could not be restarted")
            noise_before = len(self.server.noise)
            t = time.monotonic()
            try:
                result = self.server.request("tools/call", {"name": tr.name, "arguments": case.args}, timeout=o.timeout)
                err = None
            except RpcError as e:
                result, err = None, e
            except CallTimeout:
                tr.calls += 1
                timeouts += 1
                tr.add(Finding(tr.name, "timeout", f"no response within {o.timeout:g}s", case.args, case=case.label))
                if not self._restart():
                    raise _Abort("server could not be restarted")
                if timeouts >= o.max_timeouts:
                    tr.stopped = f"stopped after {timeouts} timeouts"
                    return
                continue
            except ServerExited as e:
                tr.calls += 1
                crashes += 1
                tail = [l for l in self.server.stderr_tail if l.strip()]
                detail = tail[-1].strip() if tail else ""
                tr.add(Finding(tr.name, "crash", f"server exited with code {e.code}", case.args, detail, case.label))
                if not self._restart():
                    raise _Abort("server could not be restarted")
                if crashes >= o.max_crashes:
                    tr.stopped = f"stopped after {crashes} crashes"
                    return
                continue
            dt = (time.monotonic() - t) * 1000
            tr.calls += 1
            tr.latencies.append(dt)
            if case.valid:
                tr.valid_calls += 1
            else:
                tr.invalid_calls += 1
            time.sleep(0.01)  # let a stray stdout line land before we compare
            if len(self.server.noise) > noise_before:
                line = self.server.noise[noise_before]
                tr.add(Finding(tr.name, "stdout-noise", "non-JSON line on stdout", case.args, short_json(line, 90), case.label))
            self._classify(tr, case, result, err, out_schema)
        p95 = tr.p95()
        if p95 is not None and p95 > o.slow_ms:
            tr.add(Finding(tr.name, "slow", f"p95 {p95:.0f}ms > {o.slow_ms:g}ms"))

    def _classify(self, tr: ToolReport, case: Case, result: Any, err: Optional[RpcError], out_schema: Any) -> None:
        if err is not None:
            msg = str(err.error.get("message", ""))
            data = err.error.get("data")
            blob = msg + "\n" + (data if isinstance(data, str) else json.dumps(data) if data is not None else "")
            if TRACE_RE.search(blob):
                tr.add(Finding(tr.name, "leaks-trace", "JSON-RPC error message includes a stack trace", case.args, _first_line(blob), case.label))
            code = err.code
            if case.valid:
                if code == -32602:
                    tr.add(Finding(tr.name, "valid-rejected", "rejected with -32602 Invalid params", case.args, _first_line(blob), case.label))
                else:
                    tr.add(Finding(tr.name, "internal-error", f"JSON-RPC error {code}", case.args, _first_line(blob), case.label))
            elif code not in (-32602,) and code is not None and code != -32600:
                tr.add(Finding(tr.name, "unhandled-invalid", f"JSON-RPC error {code} instead of -32602 or isError", case.args, _first_line(blob), case.label))
            return
        problems = check_result_shape(result)
        if problems:
            tr.add(Finding(tr.name, "bad-result", problems[0], case.args, short_json(result, 90), case.label))
            return
        is_error = result.get("isError") is True
        text = _text_of(result)
        if is_error and TRACE_RE.search(text):
            tr.add(Finding(tr.name, "leaks-trace", "isError text includes a stack trace", case.args, _first_line(text), case.label))
        if case.valid and is_error:
            tr.add(Finding(tr.name, "valid-is-error", "tool reported isError for schema-valid input", case.args, _first_line(text), case.label))
        if not case.valid and not is_error:
            tr.add(Finding(tr.name, "invalid-accepted", "invalid input returned a normal result", case.args, _first_line(text), case.label))
        if out_schema is not None and not is_error:
            sc = result.get("structuredContent")
            if sc is None:
                tr.add(Finding(tr.name, "output-schema", "outputSchema declared but no structuredContent", case.args, case=case.label))
            else:
                errs = validate(sc, out_schema)
                if errs:
                    tr.add(Finding(tr.name, "output-schema", errs[0], case.args, short_json(sc, 90), case.label))


class _Abort(Exception):
    pass
