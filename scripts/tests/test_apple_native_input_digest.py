from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "apple_native_input_digest.py"
SPEC = importlib.util.spec_from_file_location("apple_native_input_digest", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class AppleNativeInputDigestTests(unittest.TestCase):
    def test_contract_includes_all_public_native_inputs(self) -> None:
        required = {
            "scripts/apple_native_input_digest.py",
            "scripts/build_apple_static.sh",
            "scripts/prepare_pinned_conan.py",
            "scripts/prune_apple_compiler_builtins.py",
            "scripts/verify_apple_archive.py",
            "scripts/pins/quiche-0.17.1.Cargo.lock",
            "TrustTunnelClient/trusttunnel/Cargo.lock",
            "dobby_bridge/dobby_bridge_ios.mm",
        }
        self.assertTrue(required.issubset(MODULE.SHARED_INPUTS))
        self.assertEqual(
            MODULE.PLATFORMS["ios"]["conan_lock"],
            "scripts/pins/conan/apple-ios-arm64.lock",
        )
        self.assertEqual(
            MODULE.PLATFORMS["macos"]["conan_lock"],
            "scripts/pins/conan/apple-macos-arm64.lock",
        )

    def test_digest_is_deterministic_and_platform_specific(self) -> None:
        root = SCRIPT.parents[1]
        common = dict(
            root=root,
            conan_version="2.12.2",
            xcode_key="xcode-26.3-17C529",
            deployment_target="15.6",
            rust_toolchain="1.85.0",
        )
        first = MODULE.digest(platform="ios", **common)
        self.assertRegex(first, r"^[0-9a-f]{64}$")
        self.assertEqual(first, MODULE.digest(platform="ios", **common))
        self.assertNotEqual(first, MODULE.digest(platform="macos", **(common | {"deployment_target": "15.0"})))

    def test_dirty_or_mismatched_submodule_is_rejected(self) -> None:
        root = SCRIPT.parents[1]
        responses = iter((
            "160000 commit abcdef0123456789abcdef0123456789abcdef01\tTrustTunnelClient\n",
            "abcdef0123456789abcdef0123456789abcdef01\n",
            " M trusttunnel/Cargo.lock\n",
        ))
        with mock.patch.object(MODULE, "git_output", side_effect=lambda *_: next(responses)):
            with self.assertRaisesRegex(MODULE.InputError, "must be clean"):
                MODULE.trusttunnel_gitlink(root)

    def test_constants_affect_the_digest(self) -> None:
        root = SCRIPT.parents[1]
        common = dict(
            root=root,
            platform="ios",
            conan_version="2.12.2",
            xcode_key="xcode-26.3-17C529",
            deployment_target="15.6",
            rust_toolchain="1.85.0",
        )
        baseline = MODULE.digest(**common)
        self.assertNotEqual(baseline, MODULE.digest(**(common | {"xcode_key": "xcode-other"})))
        self.assertNotEqual(baseline, MODULE.digest(**(common | {"rust_toolchain": "1.85.1"})))


if __name__ == "__main__":
    unittest.main()
