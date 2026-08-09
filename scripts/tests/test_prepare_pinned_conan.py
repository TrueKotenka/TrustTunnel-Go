from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "prepare_pinned_conan.py"
SPEC = importlib.util.spec_from_file_location("prepare_pinned_conan", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class PinnedConanPreparationTests(unittest.TestCase):
    def test_locked_mode_requires_an_existing_absolute_lockfile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lockfile = Path(temporary) / "apple.lock"
            lockfile.write_text("{}", encoding="utf-8")
            with mock.patch.dict("os.environ", {"DOBBY_CONAN_LOCKFILE": str(lockfile)}, clear=True):
                MODULE.validate_preparation_mode("locked")

            with mock.patch.dict("os.environ", {"DOBBY_CONAN_LOCKFILE": "relative.lock"}, clear=True):
                with self.assertRaisesRegex(MODULE.PreparationError, "existing absolute"):
                    MODULE.validate_preparation_mode("locked")

            with mock.patch.dict("os.environ", {}, clear=True):
                with self.assertRaisesRegex(MODULE.PreparationError, "requires DOBBY_CONAN_LOCKFILE"):
                    MODULE.validate_preparation_mode("locked")

    def test_unlocked_mode_rejects_a_lockfile_environment(self) -> None:
        with mock.patch.dict("os.environ", {"DOBBY_CONAN_LOCKFILE": "/tmp/apple.lock"}, clear=True):
            with self.assertRaisesRegex(MODULE.PreparationError, "forbids DOBBY_CONAN_LOCKFILE"):
                MODULE.validate_preparation_mode("unlocked")

        with mock.patch.dict("os.environ", {}, clear=True):
            MODULE.validate_preparation_mode("unlocked")

    def test_tracked_apple_locks_bind_clean_cache_local_recipe_revisions(self) -> None:
        pins = SCRIPT.parent / "pins" / "conan"
        for name in ("apple-ios-arm64.lock", "apple-macos-arm64.lock"):
            MODULE.validate_locked_local_recipes(
                pins / name,
                set(MODULE.LOCAL_LOCKED_RECIPE_REVISIONS),
            )

    def test_local_recipe_lock_rejects_cache_timestamps_and_export_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lockfile = Path(temporary) / "apple.lock"
            references = sorted(MODULE.LOCAL_LOCKED_RECIPE_REVISIONS)
            lockfile.write_text(
                '{"requires": [' + ",".join(f'"{reference}"' for reference in references) + "]}",
                encoding="utf-8",
            )
            MODULE.validate_locked_local_recipes(lockfile, set(references))

            lockfile.write_text(
                '{"requires": ["' + references[0] + '%1.0"]}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(MODULE.PreparationError, "must not contain cache timestamps"):
                MODULE.validate_locked_local_recipes(lockfile, set(references))

            lockfile.write_text(
                '{"requires": [' + ",".join(f'"{reference}"' for reference in references) + "]}",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(MODULE.PreparationError, "exported Apple Conan"):
                MODULE.validate_locked_local_recipes(lockfile, set(references[1:]))
            with self.assertRaisesRegex(MODULE.PreparationError, "exported Apple Conan"):
                MODULE.validate_locked_local_recipes(
                    lockfile,
                    set(references) | {"unexpected/1.0@adguard/oss#0123456789abcdef"},
                )

    def test_recipe_export_returns_exact_revision_without_timestamp(self) -> None:
        recipe = Path("recipe")
        expected = "example/1.0@adguard/oss#0123456789abcdef"
        with mock.patch.object(MODULE, "run", return_value=f'{{"reference": "{expected}"}}') as command:
            self.assertEqual(MODULE.export_recipe(recipe, "1.0"), expected)
        command.assert_called_once_with(
            [
                "conan", "export", str(recipe), "--user", "adguard", "--channel", "oss",
                "--version", "1.0", "--format", "json", "-vquiet",
            ]
        )

    def test_prepares_default_conan_build_profile_explicitly(self) -> None:
        with mock.patch.object(MODULE, "run", return_value="") as command:
            MODULE.prepare_default_conan_profile()

        command.assert_called_once_with(["conan", "profile", "detect", "--force"])

    def test_profile_detection_follows_custom_settings_installation(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        prepare = source[source.index("def prepare(trusttunnel: Path, mode: str)") :]
        self.assertLess(
            prepare.index("replace_generated_provider(nlc, trusttunnel)"),
            prepare.index("prepare_default_conan_profile()"),
        )
        self.assertLess(
            prepare.index("prepare_default_conan_profile()"),
            prepare.index("export_recipe(nlc, NLC_VERSION)"),
        )

    def test_pins_trusttunnel_dns_requirement_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            trusttunnel = Path(temporary)
            conanfile = trusttunnel / "conanfile.py"
            conanfile.write_text(
                "prefix\n" + MODULE.TRUSTTUNNEL_OLD_DNS_REQUIREMENT + "suffix\n",
                encoding="utf-8",
            )

            MODULE.pin_trusttunnel_dns_requirement(trusttunnel)
            first = conanfile.read_text(encoding="utf-8")
            MODULE.pin_trusttunnel_dns_requirement(trusttunnel)

            self.assertEqual(first, conanfile.read_text(encoding="utf-8"))
            self.assertIn(MODULE.TRUSTTUNNEL_DNS_REQUIREMENT, first)
            self.assertNotIn(MODULE.TRUSTTUNNEL_OLD_DNS_REQUIREMENT, first)

    def test_rejects_unexpected_trusttunnel_dns_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            trusttunnel = Path(temporary)
            (trusttunnel / "conanfile.py").write_text(
                '        self.requires("dns-libs/other@adguard/oss")\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(MODULE.PreparationError, "differs from the pinned input"):
                MODULE.pin_trusttunnel_dns_requirement(trusttunnel)

    def test_provider_requires_exact_conan_graph_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            provider = Path(temporary) / "conan_provider.cmake"
            provider.write_text(
                "prefix\n"
                + MODULE.PROVIDER_LOCK_GUARD_POINT
                + "one --build=missing ${generator})\n"
                + "two --build=missing ${generator})\n"
                + "three --build=missing ${generator})\n",
                encoding="utf-8",
            )

            MODULE.enforce_conan_lockfile_provider(provider)

            generated = provider.read_text(encoding="utf-8")
            self.assertIn("DOBBY_CONAN_LOCKFILE is required", generated)
            self.assertIn("must be an existing absolute path", generated)
            self.assertEqual(generated.count("--lockfile=$ENV{DOBBY_CONAN_LOCKFILE}"), 3)

    def test_unlocked_provider_is_not_mutated_with_an_apple_lock_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            provider = Path(temporary) / "conan_provider.cmake"
            original = (
                "prefix\n"
                + MODULE.PROVIDER_LOCK_GUARD_POINT
                + "one --build=missing ${generator})\n"
                + "two --build=missing ${generator})\n"
                + "three --build=missing ${generator})\n"
            )
            provider.write_text(original, encoding="utf-8")

            MODULE.configure_conan_provider(provider, "unlocked")

            self.assertEqual(provider.read_text(encoding="utf-8"), original)

    def test_pins_quiche_source_lock_and_cargo_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            nlc = Path(temporary)
            recipe_directory = nlc / "conan" / "recipes" / "quiche"
            recipe_directory.mkdir(parents=True)
            recipe = recipe_directory / "conanfile.py"
            recipe.write_text(
                'from conan.tools.files import copy, replace_in_file\nfrom os.path import join\n\n'
                'class QuicheConan:\n'
                f'    version = "{MODULE.QUICHE_VERSION}"\n'
                '    exports_sources = ["CMakeLists.txt", "patches/*"]\n\n'
                '    def source(self):\n'
                '        self.run("git clone https://github.com/cloudflare/quiche.git source_subfolder")\n'
                '        self.run(f"cd source_subfolder && git checkout {self.version}")\n\n'
                '    def build(self):\n'
                '        if linux:\n'
                '            replace_in_file(self, join(self.source_folder, "source_subfolder/quiche", "Cargo.toml"), "ring = \\"0.16\\"", "ring = \\"0.17\\"")\n'
                '        if windows:\n'
                '            if windows_arm64:\n'
                '                replace_in_file(self, join(self.source_folder, "source_subfolder/quiche", "Cargo.toml"), "ring = \\"0.16\\"", "ring = \\"0.17\\"")\n'
                '        self.run("cd source_subfolder/quiche && cargo %s" % (cargo_args))\n',
                encoding="utf-8",
            )

            MODULE.pin_quiche_recipe(nlc)

            generated = recipe.read_text(encoding="utf-8")
            self.assertIn(f"git fetch --depth 1 origin {MODULE.QUICHE_SOURCE_COMMIT}", generated)
            self.assertIn(f"git checkout --detach {MODULE.QUICHE_SOURCE_COMMIT}", generated)
            self.assertIn(
                f"git merge-base --is-ancestor {MODULE.QUICHE_SOURCE_COMMIT} HEAD",
                generated,
            )
            self.assertIn(
                f"git merge-base --is-ancestor HEAD {MODULE.QUICHE_SOURCE_COMMIT}",
                generated,
            )
            self.assertNotIn("test $(git rev-parse HEAD)", generated)
            self.assertIn("from shutil import copyfile", generated)
            self.assertIn('copy(self, "Cargo.lock"', generated)
            self.assertEqual(generated.count('copyfile(join(self.export_sources_folder, "Cargo-ring-0.17.lock")'), 2)
            self.assertIn("cargo %s --locked", generated)
            self.assertEqual(
                hashlib.sha256((recipe_directory / "Cargo.lock").read_bytes()).hexdigest(),
                MODULE.QUICHE_LOCK_SHA256,
            )
            self.assertEqual(
                hashlib.sha256((recipe_directory / "Cargo-ring-0.17.lock").read_bytes()).hexdigest(),
                MODULE.QUICHE_RING_017_LOCK_SHA256,
            )
            self.assertIn(
                'name = "ring"\nversion = "0.16.20"',
                (recipe_directory / "Cargo.lock").read_text(encoding="utf-8"),
            )
            self.assertIn(
                'name = "ring"\nversion = "0.17.14"',
                (recipe_directory / "Cargo-ring-0.17.lock").read_text(encoding="utf-8"),
            )

    def test_pins_nested_native_libs_common_source_and_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary)
            recipe = checkout / "conanfile.py"
            recipe.write_text("prefix\n" + MODULE.NLC_SOURCE_METHOD + "suffix\n", encoding="utf-8")

            MODULE.pin_native_libs_common_recipe(checkout)

            generated = recipe.read_text(encoding="utf-8")
            self.assertIn(f"git fetch --depth 1 origin {MODULE.NLC_COMMIT}", generated)
            self.assertIn(f"git checkout -f {MODULE.NLC_COMMIT}", generated)
            self.assertIn(f"git merge-base --is-ancestor {MODULE.NLC_COMMIT} HEAD", generated)
            self.assertIn(f"git merge-base --is-ancestor HEAD {MODULE.NLC_COMMIT}", generated)
            self.assertNotIn("test $(git rev-parse HEAD)", generated)
            self.assertIn("settings_file.write(", generated)
            self.assertIn('version: ["17"]', generated)
            self.assertIn('clang:\\n    version: ["21"]', generated)
            self.assertIn('gcc:\\n    version: ["15"]', generated)
            self.assertNotIn("git fetch --tags", generated)

    def test_rejects_unexpected_native_libs_common_recipe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary)
            (checkout / "conanfile.py").write_text("different source method\n", encoding="utf-8")

            with self.assertRaisesRegex(MODULE.PreparationError, "differs from the pinned input"):
                MODULE.pin_native_libs_common_recipe(checkout)

    def test_pins_nested_dns_libs_source_and_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            native = root / "native"
            native.mkdir()
            (native / "cmake").mkdir()
            (native / "cmake" / "conan_provider.cmake").write_text("provider\n", encoding="utf-8")
            (native / "conan").mkdir()
            (native / "conan" / "settings_user.yml").write_text("arch: [mipsel]\n", encoding="utf-8")
            checkout = root / "dns"
            checkout.mkdir()
            recipe = checkout / "conanfile.py"
            recipe.write_text(
                "prefix\n    exports_sources = patch_files\n"
                + MODULE.DNSLIBS_SOURCE_METHOD
                + "suffix\n",
                encoding="utf-8",
            )
            (checkout / "cmake").mkdir()
            settings = checkout / "conan" / "settings_user.yml"

            MODULE.pin_dns_libs_recipe(checkout, native)

            generated = recipe.read_text(encoding="utf-8")
            self.assertIn(f"git fetch --depth 1 origin {MODULE.DNSLIBS_SOURCE_COMMIT}", generated)
            self.assertIn(f"git checkout -f {MODULE.DNSLIBS_SOURCE_COMMIT}", generated)
            self.assertIn(
                f"git merge-base --is-ancestor {MODULE.DNSLIBS_SOURCE_COMMIT} HEAD",
                generated,
            )
            self.assertIn(
                f"git merge-base --is-ancestor HEAD {MODULE.DNSLIBS_SOURCE_COMMIT}",
                generated,
            )
            self.assertNotIn("test $(git rev-parse HEAD)", generated)
            self.assertIn('exports_sources = patch_files + ["cmake/conan_provider.cmake", "conan/*"]', generated)
            self.assertNotIn("settings_file.write(", generated)
            self.assertEqual((checkout / "cmake" / "conan_provider.cmake").read_text(), "provider\n")
            self.assertIn('version: ["17"]', settings.read_text(encoding="utf-8"))
            self.assertIn('clang:\n    version: ["21"]', settings.read_text(encoding="utf-8"))
            self.assertIn('gcc:\n    version: ["15"]', settings.read_text(encoding="utf-8"))
            self.assertNotIn("self.conan_data", generated)

    def test_generated_provider_installs_supported_pinned_compiler_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            native = root / "native"
            trusttunnel = root / "trusttunnel"
            (native / "cmake").mkdir(parents=True)
            (native / "cmake" / "conan_provider.cmake").write_text("provider\n", encoding="utf-8")
            (native / "conan").mkdir()
            (native / "conan" / "settings_user.yml").write_text("arch: [mipsel]\n", encoding="utf-8")
            (trusttunnel / "cmake").mkdir(parents=True)

            with mock.patch.object(MODULE, "run", return_value="") as command:
                MODULE.replace_generated_provider(native, trusttunnel)

            settings = (trusttunnel / "conan" / "settings_user.yml")
            self.assertEqual((trusttunnel / "cmake" / "conan_provider.cmake").read_text(), "provider\n")
            self.assertIn('apple-clang:\n    version: ["17"]', settings.read_text(encoding="utf-8"))
            self.assertIn('clang:\n    version: ["21"]', settings.read_text(encoding="utf-8"))
            self.assertIn('gcc:\n    version: ["15"]', settings.read_text(encoding="utf-8"))
            command.assert_called_once_with(["conan", "config", "install", str(settings)])


if __name__ == "__main__":
    unittest.main()
