"""A small JSON Schema validator, enough for MCP tool input and output schemas.

Covers the keywords tool schemas use in practice (types, enums, numeric and
length bounds, patterns, arrays, objects, combinators and local $refs).
Unknown keywords are ignored, as JSON Schema says they should be.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, List

__all__ = ["validate", "type_of", "resolve"]


def type_of(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "integer" if math.isfinite(value) and value.is_integer() else "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _is_type(value: Any, t: str) -> bool:
    actual = type_of(value)
    if t == "number":
        return actual in ("integer", "number")
    return actual == t


def resolve(ref: str, root: Any) -> Any:
    """Resolve a local JSON pointer like '#/$defs/Item'. Returns None if unknown."""
    if not isinstance(ref, str) or not ref.startswith("#"):
        return None
    node = root
    path = ref[1:].lstrip("/")
    if not path:
        return root
    for part in path.split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return None
    return node


def _key(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def _fmt(value: Any) -> str:
    s = json.dumps(value, ensure_ascii=False)
    return s if len(s) <= 40 else s[:37] + "..."


def validate(instance: Any, schema: Any, root: Any = None, path: str = "$", _depth: int = 0) -> List[str]:
    """Return a list of human-readable errors (empty when the instance is valid)."""
    if root is None:
        root = schema
    if _depth > 64:
        return []
    if schema is True or schema is None:
        return []
    if schema is False:
        return [f"{path}: no value is allowed here"]
    if not isinstance(schema, dict):
        return []
    errs: List[str] = []
    d = _depth + 1

    if "$ref" in schema:
        target = resolve(schema["$ref"], root)
        if target is not None:
            errs += validate(instance, target, root, path, d)

    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_is_type(instance, x) for x in types):
            errs.append(f"{path}: expected {'/'.join(map(str, types))}, got {type_of(instance)}")
            return errs

    if "enum" in schema and isinstance(schema["enum"], list):
        if _key(instance) not in {_key(v) for v in schema["enum"]}:
            errs.append(f"{path}: {_fmt(instance)} is not one of {_fmt(schema['enum'])}")
    if "const" in schema and _key(instance) != _key(schema["const"]):
        errs.append(f"{path}: expected {_fmt(schema['const'])}")

    if type_of(instance) in ("integer", "number"):
        x = instance
        mn, mx = schema.get("minimum"), schema.get("maximum")
        emn, emx = schema.get("exclusiveMinimum"), schema.get("exclusiveMaximum")
        if emn is True and isinstance(mn, (int, float)):
            emn, mn = mn, None
        if emx is True and isinstance(mx, (int, float)):
            emx, mx = mx, None
        if isinstance(mn, (int, float)) and not isinstance(mn, bool) and x < mn:
            errs.append(f"{path}: {x} is below minimum {mn}")
        if isinstance(mx, (int, float)) and not isinstance(mx, bool) and x > mx:
            errs.append(f"{path}: {x} is above maximum {mx}")
        if isinstance(emn, (int, float)) and not isinstance(emn, bool) and x <= emn:
            errs.append(f"{path}: {x} must be greater than {emn}")
        if isinstance(emx, (int, float)) and not isinstance(emx, bool) and x >= emx:
            errs.append(f"{path}: {x} must be less than {emx}")
        mo = schema.get("multipleOf")
        if isinstance(mo, (int, float)) and not isinstance(mo, bool) and mo > 0:
            q = x / mo
            if not math.isfinite(q) or abs(q - round(q)) > 1e-9:
                errs.append(f"{path}: {x} is not a multiple of {mo}")

    if isinstance(instance, str):
        n = len(instance)
        if isinstance(schema.get("minLength"), int) and n < schema["minLength"]:
            errs.append(f"{path}: shorter than minLength {schema['minLength']}")
        if isinstance(schema.get("maxLength"), int) and n > schema["maxLength"]:
            errs.append(f"{path}: longer than maxLength {schema['maxLength']}")
        pat = schema.get("pattern")
        if isinstance(pat, str):
            try:
                if not re.search(pat, instance):
                    errs.append(f"{path}: does not match pattern {pat}")
            except re.error:
                pass

    if isinstance(instance, list):
        n = len(instance)
        if isinstance(schema.get("minItems"), int) and n < schema["minItems"]:
            errs.append(f"{path}: fewer than minItems {schema['minItems']}")
        if isinstance(schema.get("maxItems"), int) and n > schema["maxItems"]:
            errs.append(f"{path}: more than maxItems {schema['maxItems']}")
        if schema.get("uniqueItems") is True and len({_key(v) for v in instance}) != n:
            errs.append(f"{path}: items are not unique")
        prefix = schema.get("prefixItems")
        items = schema.get("items")
        if isinstance(items, list):  # draft 4-7 tuple form
            prefix, items = items, schema.get("additionalItems")
        start = 0
        if isinstance(prefix, list):
            for i, sub in enumerate(prefix[:n]):
                errs += validate(instance[i], sub, root, f"{path}[{i}]", d)
            start = len(prefix)
        if items is not None and not isinstance(items, list):
            for i in range(start, n):
                errs += validate(instance[i], items, root, f"{path}[{i}]", d)
        if "contains" in schema and not any(not validate(v, schema["contains"], root, path, d) for v in instance):
            errs.append(f"{path}: no item matches 'contains'")

    if isinstance(instance, dict):
        props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        for k in schema.get("required") or []:
            if k not in instance:
                errs.append(f"{path}: missing required property '{k}'")
        n = len(instance)
        if isinstance(schema.get("minProperties"), int) and n < schema["minProperties"]:
            errs.append(f"{path}: fewer than minProperties {schema['minProperties']}")
        if isinstance(schema.get("maxProperties"), int) and n > schema["maxProperties"]:
            errs.append(f"{path}: more than maxProperties {schema['maxProperties']}")
        pprops = schema.get("patternProperties") if isinstance(schema.get("patternProperties"), dict) else {}
        addl = schema.get("additionalProperties", True)
        for k, v in instance.items():
            matched = False
            if k in props:
                matched = True
                errs += validate(v, props[k], root, f"{path}.{k}", d)
            for pat, sub in pprops.items():
                try:
                    if re.search(pat, k):
                        matched = True
                        errs += validate(v, sub, root, f"{path}.{k}", d)
                except re.error:
                    pass
            if not matched:
                if addl is False:
                    errs.append(f"{path}: unexpected property '{k}'")
                elif isinstance(addl, dict):
                    errs += validate(v, addl, root, f"{path}.{k}", d)

    for sub in schema.get("allOf") or []:
        errs += validate(instance, sub, root, path, d)
    if isinstance(schema.get("anyOf"), list) and schema["anyOf"]:
        if not any(not validate(instance, s, root, path, d) for s in schema["anyOf"]):
            errs.append(f"{path}: matches none of anyOf")
    if isinstance(schema.get("oneOf"), list) and schema["oneOf"]:
        hits = sum(1 for s in schema["oneOf"] if not validate(instance, s, root, path, d))
        if hits != 1:
            errs.append(f"{path}: matches {hits} of oneOf (expected exactly 1)")
    if "not" in schema and not validate(instance, schema["not"], root, path, d):
        errs.append(f"{path}: must not match 'not' schema")
    if "if" in schema:
        branch = "then" if not validate(instance, schema["if"], root, path, d) else "else"
        if branch in schema:
            errs += validate(instance, schema[branch], root, path, d)
    return errs
