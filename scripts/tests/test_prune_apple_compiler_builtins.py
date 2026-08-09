import hashlib
from pathlib import Path
import sys
import unittest


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from prune_apple_compiler_builtins import (  # noqa: E402
    ARCHIVE_MAGIC,
    PruningError,
    archive_bytes_without,
    members_above,
    parse_archive,
    prunable_indexes,
    require_unmerged_input,
)
from verify_apple_archive import MemberTarget  # noqa: E402


def record(name: str, payload: bytes, *, extended: bool = False) -> bytes:
    if extended:
        encoded_name = name.encode() + b"\0"
        token = f"#1/{len(encoded_name)}"
        body = encoded_name + payload
    else:
        token = name
        body = payload
    header = (
        f"{token:<16}{0:<12}{0:<6}{0:<6}{0o100644:<8}{len(body):<10}`\n"
    ).encode("ascii")
    return header + body + (b"\n" if len(body) % 2 else b"")


class ArchiveTests(unittest.TestCase):
    def test_uses_normalized_otool_member_names(self):
        records = [MemberTarget("runtime-object.o", "2", (16, 0, 0))]
        self.assertEqual(members_above(records, (15, 6, 0)), {"runtime-object.o": 1})

    def test_parses_bsd_names_and_duplicate_payloads(self):
        raw = ARCHIVE_MAGIC + record("__.SYMDEF", b"index", extended=True)
        raw += record("runtime-object.o", b"same", extended=True)
        raw += record("runtime-object.o", b"same", extended=True)
        members = parse_archive(raw)
        self.assertEqual([member.name for member in members], ["__.SYMDEF", "runtime-object.o", "runtime-object.o"])
        self.assertEqual(members[1].payload, b"same")

    def test_removes_only_exact_counted_payloads_and_symbol_index(self):
        raw = ARCHIVE_MAGIC + record("__.SYMDEF", b"index", extended=True)
        raw += record("runtime-object.o", b"same", extended=True)
        raw += record("runtime-object.o", b"same", extended=True)
        raw += record("kept.o/", b"kept")
        members = parse_archive(raw)
        digest = hashlib.sha256(b"same").hexdigest()
        removed = prunable_indexes(
            members,
            {"runtime-object.o": 2},
            {"runtime-object.o": digest},
        )
        rewritten = parse_archive(archive_bytes_without(members, removed))
        self.assertEqual([member.name for member in rewritten], ["kept.o"])
        self.assertEqual(rewritten[0].payload, b"kept")

    def test_rejects_same_name_with_different_payload(self):
        raw = ARCHIVE_MAGIC + record("runtime-object.o", b"unexpected", extended=True)
        members = parse_archive(raw)
        with self.assertRaisesRegex(PruningError, "does not exactly match"):
            prunable_indexes(
                members,
                {"runtime-object.o": 1},
                {"runtime-object.o": hashlib.sha256(b"expected").hexdigest()},
            )

    def test_rejects_count_mismatch(self):
        raw = ARCHIVE_MAGIC + record("runtime-object.o", b"same", extended=True)
        members = parse_archive(raw)
        with self.assertRaisesRegex(PruningError, "counts differ"):
            prunable_indexes(
                members,
                {"runtime-object.o": 2},
                {"runtime-object.o": hashlib.sha256(b"same").hexdigest()},
            )

    def test_rejects_duplicate_names_in_unmerged_input(self):
        raw = ARCHIVE_MAGIC + record("runtime-object.o", b"first", extended=True)
        raw += record("runtime-object.o", b"second", extended=True)
        with self.assertRaisesRegex(PruningError, "duplicate member names"):
            require_unmerged_input(parse_archive(raw))


if __name__ == "__main__":
    unittest.main()
