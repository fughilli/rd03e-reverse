# On-silicon flash dumps

Full flash dumps pulled off a real Rd-03E module's **GD32E230K8** host MCU over
SWD, for cross-checking the reverse engineering.

## `gd32e230k8_build2268_flash.bin`

- **64 KB** — the complete GD32E230K8 internal flash (`0x08000000..0x0801FFFF`),
  read over SWD (RP2350 CMSIS-DAP probe + pyOCD gdbserver). sha256
  `9b73c53de4aa2264aa7bb0d04cba1caa951fe32346a69d9af65a95f2b53b9549`.
- **Build 2268 (precision ranging).** The code/rodata region
  `0x08000000..0x0800F3FF` is **byte-for-byte identical** to Ai-Thinker's
  published build-2268 image (`（2268）XenP102RM01.hex`), so this is an
  authentic on-silicon copy of that firmware.
- The bytes at **`0x0800F400..0x0800FDA3`** are this specific unit's
  **runtime-written calibration/NVM** (the firmware stores calibration in the
  upper flash pages). That region is blank (`0xFF`) in the shipped `.hex` and is
  device-specific — treat it as *this board's* calibration, not part of the
  distributed image.
- Vectors: initial SP `0x20001E28`, reset handler `0x080000E0` — matching the
  static RE (`../09-handoff.md`, `../00-inventory.md`).

### Restoring a module (full revert)

This `.bin` is the **complete 64 KB flash image** — code **and** the
`0x0800F400+` calibration page. To fully revert *this* module to the exact state
captured here, flash the whole 64 KB back to `0x08000000` (e.g. `pyocd flash` /
`probe-rs download` over SWD). Flashing only the vendor `.hex` restores the code
but leaves the calibration page blank, which may degrade or break ranging if the
firmware does not regenerate it. It is not yet confirmed whether this calibration
is factory-write-once or self-regenerated at boot, so treat this full dump as the
authoritative restore image and keep it safe.

### Provenance / attribution

The firmware code is **Ai-Thinker's proprietary Rd-03E firmware**, not ours. It
is mirrored here only as a convenience for people reverse-engineering the module.
The original, authoritative image is published by the vendor and downloadable
from the [Ai-Thinker Rd-03E docs page](https://docs.ai-thinker.com/en/Rd-03E/)
(build 2268). All copyright in the firmware belongs to Ai-Thinker; it is included
here for interoperability research and documentation purposes.

### Reproduce it yourself

Any Rd-03E can be dumped the same way with an SWD probe — see
[`../../rp2350-swd-probe`](https://github.com/fughilli/rp2350-swd-probe) for the
probe + gdbserver, then read `0x08000000` length `0x10000` over the GDB remote
protocol. Your unit's `0x0800F400+` calibration will differ.
