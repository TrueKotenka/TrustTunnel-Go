#!/usr/bin/env python3
"""Build and verify deterministic public go-go-tunnel release assets.

The script intentionally packages only the seven files produced by the named
platform workflows.  It does not build native code and accepts neither local
configuration nor credentials.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile


SOURCE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
RUN_ID_RE = re.compile(r"^[1-9][0-9]*$")
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
MANIFEST_NAME = "release-assets.manifest.json"
REQUIRED_RUNS = ("android", "ios", "linux", "macos", "windows")


class ReleaseAssetError(RuntimeError):
    """Raised when release inputs or published assets violate the contract."""


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    input_directory: str
    members: tuple[tuple[str, str], ...]


ASSETS = (
    ReleaseAsset(
        "dobby_bridge-windows-x86_64.zip",
        "windows",
        (("dobby_bridge.dll", "dobby_bridge.dll"),
         ("dobby_bridge.lib", "dobby_bridge.lib")),
    ),
    ReleaseAsset(
        "libdobby_bridge-linux-x86_64.zip",
        "linux",
        (("libdobby_bridge.so", "libdobby_bridge.so"),),
    ),
    ReleaseAsset(
        "libdobby_bridge-android-arm64-v8a.zip",
        "android-dynamic",
        (("libdobby_bridge.so", "libdobby_bridge.so"),),
    ),
    ReleaseAsset(
        "libdobby_bridge-android-arm64-v8a-static.zip",
        "android-static",
        (("libdobby_bridge.a", "libdobby_bridge.a"),),
    ),
    ReleaseAsset(
        "libdobby_bridge-ios-arm64.zip",
        "ios",
        (("libdobby_bridge.a", "libdobby_bridge.a"),),
    ),
    ReleaseAsset(
        "libdobby_bridge-macos-arm64.zip",
        "macos-dynamic",
        (("libdobby_bridge.dylib", "libdobby_bridge.dylib"),),
    ),
    ReleaseAsset(
        "libdobby_bridge-macos-arm64-static.zip",
        "macos-static",
        (("libdobby_bridge.a", "libdobby_bridge.a"),),
    ),
)


def sha256_bytes(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_runs(raw_runs: list[str]) -> dict[str, str]:
    runs: dict[str, str] = {}
    for raw in raw_runs:
        name, separator, run_id = raw.partition("=")
        if not separator or name in runs or name not in REQUIRED_RUNS or not RUN_ID_RE.fullmatch(run_id):
            raise ReleaseAssetError("runs must be unique NAME=positive-number entries for every platform")
        runs[name] = run_id
    if tuple(sorted(runs)) != REQUIRED_RUNS:
        raise ReleaseAssetError("runs must contain exactly android, ios, linux, macos, and windows")
    return runs


def canonical_json(document: object) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def validate_source_sha(source_sha: str) -> None:
    if not SOURCE_SHA_RE.fullmatch(source_sha):
        raise ReleaseAssetError("source SHA must be a lowercase full 40-character Git SHA")


def files_in(directory: Path) -> set[str]:
    if directory.is_symlink() or not directory.is_dir():
        raise ReleaseAssetError(f"required input directory is missing: {directory}")
    files: set[str] = set()
    for path in directory.rglob("*"):
        if path.is_symlink() or not path.is_file():
            if path.is_symlink():
                raise ReleaseAssetError(f"release input must not contain symlinks: {path}")
            continue
        files.add(path.relative_to(directory).as_posix())
    return files


def input_members(input_root: Path, asset: ReleaseAsset) -> list[tuple[str, Path]]:
    directory = input_root / asset.input_directory
    expected = {source for _, source in asset.members}
    observed = files_in(directory)
    if observed != expected:
        raise ReleaseAssetError(
            f"{asset.input_directory} input allowlist mismatch: expected {sorted(expected)}, got {sorted(observed)}"
        )
    return [(member, directory / source) for member, source in asset.members]


def write_zip(output: Path, members: list[tuple[str, Path]]) -> list[dict[str, object]]:
    output.parent.mkdir(parents=True, exist_ok=True)
    member_manifest: list[dict[str, object]] = []
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9, strict_timestamps=True) as archive:
        archive.comment = b""
        for name, source in sorted(members):
            contents = source.read_bytes()
            entry = zipfile.ZipInfo(filename=name, date_time=ZIP_TIMESTAMP)
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, contents, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
            member_manifest.append({"name": name, "sha256": sha256_bytes(contents), "size": len(contents)})
    return member_manifest


def package_assets(source_sha: str, runs: dict[str, str], input_root: Path, output_directory: Path) -> Path:
    validate_source_sha(source_sha)
    if tuple(sorted(runs)) != REQUIRED_RUNS:
        raise ReleaseAssetError("release-run mapping is incomplete")
    if output_directory.exists():
        raise ReleaseAssetError(f"refusing to reuse release output directory: {output_directory}")
    output_directory.mkdir(parents=True)
    manifest_assets: list[dict[str, object]] = []
    for asset in ASSETS:
        members = write_zip(output_directory / asset.name, input_members(input_root, asset))
        output = output_directory / asset.name
        manifest_assets.append(
            {"name": asset.name, "sha256": sha256_file(output), "size": output.stat().st_size, "members": members}
        )
    document = {"assets": manifest_assets, "runs": runs, "schema": 1, "source_sha": source_sha}
    manifest = output_directory / MANIFEST_NAME
    manifest.write_bytes(canonical_json(document))
    return manifest


def verify_assets(source_sha: str, asset_directory: Path, manifest: Path) -> None:
    validate_source_sha(source_sha)
    if manifest.name != MANIFEST_NAME or manifest.parent != asset_directory:
        raise ReleaseAssetError("manifest must be the canonical file in the release asset directory")
    try:
        document = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseAssetError("release manifest is unreadable") from error
    if manifest.read_bytes() != canonical_json(document):
        raise ReleaseAssetError("release manifest is not canonical JSON")
    expected_assets = [asset.name for asset in ASSETS]
    if (
        not isinstance(document, dict)
        or document.get("schema") != 1
        or document.get("source_sha") != source_sha
        or not isinstance(document.get("assets"), list)
        or len(document["assets"]) != len(expected_assets)
        or not all(isinstance(item, dict) for item in document["assets"])
        or [item.get("name") for item in document["assets"]] != expected_assets
    ):
        raise ReleaseAssetError("release manifest contract mismatch")
    runs = document.get("runs")
    if not isinstance(runs, dict) or tuple(sorted(runs)) != REQUIRED_RUNS or any(
        not isinstance(value, str) or not RUN_ID_RE.fullmatch(value) for value in runs.values()
    ):
        raise ReleaseAssetError("release manifest run provenance is invalid")
    allowed_files = set(expected_assets + [MANIFEST_NAME])
    observed_files = files_in(asset_directory)
    if observed_files != allowed_files:
        raise ReleaseAssetError(f"release asset allowlist mismatch: expected {sorted(allowed_files)}, got {sorted(observed_files)}")
    for spec, recorded in zip(ASSETS, document["assets"], strict=True):
        if not isinstance(recorded, dict):
            raise ReleaseAssetError("release manifest asset record is invalid")
        archive_path = asset_directory / spec.name
        if recorded.get("sha256") != sha256_file(archive_path) or recorded.get("size") != archive_path.stat().st_size:
            raise ReleaseAssetError(f"published asset digest mismatch: {spec.name}")
        expected_members = [name for name, _ in spec.members]
        recorded_members = recorded.get("members")
        if not isinstance(recorded_members, list) or [item.get("name") for item in recorded_members if isinstance(item, dict)] != expected_members:
            raise ReleaseAssetError(f"published asset member manifest mismatch: {spec.name}")
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            if archive.comment or [info.filename for info in infos] != expected_members or archive.testzip() is not None:
                raise ReleaseAssetError(f"published asset ZIP structure mismatch: {spec.name}")
            for info, member in zip(infos, recorded_members, strict=True):
                if (
                    info.is_dir()
                    or info.date_time != ZIP_TIMESTAMP
                    or info.compress_type != zipfile.ZIP_DEFLATED
                    or info.flag_bits & 0x1
                    or (info.external_attr >> 16) != 0o100644
                ):
                    raise ReleaseAssetError(f"published asset ZIP metadata mismatch: {spec.name}:{info.filename}")
                contents = archive.read(info)
                if member.get("sha256") != sha256_bytes(contents) or member.get("size") != len(contents):
                    raise ReleaseAssetError(f"published asset member digest mismatch: {spec.name}:{info.filename}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    package = subcommands.add_parser("package", help="build deterministic ZIP assets and manifest")
    package.add_argument("--source-sha", required=True)
    package.add_argument("--run", action="append", default=[], metavar="NAME=ID")
    package.add_argument("--input-dir", type=Path, required=True)
    package.add_argument("--output-dir", type=Path, required=True)
    verify = subcommands.add_parser("verify", help="verify a release directory against its manifest")
    verify.add_argument("--source-sha", required=True)
    verify.add_argument("--asset-dir", type=Path, required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "package":
            manifest = package_assets(args.source_sha, parse_runs(args.run), args.input_dir, args.output_dir)
            print(manifest)
        else:
            verify_assets(args.source_sha, args.asset_dir, args.manifest)
    except ReleaseAssetError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
