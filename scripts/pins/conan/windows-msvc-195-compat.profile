# The Windows workflow verifies that cl.exe is MSVC 19.51 (VS 2026 / v145).
# Conan 2.12.2 accepts compiler.version=195 after our narrow settings extension,
# but its CMakeToolchain implementation has no 195 mapping and raises KeyError.
#
# MSVC guarantees binary compatibility across the v140-v145 toolsets when the
# newest linker is used. This final host-profile layer therefore gives Conan
# its newest supported compatible package identity while the actual compiler,
# linker, and static runtime remain the workflow-verified v145 tools.
#
# This exception is unsafe with /GL, /LTCG, or CMake IPO. Repository tests fail
# if those flags are introduced. Remove this profile when Conan is upgraded to
# a version whose complete toolchain implementation supports msvc/195.
# The workflow has already activated and bound the exact v145 environment.
# Disable Conan's redundant VCVars generation so identity 194 cannot make
# Conan 2.12 search for or reactivate a Visual Studio 17 / v143 installation.

[settings]
compiler.version=194

[conf]
tools.microsoft.msbuild:installation_path=
