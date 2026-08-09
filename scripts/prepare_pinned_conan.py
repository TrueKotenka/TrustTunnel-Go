#!/usr/bin/env python3
"""Install the exact Conan recipes/provider used by native bridge builds."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


DNSLIBS_VERSION = "2.8.52"
DNSLIBS_COMMIT = "c482c51a8b37a1cf90f43d37dfffaf8c9e80f816"
DNSLIBS_SOURCE_COMMIT = "322b7aad75a6c7a6c090205306b534c08641ab7c"
DNSLIBS_URL = "https://github.com/AdguardTeam/DnsLibs.git"
NLC_VERSION = "8.1.28"
NLC_COMMIT = "e9ea7f8b99294d1638c40edb1c14001416edf8fa"
NLC_URL = "https://github.com/AdguardTeam/NativeLibsCommon.git"
QUICHE_VERSION = "0.17.1"
QUICHE_SOURCE_COMMIT = "adca4bc48b2061e92cbc6faa4ec972da7e356b5b"
QUICHE_LOCK_SHA256 = "8a84dc0a2e85ccb254002b5284f62be87817df5c40487a3cf197168973bc2836"
QUICHE_LOCK = Path(__file__).resolve().parent / "pins" / "quiche-0.17.1.Cargo.lock"
TRUSTTUNNEL_DNS_REQUIREMENT = '        self.requires("dns-libs/2.8.52@adguard/oss", transitive_headers=True)\n'
TRUSTTUNNEL_OLD_DNS_REQUIREMENT = '        self.requires("dns-libs/2.8.51@adguard/oss", transitive_headers=True)\n'
APPLE_CLANG_SETTINGS = """

# Conan 2.12 predates Xcode 26.3. Keep the supported compiler extension local
# to this exact build input instead of accepting an unpinned Conan upgrade.
compiler:
  apple-clang:
    version: ["17"]
