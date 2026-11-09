#!/usr/bin/env bash
# Fetch the Ai-Thinker Rd-03E vendor firmware images (Intel HEX) for RE.
#
# These are Ai-Thinker's proprietary binaries; they are gitignored (see
# /.gitignore) and NOT redistributed from this repo. URLs are the stable vendor
# OSS links published on the Rd-03E docs page (docs.ai-thinker.com/en/Rd-03E),
# pinned by exact build number so decompiled addresses stay reproducible.
#
#   2268 — Precision Ranging standard firmware, V1.0.0  (（2268）XenP102RM01.hex)
#   2269 — Gesture Recognition standard firmware, V1.0.0 (（2269）XenG102 ST.hex)
#
# Both are Intel HEX for the GD32E230K8 (ARM Cortex-M23), based at 0x08000000.
set -euo pipefail

DEST="$(cd "$(dirname "$0")" && pwd)/rd03e"
mkdir -p "$DEST"

BASE="https://aithinker-static.oss-cn-shenzhen.aliyuncs.com/docs/Public%20Document%20Center/Radar/rd-03/Rd-03E/Firmware"

fetch() { # <url> <local-name>
  echo ">> $2"
  curl --fail --location --proto '=https' --tlsv1.2 -sS -o "$DEST/$2" "$1"
}

# %EF%BC%88 / %EF%BC%89 are the full-width parens （ ） in the vendor filenames.
fetch "$BASE/%EF%BC%882268%EF%BC%89XenP102RM01.hex" "2268_XenP102RM01.hex"   # precision ranging
fetch "$BASE/%EF%BC%882269%EF%BC%89XenG102%20ST.hex" "2269_XenG102ST.hex"    # gesture recognition

echo "fetched $(ls "$DEST"/*.hex | wc -l) firmware image(s) into $DEST"
echo "note: SWD flash dumps (if read protection is off) go in ./dumps/ (gitignored), not here."
