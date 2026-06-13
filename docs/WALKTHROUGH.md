# Code Walkthrough — understand every line

This guide explains the whole design so you can read it with confidence. For
each piece it answers two questions:

1. **What does this do?** (the mechanics)
2. **Where did it come from?** (a datasheet requirement, or a generic FPGA/
   SystemVerilog technique)

Read it with the source open beside you. Suggested order is the order below —
it follows the data as it flows through the chip.

> Citations like *(RHD2164 p.10)* point at the Intan datasheets. Citations like
> *(FPGA idiom)* mean it's a standard digital-design technique, not something
> Intan-specific — those are the transferable skills.

---

## 0. How to study this (a path)

1. Read **§1 Big picture** so you know the block diagram.
2. Read **§2 Concepts** once. These are the ~10 ideas the whole project is
   built from. If you understand these, the code is just their application.
3. Walk the files **§3–§9** in order. Each is short.
4. Open `docs/SPEC.md` whenever a "why" is about the protocol.
5. Run `./sim/run_sim.sh`, open `sim/tb_rhd2164.vcd` in GTKWave, and watch the
   signals move while you reread §3–§6. Seeing `cs`, `sclk`, `miso`, and the
   shift registers change is worth more than any paragraph.

---

## 1. Big picture

Two emulated chips share one command bus; each sends its own MISO back:

```
                 ┌──────────────────── rhd2164_top (synthesis only) ──────────────────┐
  100 MHz ──IBUF──► MMCM ──BUFG──► clk_fast (400 MHz)                                  │
                                                                                       │
  CS±  ──IBUFDS─┐                                                                      │
  SCLK±──IBUFDS─┼─(single-ended)─► ┌─ rhd2164_emulator (chip0) ─► miso0 ─ODDR─OBUFDS─► MISO0±
  MOSI±──IBUFDS─┘                  └─ rhd2164_emulator (chip1) ─► miso1 ─ODDR─OBUFDS─► MISO1±
                                                                                       │
                 └─────────────────────────────────────────────────────────────────┘

Inside one rhd2164_emulator:

  cs/sclk/mosi ─► spi_frontend ─► (edge strobes + cmd_word) ─► command_decoder ─► result_a/b ─► ddr_miso ─► miso
                                          │                          │  ▲
                                          │                          ▼  │
                                          └──────────────► register_file (RAM/ROM)
                                                                     │
                                          command_decoder ─► bram_addr ─► channel BRAMs ─► data
```

The **core** (everything except `rhd2164_top`) uses only plain signals, so it
simulates in Icarus Verilog. The **top** adds the Xilinx-specific differential
buffers and clocking and is built only in Vivado.

---

## 2. Concepts you need (the whole toolbox)

These ten ideas explain ~90% of the code.

**2.1 `reg`/`wire`, `always @(posedge clk)`** — a `reg` updated inside
`always @(posedge clk)` becomes a hardware **flip-flop** (it remembers a value,
changing only on the clock edge). A `wire` or a `reg` in `always @(*)` is
**combinational** logic (gates; output follows inputs immediately). *(FPGA idiom)*

**2.2 Nonblocking `<=` vs blocking `=`** — inside clocked blocks we use `<=`
("update all these flip-flops simultaneously at the edge"). Inside combinational
blocks and testbench procedural code we use `=`. Mixing them wrong is the
classic beginner bug; the rule "`<=` in clocked, `=` in combinational" avoids it.
*(FPGA idiom)*

**2.3 Synchronizer** — when an external signal (here CS/SCLK/MOSI) is not
aligned to our clock, sampling it directly can make a flip-flop go
**metastable** (briefly undefined). The fix: pass it through 2–3 chained
flip-flops before using it. We use 3. *(FPGA idiom — see `spi_frontend`.)*

**2.4 Edge detection** — to find "the moment SCLK went 0→1", keep the previous
sampled value and compare: `rising = (prev==0 && curr==1)`. Produces a
one-clock pulse. *(FPGA idiom.)*

**2.5 Shift register** — `sr <= {sr[14:0], new_bit}` shifts everything left and
drops `new_bit` in at the bottom. Used to (a) assemble a serial command MSB-
first, and (b) play a word out serially MSB-first with `bit = sr[15]; sr <= {sr[14:0],0}`. *(FPGA idiom.)*

**2.6 Oversampling** — instead of using SCLK as a clock, we run a much faster
clock (400 MHz) and *watch* SCLK with it. One clean clock domain, no gated
clocks. The cost: a few ns of latency, which we showed is acceptable. *(Design
choice; see SPEC §8.)*

