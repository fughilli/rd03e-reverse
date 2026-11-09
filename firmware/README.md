# rd03e-fw — from-source GD32E230 firmware

A reverse-engineered, from-source firmware for the Rd-03E's **GD32E230K8**
(Cortex-M23) host MCU. The goal is to drive the **S3KM111L** radar SoC with our
own code so new radar algorithms can run on the module. It mirrors the hardware
bring-up recovered in [`../docs/re/`](../docs/re/).

## Status

| Layer | State |
| --- | --- |
| Clocks / peripheral enables (`rcu`) | RE-faithful; PLL→72 MHz SYSCLK is TODO |
| Pin mux (`gpio`) — I2C0 PB6/7, SPI0 PA4/5/7 | RE-confirmed pins |
| I2C0 master, S3KM111L control (`i2c`, `s3km111l`) | transaction flow ported from vendor `FUN_08004410`; **register tables TODO** |
| SPI0 + DMA data path (`datapath`) | wired to `SPI0_DATA`; direction/format TODO |
| USART1 host UART (`usart`) | skeleton; baud divisor TODO |
| Radar signal processing | not started — this is where you come in |

The two biggest unknowns (the concrete S3KM111L register values, and the SPI0
frame format) are tracked in `../docs/re/01-s3km111l.md` and `02-bus.md`.

## Build

Cortex-M23 target is `thumbv8m.base-none-eabi`:

```sh
rustup target add thumbv8m.base-none-eabi
cargo build --release
# -> target/thumbv8m.base-none-eabi/release/rd03e-fw  (ELF; ~1.6 KB text)
```

A raw image for comparison against the vendor `.hex`:

```sh
cargo objcopy --release -- -O binary rd03e-fw.bin     # needs cargo-binutils + llvm-tools
```

## Flash (SWD, J3 header)

J3 = 3V3 / CLK (SWCLK) / DIO (SWDIO) / GND, per `../docs/re/09-handoff.md`.
With a J-Link or CMSIS-DAP probe and [probe-rs](https://probe.rs):

```sh
cargo install probe-rs-tools
probe-rs run --chip GD32E230K8 target/thumbv8m.base-none-eabi/release/rd03e-fw
```

(Uncomment the `runner` line in `.cargo/config.toml` to make `cargo run` flash.)

> Check that read protection is off before overwriting the vendor image, and
> keep a dump of the original firmware first (`../docs/re/09-handoff.md`).

## Layout

```
src/pac.rs        raw GD32E230 register map (only the RE'd peripherals)
src/rcu.rs        clock + peripheral-enable bring-up
src/gpio.rs       alternate-function pin mux
src/i2c.rs        I2C0 master (ported vendor transaction flow)
src/s3km111l.rs   radar driver: read/write reg + table replay (dev 0x40/0x44)
src/datapath.rs   SPI0 + DMA CH0 radar-sample stream
src/usart.rs      USART1 host UART
src/main.rs       bring-up in the vendor's order, then your algorithm loop
```
