# W3 · Part B · A4 — Binary file corruption fix (2026-08)

## Red proof (live, before fix)
Real 69-byte PNG fixture, through the REAL `github_api_writer.fetch_file()`
(mocked HTTP transport only, not the function):
- `fetch_file()` returned a string containing U+FFFD (was NOT rejected).
- Re-encoding that string via the same line `commit_files()` uses
  (`content.encode("utf-8")`) produced 95 bytes vs original 69 —
  `sha256` mismatch, `BYTE-IDENTICAL: False`.

## Choke point
`services/github_api_writer.py::fetch_file()` — the single shared
boundary where the edit/verify/execute path decodes file bytes to str.
Called from the LIVE path (`services/loop_engine.py::_gen_via_parliament`,
~line 1316) and the currently-dead-in-production
`services/loop_execute.py::_generate_one_inner`.

## Fix
- NUL byte in first 8 KiB of raw decoded bytes → `BinaryFileError`.
- Fails strict UTF-8, no NUL → `UnsupportedEncodingError` (legacy
  encoding, e.g. Latin-1/Cp1252 text) — rejected, never silently
  replaced with U+FFFD.
- Both raised from `core/errors.py`, classified to
  `FILE_BINARY_NOT_EDITABLE` / `FILE_ENCODING_UNSUPPORTED`, catalog
  entries in `i18n/errors_en.json`.
- `_gen_via_parliament` and `_generate_one_inner` catch these
  specifically BEFORE their existing generic
  `except Exception: current = ""` and skip that one file (typed
  emit + narrate), never treat it as a blank new file.
- Defensive catches added to 3 OTHER pre-existing `fetch_file` callers
  whose own docstrings/comments explicitly assumed "never raises":
  `rollback_snapshot.py::_capture_files`,
  `rollback_two_phase.py::_current_contents` + the read-back
  verification loop, `qa_matrix.py::verify_pass_is_real`. These map
  the new exceptions back to `None`/absent, preserving their existing
  contracts exactly — no new feature built there, just no new crash.

## Tests
`tests/test_iter_w3_a4_binary_file_refusal.py` — 5 tests, all real
fixtures (valid PNG built via zlib/struct, real Latin-1 bytes), real
`fetch_file()` calls (httpx.MockTransport, not mocked functions), one
full engine-level test through the actual live `_do_execute()` →
`_gen_via_parliament` path with only the GitHub HTTP transport and
Parliament LLM call faked.

## Follow-up (not built this pass)
Full legacy-encoding (Latin-1/Cp1252) support — currently a hard
refusal. Would need real re-encoding + round-trip verification.