**2.7 Combinational read / synchronous read** — the register file is read with
`always @(*)` (answer appears instantly, like a lookup table → good for the
small register mux). The channel memories are read with `always @(posedge clk)`
(one-cycle latency) because that pattern is what Vivado recognizes as a **block
RAM (BRAM)**. *(FPGA idiom — "BRAM inference".)*

**2.8 `$readmemh`** — fills a memory array from a hex text file at load/build
time. How the channel data gets into the BRAMs. *(SystemVerilog built-in.)*

**2.9 Pipelining** — the protocol returns a command's result two transfers
later, so we carry results through two registers before output. *(Protocol
requirement, RHD2000 p.16; implemented as a 2-stage delay.)*

**2.10 Vendor primitives** — `IBUFDS`/`OBUFDS` convert a differential pin pair
to/from one internal signal; `ODDR` is a dual-edge output flip-flop in the I/O
block; `MMCME2_BASE` is the PLL that multiplies 100→400 MHz; `BUFG` is a global
clock-distribution buffer. These are specific Xilinx hardware blocks you
instantiate by name. *(Xilinx 7-series.)*

---

## 3. `spi_frontend.sv` — turning pins into events

**Job:** watch the shared CS/SCLK/MOSI, and output (a) clean edge pulses and
(b) the assembled 16-bit command.

- **`parameter SYNC_STAGES = 3`** — number of synchronizer flops (concept 2.3).
- **`cs_sync`, `sclk_sync`, `mosi_sync` shift each input through 3 flops**
  (`{cs_sync[...], cs_in}`). `cs_curr` etc. are the safe, synchronized values.
  *(FPGA idiom 2.3.)* CS resets to 1 because CS is **active-low** and idles high
  *(RHD2000 p.5)*.
- **`cs_prev`, `sclk_prev`** hold last cycle's values for edge detection (2.4).
- **The big `always @(posedge clk)`**:
  - Defaults all the strobes to 0 each cycle, so they're **one-clock pulses**.
  - **CS falling** (`cs_prev && !cs_curr`): start of a transfer → clear the bit
    counter and the command shift register. The falling edge of CS is also the
    chip's "sample the analog now" moment *(RHD2164 p.10)*; here it's where we
    begin a fresh word.
  - **CS rising**: end of transfer → pulse `cs_rising`.
  - **SCLK rising while CS low**: the chip samples MOSI here *(RHD2000 p.15
    "samples MOSI on the rising edge of SCLK")*. We shift `mosi_curr` into
    `shift_reg` (2.5). On the **16th** bit we publish `cmd_word` and pulse
    `cmd_valid`. MSB-first falls out naturally because the first bit shifted in
    ends up in the highest position after 16 shifts.
  - **SCLK falling while CS low**: pulse `sclk_falling` (the DDR output stage
    needs both edges).

Everything downstream keys off these four strobes plus `cmd_word`/`cmd_valid`.

---

## 4. `register_file.sv` — the chip's registers

**Job:** store the 22 writable registers and answer reads (including the ROM
identity registers), giving possibly-different answers on the A and B streams.

- **`reg [7:0] ram [0:21]`** — 22 byte registers. The address space and which
  registers exist is *(RHD2164 p.13 / RHD2000 p.19–22)*.