"""
NLC_SOURCE_METHOD = '''    def source(self):
        self.run(f"git init . && git remote add origin {self.vcs_url} && git fetch --tags")
        if re.match(r'\\d+\\.\\d+\\.\\d+', self.version) is not None:
            # Use git tag for versioned releases
            self.run("git checkout -f v%s" % self.version)
        else:
            self.run("git checkout -f %s" % self.version)
        for p in self.patch_files:
            patch(self, patch_file=p)
'''
DNSLIBS_SOURCE_METHOD = '''    def source(self):
        self.run(f"git init . && git remote add origin {self.vcs_url} && git fetch")
        if re.match(r'\\d+\\.\\d+\\.\\d+', self.version) is not None:
            version_hash = self.conan_data["commit_hash"][self.version]["hash"]
            self.run("git checkout -f %s" % version_hash)
        else:
            self.run("git checkout -f %s" % self.version)
            for p in self.patch_files:
                patch(self, patch_file=p)
'''
PROVIDER_LOCK_GUARD_POINT = '''        get_property(_multiconfig_generator GLOBAL PROPERTY GENERATOR_IS_MULTI_CONFIG)
'''
PROVIDER_INSTALL_SUFFIX = "--build=missing ${generator})"
PREPARATION_MODES = ("locked", "unlocked")


class PreparationError(RuntimeError):
    pass


def replace_exact(source: str, old: str, new: str, description: str) -> str:
    if source.count(old) != 1:
        raise PreparationError(f"{description} differs from the pinned input")
    return source.replace(old, new)


def pin_trusttunnel_dns_requirement(trusttunnel: Path) -> None:
    """Apply the exact upstream DNS requirement used by the locked graph."""
    conanfile = trusttunnel / "conanfile.py"
    source = conanfile.read_text(encoding="utf-8")
    if source.count(TRUSTTUNNEL_DNS_REQUIREMENT) == 1 and TRUSTTUNNEL_OLD_DNS_REQUIREMENT not in source:
        return
    conanfile.write_text(
        replace_exact(
            source,
            TRUSTTUNNEL_OLD_DNS_REQUIREMENT,
            TRUSTTUNNEL_DNS_REQUIREMENT,
            "TrustTunnelClient dns-libs requirement",
        ),
        encoding="utf-8",
    )


def validate_preparation_mode(mode: str) -> None:
    """Require an explicit, internally consistent Conan-provider mode."""
    lockfile = os.environ.get("DOBBY_CONAN_LOCKFILE")
    if mode == "locked":
        if lockfile is None:
            raise PreparationError("locked preparation requires DOBBY_CONAN_LOCKFILE")
        path = Path(lockfile)
        if not path.is_absolute() or not path.is_file():
            raise PreparationError(
                "locked preparation requires DOBBY_CONAN_LOCKFILE to be an existing absolute file"
            )
        return
    if mode == "unlocked":
        if lockfile is not None:
            raise PreparationError("unlocked preparation forbids DOBBY_CONAN_LOCKFILE")
        return
    raise PreparationError("unknown Conan preparation mode")


def prepare_default_conan_profile() -> None:
    """Materialize the native build profile before CMake-Conan needs it."""
    run(["conan", "profile", "detect", "--force"])


def run(arguments: list[str], *, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        arguments,
        cwd=cwd,
        check=True,
        stdin=subprocess.DEVNULL,
        text=True,
        stdout=subprocess.PIPE,
    )
    return completed.stdout.strip()


def checkout(url: str, commit: str, destination: Path) -> None:
    destination.mkdir()
    run(["git", "init", "--quiet"], cwd=destination)
    run(["git", "remote", "add", "origin", url], cwd=destination)
    run(["git", "fetch", "--quiet", "--depth", "1", "origin", commit], cwd=destination)
    run(["git", "checkout", "--quiet", "--detach", "FETCH_HEAD"], cwd=destination)
    if run(["git", "rev-parse", "HEAD"], cwd=destination) != commit:
        raise PreparationError("dependency checkout differs from its pinned commit")


def pin_nested_recipe_source(
    checkout_root: Path, expected_method: str, source_commit: str, dependency: str,
    *, append_settings: bool = True,
) -> None:
    """Make an exported recipe use its exact source and nested settings."""
    recipe_path = checkout_root / "conanfile.py"
    recipe = recipe_path.read_text(encoding="utf-8")
    if recipe.count(expected_method) != 1:
        raise PreparationError(f"{dependency} source method differs from the pinned input")
    settings_injection = f'''        settings_path = join(self.source_folder, "conan", "settings_user.yml")
        with open(settings_path, "a", encoding="utf-8") as settings_file:
            settings_file.write({APPLE_CLANG_SETTINGS!r})
''' if append_settings else ""
    replacement = f'''    def source(self):
        self.run("git init . && git remote add origin {{}}".format(self.vcs_url))
        self.run("git fetch --depth 1 origin {source_commit}")
        self.run("git checkout -f {source_commit}")
        self.run("test $(git rev-parse HEAD) = {source_commit}")
{settings_injection}        for p in self.patch_files:
            patch(self, patch_file=p)
'''
    recipe_path.write_text(recipe.replace(expected_method, replacement), encoding="utf-8")


def pin_native_libs_common_recipe(nlc: Path) -> None:
    pin_nested_recipe_source(nlc, NLC_SOURCE_METHOD, NLC_COMMIT, "NativeLibsCommon")


def pin_quiche_recipe(nlc: Path) -> None:
    recipe_directory = nlc / "conan" / "recipes" / "quiche"
    recipe_path = recipe_directory / "conanfile.py"
    lock = QUICHE_LOCK.read_bytes()
    if hashlib.sha256(lock).hexdigest() != QUICHE_LOCK_SHA256:
        raise PreparationError("tracked quiche Cargo lock differs from the pinned input")
    lock_destination = recipe_directory / "Cargo.lock"
    if lock_destination.exists() or lock_destination.is_symlink():
        raise PreparationError("quiche recipe Cargo lock destination is not empty")
    lock_destination.write_bytes(lock)

    recipe = recipe_path.read_text(encoding="utf-8")
    recipe = replace_exact(
        recipe,
        '    exports_sources = ["CMakeLists.txt", "patches/*"]\n',
        '    exports_sources = ["CMakeLists.txt", "patches/*", "Cargo.lock"]\n',
        "quiche exported-source contract",
    )
    recipe = replace_exact(
        recipe,
        '        self.run("git clone https://github.com/cloudflare/quiche.git source_subfolder")\n',
        '        self.run("git init source_subfolder && cd source_subfolder && git remote add origin https://github.com/cloudflare/quiche.git")\n'
        f'        self.run("cd source_subfolder && git fetch --depth 1 origin {QUICHE_SOURCE_COMMIT}")\n',
        "quiche source clone contract",
    )
    recipe = replace_exact(
        recipe,
        '        self.run(f"cd source_subfolder && git checkout {self.version}")\n',
        f'        self.run("cd source_subfolder && git checkout --detach {QUICHE_SOURCE_COMMIT}")\n'
        f'        self.run("cd source_subfolder && test $(git rev-parse HEAD) = {QUICHE_SOURCE_COMMIT}")\n'
        '        copy(self, "Cargo.lock", src=self.export_sources_folder, dst=join(self.source_folder, "source_subfolder"))\n',
        "quiche source checkout contract",
    )
    recipe = replace_exact(
        recipe,
        '        self.run("cd source_subfolder/quiche && cargo %s" % (cargo_args))\n',
        '        self.run("cd source_subfolder/quiche && cargo %s --locked" % (cargo_args))\n',
        "quiche Cargo build contract",
    )
    if recipe.count(f'    version = "{QUICHE_VERSION}"') != 1:
        raise PreparationError("quiche version differs from the pinned input")
    recipe_path.write_text(recipe, encoding="utf-8")


def enforce_conan_lockfile_provider(provider: Path) -> None:
    source = provider.read_text(encoding="utf-8")
    guard = '''        if("$ENV{DOBBY_CONAN_LOCKFILE}" STREQUAL "")
            message(FATAL_ERROR "DOBBY_CONAN_LOCKFILE is required")
        endif()
        if(NOT IS_ABSOLUTE "$ENV{DOBBY_CONAN_LOCKFILE}" OR NOT EXISTS "$ENV{DOBBY_CONAN_LOCKFILE}")
            message(FATAL_ERROR "DOBBY_CONAN_LOCKFILE must be an existing absolute path")
        endif()
'''
    source = replace_exact(
        source,
        PROVIDER_LOCK_GUARD_POINT,
        guard + PROVIDER_LOCK_GUARD_POINT,
        "Conan provider lock guard insertion point",
    )
    if source.count(PROVIDER_INSTALL_SUFFIX) != 3:
        raise PreparationError("Conan provider install calls differ from the pinned input")
    source = source.replace(
        PROVIDER_INSTALL_SUFFIX,
        '--build=missing --lockfile=$ENV{DOBBY_CONAN_LOCKFILE} ${generator})',
    )
    provider.write_text(source, encoding="utf-8")


def configure_conan_provider(provider: Path, mode: str) -> None:
    """Apply the only provider mutation allowed by the selected mode."""
    if mode == "locked":
        enforce_conan_lockfile_provider(provider)
    elif mode != "unlocked":
        raise PreparationError("unknown Conan preparation mode")


def pin_dns_libs_recipe(dns: Path, nlc: Path) -> None:
    provider_source = nlc / "cmake" / "conan_provider.cmake"
    settings_source = nlc / "conan"
    provider = dns / "cmake" / "conan_provider.cmake"
    settings_directory = dns / "conan"
    if not provider_source.is_file() or not settings_source.is_dir():
        raise PreparationError("pinned NativeLibsCommon provider for DnsLibs is incomplete")
    if provider.exists() or provider.is_symlink() or settings_directory.exists() or settings_directory.is_symlink():
        raise PreparationError("DnsLibs generated Conan provider destination is not empty")
    shutil.copy2(provider_source, provider)
    shutil.copytree(settings_source, settings_directory)
    settings = settings_directory / "settings_user.yml"
    settings.write_text(settings.read_text(encoding="utf-8") + APPLE_CLANG_SETTINGS, encoding="utf-8")
    recipe_path = dns / "conanfile.py"
    recipe = recipe_path.read_text(encoding="utf-8")
    exports = "    exports_sources = patch_files\n"
    if recipe.count(exports) != 1:
        raise PreparationError("DnsLibs exported-source contract differs from the pinned input")
    recipe_path.write_text(
        recipe.replace(
            exports,
            '    exports_sources = patch_files + ["cmake/conan_provider.cmake", "conan/*"]\n',
        ),
        encoding="utf-8",
    )
    pin_nested_recipe_source(
        dns, DNSLIBS_SOURCE_METHOD, DNSLIBS_SOURCE_COMMIT, "DnsLibs", append_settings=False,
    )


def replace_generated_provider(nlc: Path, trusttunnel: Path) -> None:
    provider_source = nlc / "cmake" / "conan_provider.cmake"
    settings_source = nlc / "conan"
    provider_destination = trusttunnel / "cmake" / "conan_provider.cmake"
    settings_destination = trusttunnel / "conan"
    if not provider_source.is_file() or not settings_source.is_dir():
        raise PreparationError("pinned NativeLibsCommon provider is incomplete")
    if provider_destination.is_symlink() or settings_destination.is_symlink():
        raise PreparationError("generated Conan provider destination is unsafe")
    provider_destination.unlink(missing_ok=True)
    if settings_destination.exists():
        shutil.rmtree(settings_destination)
    shutil.copy2(provider_source, provider_destination)
    shutil.copytree(settings_source, settings_destination)
    settings = settings_destination / "settings_user.yml"
    settings.write_text(settings.read_text(encoding="utf-8") + APPLE_CLANG_SETTINGS, encoding="utf-8")
    if hashlib.sha256(provider_destination.read_bytes()).digest() != hashlib.sha256(provider_source.read_bytes()).digest():
        raise PreparationError("copied Conan provider failed verification")
    run(["conan", "config", "install", str(settings)])


def export_recipe(recipe: Path, version: str | None = None) -> None:
    arguments = ["conan", "export", str(recipe), "--user", "adguard", "--channel", "oss"]
    if version is not None:
        arguments.extend(("--version", version))
    run(arguments)


def prepare(trusttunnel: Path, mode: str) -> None:
    if not (trusttunnel / "conanfile.py").is_file() or not (trusttunnel / "cmake").is_dir():
        raise PreparationError("TrustTunnelClient checkout is invalid")
    validate_preparation_mode(mode)
    pin_trusttunnel_dns_requirement(trusttunnel)
    with tempfile.TemporaryDirectory(prefix="dobbyvpn-conan-recipes-") as temporary:
        root = Path(temporary)
        dns = root / "dns-libs"
        nlc = root / "native-libs-common"
        checkout(DNSLIBS_URL, DNSLIBS_COMMIT, dns)
        checkout(NLC_URL, NLC_COMMIT, nlc)
        configure_conan_provider(nlc / "cmake" / "conan_provider.cmake", mode)
        pin_dns_libs_recipe(dns, nlc)
        pin_native_libs_common_recipe(nlc)
        pin_quiche_recipe(nlc)
        replace_generated_provider(nlc, trusttunnel)
        prepare_default_conan_profile()
        export_recipe(nlc, NLC_VERSION)
        recipes = nlc / "conan" / "recipes"
        for recipe in sorted(path for path in recipes.iterdir() if path.is_dir()):
            export_recipe(recipe)
        export_recipe(dns, DNSLIBS_VERSION)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trusttunnel", type=Path, required=True)
    parser.add_argument("--mode", choices=PREPARATION_MODES, required=True)
    args = parser.parse_args()
    try:
        prepare(args.trusttunnel.resolve(strict=True), args.mode)
    except (OSError, PreparationError, subprocess.CalledProcessError) as error:
        print(f"error: pinned Conan preparation failed: {type(error).__name__}")
        return 1
    print(
        "pinned Conan inputs prepared "
        f"mode={args.mode} "
        f"dns-libs={DNSLIBS_VERSION}@{DNSLIBS_COMMIT} "
        f"native-libs-common={NLC_VERSION}@{NLC_COMMIT}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
