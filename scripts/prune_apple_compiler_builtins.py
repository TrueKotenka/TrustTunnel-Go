#!/usr/bin/env python3
"""Remove exact incompatible Rust compiler-builtins members from one input archive.

The operation is deliberately narrow: every too-new member in an unmerged
input archive must have the same unique name and exact payload as a member
from one immutable compiler-builtins rlib.  Any ambiguity fails closed.  The
caller must operate on a staging copy, verify the final merged archive, and
link the real consumer.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

from verify_apple_archive import (
    MemberTarget,
    PLATFORMS,
    VerificationError,
    parse_otool,
    verify,
    version,
)


ARCHIVE_MAGIC = b"!<arch>\n"
HEADER_SIZE = 60
INDEX_MEMBERS = {"/", "/SYM64/", "__.SYMDEF", "__.SYMDEF SORTED"}


class PruningError(ValueError):
    pass


@dataclass(frozen=True)
class ArchiveMember:
    name: str
    payload: bytes
    raw_record: bytes


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def decode_name(token: str, payload: bytes, string_table: bytes | None) -> tuple[str, bytes]:
    token = token.strip()
    if token.startswith("#1/"):
        try:
            length = int(token[3:])
        except ValueError as error:
            raise PruningError("archive has an invalid BSD extended name") from error
        if length < 1 or length > len(payload):
            raise PruningError("archive has a truncated BSD extended name")
        try:
            name = payload[:length].rstrip(b"\0").decode("utf-8")
        except UnicodeDecodeError as error:
            raise PruningError("archive member name is not UTF-8") from error
        return name, payload[length:]
    if token.startswith("/") and token[1:].isdigit():
        if string_table is None:
            raise PruningError("archive long name lacks a string table")
        offset = int(token[1:])
        if offset >= len(string_table):
            raise PruningError("archive long-name offset is invalid")
        end = len(string_table)
        for marker in (b"/\n", b"\0"):
            found = string_table.find(marker, offset)
            if found >= 0:
                end = min(end, found)
        try:
            return string_table[offset:end].decode("utf-8"), payload
        except UnicodeDecodeError as error:
            raise PruningError("archive member name is not UTF-8") from error
    if token in {"/", "//", "/SYM64/"}:
        return token, payload
    return token.removesuffix("/"), payload


def parse_archive(raw: bytes) -> list[ArchiveMember]:
    if not raw.startswith(ARCHIVE_MAGIC):
        raise PruningError("input is not a regular ar archive")
    members: list[ArchiveMember] = []
    string_table: bytes | None = None
    offset = len(ARCHIVE_MAGIC)
    while offset < len(raw):
        if len(raw) - offset < HEADER_SIZE:
            raise PruningError("archive ends inside a member header")
        header = raw[offset : offset + HEADER_SIZE]
        if header[58:60] != b"`\n":
            raise PruningError("archive member header is invalid")
        try:
            token = header[:16].decode("ascii")
            size = int(header[48:58].decode("ascii").strip())
        except (UnicodeDecodeError, ValueError) as error:
            raise PruningError("archive member header is malformed") from error
        body_start = offset + HEADER_SIZE
        body_end = body_start + size
        padded_end = body_end + (size % 2)
        if body_end > len(raw) or padded_end > len(raw):
            raise PruningError("archive member payload is truncated")
        body = raw[body_start:body_end]
        name, payload = decode_name(token, body, string_table)
        record = raw[offset:padded_end]
        members.append(ArchiveMember(name, payload, record))
        if name == "//":
            string_table = payload
        offset = padded_end
    return members


def members_above(
    records: list[MemberTarget], limit: tuple[int, int, int]
) -> Counter[str]:
    return Counter(record.member for record in records if record.minimum > limit)


def prunable_indexes(
    members: list[ArchiveMember],
    too_new_names: Counter[str],
    reference_payloads: dict[str, str],
) -> set[int]:
    selected: set[int] = set()
    selected_names: Counter[str] = Counter()
    for index, member in enumerate(members):
        if member.name not in too_new_names:
            continue
        expected = reference_payloads.get(member.name)
        if expected is None or sha256_bytes(member.payload) != expected:
            raise PruningError(
                "too-new archive member does not exactly match pinned compiler-builtins"
            )
        selected.add(index)
        selected_names[member.name] += 1
    if selected_names != too_new_names:
        raise PruningError("too-new member counts differ between otool and archive payloads")
    return selected


def archive_bytes_without(members: list[ArchiveMember], removed: set[int]) -> bytes:
    retained = [
        member.raw_record
        for index, member in enumerate(members)
        if index not in removed and member.name not in INDEX_MEMBERS
    ]
    if not retained:
        raise PruningError("pruning would leave an empty archive")
    return ARCHIVE_MAGIC + b"".join(retained)


def require_unmerged_input(members: list[ArchiveMember]) -> None:
    candidate_names = [
        member.name for member in members if member.name not in INDEX_MEMBERS
    ]
    if len(candidate_names) != len(set(candidate_names)):
        raise PruningError("unmerged input archive contains duplicate member names")


def tool_output(arguments: list[str]) -> str:
    completed = subprocess.run(
        arguments,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout


def deployment_records(archive: Path) -> list[MemberTarget]:
    return parse_otool(tool_output(["xcrun", "otool", "-l", str(archive)]))


def check_platform(records: list[MemberTarget], platform: str) -> None:
    expected = PLATFORMS[platform]
    if any(record.platform != expected for record in records):
        raise PruningError("archive contains a member for another Apple platform")


def prune(
    archive: Path,
    compiler_builtins: Path,
    expected_compiler_builtins_sha256: str,
    platform: str,
    maximum: str,
) -> int:
    if hashlib.sha256(compiler_builtins.read_bytes()).hexdigest() != expected_compiler_builtins_sha256:
        raise PruningError("compiler-builtins rlib differs from the immutable expected input")

    target_records = deployment_records(archive)
    check_platform(target_records, platform)
    limit = version(maximum)
    target_too_new = members_above(target_records, limit)
    if not target_too_new:
        return 0

    original_mode = stat.S_IMODE(archive.stat().st_mode)
    with tempfile.TemporaryDirectory(
        prefix="dobby-compiler-builtins-", dir=archive.parent
    ) as temporary:
        temporary_path = Path(temporary)
        reference_records = deployment_records(compiler_builtins)
        check_platform(reference_records, platform)
        reference_too_new = set(members_above(reference_records, limit))
        if not set(target_too_new).issubset(reference_too_new):
            raise PruningError("too-new members are not a subset of pinned compiler-builtins")

        reference_members = parse_archive(compiler_builtins.read_bytes())
        reference_payloads: dict[str, str] = {}
        for member in reference_members:
            if member.name not in reference_too_new:
                continue
            digest = sha256_bytes(member.payload)
            previous = reference_payloads.setdefault(member.name, digest)
            if previous != digest:
                raise PruningError("pinned compiler-builtins contains ambiguous duplicate members")
        if set(reference_payloads) != reference_too_new:
            raise PruningError("pinned compiler-builtins member metadata is incomplete")

        members = parse_archive(archive.read_bytes())
        require_unmerged_input(members)
        removed = prunable_indexes(members, target_too_new, reference_payloads)
        candidate = temporary_path / archive.name
        candidate.write_bytes(archive_bytes_without(members, removed))
        candidate.chmod(original_mode)
        subprocess.run(
            ["xcrun", "ranlib", str(candidate)],
            check=True,
            stdin=subprocess.DEVNULL,
        )
        retained_records = deployment_records(candidate)
        verify(retained_records, platform, maximum)
        os.replace(candidate, archive)
    return sum(target_too_new.values())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--compiler-builtins", required=True, type=Path)
    parser.add_argument("--expected-compiler-builtins-sha256", required=True)
    parser.add_argument("--platform", required=True, choices=sorted(PLATFORMS))
    parser.add_argument("--maximum-deployment-target", required=True)
    args = parser.parse_args()
    try:
        if re.fullmatch(r"[0-9a-f]{64}", args.expected_compiler_builtins_sha256) is None:
            raise PruningError("expected compiler-builtins digest is malformed")
        archive = args.archive.resolve(strict=True)
        compiler_builtins = args.compiler_builtins.resolve(strict=True)
        removed = prune(
            archive,
            compiler_builtins,
            args.expected_compiler_builtins_sha256,
            args.platform,
            args.maximum_deployment_target,
        )
    except (
        OSError,
        PruningError,
        VerificationError,
        subprocess.CalledProcessError,
    ) as error:
        print(f"error: Apple compiler-builtins pruning failed: {error}")
        return 1
    print(f"Apple compiler-builtins pruning verified removed_members={removed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
