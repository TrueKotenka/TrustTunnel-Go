#!/usr/bin/env python3
"""Reject non-reproducible Apple archives or objects above the supported OS."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess


PLATFORMS = {"macos": "1", "ios": "2"}
VERSION = re.compile(r"^(0|[1-9][0-9]*)(?:\.(0|[1-9][0-9]*)){0,2}$")
ARCHIVE_INDEX_MEMBERS = frozenset(
    {"/", "//", "/SYM64/", "__.SYMDEF", "__.SYMDEF SORTED"}
)
ARCHIVE_MAGIC = b"!<arch>\n"
ARCHIVE_HEADER_SIZE = 60
DETERMINISTIC_ARCHIVE_MODE = 0o100644
CANONICAL_ARCHIVE_METADATA = (
    (16, 28, b"0           "),
    (28, 34, b"0     "),
    (34, 40, b"0     "),
    (40, 48, b"100644  "),
)


class VerificationError(ValueError):
    pass


@dataclass(frozen=True)
class MemberTarget:
    member: str
    platform: str
    minimum: tuple[int, int, int]


def version(value: str) -> tuple[int, int, int]:
    if VERSION.fullmatch(value) is None:
        raise VerificationError("invalid deployment target in Mach-O metadata")
    pieces = [int(piece) for piece in value.split(".")]
    return tuple((pieces + [0, 0])[:3])  # type: ignore[return-value]


def parse_otool(output: str) -> list[MemberTarget]:
    records: list[MemberTarget] = []
    members: list[str] = []
    archive: str | None = None
    member: str | None = None
    command: str | None = None
    platform: str | None = None
    for raw in output.splitlines():
        line = raw.strip()
        if line.startswith("Archive : "):
            archive = line.removeprefix("Archive : ")
            member = command = platform = None
        elif archive is not None and line.startswith(f"{archive}(") and line.endswith("):"):
            member = line[len(archive) + 1 : -2]
            members.append(member)
            command = platform = None
        elif line.startswith("cmd "):
            command = line.removeprefix("cmd ")
            platform = None
        elif command == "LC_BUILD_VERSION" and line.startswith("platform "):
            platform = line.split()[1]
        elif command == "LC_BUILD_VERSION" and line.startswith("minos "):
            if member is None or platform is None:
                raise VerificationError("incomplete LC_BUILD_VERSION metadata")
            records.append(MemberTarget(member, platform, version(line.split()[1])))
            command = platform = None
        elif command in {"LC_VERSION_MIN_IPHONEOS", "LC_VERSION_MIN_MACOSX"} and line.startswith("version "):
            if member is None:
                raise VerificationError("incomplete legacy deployment metadata")
            inferred = "2" if command == "LC_VERSION_MIN_IPHONEOS" else "1"
            records.append(MemberTarget(member, inferred, version(line.split()[1])))
            command = platform = None
    if not members:
        raise VerificationError("archive contains no readable object members")
    if Counter(record.member for record in records) != Counter(members):
        raise VerificationError(
            "every archive object must contain exactly one readable deployment target"
        )
    if not records:
        raise VerificationError("archive contains no readable deployment metadata")
    return records


def parse_archive_members(output: str) -> list[str]:
    members = [
        line.strip()
        for line in output.splitlines()
        if line.strip() and line.strip() not in ARCHIVE_INDEX_MEMBERS
    ]
    if not members:
        raise VerificationError("archive member table contains no object members")
    return members


def _archive_number(field: bytes, base: int, name: str) -> int:
    value = field.rstrip(b" ")
    digits = b"01234567" if base == 8 else b"0123456789"
    if not value or any(byte not in digits for byte in value):
        raise VerificationError(f"archive contains an invalid {name} field")
    return int(value, base)


def _archive_header_offsets(raw: bytes) -> list[int]:
    """Validate a regular archive's framing and return every header offset."""
    if not raw.startswith(ARCHIVE_MAGIC):
        raise VerificationError("archive magic is invalid")
    offset = len(ARCHIVE_MAGIC)
    headers: list[int] = []
    while offset < len(raw):
        end = offset + ARCHIVE_HEADER_SIZE
        if end > len(raw):
            raise VerificationError("archive member header is truncated")
        header = raw[offset:end]
        if header[58:60] != b"`\n":
            raise VerificationError("archive member header trailer is invalid")
        _archive_number(header[16:28], 10, "date")
        _archive_number(header[28:34], 10, "uid")
        _archive_number(header[34:40], 10, "gid")
        _archive_number(header[40:48], 8, "mode")
        size = _archive_number(header[48:58], 10, "size")
        headers.append(offset)
        offset = end + size
        if offset > len(raw):
            raise VerificationError("archive member data is truncated")
        if size % 2:
            if offset >= len(raw) or raw[offset : offset + 1] != b"\n":
                raise VerificationError("archive member padding is invalid")
            offset += 1
    if not headers:
        raise VerificationError("archive contains no members")
    return headers


