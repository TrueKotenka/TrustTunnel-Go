from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile


SCRIPT = Path(__file__).resolve().parents[1] / "package_release_assets.py"
SPEC = importlib.util.spec_from_file_location("package_release_assets", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

SOURCE_SHA = "a" * 40
RUNS = {"android": "1", "ios": "2", "linux": "3", "macos": "4", "windows": "5"}


class PackageReleaseAssetsTests(unittest.TestCase):
    def make_input(self, root: Path) -> None:
        for spec in MODULE.ASSETS:
            for _, source in spec.members:
                path = root / spec.input_directory / source
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(f"{spec.name}:{source}".encode())

    def test_package_is_deterministic_and_verifiable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_root = root / "input"
            self.make_input(input_root)
            first = root / "first"
            second = root / "second"
            first_manifest = MODULE.package_assets(SOURCE_SHA, RUNS, input_root, first)
            second_manifest = MODULE.package_assets(SOURCE_SHA, RUNS, input_root, second)
            self.assertEqual(first_manifest.read_bytes(), second_manifest.read_bytes())
            for spec in MODULE.ASSETS:
                self.assertEqual((first / spec.name).read_bytes(), (second / spec.name).read_bytes())
                with zipfile.ZipFile(first / spec.name) as archive:
                    self.assertEqual(archive.namelist(), [name for name, _ in spec.members])
            MODULE.verify_assets(SOURCE_SHA, first, first_manifest)

    def test_unexpected_input_and_published_bytes_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_root = root / "input"
            self.make_input(input_root)
            unexpected = input_root / "linux" / "unexpected.bin"
            unexpected.write_bytes(b"no")
            with self.assertRaisesRegex(MODULE.ReleaseAssetError, "allowlist mismatch"):
                MODULE.package_assets(SOURCE_SHA, RUNS, input_root, root / "rejected-output")
            unexpected.unlink()
            output = root / "output"
            manifest = MODULE.package_assets(SOURCE_SHA, RUNS, input_root, output)
            (output / MODULE.ASSETS[0].name).write_bytes(b"changed")
            with self.assertRaisesRegex(MODULE.ReleaseAssetError, "digest mismatch"):
                MODULE.verify_assets(SOURCE_SHA, output, manifest)

    def test_manifest_requires_exact_source_and_run_provenance(self) -> None:
        self.assertEqual(MODULE.parse_runs([f"{name}={RUNS[name]}" for name in MODULE.REQUIRED_RUNS]), RUNS)
        with self.assertRaisesRegex(MODULE.ReleaseAssetError, "runs"):
            MODULE.parse_runs(["ios=1"])
        with self.assertRaisesRegex(MODULE.ReleaseAssetError, "source SHA"):
            MODULE.validate_source_sha("not-a-sha")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_root = root / "input"
            self.make_input(input_root)
            output = root / "output"
            manifest = MODULE.package_assets(SOURCE_SHA, RUNS, input_root, output)
            document = json.loads(manifest.read_text())
            document["source_sha"] = "b" * 40
            manifest.write_bytes(MODULE.canonical_json(document))
            with self.assertRaisesRegex(MODULE.ReleaseAssetError, "contract mismatch"):
                MODULE.verify_assets(SOURCE_SHA, output, manifest)

    def test_release_workflow_requires_exact_run_provenance_and_reverification(self) -> None:
        workflow = (SCRIPT.parents[1] / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        for required in (
            "source_sha:",
            "android_run_id:",
            "ios_run_id:",
            "linux_run_id:",
            "macos_run_id:",
            "windows_run_id:",
            'test "$current_main" = "$SOURCE_SHA"',
            'test "$(jq -r .headSha <<<"$run")" = "$expected_sha"',
            'test "$(jq -r .conclusion <<<"$run")" = "success"',
            'test "$(jq -r .workflowName <<<"$run")" = "$workflow_name"',
            "git tag --annotate",
            "--draft",
            "--verify-tag",
            "release-assets.manifest.json",
            "lib/static-libraries.provenance.json",
            'test "$(git rev-parse "$SOURCE_SHA^")" = "$static_source_sha"',
            'git diff --name-only "$static_source_sha" "$SOURCE_SHA"',
            'verify_recorded_static android "Build Android"',
            "cmp release-input/android-static/libdobby_bridge.a lib/android/arm64-v8a/libdobby_bridge.a",
            "cmp release-input/ios/libdobby_bridge.a lib/ios/libdobby_bridge.a",
            "cmp release-input/macos-static/libdobby_bridge.a lib/macos/libdobby_bridge.a",
            "gh release download",
            "gh release edit",
            "rollback()",
            "exact_remote_tag_object()",
            "verify_owned_remote_tag()",
            'git rev-parse "$RELEASE_TAG^{tag}"',
            'test "$remote_tag_object" = "$tag_object"',
            'test "$remote_target" = "$SOURCE_SHA"',
            'test "$(jq -r .draft <<<"$release_record")" = true',
            'test "$(jq -r .draft <<<"$release_record")" = false',
            "published=true",
            "trap - EXIT",
        ):
            self.assertIn(required, workflow)
        self.assertEqual(workflow.count("verify_owned_remote_tag"), 5)
        self.assertIn("^v(0|[1-9][0-9]*)", workflow)
        self.assertNotIn('if gh release view "$RELEASE_TAG"', workflow)


if __name__ == "__main__":
    unittest.main()
