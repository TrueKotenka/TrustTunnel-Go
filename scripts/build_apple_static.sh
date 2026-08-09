#!/usr/bin/env bash
# Build and verify one arm64 Apple static bridge with an isolated Conan cache.
set -euo pipefail

readonly platform="${1:-}"
case "$platform" in
  ios)
    readonly deployment_target=15.6
    readonly sdk=iphoneos
    readonly system_name=iOS
    ;;
  macos)
    readonly deployment_target=15.0
    readonly sdk=macosx
    readonly system_name=Darwin
    ;;
  *)
    echo "usage: $0 ios|macos" >&2
    exit 2
    ;;
esac

readonly root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
readonly trusttunnel="$root/TrustTunnelClient"
readonly build="$trusttunnel/build-$platform-arm64"
readonly output_directory="$root/lib/$platform"
readonly output="$output_directory/libdobby_bridge.a"
readonly conan_lockfile="$root/scripts/pins/conan/apple-$platform-arm64.lock"
readonly ios_compiler_builtins_sha256=907dea761e9fd300f3c713602b42934e33c0b480861b8cdb8b529b82a4f48402
readonly trusttunnel_cargo_lock_sha256=5dfa92024c6ff9dd09f0110fe7f094c5d2e25131787b3cdbacdafb94554b2f93
readonly conan_version=2.12.2
readonly rust_release=1.85.0
readonly rust_commit=4d91de4e48198da2e33413efdcd9cd2cc0c46688
readonly effective_cargo_home="${CARGO_HOME:-${HOME:?HOME is required}/.cargo}"
readonly effective_rustup_home="${RUSTUP_HOME:-${HOME:?HOME is required}/.rustup}"