def canonicalize_archive_metadata(raw: bytes) -> bytes:
    """Normalize only date, uid, gid, and mode in every validated header."""
    normalized = bytearray(raw)
    for offset in _archive_header_offsets(raw):
        for start, end, value in CANONICAL_ARCHIVE_METADATA:
            normalized[offset + start : offset + end] = value
    return bytes(normalized)


def verify_deterministic_archive(raw: bytes) -> int:
    """Require canonical metadata on every Apple archive member."""
    headers = _archive_header_offsets(raw)
    for offset in headers:
        header = raw[offset : offset + ARCHIVE_HEADER_SIZE]
        date = _archive_number(header[16:28], 10, "date")
        uid = _archive_number(header[28:34], 10, "uid")
        gid = _archive_number(header[34:40], 10, "gid")
        mode = _archive_number(header[40:48], 8, "mode")
        if date != 0 or uid != 0 or gid != 0 or mode != DETERMINISTIC_ARCHIVE_MODE:
            raise VerificationError(
                "archive member metadata is not deterministic "
                f"(date={date} uid={uid} gid={gid} mode={mode:o})"
            )
    return len(headers)


def verify(
    records: list[MemberTarget],
    platform: str,
    maximum: str,
    *,
    expected_members: list[str] | None = None,
) -> None:
    expected = PLATFORMS[platform]
    limit = version(maximum)
    if expected_members is not None and Counter(record.member for record in records) != Counter(expected_members):
        raise VerificationError(
            "deployment metadata does not cover the exact archive object-member table"
        )
    wrong_platform = [record for record in records if record.platform != expected]
    too_new = [record for record in records if record.minimum > limit]
    if wrong_platform:
        raise VerificationError("archive contains an object for another Apple platform")
    if too_new:
        first = too_new[0]
        observed = ".".join(str(piece) for piece in first.minimum)
        targets = ",".join(
            f"{'.'.join(str(piece) for piece in minimum)}:{count}"
            for minimum, count in sorted(Counter(record.minimum for record in too_new).items())
        )
        raise VerificationError(
            f"{len(too_new)} archive members require {platform} above supported {maximum} "
            f"(minimum-target counts {targets}); first={observed}:{first.member}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--platform", required=True, choices=sorted(PLATFORMS))
    parser.add_argument("--maximum-deployment-target", required=True)
    parser.add_argument("--canonicalize-metadata", action="store_true")
    args = parser.parse_args()
    try:
        archive = args.archive.resolve(strict=True)
        raw = archive.read_bytes()
        if args.canonicalize_metadata:
            archive.write_bytes(canonicalize_archive_metadata(raw))
            raw = archive.read_bytes()
        deterministic_members = verify_deterministic_archive(raw)
        completed = subprocess.run(
            ["xcrun", "otool", "-l", str(archive)],
            check=True,
            stdin=subprocess.DEVNULL,
            text=True,
            stdout=subprocess.PIPE,
        )
        member_table = subprocess.run(
            ["xcrun", "ar", "-t", str(archive)],
            check=True,
            stdin=subprocess.DEVNULL,
            text=True,
            stdout=subprocess.PIPE,
        )
        records = parse_otool(completed.stdout)
        members = parse_archive_members(member_table.stdout)
        verify(
            records,
            args.platform,
            args.maximum_deployment_target,
            expected_members=members,
        )
    except (OSError, VerificationError, subprocess.CalledProcessError) as error:
        print(f"error: Apple archive verification failed: {error}")
        return 1
    print(
        f"Apple archive verified platform={args.platform} "
        f"maximum={args.maximum_deployment_target} members={len(records)} "
        f"deterministic-members={deterministic_members}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
