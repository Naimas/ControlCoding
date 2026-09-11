"""Tests for control_plane_utils.py - Sprint 0 deliverable.

Minimum 15 tests covering:
- atomic_write: creates files via temp+rename, handles errors, encoding
- append_event: produces valid JSONL, creates files, appends multiple
- canonical_dedup_key: identical hashes for equivalent, different for distinct
- generate_id: correct format, auto-increment, multi-prefix
- Schema constants: completeness
"""

import json
import os
import sys
import unittest

# Allow importing from templates/scripts/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "templates", "scripts"))

from control_plane_utils import (
    CATEGORY_TYPES,
    DECISION_TYPES,
    EVENT_TYPES,
    KNOWN_PREFIXES,
    SEVERITY_LEVELS,
    append_event,
    atomic_write,
    canonical_dedup_key,
    generate_id,
    utc_now_iso,
)


class TestAtomicWrite(unittest.TestCase):
    """Tests for atomic_write()."""

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_creates_file_with_correct_json(self):
        path = os.path.join(self.tmpdir, "out.json")
        data = {"id": "CW-001", "status": "pending"}
        atomic_write(path, data)
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        self.assertEqual(loaded, data)

    def test_json_indent_is_2(self):
        path = os.path.join(self.tmpdir, "out.json")
        atomic_write(path, {"a": 1})
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        # indent=2 produces "  " before keys in nested objects
        expected = json.dumps({"a": 1}, indent=2, ensure_ascii=False) + "\n"
        self.assertEqual(raw, expected)

    def test_utf8_content(self):
        path = os.path.join(self.tmpdir, "utf8.json")
        data = {"text": "criterio con accenti: e\u0300 o\u0300 u\u0300"}
        atomic_write(path, data)
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        self.assertEqual(loaded["text"], data["text"])

    def test_overwrites_existing_file(self):
        path = os.path.join(self.tmpdir, "out.json")
        atomic_write(path, {"v": 1})
        atomic_write(path, {"v": 2})
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        self.assertEqual(loaded["v"], 2)

    def test_creates_parent_dirs(self):
        path = os.path.join(self.tmpdir, "a", "b", "out.json")
        atomic_write(path, {"ok": True})
        self.assertTrue(os.path.isfile(path))

    def test_no_temp_file_left_on_success(self):
        path = os.path.join(self.tmpdir, "clean.json")
        atomic_write(path, {"x": 1})
        files = os.listdir(self.tmpdir)
        tmp_files = [f for f in files if f.endswith(".tmp")]
        self.assertEqual(tmp_files, [])

    def test_raises_on_non_serializable(self):
        path = os.path.join(self.tmpdir, "bad.json")
        with self.assertRaises(TypeError):
            atomic_write(path, {"fn": lambda: None})
        # temp file should be cleaned up
        tmp_files = [f for f in os.listdir(self.tmpdir) if f.endswith(".tmp")]
        self.assertEqual(tmp_files, [])


class TestAppendEvent(unittest.TestCase):
    """Tests for append_event()."""

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_creates_file_if_not_exists(self):
        path = os.path.join(self.tmpdir, "events.jsonl")
        event = {"id": "EVT-001", "event": "agent_started"}
        append_event(path, event)
        self.assertTrue(os.path.isfile(path))

    def test_produces_valid_jsonl(self):
        path = os.path.join(self.tmpdir, "events.jsonl")
        append_event(path, {"id": "EVT-001", "event": "agent_started"})
        append_event(path, {"id": "EVT-002", "event": "agent_completed"})
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 2)
        for line in lines:
            parsed = json.loads(line)
            self.assertIn("id", parsed)

    def test_appends_to_existing(self):
        path = os.path.join(self.tmpdir, "events.jsonl")
        append_event(path, {"n": 1})
        append_event(path, {"n": 2})
        append_event(path, {"n": 3})
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(json.loads(lines[2])["n"], 3)

    def test_creates_parent_dirs(self):
        path = os.path.join(self.tmpdir, "sub", "dir", "events.jsonl")
        append_event(path, {"id": "EVT-001"})
        self.assertTrue(os.path.isfile(path))