[[ -n "${CONAN_HOME:-}" && "$CONAN_HOME" = /* ]] || {
  echo "CONAN_HOME must name an isolated absolute platform cache" >&2
  exit 2
}
[[ "$effective_cargo_home" = /* && "$effective_rustup_home" = /* ]] || {
  echo "Cargo and Rustup homes must be absolute" >&2
  exit 2
}
[[ "$root$CONAN_HOME$effective_cargo_home$effective_rustup_home" != *[$'\t\r\n ']* ]] || {
  echo "Apple build and toolchain paths must not contain whitespace" >&2
  exit 2
}
[[ ! -e "$build" ]] || {
  echo "refusing to reuse an existing build directory: $build" >&2
  exit 2
}
for tool in cmake ninja conan cargo rustc go xcrun libtool strip strings; do
  command -v "$tool" >/dev/null || { echo "missing build tool: $tool" >&2; exit 2; }
done
[[ "$(conan --version)" == "Conan version $conan_version" ]] || {
  echo "Conan version differs from the pinned Apple archive input" >&2
  exit 1
}
rust_details="$(rustc --version --verbose)"
grep -Fxq "release: $rust_release" <<< "$rust_details" || {
  echo "Rust release differs from the pinned Apple archive input" >&2
  exit 1
}
grep -Fxq "commit-hash: $rust_commit" <<< "$rust_details" || {
  echo "Rust commit differs from the pinned Apple archive input" >&2
  exit 1
}
[[ -f "$trusttunnel/conan/settings_user.yml" && -f "$trusttunnel/cmake/conan_provider.cmake" ]] || {
  echo "run prepare_pinned_conan.py before the Apple build" >&2
  exit 2
}
grep -Fq 'DOBBY_CONAN_LOCKFILE is required' "$trusttunnel/cmake/conan_provider.cmake" || {
  echo "Apple build requires the locked Conan provider" >&2
  exit 2
}
[[ -f "$conan_lockfile" ]] || {
  echo "Apple Conan graph lock is missing" >&2
  exit 1
}
export DOBBY_CONAN_LOCKFILE="$conan_lockfile"
[[ "$(shasum -a 256 "$trusttunnel/trusttunnel/Cargo.lock" | awk '{print $1}')" == "$trusttunnel_cargo_lock_sha256" ]] || {
  echo "TrustTunnel Cargo lock differs from the pinned Apple input" >&2
  exit 1
}

# The pinned upstream checkout does not expose an extension hook. Apply one
# exact, idempotent build-copy edit rather than maintaining a fork of it.
if ! grep -Fxq 'add_subdirectory("../dobby_bridge" "dobby_bridge")' "$trusttunnel/CMakeLists.txt"; then
  sed -i '' '$a\
add_subdirectory("../dobby_bridge" "dobby_bridge")
' "$trusttunnel/CMakeLists.txt"
fi

export IPHONEOS_DEPLOYMENT_TARGET="${IPHONEOS_DEPLOYMENT_TARGET:-15.6}"
export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-15.0}"
[[ "$IPHONEOS_DEPLOYMENT_TARGET" == 15.6 && "$MACOSX_DEPLOYMENT_TARGET" == 15.0 ]] || {
  echo "Apple deployment environment differs from the supported contract" >&2
  exit 2
}
prefix_maps=(
  "-ffile-prefix-map=$root=/dobbyvpn/source"
  "-ffile-prefix-map=$CONAN_HOME=/dobbyvpn/conan"
)
rust_prefix_maps=(
  "--remap-path-prefix=$root=/dobbyvpn/source"
  "--remap-path-prefix=$CONAN_HOME=/dobbyvpn/conan"
  "--remap-path-prefix=$effective_cargo_home=/dobbyvpn/cargo"
  "--remap-path-prefix=$effective_rustup_home=/dobbyvpn/rustup"
)
printf -v prefix_flags '%s ' "${prefix_maps[@]}"
printf -v rust_prefix_flags '%s ' "${rust_prefix_maps[@]}"
export CFLAGS="${CFLAGS:+$CFLAGS }${prefix_flags% }"
export CXXFLAGS="${CXXFLAGS:+$CXXFLAGS }${prefix_flags% }"
export RUSTFLAGS="${RUSTFLAGS:+$RUSTFLAGS }${rust_prefix_flags% }"

configure=(
  cmake -S "$trusttunnel" -B "$build" -GNinja
  -DCMAKE_BUILD_TYPE=Release
  -DCMAKE_OSX_ARCHITECTURES=arm64
  -DCMAKE_OSX_DEPLOYMENT_TARGET="$deployment_target"
  -DCMAKE_OSX_SYSROOT="$(xcrun --sdk "$sdk" --show-sdk-path)"
  -DCMAKE_C_COMPILER="$(xcrun --sdk "$sdk" --find clang)"
  -DCMAKE_CXX_COMPILER="$(xcrun --sdk "$sdk" --find clang++)"
  "-DCMAKE_C_FLAGS=${prefix_flags% }"
  "-DCMAKE_CXX_FLAGS=-stdlib=libc++ ${prefix_flags% }"
  -DIPV6_UNAVAILABLE=ON
  -DDOBBY_BRIDGE_STATIC=ON
  -DCARGO_EXTRA_ARGS=--locked
)
if [[ "$platform" == ios ]]; then
  configure+=( -DCMAKE_SYSTEM_NAME="$system_name" )
fi
if command -v ccache >/dev/null; then
  configure+=( -DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache )
fi
"${configure[@]}"
cmake --build "$build" --target dobby_bridge

static_libraries=()
while IFS= read -r library; do static_libraries+=("$library"); done < <(
  find "$build" -type f -name '*.a' \
    -not -path '*/CMakeFiles/*' \
    -not -name '*.dll.a' \
    -not -name 'libdobby_bridge-merged.a' \
    -print | sort
)
while IFS= read -r library; do
  if xcrun lipo -info "$library" 2>/dev/null | grep -q 'arm64'; then
    static_libraries+=("$library")
  fi
done < <(find "$CONAN_HOME/p" -type f -name '*.a' -print | sort)
(( ${#static_libraries[@]} > 0 )) || { echo "no arm64 static libraries found" >&2; exit 1; }

# Recursive Conan cache discovery can encounter byte-identical build and
# package copies. Merging each copy only duplicates object members, so retain
# the first occurrence of every exact archive digest.
readonly digest_index="$build/static-library-sha256.txt"
: > "$digest_index"
unique_static_libraries=()
for library in "${static_libraries[@]}"; do
  digest="$(shasum -a 256 "$library" | awk '{print $1}')"
  if grep -Fqx "$digest" "$digest_index"; then
    continue
  fi
  printf '%s\n' "$digest" >> "$digest_index"
  unique_static_libraries+=( "$library" )
done
(( ${#unique_static_libraries[@]} > 0 )) || { echo "no unique static libraries found" >&2; exit 1; }

merge_libraries=( "${unique_static_libraries[@]}" )
if [[ "$platform" == ios ]]; then
  readonly rust_target_libdir="$(rustc --print target-libdir --target aarch64-apple-ios)"
  compiler_builtins=( "$rust_target_libdir"/libcompiler_builtins-*.rlib )
  (( ${#compiler_builtins[@]} == 1 )) && [[ -f "${compiler_builtins[0]}" ]] || {
    echo "expected exactly one pinned iOS compiler-builtins rlib" >&2
    exit 1
  }

  readonly sanitized_directory="$build/sanitized-static-inputs"
  mkdir "$sanitized_directory"
  merge_libraries=()
  input_number=0
  for library in "${unique_static_libraries[@]}"; do
    input_number=$((input_number + 1))
    original_digest="$(shasum -a 256 "$library" | awk '{print $1}')"
    staged="$sanitized_directory/input-$input_number.a"
    cp -p "$library" "$staged"
    python3 "$root/scripts/prune_apple_compiler_builtins.py" \
      --archive "$staged" \
      --compiler-builtins "${compiler_builtins[0]}" \
      --expected-compiler-builtins-sha256 "$ios_compiler_builtins_sha256" \
      --platform ios \
      --maximum-deployment-target "$deployment_target"
    [[ "$(shasum -a 256 "$library" | awk '{print $1}')" == "$original_digest" ]] || {
      echo "Apple input sanitizer modified a Conan/build-cache archive" >&2
      exit 1
    }
    merge_libraries+=( "$staged" )
  done
fi

mkdir -p "$output_directory"
readonly merged="$build/libdobby_bridge-merged.a"
libtool -static -D -o "$merged" "${merge_libraries[@]}"
strip -S -D "$merged"
install -m 0644 "$merged" "$output"
python3 "$root/scripts/verify_apple_archive.py" \
  --archive "$output" \
  --platform "$platform" \
  --maximum-deployment-target "$deployment_target" \
  --canonicalize-metadata
readonly strings_inventory="$build/archive-strings.txt"
strings "$output" > "$strings_inventory" || {
  echo "Apple archive local-path scan failed" >&2
  exit 1
}
if grep -F \
  -e "$root" -e "$CONAN_HOME" -e "$effective_cargo_home" -e "$effective_rustup_home" \
  -e '/Users/' -e '/home/' \
  "$strings_inventory" >/dev/null; then
  echo "Apple archive contains an unremapped local build path" >&2
  exit 1
fi

if [[ "$platform" == ios ]]; then
  (
    cd "$root/examples"
    CGO_ENABLED=1 GOOS=ios GOARCH=arm64 \
      CC="$(xcrun --sdk iphoneos --find clang) -arch arm64 -isysroot $(xcrun --sdk iphoneos --show-sdk-path) -miphoneos-version-min=$deployment_target" \
      CXX="$(xcrun --sdk iphoneos --find clang++) -arch arm64 -isysroot $(xcrun --sdk iphoneos --show-sdk-path) -miphoneos-version-min=$deployment_target" \
      go build -trimpath -tags static -o "$build/example-ios"
  )
else
  (
    cd "$root/examples"
    CGO_ENABLED=1 GOOS=darwin GOARCH=arm64 \
      CC="$(xcrun --sdk macosx --find clang) -arch arm64 -isysroot $(xcrun --sdk macosx --show-sdk-path) -mmacosx-version-min=$deployment_target" \
      CXX="$(xcrun --sdk macosx --find clang++) -arch arm64 -isysroot $(xcrun --sdk macosx --show-sdk-path) -mmacosx-version-min=$deployment_target" \
      go build -trimpath -tags static -o "$build/example-macos-arm64"
  )
fi

shasum -a 256 "$output"
