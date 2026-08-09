# Apple static bridge build

The iOS and macOS static archives are built from public repository inputs only.
The supported build contract is:

- Xcode 26.3 (`17C529`);
- Conan 2.12.2 in a new, absolute `CONAN_HOME` for each platform;
- Rust 1.85.0 for the Apple Rust targets;
- iOS arm64 with a maximum deployment target of 15.6;
- macOS arm64 with a maximum deployment target of 15.0.

From a clean recursive checkout, install the workflow prerequisites and run:

```sh
export CONAN_HOME=/absolute/path/to/a/new/conan-ios-cache
export DOBBY_CONAN_LOCKFILE="$PWD/scripts/pins/conan/apple-ios-arm64.lock"
python3 scripts/prepare_pinned_conan.py --trusttunnel TrustTunnelClient --mode locked
scripts/build_apple_static.sh ios
```

Use another new cache and replace `ios` with `macos` for the macOS archive.
Replace the lock path with `scripts/pins/conan/apple-macos-arm64.lock` for
macOS. Locked mode requires that exact existing absolute lock path and writes a
provider which refuses every Conan install without it. Non-Apple workflows use
`--mode unlocked` until their platform graph locks exist; that mode rejects a
`DOBBY_CONAN_LOCKFILE` environment rather than silently using an accidental
lock.
The Apple locks retain exact public-remote revisions and exact locally
exported recipe revisions, but deliberately omit cache timestamps from local
references. Preparation verifies all 15 graph-relevant local recipe RREVs
against the lock before CMake runs, while ignoring separately exported legacy
or platform-only recipes. A clean cache therefore works while a missing,
stale, changed, or duplicate graph recipe fails closed.
The build script refuses to reuse a build directory. It preserves dependency
cache archives, sanitizes only isolated iOS staging copies, verifies every
non-index archive member's Apple platform and deployment target, and links a
small Go consumer before succeeding. Compiler path remapping and a final
archive scan prevent owner-local source, Conan, or Cargo paths from entering
the published binaries. Preparation materializes the native Conan build
profile before CMake configuration and applies the exact fail-closed
TrustTunnel DNS requirement represented by the checked Conan graph, so the
documented local commands and hosted workflows share one build path.

Run the bounded tooling tests with:

```sh
python3 -m unittest \
  scripts/tests/test_apple_native_input_digest.py \
  scripts/tests/test_build_apple_static.py \
  scripts/tests/test_package_release_assets.py \
  scripts/tests/test_prepare_pinned_conan.py \
  scripts/tests/test_prune_apple_compiler_builtins.py \
  scripts/tests/test_repository_text_contract.py \
  scripts/tests/test_verify_apple_archive.py \
  scripts/tests/test_workflow_contracts.py
```

The GitHub workflows are the canonical hosted examples. A missing exact
toolchain or unreadable archive member is a build failure, not a warning.

## Deterministic cache inputs

`apple_native_input_digest.py` produces the cache key component used by the
Apple workflows. It requires a clean TrustTunnel submodule at the exact
superproject gitlink and hashes the public Apple build/preparation/verifier
scripts, Quiche and platform Conan locks, relevant TrustTunnel and bridge
inputs, the selected workflow, and the Conan/Xcode/deployment/Rust contract.
It reads neither local caches nor build outputs. Run it from a clean recursive
checkout with the same arguments used by the workflow; any missing or dirty
input is a failure rather than a cache fallback.

Quiche keeps two immutable Cargo locks because its pinned Conan recipe upgrades
`ring` from 0.16 to 0.17 only for Linux and Windows arm64. The base lock is used
everywhere else; the ring-0.17 lock is selected immediately after that exact
recipe edit. Both are hashed build inputs and every Cargo invocation remains
`--locked`.

## Unlocked platform preparation

Linux, Windows, and Android invoke the same pinned DNS, NativeLibsCommon, and
Quiche source/recipe preparation with `--mode unlocked` and an isolated Conan
cache. This is intentionally fail-closed about mode selection: unlocked mode
rejects `DOBBY_CONAN_LOCKFILE`, while Apple locked mode requires an existing
absolute lock file and installs a provider that requires it for every Conan
install. Until per-platform Conan graph locks are added, non-Apple package
resolution is reproducible only to those exact prepared recipe/source inputs,
not a fully locked binary dependency graph.
Compiler versions used by the pinned hosted images but absent from Conan
2.12.2's public settings (GCC 15, MSVC 195, Clang 21, and Apple Clang 17) are
added through the same generated, hash-bound settings input. This neither
upgrades Conan nor accepts other unpinned compiler versions.
The hosted Windows Release build also declares CMake's `MultiThreaded` runtime
before dependency resolution, so the generated Conan host profile is bound to
`msvc.runtime=static` and `msvc.runtime_type=Release`. This matches the existing
TrustTunnel and delivered bridge target settings instead of depending on an
image-specific implicit default.
Conan 2.12.2 accepts the detected MSVC 195 setting but its internal CMake
toolchain mapping ends at 194. The Windows workflow therefore requires the
actual hosted compiler and tools to be exactly MSVC 19.51 / 14.51, then patches
the pinned Conan provider to use the binary-compatible 194 package identity in
every recursively generated host profile. Those profiles retain the exact v145
compiler paths and disable redundant Visual Studio activation, so Conan cannot
mistake identity 194 for a request to find Visual Studio 17 / v143. The v145
compiler, linker, and static runtime still perform the real build. An exact
context patch carries the same provider change through NativeLibsCommon's fresh
pinned source checkout and fails if its provider source drifts. Repository tests
forbid `/GL`, `/LTCG`, and CMake interprocedural optimization because those
modes require an exact toolset match and would invalidate this narrow
compatibility exception. Remove the exception when the pinned Conan
implementation fully supports MSVC 195.

## Updating tracked static libraries

The `Update Static Libraries` workflow accepts one full source SHA and explicit
successful Android, iOS, and macOS run IDs. It verifies that every run belongs
to the named workflow at that exact SHA, stages all three artifacts
fail-closed, records their public SHA-256 values and run IDs in
`lib/static-libraries.provenance.json`, and refuses to push if the target branch
has moved. After its binary-only commit, run the platform workflows again at
the resulting exact head before tagging a module release.

## Publishing a module release

`Release go-go-tunnel` is a manual public workflow. It accepts a semantic tag,
one full source SHA, and the five successful Android, iOS, Linux, macOS, and
Windows workflow run IDs. It refuses a moved `main`, a mismatched run SHA or
workflow name, an existing tag or release, and any unexpected artifact member.

It downloads exactly the seven platform artifacts, creates deterministic ZIP
files (fixed timestamp, mode, compression, and member order), and publishes a
`release-assets.manifest.json` with the source SHA, run IDs, member digests,
and archive digests. The release gate also proves that the checked-in static
libraries came from successful exact-parent update runs and are byte-identical
to the final exact-head hosted builds. Before completion it downloads the
public release again and verifies every published file byte-for-byte against
that manifest. The
manifest contains only public source and digest provenance; no private data,
profiles, credentials, endpoint identities, or test evidence belong here.

For a static-library update, first run the exact-source update workflow, then
run all five platform workflows again at its resulting exact `main` head. Use
those second-run IDs as the release workflow inputs. This prevents a tag from
being created for a source commit whose embedded static archives were built
from an earlier commit.
