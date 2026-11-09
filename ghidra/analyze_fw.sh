#!/usr/bin/env bash
#
# One-shot reverse-engineering pass over an Rd-03E GD32E230 firmware IMAGE
# (Intel HEX or raw .bin):
#   * import it with the ARM Cortex-M language (ARM:LE:32:Cortex, Thumb),
#   * lay down the GD32E230 memory map + mark the reset vector (SetupMemoryMap.py,
#     a preScript) so MMIO references resolve and analysis starts from real code,
#   * run full auto-analysis,
#   * export observed MMIO register accesses over the GD32 peripheral window
#     (ExportMmio.py) and decompiled C for functions matching a name regex
#     (DecompileFuncs.py).
#
# Unlike analyze_lib in rules_reverse_engineering (which `ar x`-extracts a static
# library), this imports a single whole-firmware image at a fixed base — what the
# vendor .hex downloads and SWD flash dumps are.
#
# Usage:
#   bazel run //ghidra:analyze_fw -- <firmware.hex|.bin> [out_dir] [name_regex] [base_hex]
#
# Examples:
#   bazel run //ghidra:analyze_fw -- targets/rd03e/2268_XenP102RM01.hex out/fw2268
#   bazel run //ghidra:analyze_fw -- dumps/module.bin out/dump '(SPI|I2C|USART|DMA)' 0x08000000
#
# Requires `nix` (Ghidra + ARM binutils come from //flake.nix). If nix is not yet
# live in the container, relaunch so .claude-container-overlay installs it.
set -euo pipefail

# --- begin runfiles.bash initialization v3 (locate the generic RE scripts) ---
set +e
_rf=bazel_tools/tools/bash/runfiles/runfiles.bash
source "${RUNFILES_DIR:-/dev/null}/$_rf" 2>/dev/null ||
  source "$(grep -sm1 "^$_rf " "${RUNFILES_MANIFEST_FILE:-/dev/null}" | cut -f2- -d' ')" 2>/dev/null ||
  source "$0.runfiles/$_rf" 2>/dev/null ||
  source "$(grep -sm1 "^$_rf " "$0.runfiles_manifest" | cut -f2- -d' ')" 2>/dev/null ||
  source "$(grep -sm1 "^$_rf " "$0.exe.runfiles_manifest" | cut -f2- -d' ')" 2>/dev/null ||
  { echo >&2 "ERROR: cannot find runfiles.bash"; exit 1; }
set -e
# --- end runfiles.bash initialization v3 ---

if ! command -v nix >/dev/null 2>&1; then
  echo "error: \`nix\` not on PATH — relaunch the container so the Nix overlay (.claude-container-overlay/Dockerfile) activates." >&2
  exit 127
fi

# The flake lives at the workspace root; `bazel run` exports
# BUILD_WORKSPACE_DIRECTORY (absolute path to the real source tree).
WS="${BUILD_WORKSPACE_DIRECTORY:-$PWD}"
FLAKE_DIR="${GHIDRA_FLAKE_DIR:-$WS}"

FW="${1:?usage: analyze_fw <firmware.hex|.bin> [out_dir] [name_regex] [base_hex]}"
OUT="${2:-out/$(basename "${FW%.*}")}"
REGEX="${3:-.*}"          # vendor images are stripped -> match all by default
BASE="${4:-0x08000000}"   # GD32E230 flash base; used for .bin + vector table

# Under `bazel run`, CWD is the runfiles tree; resolve user-supplied relative
# paths against BUILD_WORKING_DIRECTORY (where `bazel run` was invoked) instead.
INVOKED="${BUILD_WORKING_DIRECTORY:-$PWD}"
case "$FW"  in /*) : ;; *) FW="$INVOKED/$FW"   ;; esac
case "$OUT" in /*) : ;; *) OUT="$INVOKED/$OUT" ;; esac

FW="$(cd "$(dirname "$FW")" && pwd)/$(basename "$FW")"
mkdir -p "$OUT"; OUT="$(cd "$OUT" && pwd)"

# Local Cortex-M preScript (from the source tree) + the generic post-scripts
# (from the rules_reverse_engineering runfiles). Ghidra -scriptPath joins dirs
# with ';'.
LOCAL_SCRIPTS="$WS/ghidra/scripts"                              # SetupMemoryMap.py
RE_SCRIPTS="$(dirname "$(rlocation rules_reverse_engineering/ghidra/scripts/ExportMmio.py)")"  # ExportMmio.py, DecompileFuncs.py
SCRIPT_PATH="${LOCAL_SCRIPTS};${RE_SCRIPTS}"

proj="$(mktemp -d)"
trap 'rm -rf "$proj"' EXIT

# ExportMmio.py appends; start clean.
rm -f "${OUT}/mmio_accesses.csv"

PROJNAME="re_$(basename "${FW%.*}" | tr -c 'A-Za-z0-9_' '_')"

case "$FW" in
  *.hex) IMPORT_ARGS=( -import "$FW" -processor "ARM:LE:32:Cortex" ) ;;
  *.bin) IMPORT_ARGS=( -import "$FW" -loader BinaryLoader -loader-baseAddr "$BASE" -processor "ARM:LE:32:Cortex" ) ;;
  *) echo "error: expected a .hex or .bin firmware image, got: $FW" >&2; exit 2 ;;
esac

echo ">> importing $(basename "$FW") as ARM:LE:32:Cortex (base ${BASE}); first run fetches Ghidra..."

exec nix run \
  --extra-experimental-features "nix-command flakes" \
  "${FLAKE_DIR}#analyzeHeadless" -- \
  "$proj" "$PROJNAME" \
  "${IMPORT_ARGS[@]}" \
  -scriptPath "$SCRIPT_PATH" \
  -preScript SetupMemoryMap.py "$BASE" \
  -postScript ExportMmio.py "${OUT}/mmio_accesses.csv" 0x40000000 0x48010000 \
  -postScript DecompileFuncs.py "${OUT}/decomp" "$REGEX" \
  -deleteProject
