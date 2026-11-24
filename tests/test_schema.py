from mcpshake.schema import validate


def ok(v, s):
    return not validate(v, s)


def test_types():
    assert ok(1, {"type": "integer"})
    assert ok(1.0, {"type": "integer"})
    assert not ok(1.5, {"type": "integer"})
    assert not ok(True, {"type": "integer"})
    assert not ok(True, {"type": "number"})
    assert ok(None, {"type": ["string", "null"]})
    assert not ok("1", {"type": "number"})


def test_bounds_and_strings():
    s = {"type": "integer", "minimum": 1, "maximum": 5}
    assert ok(1, s) and ok(5, s) and not ok(0, s) and not ok(6, s)
    assert not ok(3, {"exclusiveMaximum": 3})
    assert not ok(3, {"maximum": 3, "exclusiveMaximum": True})
    assert not ok(7, {"multipleOf": 2})
    assert ok("ab", {"minLength": 2, "maxLength": 2})
    assert not ok("abc", {"maxLength": 2})
    assert ok("a-1", {"pattern": "^[a-z]-[0-9]$"})
    assert not ok("A-1", {"pattern": "^[a-z]-[0-9]$"})


def test_objects_arrays():
    s = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "array", "items": {"type": "integer"}, "maxItems": 2}},
        "required": ["a"],
        "additionalProperties": False,
    }
    assert ok({"a": "x"}, s)
    assert ok({"a": "x", "b": [1, 2]}, s)
    errs = validate({"b": [1, "2", 3], "c": 1}, s)
    joined = "\n".join(errs)
    assert "missing required property 'a'" in joined
    assert "$.b[1]: expected integer" in joined
    assert "more than maxItems" in joined
    assert "unexpected property 'c'" in joined
    assert not ok([1, 1], {"uniqueItems": True})


def test_combinators_and_refs():
    s = {"$defs": {"n": {"type": "integer"}}, "anyOf": [{"$ref": "#/$defs/n"}, {"type": "string"}]}
    assert ok(3, s) and ok("x", s) and not ok(1.5, s)
    assert not ok(3, {"oneOf": [{"type": "integer"}, {"type": "number"}]})
    assert ok("x", {"enum": ["x", "y"]}) and not ok("z", {"enum": ["x", "y"]})
    assert not ok(1, {"not": {"type": "integer"}})
    assert ok({}, True) and not ok({}, False)
