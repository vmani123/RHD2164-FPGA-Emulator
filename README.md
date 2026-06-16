# RHD2164 Emulator (SystemVerilog, Spartan-7)

<!-- CI badge: update <USER>/<REPO> to your GitHub path after pushing. -->
<!-- ![sim](https://github.com/<USER>/<REPO>/actions/workflows/sim.yml/badge.svg) -->

A synthesizable SystemVerilog emulator of **two Intan RHD2164** digital
electrophysiology interface chips, targeting the AMD/Xilinx **XC7S25**
(Spartan-7) in Vivado. It reproduces the chip's **LVDS double-data-rate SPI
protocol** bit-for-bit and responds to the RHD2000 command set exactly as the
silicon does, so it can stand in for real headstage chips during host/FPGA
controller bring-up.

The channel "ADC" data is sourced from on-chip BRAM initialized from `.mem`
files, so the host can validate that every channel returns a known, distinct
16-bit value.

## Verified

Self-checking testbench with an **independent reference model** and **functional
coverage**:

```
153 transfers x 4 streams checked,  0 errors
opcodes 6/6 · CONVERT channels 32/32 · RAM writes 22/22 · reg reads 32/32
twoscomp 2/2 · CALIBRATE window · CONVERT(63) auto-increment
```

The reference model caught a real off-by-one in the CALIBRATE ignore window
during development (see git history).

## What it implements

- **DDR MISO merge** (RHD2164 p.10): module A (ch 0–31) on SCLK falling edges,
  module B (ch 32–63) on rising edges, first rising edge ignored, B-LSB on the
  CS rising edge.
- **2-command pipeline**: a command's result is returned two CS cycles later.
- **Command set**: CONVERT (incl. CONVERT(63) auto-increment), CALIBRATE (9-
  command ignore window), CLEAR, WRITE, READ, and invalid-command handling.
- **Register map**: 22 RAM registers + ROM identity (`INTAN`, chip ID = 4,
  64 amplifiers, the reg-59 A/B marker `0x35`/`0x3A`, and the reg 18–21 B-only
  read quirk).
- **Two chips** sharing one CS/SCLK/MOSI LVDS bus, each with its own MISO pair
  (the 128-channel headstage topology).

## Repository layout

```
rtl/    spi_frontend, command_decoder, register_file, ddr_miso,
        rhd2164_emulator (core), rhd2164_top (Vivado synthesis wrapper)
sim/    tb_rhd2164 (reference model + coverage), run_sim.sh
mem/    chipN_{A,B}.mem  channel data patterns
constraints/  rhd2164_top.xdc  (XC7S25 pins/LVDS/timing)
docs/   SPEC.md (distilled protocol), WALKTHROUGH.md (line-by-line guide)
```

## Simulate

Requires [Icarus Verilog](https://steveicarus.github.io/iverilog/) (`brew
install icarus-verilog` / `apt-get install iverilog`).

```bash
./sim/run_sim.sh          # compiles, runs, exits non-zero on any failure
```

Waveforms are written to `sim/tb_rhd2164.vcd` (open with GTKWave).

## Synthesize (Vivado, XC7S25)

1. Add `rtl/*.sv` and `constraints/rhd2164_top.xdc`; add `mem/*.mem` so
   `$readmemh` resolves; set `rhd2164_top` as top.
2. **Edit the XDC**: fill in every `<PIN>` placeholder from your board, and
   confirm the LVDS bank VCCO (the file assumes `LVDS_25` / a 2.5 V bank).
3. Clocking: 100 MHz oscillator → MMCM → 400 MHz fast oversampling clock.

## Scope / honesty

This is a faithful **digital-protocol** emulator, not a chip-perfect analog
model. It does **not** model the amplifiers, ADC noise, impedance DAC, temp/
supply sensors, or characterized silicon timing; CONVERT data is BRAM, RAM
resets to 0, and CALIBRATE is modeled as the ignore window only. See
[`docs/SPEC.md`](docs/SPEC.md) §9 for the full list of deviations.

## References

Intan RHD2164 datasheet and RHD2000-series datasheet (intantech.com).
