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
        rhd2164_emulator (core),
        rhd2164_top     (Vivado synthesis wrapper, LVDS I/O)
        rhd2164_top_se  (same, single-ended I/O — Arty S7 bring-up)
sim/    tb_rhd2164 (reference model + coverage), run_sim.sh
mem/    chipN_{A,B}.mem  channel data patterns
constraints/  rhd2164_top.xdc             (XC7S25 pins/LVDS/timing)
              rhd2164_top_se_arty_s7.xdc  (Arty S7-25/S7-50, LVCMOS33 on Pmod JA)
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

## Single-ended mode (Arty S7 bring-up)

`rhd2164_top.sv` presents the SPI bus as LVDS, which is correct for a real
headstage cable and for the custom PCB. It cannot be built for a Digilent
**Arty S7**: every user I/O bank on that board is hard-wired to VCCO = 3.3 V,
and a 7-series HR bank only supports `LVDS_25` (VCCO 2.375–2.625 V). Vivado
rejects it at DRC.

`rtl/rhd2164_top_se.sv` is the single-ended sibling. It keeps the emulator
cores byte-identical and swaps only the pad buffers — `IBUFDS` → `IBUF`,
`OBUFDS` → `OBUF`, LVCMOS33 pins on Pmod JA — so the bit-level protocol on the
wire is unchanged. Pick it as the Vivado top instead of `rhd2164_top`, and use
`constraints/rhd2164_top_se_arty_s7.xdc`. The S7-25 and S7-50 have identical
pinouts, so that one file covers both boards.

| JA pin | Package pin | Emulator | Controller board |
|---|---|---|---|
| JA1 | L17 | `cs_in` (in) | `cs_out` (out) |
| JA2 | L18 | `sclk_in` (in) | `sclk_out` (out) |
| JA3 | M14 | `mosi_in` (in) | `mosi_out` (out) |
| JA4 | N14 | `miso0_out` (out) | `miso0_in` (in) |
| JA7 | M16 | `miso1_out` (out) | `miso1_in` (in) |
| JA5/11 | — | GND | GND |

Same positions on both ends, so a straight-through female-to-female 12-pin
Pmod ribbon works. **Do not bridge JA6/JA12 (VCC) between two separately
powered boards** — use five signal jumpers plus a ground, or pull the two VCC
conductors from the ribbon.

Two board notes that catch people: the Arty S7's 100 MHz oscillator is on
**R2 with `IOSTANDARD SSTL135`** (it sits in the DDR3 bank), not LVCMOS33; and
Arty S7 boards are speed grade **−1**, so if 400 MHz fails timing set
`FAST_CLK_DIV = 4.000` for a 200 MHz fast clock — still ~8× oversampling at
24 MHz SCLK.

The simulation is unaffected: `sim/run_sim.sh` compiles the core sources
explicitly and neither top is part of the testbench.

## Scope / honesty

This is a faithful **digital-protocol** emulator, not a chip-perfect analog
model. It does **not** model the amplifiers, ADC noise, impedance DAC, temp/
supply sensors, or characterized silicon timing; CONVERT data is BRAM, RAM
resets to 0, and CALIBRATE is modeled as the ignore window only. See
[`docs/SPEC.md`](docs/SPEC.md) §9 for the full list of deviations.

## References

Intan RHD2164 datasheet and RHD2000-series datasheet (intantech.com).
