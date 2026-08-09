from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "build_apple_static.sh"


class AppleStaticBuildContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SCRIPT.read_text(encoding="utf-8")

    def test_both_consumer_links_use_an_explicit_sdk_sysroot(self) -> None:
        self.assertGreaterEqual(self.source.count("-isysroot $(xcrun --sdk"), 4)

    def test_native_compilers_remap_source_and_dependency_paths(self) -> None:
        for required in (
            "-ffile-prefix-map=$root=/dobbyvpn/source",
            "-ffile-prefix-map=$CONAN_HOME=/dobbyvpn/conan",
            "--remap-path-prefix=$root=/dobbyvpn/source",
            "--remap-path-prefix=$CONAN_HOME=/dobbyvpn/conan",
            '${CARGO_HOME:-${HOME:?HOME is required}/.cargo}',
            '${RUSTUP_HOME:-${HOME:?HOME is required}/.rustup}',
            "--remap-path-prefix=$effective_cargo_home=/dobbyvpn/cargo",
            "--remap-path-prefix=$effective_rustup_home=/dobbyvpn/rustup",
            "-e '/Users/' -e '/home/'",
            "Apple archive contains an unremapped local build path",
        ):
            self.assertIn(required, self.source)

    def test_exact_rust_toolchain_is_checked_for_both_platforms(self) -> None:
        platform_branch = self.source.index('if [[ "$platform" == ios ]]')
        for required in (
            "rustc --version --verbose",
            'release: $rust_release',
            'commit-hash: $rust_commit',
        ):
            self.assertLess(self.source.index(required), platform_branch)

    def test_exact_conan_version_is_checked_for_both_platforms(self) -> None:
        platform_branch = self.source.index('if [[ "$platform" == ios ]]')
        for required in (
            "readonly conan_version=2.12.2",
            '$(conan --version)',
            'Conan version $conan_version',
        ):
            self.assertLess(self.source.index(required), platform_branch)

    def test_trusttunnel_rust_uses_immutable_lock(self) -> None:
        self.assertIn(
            "5dfa92024c6ff9dd09f0110fe7f094c5d2e25131787b3cdbacdafb94554b2f93",
            self.source,
        )
        self.assertIn("-DCARGO_EXTRA_ARGS=--locked", self.source)

    def test_conan_graph_uses_platform_lock(self) -> None:
        self.assertIn('apple-$platform-arm64.lock', self.source)
        self.assertIn('export DOBBY_CONAN_LOCKFILE="$conan_lockfile"', self.source)
        self.assertIn('DOBBY_CONAN_LOCKFILE is required', self.source)
        self.assertIn('Apple build requires the locked Conan provider', self.source)

    def test_final_archive_verification_precedes_consumer_link(self) -> None:
        verifier = self.source.index("scripts/verify_apple_archive.py")
        privacy_scan = self.source.index("Apple archive contains an unremapped local build path")
        consumer = self.source.index("CGO_ENABLED=1 GOOS=ios")
        self.assertLess(verifier, privacy_scan)
        self.assertLess(privacy_scan, consumer)


if __name__ == "__main__":
    unittest.main()
