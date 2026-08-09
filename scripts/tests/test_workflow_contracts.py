from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


class WorkflowContractTests(unittest.TestCase):
    def test_apple_workflows_pin_the_arm64_macos_image(self) -> None:
        for name in ("build-ios.yml", "build-macos.yml"):
            source = (WORKFLOWS / name).read_text(encoding="utf-8")
            self.assertNotIn("runs-on: macos-latest", source, name)
            self.assertIn("runs-on: macos-15", source, name)

    def test_public_workflows_do_not_require_live_connection_secrets(self) -> None:
        for workflow in WORKFLOWS.glob("*.yml"):
            source = workflow.read_text(encoding="utf-8")
            self.assertNotIn("TT_CONFIG", source, workflow.name)
            self.assertNotIn("secrets.TT_CONFIG", source, workflow.name)

    def test_dynamic_macos_build_is_reproducibly_constrained(self) -> None:
        source = (WORKFLOWS / "build-macos.yml").read_text(encoding="utf-8")
        for required in (
            "dobby-conan-macos-dynamic",
            'test "$(conan --version)" = "Conan version $CONAN_VERSION"',
            "-DCMAKE_OSX_ARCHITECTURES=arm64",
            '-DCMAKE_OSX_SYSROOT="$(xcrun --sdk macosx --show-sdk-path)"',
            "-DCARGO_EXTRA_ARGS=--locked",
            "-ffile-prefix-map=$GITHUB_WORKSPACE=/dobbyvpn/source",
            "--remap-path-prefix=$CONAN_HOME=/dobbyvpn/conan",
            'export CFLAGS="$PREFIX_FLAGS"',
            'export CXXFLAGS="$PREFIX_FLAGS"',
            "lipo -archs lib/macos/libdobby_bridge.dylib",
            "vtool -show-build lib/macos/libdobby_bridge.dylib",
            "^[[:space:]]*minos[[:space:]]+15(\\.0+)?[[:space:]]*$",
            "macOS dynamic archive contains an unremapped local build path",
        ):
            self.assertIn(required, source)
        self.assertNotIn("grep -Eq 'minos +15(\\.0+)?'", source)

    def test_apple_static_jobs_run_every_tooling_suite(self) -> None:
        suites = (
            "test_apple_native_input_digest.py",
            "test_build_apple_static.py",
            "test_package_release_assets.py",
            "test_prepare_pinned_conan.py",
            "test_prune_apple_compiler_builtins.py",
            "test_repository_text_contract.py",
            "test_verify_apple_archive.py",
            "test_workflow_contracts.py",
        )
        for name in ("build-ios.yml", "build-macos.yml"):
            source = (WORKFLOWS / name).read_text(encoding="utf-8")
            static_source = source if name == "build-ios.yml" else source[source.index("macos-arm64-static:"):]
            for suite in suites:
                self.assertIn(suite, static_source, f"{name}: {suite}")
            self.assertLess(
                static_source.index("Verify Apple static tooling on pristine checkout"),
                static_source.index("Prepare pinned Conan recipes and provider"),
                name,
            )
            self.assertLess(
                static_source.index("Prepare pinned Conan recipes and provider"),
                static_source.index("scripts/build_apple_static.sh"),
                name,
            )

    def test_apple_workflows_explicitly_prepare_guarded_conan_providers(self) -> None:
        for name in ("build-ios.yml", "build-macos.yml"):
            source = (WORKFLOWS / name).read_text(encoding="utf-8")
            self.assertIn("DOBBY_CONAN_LOCKFILE:", source, name)
            self.assertIn("prepare_pinned_conan.py --trusttunnel TrustTunnelClient --mode locked", source, name)

    def test_non_apple_workflows_use_exact_unlocked_preparation(self) -> None:
        for name in ("build-linux.yml", "build-windows.yml", "build-android.yml"):
            source = (WORKFLOWS / name).read_text(encoding="utf-8")
            self.assertIn("CONAN_VERSION: 2.12.2", source, name)
            self.assertIn("Configure isolated Conan home", source, name)
            self.assertIn("CONAN_HOME=$RUNNER_TEMP/", source, name)
            self.assertIn("$GITHUB_ENV", source, name)
            self.assertNotIn("CONAN_HOME: ${{ runner.temp }}", source, name)
            self.assertIn("prepare_pinned_conan.py", source, name)
            self.assertIn("--mode unlocked", source, name)
            self.assertIn("scripts/prepare_pinned_conan.py", source, name)
            self.assertIn("scripts/pins/quiche-0.17.1.Cargo.lock", source, name)
            self.assertIn("scripts/pins/quiche-0.17.1-ring-0.17.Cargo.lock", source, name)
            self.assertIn('toolchain: "1.85.0"', source, name)
            self.assertIn("conan --version", source, name)
            self.assertNotIn("bootstrap_conan_deps.py", source, name)
            self.assertNotIn("Patch dns-libs", source, name)
            self.assertNotIn("2.8.51", source, name)
            self.assertNotIn("DOBBY_CONAN_LOCKFILE", source, name)
            self.assertNotIn("restore-keys:", source, name)
            self.assertNotIn("pip install -r TrustTunnelClient", source, name)
            self.assertIn('"tqdm==4.67.1"', source, name)

        self.assertIn("runs-on: ubuntu-24.04", (WORKFLOWS / "build-linux.yml").read_text(encoding="utf-8"))
        self.assertEqual(
            (WORKFLOWS / "build-android.yml").read_text(encoding="utf-8").count("runs-on: ubuntu-24.04"),
            2,
        )

    def test_llvm_installer_is_verified_before_execution(self) -> None:
        expected_hash = "03878e08f47b66cc95bc4b544b0db3c6d9ce8d60e6cf2492ae357984330a9eae"
        for name, expected_count in (("build-linux.yml", 1), ("build-android.yml", 2)):
            source = (WORKFLOWS / name).read_text(encoding="utf-8")
            self.assertIn(f"LLVM_INSTALLER_SHA256: {expected_hash}", source, name)
            self.assertEqual(source.count("sha256sum --check --strict"), expected_count, name)
            self.assertEqual(source.count('sudo bash "$llvm_installer" "$LLVM_MAJOR_VER"'), expected_count, name)
            self.assertNotIn("curl -O https://apt.llvm.org/llvm.sh", source, name)

    def test_all_conan_homes_use_step_level_runner_context(self) -> None:
        for name in (
            "build-android.yml",
            "build-ios.yml",
            "build-linux.yml",
            "build-macos.yml",
            "build-windows.yml",
        ):
            source = (WORKFLOWS / name).read_text(encoding="utf-8")
            self.assertNotIn("CONAN_HOME: ${{ runner.temp }}", source, name)
            self.assertIn("Configure isolated Conan home", source, name)

    def test_android_hosted_setup_and_cargo_ndk_are_exact(self) -> None:
        source = (WORKFLOWS / "build-android.yml").read_text(encoding="utf-8")
        self.assertEqual(source.count("uses: actions/setup-java@v5"), 2)
        self.assertEqual(source.count("uses: android-actions/setup-android@v4"), 2)
        self.assertNotIn("actions/setup-java@v4", source)
        self.assertNotIn("android-actions/setup-android@v3", source)
        self.assertEqual(source.count("cargo install cargo-ndk --version 3.5.4"), 2)
        self.assertNotIn("cargo install cargo-ndk || true", source)
        self.assertEqual(source.count('go-version-file: "go.mod"'), 2)
        self.assertNotIn("go-version: '1.25'", source)

    def test_windows_declares_the_exact_static_msvc_runtime(self) -> None:
        source = (WORKFLOWS / "build-windows.yml").read_text(encoding="utf-8")
        self.assertEqual(
            source.count("-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded `"),
            1,
        )
        trusttunnel = (ROOT / "TrustTunnelClient" / "CMakeLists.txt").read_text(encoding="utf-8")
        bridge = (ROOT / "dobby_bridge" / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("set(CMAKE_MSVC_RUNTIME_LIBRARY MultiThreaded)", trusttunnel)
        self.assertIn('MSVC_RUNTIME_LIBRARY "MultiThreaded"', bridge)
        self.assertNotIn("MultiThreadedDLL", source + trusttunnel + bridge)

    def test_windows_msvc_195_compatibility_is_narrow_and_fail_closed(self) -> None:
        source = (WORKFLOWS / "build-windows.yml").read_text(encoding="utf-8")
        profile_path = ROOT / "scripts" / "pins" / "conan" / "windows-msvc-195-compat.profile"
        profile = profile_path.read_text(encoding="utf-8")
        expected_profiles = (
            "TrustTunnelClient/conan/profiles/windows-msvc.jinja;"
            "auto-cmake;"
            "${{ github.workspace }}/scripts/pins/conan/windows-msvc-195-compat.profile"
        )

        self.assertIn("Get-Command cl.exe -CommandType Application -ErrorAction Stop", source)
        self.assertIn("Get-Command link.exe -CommandType Application -ErrorAction Stop", source)
        self.assertIn("$compilerVersion -notmatch '^19\\.51\\.'", source)
        self.assertIn("$linkerVersion -notmatch '^14\\.51\\.'", source)
        self.assertIn("$env:VCToolsVersion -notmatch '^14\\.51\\.'", source)
        self.assertIn("$compiler.Source -notmatch '\\\\Hostx64\\\\x64\\\\cl\\.exe$'", source)
        self.assertIn("$linker.Source -notmatch '\\\\Hostx64\\\\x64\\\\link\\.exe$'", source)
        self.assertIn(expected_profiles, source)
        self.assertEqual(source.count("scripts/pins/conan/windows-msvc-195-compat.profile"), 6)
        self.assertIn("(?m)^compiler\\.version=195\\r?$", source)
        self.assertIn("$settings.'compiler.version' -ne '194'", source)
        self.assertIn("$settings.'compiler.runtime' -ne 'static'", source)
        self.assertIn("$settings.'compiler.runtime_type' -ne 'Release'", source)
        self.assertEqual(profile.count("compiler.version=194"), 1)
        self.assertIn("actual compiler", profile)
        self.assertIn("/GL", profile)
        self.assertIn("/LTCG", profile)
        self.assertIn("CMake IPO", profile)

    def test_windows_compatibility_contract_forbids_whole_program_optimization(self) -> None:
        build_input_patterns = (
            "CMakeLists.txt",
            "CMakePresets.json",
            "CMakeUserPresets.json",
            "Makefile",
            "*.bat",
            "*.cmake",
            "*.cmd",
            "*.jinja",
            "*.mk",
            "*.profile",
            "*.props",
            "*.ps1",
            "*.py",
            "*.sh",
            "*.targets",
            "*.vcxproj",
            "*.yaml",
            "*.yml",
        )
        checked = {
            path
            for pattern in build_input_patterns
            for path in ROOT.rglob(pattern)
            if ".git" not in path.parts
            and "tests" not in path.parts
            and "integration-tests" not in path.parts
        }
        forbidden = (
            "/gl",
            "/ltcg",
            "-flto",
            "interprocedural_optimization",
            "wholeprogramoptimization",
        )
        comment_prefixes = ("#", "//", "<!--", "*", "-->")
        for path in checked:
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if line.lstrip().startswith(comment_prefixes):
                    continue
                folded = line.casefold()
                for value in forbidden:
                    self.assertNotIn(value, folded, f"{path}:{line_number}: {value}")

    def test_documentation_marks_non_apple_conan_graphs_unlocked(self) -> None:
        source = (ROOT / "scripts" / "README.md").read_text(encoding="utf-8")
        self.assertIn("--mode locked", source)
        self.assertIn("--mode unlocked", source)
        self.assertIn("not a fully locked binary dependency graph", source)

    def test_static_library_update_is_exact_source_and_fail_closed(self) -> None:
        source = (WORKFLOWS / "update-static-libs.yml").read_text(encoding="utf-8")
        for required in (
            "source_sha:",
            "android_run_id:",
            "ios_run_id:",
            "macos_run_id:",
            'git check-ref-format --branch "$TARGET_BRANCH"',
            'test "$(git rev-parse HEAD)" = "$SOURCE_SHA"',
            'gh run view "$run_id" --json headSha',
            'gh run view "$run_id" --json conclusion',
            'gh run view "$run_id" --json workflowName',
            "lib/static-libraries.provenance.json",
            "verify_single_archive()",
            'test "$entries" = "libdobby_bridge.a"',
            'test ! -L "$expected"',
            'git fetch origin "+refs/heads/$TARGET_BRANCH:refs/remotes/origin/$TARGET_BRANCH"',
            'test "$(git rev-parse "origin/$TARGET_BRANCH")" = "$SOURCE_SHA"',
        ):
            self.assertIn(required, source)
        self.assertNotIn("get_latest_run", source)
        self.assertNotIn("|| echo", source)
        self.assertEqual(source.count("set -euo pipefail"), 3)


if __name__ == "__main__":
    unittest.main()