class TestCanonicalDedupKey(unittest.TestCase):
    """Tests for canonical_dedup_key()."""

    def test_identical_input_same_hash(self):
        h1 = canonical_dedup_key("Some violation", "codewarden", "f.py", "10-20", "R1")
        h2 = canonical_dedup_key("Some violation", "codewarden", "f.py", "10-20", "R1")
        self.assertEqual(h1, h2)

    def test_whitespace_normalized(self):
        h1 = canonical_dedup_key("some  violation", "cw")
        h2 = canonical_dedup_key("some violation", "cw")
        self.assertEqual(h1, h2)

    def test_case_normalized(self):
        h1 = canonical_dedup_key("Some Violation", "cw")
        h2 = canonical_dedup_key("some violation", "cw")
        self.assertEqual(h1, h2)

    def test_different_file_different_hash(self):
        h1 = canonical_dedup_key("violation", "cw", file="a.py")
        h2 = canonical_dedup_key("violation", "cw", file="b.py")
        self.assertNotEqual(h1, h2)

    def test_different_agent_different_hash(self):
        h1 = canonical_dedup_key("violation", "codewarden")
        h2 = canonical_dedup_key("violation", "verifier")
        self.assertNotEqual(h1, h2)

    def test_different_rule_different_hash(self):
        h1 = canonical_dedup_key("violation", "cw", rule_id="R1")
        h2 = canonical_dedup_key("violation", "cw", rule_id="R2")
        self.assertNotEqual(h1, h2)

    def test_none_optionals_consistent(self):
        h1 = canonical_dedup_key("text", "agent")
        h2 = canonical_dedup_key("text", "agent", None, None, None)
        self.assertEqual(h1, h2)

    def test_returns_hex_string(self):
        h = canonical_dedup_key("x", "a")
        self.assertEqual(len(h), 64)  # SHA-256 hex
        self.assertTrue(all(c in "0123456789abcdef" for c in h))


class TestGenerateId(unittest.TestCase):
    """Tests for generate_id()."""

    def test_first_id(self):
        self.assertEqual(generate_id("CW"), "CW-001")

    def test_increments(self):
        existing = ["CW-001", "CW-002", "CW-003"]
        self.assertEqual(generate_id("CW", existing), "CW-004")

    def test_ignores_other_prefixes(self):
        existing = ["PL-010", "CW-001"]
        self.assertEqual(generate_id("CW", existing), "CW-002")
        self.assertEqual(generate_id("PL", existing), "PL-011")

    def test_zero_padded(self):
        result = generate_id("EVT", [])
        self.assertEqual(result, "EVT-001")

    def test_handles_gaps(self):
        existing = ["DEC-001", "DEC-005"]
        self.assertEqual(generate_id("DEC", existing), "DEC-006")

    def test_empty_existing(self):
        self.assertEqual(generate_id("VF", []), "VF-001")

    def test_none_existing(self):
        self.assertEqual(generate_id("AUD", None), "AUD-001")


class TestSchemaConstants(unittest.TestCase):
    """Tests for schema constant completeness."""

    def test_decision_types_complete(self):
        expected = {
            "architecture_choice", "plan_approval", "plan_amendment",
            "scope_change", "wont_fix", "override", "escalation_resolution",
        }
        self.assertEqual(DECISION_TYPES, expected)

    def test_severity_levels_complete(self):
        self.assertEqual(SEVERITY_LEVELS, {"critical", "high", "medium", "low"})

    def test_category_types_complete(self):
        expected = {
            "architecture", "domain_invariant", "coding_convention",
            "security", "resource_management", "style",
        }
        self.assertEqual(CATEGORY_TYPES, expected)

    def test_event_types_count(self):
        # 25 original + 4 control-plane/runtime additions.
        self.assertEqual(len(EVENT_TYPES), 29)

    def test_known_prefixes(self):
        self.assertIn("PL", KNOWN_PREFIXES)
        self.assertIn("CW", KNOWN_PREFIXES)
        self.assertIn("DEC", KNOWN_PREFIXES)
        self.assertIn("EVT", KNOWN_PREFIXES)

    def test_constants_are_frozenset(self):
        for const in (DECISION_TYPES, EVENT_TYPES, SEVERITY_LEVELS,
                      CATEGORY_TYPES, KNOWN_PREFIXES):
            self.assertIsInstance(const, frozenset)


class TestUtcNowIso(unittest.TestCase):
    """Tests for utc_now_iso()."""

    def test_format(self):
        ts = utc_now_iso()
        import re
        self.assertRegex(ts, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


if __name__ == "__main__":
    unittest.main()
