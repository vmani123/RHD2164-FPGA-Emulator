# RHD2164 Emulator (SystemVerilog, Spartan-7)

![sim](https://github.com/vmani123/RHD2164-FPGA-Emulator/actions/workflows/sim.yml/badge.svg)

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
        rhd2164_emulator (core), rhd2164_top (LVDS synthesis top),
        rhd2164_top_se (single-ended bench top for Arty S7 / Pmod)
sim/    tb_rhd2164 (reference model + coverage), run_sim.sh
mem/    chipN_{A,B}.mem  channel playback data (256 samples x 32 ch each)
constraints/  rhd2164_top.xdc (LVDS, custom board)  arty_s7.xdc (Arty S7 Pmod)
vivado/ build_arty_s7.tcl  (one-command batch build -> vivado/out/*.bit)
docs/   SPEC.md (distilled protocol), WALKTHROUGH.md (line-by-line guide)
host_tools/  gen_neural_mem.py (playback data), emu_verify.py (bit-exact check)
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

## Run it on a Digilent Arty S7

The Arty S7's banks are 3.3 V (and 1.35 V), so true LVDS is not possible on
this board; use the **single-ended bench top** `rhd2164_top_se` +
`constraints/arty_s7.xdc` (SPI over Pmod JA, 3.3 V logic) to bring up your
controller code against the emulator. Batch build:

```bash
vivado -mode batch -source vivado/build_arty_s7.tcl                          # Arty S7-25
vivado -mode batch -source vivado/build_arty_s7.tcl -tclargs xc7s50csga324-1 # Arty S7-50
```

The bitstream lands in `vivado/out/rhd2164_top_se.bit` (the script fails if
timing isn't met — check `vivado/out/timing_summary.rpt`). GUI alternative:
create a project for your board's part, add `rtl/*.sv` + `mem/*.mem` +
`constraints/arty_s7.xdc`, set `rhd2164_top_se` as top, Generate Bitstream,
program over USB (Hardware Manager). After programming, **LD2 lights (MMCM
locked) and LD3 blinks** (~0.7 s heartbeat).

**Wiring (Pmod JA):** JA1=CS, JA3=SCLK, JA7=MOSI, JA9=MISO0 (chip 0),
JA10=MISO1 (chip 1), JA5/JA11=GND — full table in `arty_s7.xdc`. 3.3 V logic
only; always share ground with your master. CS idles high (internal pull-up),
so the emulator sits quiet until you drive it.

### Talking to it from your controller code

- **Protocol**: CPOL=0, 16-bit words MSB first; CS must pulse high between
  *every* word (≥ 154 ns) and each result comes back **two CS cycles** after
  its command (see `docs/SPEC.md`). Spec max SCLK is 24 MHz; over Pmod jumper
  wires stay ≤ ~8–10 MHz, and start slower.
- **The DDR MISO split** (the RHD2164's quirk): module **A** bits (channels
  0–31, and most register reads) are valid on the 16 SCLK **falling** edges;
  module **B** bits (channels 32–63, regs 18–21, reg 59 = `0x3A`) on rising
  edges 2–16 **plus B\[0\] on the CS rising edge**. A standard SPI peripheral
  in mode 0 sends commands correctly but only captures the B stream (shifted,
  missing B\[0\]) — fine for smoke tests, not full data.
- **Capture options**, simplest first: (1) **bit-bang GPIO** at ≤ 1 MHz and
  sample MISO after each edge + after CS↑ — complete A+B capture, best first
  step; (2) master SPI in mode 0 for TX + a second **receive-only slave SPI**
  (CPOL=0/CPHA=1, fed the same SCLK/CS) which samples on falling edges and
  captures the full A stream; (3) a DDR-capable peripheral or your own FPGA
  logic for full rate.

### Bring-up checklist (hardware verification)

1. `READ(40..44)` → `'I' 'N' 'T' 'A' 'N'`; `READ(62)` → `0x40` (64 amps);
   `READ(63)` → `0x04` (chip ID). Remember the 2-command pipeline.
2. `READ(59)` → `0x35` on the A stream, `0x3A` on B — proves you're really
   seeing both DDR streams, on both MISO0 and MISO1.
3. `CONVERT(ch)` for ch 1..31 returns the current playback sample from
   `mem/chip*_{A,B}.mem` on A/B. The playback pointer only advances on
   `CONVERT(0)` (one step per full 32-channel sweep), so **avoid channel 0 to
   read static values**, or regenerate simple deterministic patterns first:
   `python3 host_tools/gen_neural_mem.py --mode simple --seconds 1.0`.
4. Stream sweeps and compare against the `.mem` contents (bit-exact check
   logic lives in `host_tools/emu_verify.py`).

## Scope / honesty

This is a faithful **digital-protocol** emulator, not a chip-perfect analog
model. It does **not** model the amplifiers, ADC noise, impedance DAC, temp/
supply sensors, or characterized silicon timing; CONVERT data is BRAM, RAM
resets to 0, and CALIBRATE is modeled as the ignore window only. See
[`docs/SPEC.md`](docs/SPEC.md) §9 for the full list of deviations.

## References

Intan RHD2164 datasheet and RHD2000-series datasheet (intantech.com).
