# ld2450-workspace — HLK-LD2450 harness

Non-invasive baseline harness for the **Hi-Link HLK-LD2450**, a 24 GHz FMCW
multi-target tracking radar (1 TX + 2 RX) that does on-board tracking and
reports up to **3 targets** over UART. This mirrors how the Rd-03E work began:
read and understand the *vendor stream* first (`../radar-workspace`, `docs/re`),
before any from-source firmware RE.

## What's here

| File | Role |
| --- | --- |
| `ld2450.py` | Pure-stdlib protocol: report-frame decode (+ byte-stream resync) and config command/ACK framing |
| `ld2450_reader.py` | Transport-agnostic reader → line-delimited JSON (serial / TCP tunnel / capture replay) |
| `test_ld2450.py` | 13 unit tests, anchored on the manual's worked example (no hardware) |

```sh
python3 -m unittest test_ld2450 -v          # verify the decode
python3 ld2450_reader.py --replay cap.bin   # offline, from a raw-UART capture
```

## Wire protocol (verified in tests)

**Report stream** — default **256000-8N1, 10 frames/s**, little-endian, fixed
30-byte frame:

```
AA FF 03 00 | target1[8] | target2[8] | target3[8] | 55 CC
```

Each 8-byte target: `X(i16 mm) Y(i16 mm) SPEED(i16 cm/s) RES(u16 mm)`.
Coordinates/speed use a **sign-magnitude** encoding: bit15 **set ⇒ positive**
(`value = raw − 0x8000`), bit15 **clear ⇒ negative** (`value = −raw`). An
all-zero record = empty slot. Worked example from the manual:
`0E 03 | B1 86 | 10 00 | 40 01` → X −782 mm, Y 1713 mm, speed −16 cm/s,
res 320 mm. (`x` is + to the module's right, `y` is down-range.)

**Config protocol** — host→module `FD FC FB FA | len(u16) | cmd(u16) | value | 04 03 02 01`;
ACK echoes `cmd | 0x0100` with a u16 status (0 = success). Config words must be
wrapped in enable-config (`0x00FF`, value `0x0001`) … end-config (`0x00FE`).
Implemented: enable/end config, single/multi target (`0x0080`/`0x0090`), read
firmware (`0x00A0`); `build_command()` handles the rest from the protocol PDF.

Sources: [LD2450 Serial Communication Protocol v1.03 (PDF)](https://make.net.za/wp-content/datasheets/HLK%20LD2450%20Serial%20Communication%20Protocol%20v1.03.pdf),
[v1.02 EN (PDF)](https://assets.super.so/79c0d2a8-d37a-438f-8fbe-c44778f3b0dd/files/ee2aafb0-f93e-47cd-b722-ad0f9e76b6f7/LD2450_Serial_Communication_Protocol_V1.02_EN.pdf),
[csRon/HLK-LD2450](https://github.com/csRon/HLK-LD2450/blob/main/serial_protocol.py),
[espboards LD2450](https://www.espboards.dev/sensors/ld2450/). Note: manuals
differ on sign wording and on speed handling — the decode here matches the
worked example; **re-verify against the physical board** when it's wired.

## Running against hardware

The module isn't wired to the container, so the reader takes any transport:

```sh
# local USB-serial adapter on the LD2450 main UART (needs pyserial)
python3 ld2450_reader.py --port /dev/ttyUSB0 --mode multi --fw

# a TCP UART tunnel — e.g. the rig Pi (socat tcp-listen,...!!/dev/tty…) or an
# ESP32-C6 that bridges the LD2450 UART onto the tailnet
python3 ld2450_reader.py --tcp rig-radar:9600
```

Main-UART wiring (5-pin header): `5V · GND · TX(→host) · RX(←host)` at
256000-8N1. **Confirm the pin order against the silkscreen / board photos
before powering** — verify before relying on it.

## Firmware-RE path (later, invasive — mirrors the Rd-03E route)

The LD2450 is a GD32-class host MCU + a 24 GHz FMCW SoC (same shape as the
Rd-03E). To get to *from-source* firmware, in increasing invasiveness:

1. **USB DFU** via the `DM`/`DP` castellated pads — may dump/flash without
   soldering (try first; RDP state unknown).
2. **Serial ISP** via `PA9`/`TX`/`RX` (USART0 bootloader).
3. **True SWD** by soldering to the host MCU `PA13` (SWDIO) / `PA14` (SWCLK) —
   then the `../firmware` from-source toolchain (Bazel/`@firmware`) applies.

The back-side u.FL (lumped matching off a host-IC pad, two 24.000 MHz crystals)
is a candidate buffered ~24 MHz reference/sync port — scope it for a CW tone;
relevant to the distributed-coherent goal in `docs/re/06-capabilities-and-roadmap.md` (D.6).

## JSON output

```jsonc
{"t":"ready","module":"ld2450","transport":"serial:/dev/ttyUSB0@256000","mode":"multi"}
{"t":"targets","ts":1699999999.1,"n":2,"targets":[
   {"slot":0,"present":true,"x":300,"y":1200,"speed":20,"res":160,"r":1236.9,"angle":14.04}, ...]}
{"t":"status","fps":10.0,"frames":10,"present":17}
```

Same line-JSON shape as the Rd-03E agent, so the `../radar-workspace` bridge +
web visualization pattern can be pointed at it next.
