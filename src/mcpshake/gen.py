"""Generate tool arguments from a JSON Schema.

Valid cases come from walking the schema; every candidate is then checked with
our own validator, so a case is only labelled "valid" or "invalid" when the
schema really says so.
"""

from __future__ import annotations

import copy
import random
import string
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .schema import resolve, type_of, validate

WORDS = ["hello", "notes", "q3 report", "café", "x", "todo list", "main", "alpha beta"]
EDGE_STRINGS = [
    "",
    " ",
    "naïve 🚀 שלום 日本",
    "zero\u200bwidth",
    "line one\nline two",
    "quote \" ' ` ; $ \\",
    "%s %d {0} ${HOME}",
    "a" * 1000,
    "NULL",
    "-1",
]
INT_EDGES = [0, 1, -1, 2**31 - 1, -(2**31), 2**53 + 1]
NUM_EDGES = [0.0, -0.0, 0.5, -1.5, 1e-9, 1e308, 3.141592653589793]

UNSET = object()


@dataclass
class Case:
    args: Dict[str, Any]
    valid: bool
    label: str


def _uuid(rng: random.Random) -> str:
    h = "".join(rng.choice("0123456789abcdef") for _ in range(32))
    return f"{h[:8]}-{h[8:12]}-4{h[13:16]}-a{h[17:20]}-{h[20:]}"


def _ipv4(rng: random.Random) -> str:
    return ".".join(str(rng.randint(1, 254)) for _ in range(4))


FORMATS = {
    "email": lambda r: r.choice(["user@example.com", "a.b+tag@example.org"]),
    "uri": lambda r: r.choice(["https://example.com/", "https://example.com/a/b?q=1#frag"]),
    "url": lambda r: "https://example.com/path",
    "uri-reference": lambda r: "/relative/path",
    "date-time": lambda r: f"2026-0{r.randint(1, 9)}-1{r.randint(0, 9)}T0{r.randint(0, 9)}:30:00Z",
    "date": lambda r: f"2026-0{r.randint(1, 9)}-1{r.randint(0, 9)}",
    "time": lambda r: "12:30:00Z",
    "uuid": _uuid,
    "ipv4": _ipv4,
    "hostname": lambda r: "example.com",
}


