import random

from mcpshake.gen import build_cases
from mcpshake.schema import validate

SCHEMAS = [
    {"type": "object", "properties": {"q": {"type": "string"}, "n": {"type": "integer", "minimum": 1, "maximum": 50}}, "required": ["q"]},
    {"type": "object", "properties": {"when": {"type": "string", "format": "date-time"}, "tags": {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True}}, "required": ["tags"], "additionalProperties": False},
    {"type": "object", "properties": {"mode": {"enum": ["a", "b"]}, "x": {"type": "number", "exclusiveMinimum": 0}}, "required": ["mode", "x"]},
    {"type": "object", "$defs": {"pt": {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}}, "properties": {"p": {"$ref": "#/$defs/pt"}}, "required": ["p"]},
    {"type": "object", "properties": {"id": {"type": "string", "pattern": "^[a-z]{3}[0-9]{2}$"}}, "required": ["id"]},
    {"type": "object"},
    {},
]


def test_labels_always_match_the_schema():
    for seed in range(5):
        for s in SCHEMAS:
            cases = build_cases(s, 20, random.Random(seed))
            assert cases, s
            for c in cases:
                assert (not validate(c.args, s if "type" in s else {**s, "type": "object"})) == c.valid, (s, c)


def test_deterministic_with_seed():
    a = [c.args for c in build_cases(SCHEMAS[0], 15, random.Random(7))]
    b = [c.args for c in build_cases(SCHEMAS[0], 15, random.Random(7))]
    assert a == b


def test_invalid_cases_cover_required_type_and_bounds():
    two = {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string"}}, "required": ["a", "b"]}
    labels = [c.label for c in build_cases(two, 5, random.Random(1)) if not c.valid]
    assert "no arguments at all" in labels
    assert "required 'a' missing" in labels and "required 'b' missing" in labels
    labels = [c.label for c in build_cases(SCHEMAS[0], 5, random.Random(1)) if not c.valid]
    assert "'q' is a number, not a string" in labels
    assert "'n' below minimum 1" in labels
    assert "'n' above maximum 50" in labels