- **Write block** (`always @(posedge clk)`): on reset, zero all of them
  (deterministic sim — *SPEC §9 deviation*; real silicon is random at power-up,
  *RHD2000 p.19*). Otherwise, if `wr_en` and the address is ≤21, store the byte.
  Writes replace the whole byte *(RHD2000 p.19 "changed only by rewriting the
  entire eight-bit contents")*.
- **`assign twoscomp = ram[4][6]`** — Register 4 bit 6 is the two's-complement
  output-format flag *(RHD2000 p.20)*. It controls the MSB of "MSB-only"
  results, so the decoder needs it.
- **`function rom_val(...)`** — the read-only registers. `INTAN` ASCII in 40–44,
  the A/B marker in 59 (0x35 vs 0x3A), die rev 60, unipolar 61, number-of-amps
  62 = 64, chip ID 63 = 4. All from *(RHD2164 p.14)*. Reg 59 is the only ROM
  value that differs between A and B (the `is_b` argument).
- **Combinational read mux** (`always @(*)`, concept 2.7): regs 0–17 read the
  same on both streams; **18–21 read correctly only on B**, A returns 0 *(the
  RHD2164 quirk, p.13)*; everything ≥22 comes from the ROM function. Two outputs
  (`rd_data_a`, `rd_data_b`) because the merged MISO needs both.

---

## 5. `command_decoder.sv` — the brain

**Job:** decode each command, produce the A and B result words, and apply the
**2-command pipeline delay**. Also owns the CALIBRATE window and CONVERT(63)
pointer.

- **`localparam OP_*`** — the top two command bits *(RHD2000 p.16–18)*:
  `00`=CONVERT, `01`=calibration family, `10`=WRITE, `11`=READ.
- **State (`always @(posedge clk)` on `cmd_valid`)**:
  - `cmd_r` latches the command so the rest of the logic can use it after the
    pulse passes.
  - `ignored_r` latches whether this command falls in the CALIBRATE dummy
    window **before** `calib_cnt` is decremented. *This line exists because of a
    real bug the reference-model testbench caught:* the result is sampled later
    (at `cs_rising`), by which time a live `calib_cnt` check would be wrong for
    the last window command. *(See the commit "Fix CALIBRATE-window off-by-one".)*
  - `calib_cnt` counts down the 9 ignored commands after a CALIBRATE *(RHD2000
    p.17 "nine dummy commands ... are not executed")*.
  - `mux_ptr` is the CONVERT(63) auto-increment pointer *(RHD2164 p.13 /
    RHD2000 p.16)*. On a normal CONVERT it tracks the channel; on CONVERT(63) it
    advances and wraps at 31 (32 amplifiers per module).
  - `bram_addr` is registered here so the channel memory read can start.
- **Register-file drive (continuous `assign`s)**: `reg_wr_en` pulses for one
  clock on a non-ignored WRITE; note it uses the live `ignored` (correct,
  because during the `cmd_valid` cycle `calib_cnt` still holds its pre-decrement
  value). `reg_rd_addr` comes from `cmd_r` for READ results.
- **Result formation (`always @(*)`)** — builds `res_new_a/b` from the latched
  command:
  - in the window → MSB-only `{~twoscomp,15'b0}` *(RHD2000 p.17)*;
  - CONVERT → the channel BRAM values;
  - WRITE → `{0xFF, data}` echo *(RHD2000 p.18)*;
  - READ → `{0x00, reg}` *(p.18)*;
  - else (CALIBRATE/CLEAR/invalid) → MSB-only *(p.17–18)*.
- **The pipeline (`always @(posedge clk)` on `cs_rising`)** — two registers,
  `res_dly` then `res_out`. Each CS cycle they shift, so the value presented in
  cycle *k* is the result computed for the command of cycle *k−2*. That's the
  "result two commands later" rule *(RHD2000 p.16)* turned into two flip-flops
  (concept 2.9).

---

## 6. `ddr_miso.sv` — merging A and B onto one wire

**Job:** serialize the A and B result words onto MISO using the RHD2164 DDR
scheme.

The exact rule *(RHD2164 p.10)*: **A bits go out on SCLK falling edges, B bits
on SCLK rising edges, the first rising edge is ignored, and B's last bit lands
on the CS rising edge.** §4 of SPEC.md works through which bit is on the wire at
each instant.

- **`a_sr`, `b_sr`** — two shift registers (concept 2.5).
- **On `cs_falling`**: load `a_sr=result_a`, `b_sr=result_b`. *(Start of the
  word.)*
- **On `sclk_rising`**: drive `miso_out = a_sr[15]` and shift `a_sr`. This puts
  the next A bit on the line during the SCLK-high phase, so the master samples
  it at the following falling edge *(p.10)*.
- **On `sclk_falling`**: drive `miso_out = b_sr[15]` and shift `b_sr` — the B
  bit for the low phase, sampled at the next rising edge (and B[0] at CS rising).

That `if (sclk_rising) ... else if (sclk_falling)` is the entire DDR mux. The
"ignored first rising edge" needs no special code: the master simply doesn't use
that sample, and our load/shift timing makes A[15] correct for the F1 sample.

---

## 7. `rhd2164_emulator.sv` — one chip, wired together

**Job:** instantiate the four sub-blocks and the two channel memories, and
connect them. There's almost no logic here — it's a wiring diagram in text.

- **Instances** of `spi_frontend`, `command_decoder`, `register_file`,
  `ddr_miso`, connected by the wires named in §1.
- **`(* ram_style = "block" *) reg [15:0] mem_a/mem_b [0:31]`** — the two
  32×16 channel memories. The attribute asks Vivado to use a real block RAM.
  *(FPGA idiom 2.7.)*
- **`$readmemh(MEM_A_FILE, mem_a)`** — load the validation patterns at build
  time (concept 2.8). The filenames are parameters so chip0 and chip1 get
  different data.
- **Synchronous read** (`always @(posedge clk)`) of `mem_a/mem_b[bram_addr]` —
  one-cycle latency, which is the BRAM-inference pattern. There's plenty of
  slack: `bram_addr` is set at `cmd_valid` and not needed until `cs_rising`.

---

## 8. `rhd2164_top.sv` — the synthesis wrapper (Vivado only)

**Job:** everything that touches real pins and the PLL. This is the only file
with Xilinx primitives, so it's excluded from the iverilog sim.

- **`IBUF` + `MMCME2_BASE` + `BUFG`** — bring in the 100 MHz oscillator,
  multiply to 400 MHz (`CLKFBOUT_MULT_F=8`→VCO 800, `CLKOUT0_DIVIDE_F=2`→400),
  and put the result on a global clock line. *(Xilinx 7-series; SPEC §8.)*
- **Reset synchronizer** — hold `rst_n` low until the PLL locks, then release it
  cleanly in the fast domain. *(FPGA idiom 2.3 applied to reset.)*
- **`IBUFDS` ×3** — convert the CS/SCLK/MOSI differential pairs to single
  signals, **once**, shared by both chips. A differential input pair can only
  feed one IBUFDS, which is exactly why the core takes single-ended inputs
  *(the reason the modules were split this way; SPEC §1)*.
- **Two `rhd2164_emulator` instances** with different `.mem` files.
- **`ODDR` + `OBUFDS` ×2** — register each MISO bit in the I/O block (low,
  deterministic output delay → helps meet tMISO ≤ 12 ns, *RHD2164 p.11*) and
  drive it out as an LVDS pair. We feed the same bit to both ODDR data inputs
  because our DDR is already resolved to a single serial stream in `ddr_miso`;
  the ODDR is used here purely for clean IOB placement.

---

## 9. `tb_rhd2164.sv` — the self-checking testbench

**Job:** behave like the host controller, and independently check every answer.

- **`always #1.25 clk = ~clk`** — generates the 400 MHz sim clock. *(TB idiom.)*
- **Two DUT instances** sharing cs/sclk/mosi — mirrors the real top.
- **Reference model** (`chanval`, `ref_read`, `ref_step`) — a *second*,
  independent implementation of the spec written in procedural style. It tracks
  the same RAM, twoscomp, calib window, and MUX pointer and computes what each
  result *should* be. Because it was written separately from the RTL, when the
  two agree you have real evidence, not a circular check. *(Verification idiom —
  "reference model / golden model".)*
- **`spi_xfer`** — drives one 16-bit transfer with correct CS framing and ~24
  MHz SCLK, and samples MISO the way the datasheet says the master should: A on
  the high phases (falling-edge values), B on the low phases (rising-edge / CS-
  rising values) *(RHD2164 p.10)*. The `#` delays use the timing symbols from
  *(RHD2164 p.11)*.
- **`do_cmd`** — model the command, then drive it, keeping the scoreboard slots
  aligned.
- **Command builders** (`CONVERT`, `READ`, `WRITE`, `CALIBRATE`, …) — encode
  the bit patterns from *(RHD2000 p.16–18)* so the stimulus reads like the
  protocol.
- **Stimulus** — a directed sequence (ROM sweep, write/readback, twoscomp flip,
  CONVERT(63) auto-increment, the full CALIBRATE window) plus 40 constrained-
  random commands.
- **Scoreboard** — compares `ret[i]` to `exp[i-2]` on all four streams (the
  pipeline offset). 0 errors = the DUT matches an independent model across every
  transfer.

---

## 10. Glossary

- **LVDS** — Low-Voltage Differential Signaling; a 0/1 sent as the difference
  between two wires. Noise-resistant; used for the SPI bus *(RHD2000 p.12)*.
- **SPI / CPOL=0** — the serial protocol; clock idles low *(RHD2000 p.15)*.
- **DDR** — Double Data Rate; data on both clock edges. Here it's how A and B
  share one MISO *(RHD2164 p.10)*.
- **MSB-first** — most-significant bit transmitted first.
- **Metastability / synchronizer** — see 2.3.
- **BRAM** — Block RAM, dedicated on-chip memory blocks; see 2.7.
- **MMCM / PLL** — clock multiplier; makes 400 MHz from 100 MHz.
- **IBUFDS / OBUFDS / ODDR / BUFG** — see 2.10.
- **tMISO / tCYCLE / tCSOFF** — named timing limits *(RHD2164 p.11)*.
- **Pipeline delay** — result returns two commands later *(RHD2000 p.16)*.
- **Reference/golden model** — independent expected-value generator in the TB.
