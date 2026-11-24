# Changelog

## 0.1.0 (2026-11-24)

First release.

- Starts a stdio MCP server, runs `initialize` and `tools/list` (with pagination).
- Generates schema-valid arguments (minimal, full, random and edge values) and
  targeted invalid arguments from each tool's `inputSchema`; every case is checked
  with a built-in JSON Schema validator so labels are never wrong.
- Reports 12 kinds of findings: crashes, timeouts, stdout noise, internal errors,
  malformed results, `outputSchema` violations, unhandled or accepted invalid
  input, rejected valid input, leaked stack traces, `isError` on valid input and
  slow tools.
- Restarts the server after a crash or hang and keeps going.
- Skips `destructiveHint: true` tools unless `--include-destructive` is given, and
  tools that require task augmentation.
- `--seed` for reproducible runs, `--json`, `--github`, `--fail-on`, `--tools`/`--skip`.
- Zero runtime dependencies, Python 3.10+.
