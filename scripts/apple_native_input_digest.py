#!/usr/bin/env python3
"""Print the deterministic Apple native-build input digest for one platform.

The digest deliberately binds cache reuse to the checked-out TrustTunnel
submodule, all public Apple build tooling, and the platform-specific locked
Conan graph.  It does not read build outputs or cache state.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import subprocess
import sys


PLATFORMS = {
    "ios": {
        "conan_lock": "scripts/pins/conan/apple-ios-arm64.lock",
        "workflow": ".github/workflows/build-ios.yml",
        "profile": "apple-ios-arm64",
        "sdk": "iphoneos",
        "rust_target": "aarch64-apple-ios",
    },
    "macos": {
        "conan_lock": "scripts/pins/conan/apple-macos-arm64.lock",
        "workflow": ".github/workflows/build-macos.yml",
        "profile": "apple-macos-arm64",
        "sdk": "macosx",
        "rust_target": "aarch64-apple-darwin",
    },
}

SHARED_INPUTS = (
    "scripts/apple_native_input_digest.py",
    "scripts/build_apple_static.sh",
    "scripts/prepare_pinned_conan.py",
    "scripts/prune_apple_compiler_builtins.py",
    "scripts/verify_apple_archive.py",
    "scripts/pins/quiche-0.17.1.Cargo.lock",
    "scripts/pins/quiche-0.17.1-ring-0.17.Cargo.lock",
    "TrustTunnelClient/conanfile.py",
    "TrustTunnelClient/CMakeLists.txt",
    "TrustTunnelClient/trusttunnel/CMakeLists.txt",
    "TrustTunnelClient/trusttunnel/Cargo.toml",
    "TrustTunnelClient/trusttunnel/Cargo.lock",
    "dobby_bridge/CMakeLists.txt",
    "dobby_bridge/dobby_bridge_common.h",
    "dobby_bridge/dobby_bridge_ios.mm",
    "dobby_bridge/dobby_bridge_unix.cpp",
)


class InputError(RuntimeError):
    """Raised when checkout state cannot safely identify native inputs."""


def git_output(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout


def trusttunnel_gitlink(root: Path) -> str:
    entry = git_output(root, "ls-tree", "HEAD", "--", "TrustTunnelClient").strip()
    fields = entry.split(None, 3)
    if (
        len(fields) != 4
        or fields[0] != "160000"
        or fields[1] != "commit"
        or fields[3] != "TrustTunnelClient"
    ):
        raise InputError("TrustTunnelClient is not the exact checked-in gitlink")
    gitlink = fields[2]
    submodule = root / "TrustTunnelClient"
    if git_output(submodule, "rev-parse", "HEAD").strip() != gitlink:
        raise InputError("TrustTunnelClient checkout differs from the checked-in gitlink")
    if git_output(submodule, "status", "--porcelain", "--untracked-files=all"):
        raise InputError("TrustTunnelClient checkout must be clean for Apple native cache reuse")
    return gitlink


def add_value(hasher: "hashlib._Hash", name: str, value: bytes) -> None:
    encoded_name = name.encode("utf-8")
    hasher.update(len(encoded_name).to_bytes(8, "big"))
    hasher.update(encoded_name)
    hasher.update(len(value).to_bytes(8, "big"))
    hasher.update(value)


def digest(
    root: Path,
    *,
    platform: str,
    conan_version: str,
    xcode_key: str,
    deployment_target: str,
    rust_toolchain: str,
) -> str:
    specification = PLATFORMS[platform]
    hasher = hashlib.sha256()
    add_value(hasher, "format", b"apple-native-input-digest-v1")
    for name, value in (
        ("platform", platform),
        ("conan_version", conan_version),
        ("xcode_key", xcode_key),
        ("deployment_target", deployment_target),
        ("rust_toolchain", rust_toolchain),
        ("conan_profile", specification["profile"]),
        ("sdk", specification["sdk"]),
        ("rust_target", specification["rust_target"]),
        ("trusttunnel_gitlink", trusttunnel_gitlink(root)),
    ):
        add_value(hasher, name, value.encode("utf-8"))
    for relative_path in (*SHARED_INPUTS, specification["conan_lock"], specification["workflow"]):
        path = root / relative_path
        if not path.is_file():
            raise InputError(f"required Apple native input is missing: {relative_path}")
        add_value(hasher, relative_path, path.read_bytes())
    return hasher.hexdigest()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=sorted(PLATFORMS), required=True)
    parser.add_argument("--conan-version", required=True)
    parser.add_argument("--xcode-key", required=True)
    parser.add_argument("--deployment-target", required=True)
    parser.add_argument("--rust-toolchain", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    root = Path(__file__).resolve().parents[1]
    try:
        print(
            digest(
                root,
                platform=args.platform,
                conan_version=args.conan_version,
                xcode_key=args.xcode_key,
                deployment_target=args.deployment_target,
                rust_toolchain=args.rust_toolchain,
            )
        )
    except (InputError, OSError, subprocess.CalledProcessError) as error:
        print(f"error: Apple native input digest failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
