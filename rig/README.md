# rig-radar — Rd-03E HITL runner (Raspberry Pi 3B+)

A self-contained [`sbc-deploy`](https://github.com/fughilli/sbc-deploy) consumer
that turns a Pi 3B+ into a tailnet-attached HITL bench for the Rd-03E radar RE.
It runs the [`hitl-reserve`](https://github.com/fughilli/hitl-reserve) daemon and
exposes **one composite unit, `radar-bench`**, reserved atomically:

- the **ESP32-C6** radar host — flash / RISC-V JTAG via its built-in USB-JTAG
  (`hitl-flash`, `hitl-monitor`, `hitl-jtag`, `hitl-gdb`; toolbox reused verbatim
  from splanc's `pi/hitl`), and
- the **RP2350 CMSIS-DAP probe** on the Rd-03E's **GD32E230** — SWD via
  `hitl-swd` (pyOCD gdbserver, GD32 pack baked in) and `hitl-swd-dump`.

So an agent reserves `radar-bench` over the tailnet and drives both the C6 and the
radar's GD32 without tying up any ports on a laptop.

This is a nested Bazel workspace (its own `MODULE.bazel`) inside `rd03e-reverse`,
so the RE-harness workspace is untouched. Build from this directory (`cd rig`).

## Commission (from the Mac, SD card attached)

Prereqs: Nix + Bazel on the Mac. The first `--cross` build compiles the Pi kernel
from source (the binary caches only hold native aarch64), so it is slow once.

```sh
cd rig

# 1. One-time deploy keypair.
bazel run //:rig-radar.keys -- init

# 2. Build the SD image and write it to the card (cross-compile on the Mac).
#    Find the card with `diskutil list`; --no-write to just build the .img.
bazel run //:rig-radar.image_sd -- --cross --device /dev/diskN

# 3. Boot the Pi 3B+ (Ethernet is simplest; or set WiFi — see nix/network.nix).
#    It comes up as rig-radar.local.

# 4. Seed the tailnet auth key (gitignored, already at ../credentials/tailscale.key)
#    so it joins the tailnet as `rig-radar`:
bazel run //:rig-radar.ssh -- rig-radar.local \
    'install -d -m700 /var/lib/tailscale'
cat ../credentials/tailscale.key | \
  bazel run //:rig-radar.ssh -- rig-radar.local \
    'cat > /var/lib/tailscale/authkey && chmod 600 /var/lib/tailscale/authkey && systemctl restart tailscaled-autoconnect'
#    (or: bazel run //:rig-radar.seed_tailscale -- rig-radar.local -- --authkey "$(cat ../credentials/tailscale.key)")

# 5. Fill in the two device paths (see below), then push:
bazel run //:rig-radar.deploy_live -- rig-radar
```

## Fill in the catalog device paths (`reserve/catalog.json`)

The composite unit needs stable `by-id` paths for the two USB devices. After the
Pi is up with the C6 and the RP2350 probe plugged in, read them:

```sh
bazel run //:rig-radar.ssh -- rig-radar 'ls -l /dev/serial/by-id/'
```

Replace in `reserve/catalog.json`:
- `<C6-MAC>` — the ESP32-C6's USB-JTAG serial (its MAC; appears in its
  `usb-Espressif_USB_JTAG_serial_debug_unit_<MAC>-if00` path), in both the device
  path and `HITL_ADAPTER_SERIAL`.
- `<PROBE-SERIAL>` — the RP2350 debugprobe's serial in its
  `usb-Raspberry_Pi_Debug_Probe__CMSIS-DAP__<SERIAL>-if00` path.

Then `bazel run //:rig-radar.deploy_live -- rig-radar` to apply (no reflash).

## Drive it (from the container / any tailnet host)

```sh
hitl reserve --unit radar-bench            # get an sshd session into the reservation env
# inside the reservation:
hitl-devices                               # DUT → tty/serial map
hitl-swd gdbserver --target gd32e230k8     # GD32 SWD gdbserver (pack baked in) → :3333
hitl-swd-dump gd32.bin                     # dump the GD32 flash
hitl-flash bundle.tar --monitor            # flash the C6
hitl-jtag / hitl-gdb                        # C6 RISC-V JTAG / GDB
```

## Layout

```
flake.nix          mkSbcProject: board raspberry-pi-3, hostName rig-radar
MODULE.bazel       sbc_deploy git_override (pinned in lockstep with flake.nix)
BUILD.bazel        sbc_application //:rig-radar.{keys,image_sd,deploy_live,ssh,seed_tailscale}
nix/hitl-app.nix   Pi system: podman + tailscale + hitl-image-load + the daemon
nix/container.nix  reservation env toolbox: C6 (esptool/openocd-esp32/riscv-gdb) + GD32 (pyocd/arm-gdb)
nix/openocd-esp32.nix, nix/riscv-gdb.nix, nix/packages.nix   vendored from splanc pi/hitl
reserve/catalog.json   the radar-bench composite unit (fill in device by-ids)
```

> Status: the full Nix config evaluates clean; the aarch64 image build + the two
> device by-ids are validated on the Mac + Pi during commissioning. Iterate there.
