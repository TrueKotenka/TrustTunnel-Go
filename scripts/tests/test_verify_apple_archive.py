from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "verify_apple_archive.py"
SPEC = importlib.util.spec_from_file_location("verify_apple_archive", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def metadata(platform: int, minimum: str, member: str = "bridge.o") -> str:
    return f"""Archive : archive.a
archive.a({member}):
Load command 1
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform {platform}
    minos {minimum}
      sdk 26.2
"""


def archive_member(
    payload: bytes,
    *,
    name: str = "bridge.o/",
    date: int = 0,
    uid: int = 0,
    gid: int = 0,
    mode: int = 0o100644,
) -> bytes:
    header = (
        f"{name:<16}{date:<12}{uid:<6}{gid:<6}{mode:<8o}{len(payload):<10}`\n"
    ).encode("ascii")
    assert len(header) == MODULE.ARCHIVE_HEADER_SIZE
    return header + payload + (b"\n" if len(payload) % 2 else b"")


class AppleArchiveVerificationTests(unittest.TestCase):
    def test_accepts_deterministic_metadata_for_every_archive_member(self) -> None:
        raw = (
            MODULE.ARCHIVE_MAGIC
            + archive_member(b"index", name="__.SYMDEF")
            + archive_member(b"object")
        )
        self.assertEqual(MODULE.verify_deterministic_archive(raw), 2)

    def test_rejects_each_non_deterministic_archive_field(self) -> None:
        for field, value in (
            ("date", 1),
            ("uid", 501),
            ("gid", 20),
            ("mode", 0o100600),
        ):
            with self.subTest(field=field):
                raw = MODULE.ARCHIVE_MAGIC + archive_member(b"object", **{field: value})
                with self.assertRaisesRegex(MODULE.VerificationError, "not deterministic"):
                    MODULE.verify_deterministic_archive(raw)

    def test_rejects_truncated_archive_member_data(self) -> None:
        raw = MODULE.ARCHIVE_MAGIC + archive_member(b"object")[:-1]
        with self.assertRaisesRegex(MODULE.VerificationError, "truncated|padding"):
            MODULE.verify_deterministic_archive(raw)

    def test_rejects_signed_archive_numeric_fields(self) -> None:
        raw = MODULE.ARCHIVE_MAGIC + archive_member(b"object", date=-1)
        with self.assertRaisesRegex(MODULE.VerificationError, "invalid date"):
            MODULE.verify_deterministic_archive(raw)

    def test_accepts_every_member_at_or_below_limit(self) -> None:
        records = MODULE.parse_otool(metadata(2, "13.0", "a.o") + metadata(2, "15.6", "b.o"))
        MODULE.verify(records, "ios", "15.6")

    def test_rejects_member_above_limit(self) -> None:
        records = MODULE.parse_otool(metadata(2, "18.5"))
        with self.assertRaisesRegex(MODULE.VerificationError, "above supported"):
            MODULE.verify(records, "ios", "15.6")

    def test_rejects_wrong_platform(self) -> None:
        records = MODULE.parse_otool(metadata(1, "15.0"))
        with self.assertRaisesRegex(MODULE.VerificationError, "another Apple platform"):
            MODULE.verify(records, "ios", "15.6")

    def test_parses_legacy_minimum_command(self) -> None:
        output = """Archive : archive.a
archive.a(legacy.o):
Load command 1
      cmd LC_VERSION_MIN_MACOSX
  cmdsize 16
  version 11.0
      sdk 15.5
"""
        records = MODULE.parse_otool(output)
        MODULE.verify(records, "macos", "15.0")

    def test_rejects_output_without_member_targets(self) -> None:
        with self.assertRaises(MODULE.VerificationError):
            MODULE.parse_otool("Archive : archive.a\n")

    def test_rejects_member_without_deployment_metadata(self) -> None:
        output = metadata(2, "15.6", "verified.o") + """Archive : archive.a
archive.a(unverified.o):
Load command 0
      cmd LC_SEGMENT_64
  cmdsize 72
"""
        with self.assertRaisesRegex(MODULE.VerificationError, "every archive object"):
            MODULE.parse_otool(output)

    def test_rejects_metadata_not_covering_exact_archive_table(self) -> None:
        records = MODULE.parse_otool(metadata(2, "15.6", "verified.o"))
        with self.assertRaisesRegex(MODULE.VerificationError, "exact archive object-member"):
            MODULE.verify(
                records,
                "ios",
                "15.6",
                expected_members=["verified.o", "unverified.o"],
            )

    def test_ignores_only_archive_index_members(self) -> None:
        members = MODULE.parse_archive_members(
            "__.SYMDEF\n__.SYMDEF SORTED\n/\n//\n/SYM64/\nbridge.o\n"
        )
        self.assertEqual(members, ["bridge.o"])


if __name__ == "__main__":
    unittest.main()