class Generator:
    def __init__(self, root: Any, rng: random.Random, max_depth: int = 6):
        self.root = root
        self.rng = rng
        self.max_depth = max_depth

    # ---------------------------------------------------------------- valid
    def value(self, schema: Any, edge: bool = False, depth: int = 0, full: Optional[bool] = None) -> Any:
        r = self.rng
        if schema is True or schema is None or schema == {}:
            return r.choice(["value", 1, True]) if not edge else r.choice(EDGE_STRINGS)
        if schema is False or not isinstance(schema, dict):
            return None
        if "$ref" in schema:
            target = resolve(schema["$ref"], self.root)
            if target is not None and depth < self.max_depth:
                merged = {k: v for k, v in schema.items() if k != "$ref"}
                return self.value({**target, **merged} if isinstance(target, dict) else target, edge, depth + 1, full)
        if "const" in schema:
            return copy.deepcopy(schema["const"])
        if isinstance(schema.get("enum"), list) and schema["enum"]:
            return copy.deepcopy(r.choice(schema["enum"]))
        for comb in ("oneOf", "anyOf"):
            if isinstance(schema.get(comb), list) and schema[comb]:
                branch = r.choice(schema[comb])
                rest = {k: v for k, v in schema.items() if k != comb}
                if isinstance(branch, dict):
                    branch = {**rest, **branch}
                return self.value(branch, edge, depth + 1, full)
        if isinstance(schema.get("allOf"), list) and schema["allOf"]:
            merged = {k: v for k, v in schema.items() if k != "allOf"}
            for sub in schema["allOf"]:
                if isinstance(sub, dict) and "$ref" in sub:
                    sub = resolve(sub["$ref"], self.root) or {}
                if isinstance(sub, dict):
                    for k, v in sub.items():
                        if k == "properties":
                            merged["properties"] = {**merged.get("properties", {}), **v}
                        elif k == "required":
                            merged["required"] = list(merged.get("required", [])) + list(v)
                        else:
                            merged.setdefault(k, v)
            return self.value(merged, edge, depth + 1, full)

        t = schema.get("type")
        if isinstance(t, list):
            t = r.choice(t) if t else None
        if t is None:
            if "properties" in schema or "required" in schema:
                t = "object"
            elif "items" in schema:
                t = "array"
            elif any(k in schema for k in ("minLength", "maxLength", "pattern", "format")):
                t = "string"
            elif any(k in schema for k in ("minimum", "maximum")):
                t = "number"
            else:
                t = r.choice(["string", "integer", "boolean"])
        if "default" in schema and not edge and r.random() < 0.25:
            return copy.deepcopy(schema["default"])
        if isinstance(schema.get("examples"), list) and schema["examples"] and not edge and r.random() < 0.3:
            return copy.deepcopy(r.choice(schema["examples"]))
        return getattr(self, "_" + t, self._unknown)(schema, edge, depth, full)

    def _unknown(self, schema, edge, depth, full):
        return "value"

    def _null(self, schema, edge, depth, full):
        return None

    def _boolean(self, schema, edge, depth, full):
        return self.rng.random() < 0.5

    def _bounds(self, schema, integer):
        lo, hi = schema.get("minimum"), schema.get("maximum")
        elo, ehi = schema.get("exclusiveMinimum"), schema.get("exclusiveMaximum")
        if elo is True:
            elo, lo = lo, None
        if ehi is True:
            ehi, hi = hi, None
        step = 1 if integer else 1e-6
        if isinstance(elo, (int, float)) and not isinstance(elo, bool):
            lo = elo + step if lo is None else max(lo, elo + step)
        if isinstance(ehi, (int, float)) and not isinstance(ehi, bool):
            hi = ehi - step if hi is None else min(hi, ehi - step)
        if integer:
            import math as _m
            lo = None if lo is None else int(_m.ceil(lo))
            hi = None if hi is None else int(_m.floor(hi))
        return lo, hi

    def _integer(self, schema, edge, depth, full):
        r = self.rng
        lo, hi = self._bounds(schema, True)
        mo = schema.get("multipleOf")
        if edge:
            cands = list(INT_EDGES)
            if lo is not None:
                cands += [lo, lo + 1]
            if hi is not None:
                cands += [hi, hi - 1]
            v = r.choice(cands)
        else:
            a = lo if lo is not None else (min(-10, hi - 20) if hi is not None else -10)
            b = hi if hi is not None else a + 110
            v = r.randint(a, max(a, b))
        if isinstance(mo, int) and mo > 0:
            v = (v // mo) * mo
            if lo is not None and v < lo:
                v += mo
        return v

    def _number(self, schema, edge, depth, full):
        r = self.rng
        lo, hi = self._bounds(schema, False)
        if edge:
            cands = list(NUM_EDGES)
            if lo is not None:
                cands.append(float(lo))
            if hi is not None:
                cands.append(float(hi))
            return r.choice(cands)
        a = lo if lo is not None else -100.0
        b = hi if hi is not None else a + 200.0
        return round(r.uniform(a, max(a, b)), 3)

    def _string(self, schema, edge, depth, full):
        r = self.rng
        lo = schema.get("minLength") if isinstance(schema.get("minLength"), int) else 0
        hi = schema.get("maxLength") if isinstance(schema.get("maxLength"), int) else None
        fmt = schema.get("format")
        if fmt in FORMATS and not (edge and r.random() < 0.3):
            s = FORMATS[fmt](r)
        elif isinstance(schema.get("pattern"), str):
            s = self._pattern_guess(schema["pattern"], lo, hi)
        elif edge:
            s = r.choice(EDGE_STRINGS)
            if hi is not None and r.random() < 0.5:
                s = ("x" * hi) if hi < 10000 else s
        else:
            s = r.choice(WORDS)
        if len(s) < lo:
            s = s + "x" * (lo - len(s))
        if hi is not None and len(s) > hi:
            s = s[:hi]
        return s

    def _pattern_guess(self, pattern: str, lo: int, hi: Optional[int]) -> str:
        import re
        r = self.rng
        pools = [string.ascii_lowercase, string.digits, string.ascii_uppercase, string.ascii_lowercase + string.digits + "-_"]
        n_target = max(lo, 1)
        for _ in range(60):
            pool = r.choice(pools)
            n = r.randint(n_target, max(n_target, min(hi or 12, 12)))
            s = "".join(r.choice(pool) for _ in range(n))
            try:
                if re.search(pattern, s):
                    return s
            except re.error:
                return s
        return "x" * n_target

    def _array(self, schema, edge, depth, full):
        r = self.rng
        lo = schema.get("minItems") if isinstance(schema.get("minItems"), int) else 0
        hi = schema.get("maxItems") if isinstance(schema.get("maxItems"), int) else None
        items = schema.get("items", {})
        prefix = schema.get("prefixItems")
        if isinstance(items, list):
            prefix, items = items, schema.get("additionalItems", {})
        if depth >= self.max_depth:
            n = lo
        elif edge:
            n = r.choice([lo, hi if hi is not None else 40])
        else:
            n = r.randint(lo, max(lo, min(hi if hi is not None else 3, lo + 3)))
        out = []
        for i in range(n):
            sub = prefix[i] if isinstance(prefix, list) and i < len(prefix) else items
            if sub is False:
                break
            out.append(self.value(sub, edge and r.random() < 0.3, depth + 1))
        if schema.get("uniqueItems"):
            seen, uniq = set(), []
            import json
            for v in out:
                k = json.dumps(v, sort_keys=True)
                if k not in seen:
                    seen.add(k)
                    uniq.append(v)
            out = uniq
        return out

    def _object(self, schema, edge, depth, full):
        r = self.rng
        props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        req = [k for k in (schema.get("required") or []) if isinstance(k, str)]
        out: Dict[str, Any] = {}
        for k in req:
            out[k] = self.value(props.get(k, {}), edge and r.random() < 0.5, depth + 1)
        if depth >= self.max_depth:
            return out
        for k, sub in props.items():
            if k in out:
                continue
            include = full if full is not None else r.random() < 0.5
            if include:
                out[k] = self.value(sub, edge and r.random() < 0.5, depth + 1)
        return out

    # -------------------------------------------------------------- invalid
    def invalid_cases(self, schema: Dict[str, Any]) -> List[Case]:
        """Targeted mutations of a valid object: one broken thing per case."""
        if not isinstance(schema, dict):
            return []
        base = self.value(schema, full=False)
        if not isinstance(base, dict):
            base = {}
        props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        req = [k for k in (schema.get("required") or []) if isinstance(k, str)]
        cases: List[Case] = []
        if req:
            cases.append(Case({}, False, "no arguments at all"))
        for k in req:
            a = dict(base)
            a.pop(k, None)
            cases.append(Case(a, False, f"required '{k}' missing"))
        for k, sub in props.items():
            if isinstance(sub, dict) and "$ref" in sub:
                sub = resolve(sub["$ref"], self.root) or sub
            if not isinstance(sub, dict):
                continue
            for bad, why in self._bad_values(sub):
                a = dict(base)
                a[k] = bad
                cases.append(Case(a, False, f"'{k}' {why}"))
        if schema.get("additionalProperties") is False:
            a = dict(base)
            a["unexpected_field"] = "surprise"
            cases.append(Case(a, False, "unexpected extra property"))
        return cases

    def _bad_values(self, sub: Dict[str, Any]):
        t = sub.get("type")
        types = t if isinstance(t, list) else ([t] if t else [])
        wrong = {
            "string": (12345, "is a number, not a string"),
            "integer": ("42", "is a string, not an integer"),
            "number": ("1.5", "is a string, not a number"),
            "boolean": ("true", "is a string, not a boolean"),
            "array": ("item", "is a string, not an array"),
            "object": ([1, 2], "is an array, not an object"),
            "null": (0, "is 0, not null"),
        }
        out = []
        for ty in types:
            if ty in wrong:
                out.append(wrong[ty])
                break
        if types:
            out.append((None, "is null"))
        if "integer" in types:
            out.append((1.5, "is a float, not an integer"))
        lo, hi = sub.get("minimum"), sub.get("maximum")
        if isinstance(lo, (int, float)) and not isinstance(lo, bool):
            out.append((lo - 1, f"below minimum {lo}"))
        if isinstance(hi, (int, float)) and not isinstance(hi, bool):
            out.append((hi + 1, f"above maximum {hi}"))
        if isinstance(sub.get("enum"), list) and sub["enum"]:
            out.append(("__not_in_enum__", "outside its enum"))
        if isinstance(sub.get("maxLength"), int):
            out.append(("y" * (sub["maxLength"] + 1), f"longer than maxLength {sub['maxLength']}"))
        if isinstance(sub.get("minLength"), int) and sub["minLength"] > 0:
            out.append(("", f"shorter than minLength {sub['minLength']}"))
        if isinstance(sub.get("minItems"), int) and sub["minItems"] > 0:
            out.append(([], f"has fewer than minItems {sub['minItems']}"))
        return out


def build_cases(schema: Any, n_valid: int, rng: random.Random, max_invalid: int = 12) -> List[Case]:
    """Valid cases (minimal, full, random, edge) then invalid ones, all verified."""
    if not isinstance(schema, dict):
        schema = {"type": "object"}
    if "type" not in schema and "properties" not in schema:
        schema = {**schema, "type": "object"}
    g = Generator(schema, rng)
    import json
    seen = set()
    out: List[Case] = []

    def add(args: Any, valid: bool, label: str) -> None:
        if not isinstance(args, dict):
            return
        key = json.dumps(args, sort_keys=True, default=str)
        if key in seen:
            return
        ok = not validate(args, schema)
        if ok != valid:
            return  # our guess was wrong; never mislabel a case
        seen.add(key)
        out.append(Case(args, valid, label))

    add(g.value(schema, full=False), True, "minimal valid")
    add(g.value(schema, full=True), True, "all properties")
    tries = 0
    while sum(c.valid for c in out) < n_valid and tries < n_valid * 8:
        tries += 1
        edge = rng.random() < 0.55
        add(g.value(schema, edge=edge), True, "edge values" if edge else "random valid")
    inv = g.invalid_cases(schema)
    for c in inv[:max_invalid]:
        add(c.args, False, c.label)
    return out


__all__ = ["Case", "Generator", "build_cases", "type_of", "UNSET"]
