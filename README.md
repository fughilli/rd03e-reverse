# rd03e-reverse

A reverse-engineering harness for the **Ai-Thinker Rd-03E** 24 GHz FMCW radar
module — specifically its **GigaDevice GD32E230K8** host MCU (ARM Cortex-M23),
toward documenting how it configures and reads the undocumented **ICLegend
S3KM111L** radar SoC. See [`docs/re/09-handoff.md`](docs/re/09-handoff.md) for the
objective, hardware, and analysis plan.

The generic Ghidra tooling + objdump scanners live in
[`rules_reverse_engineering`](https://github.com/fughilli/rules_reverse_engineering)
(pinned in `MODULE.bazel`); this repo adds only the Rd-03E specifics:

- `flake.nix` — ARM Cortex-M binutils (`arm-none-eabi-objdump`/`nm`/`readelf`) for
  disassembling the GD32E230 (ARMv8-M Baseline / Thumb) images, plus Ghidra
  re-exported from `rules_reverse_engineering`.
- `targets/` — `fetch.sh` pulls the vendor firmware builds 2268 (ranging) and
  2269 (gesture) from Ai-Thinker. **Proprietary and gitignored — never committed.**
- `ghidra/` — `analyze_fw` imports a whole firmware *image* at a fixed base with
  the Cortex-M language + GD32 memory map (the generic `analyze_lib` is for `.a`
  archives, which these downloads are not).
- `docs/re/` — the reconstructed register map and analysis notes.

## Quick start

```sh
./targets/fetch.sh                       # fetch the vendor .hex images (gitignored)

# Headless analysis of build 2268: import (ARM Cortex-M) -> GD32 memory map +
# reset vector -> auto-analyze -> export MMIO accesses + decompile all functions.
bazel run //ghidra:analyze_fw -- targets/rd03e/2268_XenP102RM01.hex out/fw2268

# Same for 2269; then diff the two to separate shared radar init from app logic.
bazel run //ghidra:analyze_fw -- targets/rd03e/2269_XenG102ST.hex out/fw2269

# Interactive Ghidra + ARM binutils shell (from rules_reverse_engineering):
bazel run @rules_reverse_engineering//ghidra:shell
```

Outputs land in `out/<name>/`: `mmio_accesses.csv` (the raw register-access
evidence over the GD32 peripheral window `0x40000000`–`0x48010000`) and
`decomp/` (one `.c` per function).

> **objdump scanners & ARM:** `rules_reverse_engineering//tools:mmio_scan` and
> `xref_callers` are RISC-V-flavored (mnemonics, and they read objdump's
> reconstructed-address comments). On Cortex-M, MMIO addresses come from literal
> pools, so Ghidra's `ExportMmio.py` (constant propagation) is the authoritative
> MMIO path here; `safety_scan` over `out/*/decomp` is architecture-agnostic.

## Requirements

- Bazel (via [bazelisk](https://github.com/bazelbuild/bazelisk); see `.bazelversion`).
- `nix` with flakes, for the Ghidra/toolchain run targets. `bazel build //...`
  stays evaluable without it. In claude-container, Nix is installed by
  `.claude-container-overlay/Dockerfile` — **relaunch the container** for it to
  take effect, then `nix flake lock` once to pin `flake.lock`.
