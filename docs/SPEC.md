# RHD2164 Emulator — Protocol Specification

Distilled from the Intan RHD2164 datasheet (1 Dec 2017) and the parent
RHD2000-series datasheet (8 Dec 2023). This is the single source of truth the
RTL is built against. Section numbers in parentheses are informal.

Target: emulate **two** RHD2164 chips on a Xilinx/AMD **XC7S25** (Spartan-7),
synthesized in Vivado. Both chips share the CS/SCLK/MOSI LVDS inputs and each
drives its own MISO LVDS output pair (the 128-channel headstage topology).

---

## 1. Pin-level interface (per chip)

LVDS_en is pulled high internally on the real chip → **full LVDS signaling**.
All SPI signals are differential pairs.

| Signal | Dir (chip) | FPGA primitive | Notes |
|--------|-----------|----------------|-------|
| CS+/CS−     | input  | `IBUFDS` | active-low chip select; falling edge triggers ADC sample |
| SCLK+/SCLK− | input  | `IBUFDS` | serial clock, CPOL=0 (idle low) |
| MOSI+/MOSI− | input  | `IBUFDS` | command in, sampled by chip on SCLK **rising** edge, MSB first |
| MISO+/MISO− | output | `ODDR`+`OBUFDS` | DDR data out (see §4) |

Shared bus: CS/SCLK/MOSI go to **both** emulated chips. Two MISO pairs out
(`MISO0`, `MISO1`).

---

## 2. SPI timing (RHD2164 table, page 11)

| Symbol | Parameter | Min | Max | Unit |
|--------|-----------|-----|-----|------|
| tSCLK   | SCLK period | 41.6 | | ns (→ **24 MHz max**) |
| tSCLKH  | SCLK high   | 20.8 | | ns |
| tSCLKL  | SCLK low    | 20.8 | | ns |
| tCS1    | CS low → SCLK high setup | 20.8 | | ns |
| tCS2    | SCLK low → CS high setup | 20.8 | | ns |
| tCSOFF  | CS high duration | 154 | | ns |
| tMOSI   | MOSI valid → SCLK high setup | 10.4 | | ns |
| tMISO   | SCLK/CS falling → MISO valid | | 12 | ns (chip output delay) |
| tCYCLE  | total cycle between ADC samples | 950 | | ns |

Note RHD2164's SCLK max is 24 MHz (parent RHD2132 is 25 MHz). We design the
emulator's output stage to meet tMISO ≤ 12 ns at 24 MHz SCLK.

One transfer = **CS low, 16 SCLK pulses, CS high**. CS **must** pulse high
between every 16-bit word, even for non-CONVERT commands.

---

## 3. Command set (16-bit word, MSB first on MOSI)

Bit 15..0. `C`=channel[5:0], `R`=register[5:0], `D`=data[7:0], `H`=DSP-reset flag.

| Command | Encoding (b15..b0) |
|---------|--------------------|
| CONVERT(C) | `00 C5 C4 C3 C2 C1 C0 0 0 0 0 0 0 0 H` |
| CALIBRATE  | `0101 0101 0000 0000` (0x5500) |
| CLEAR      | `0110 1010 0000 0000` (0x6A00) |
| WRITE(R,D) | `10 R5..R0 D7..D0` |
| READ(R)    | `11 R5..R0 0000 0000` |

Decoding by top 2 bits:
- `00` → CONVERT
- `01` → calibration family: exactly `0x5500`=CALIBRATE, `0x6A00`=CLEAR;
  any other `01…` is an **invalid command** (returns MSB-only result).
- `10` → WRITE
- `11` → READ

### 3.1 Results (what comes back on MISO)

The MSB of "MSB-only" results depends on `twoscomp` (Register 4 bit 6):
MSB = 0 if twoscomp enabled, else MSB = 1. (Applies to CALIBRATE, CLEAR,
invalid commands, and during the whole calibration window.)

| Command | 16-bit result |
|---------|---------------|
| CONVERT(C) | ADC value `A[15:0]` for channel C (here: BRAM contents) |
| CALIBRATE  | `{~twoscomp, 15'b0}` |
| CLEAR      | `{~twoscomp, 15'b0}` |
| WRITE(R,D) | `{8'hFF, D[7:0]}` (data echoed; upper byte all ones) |
| READ(R)    | `{8'h00, reg[R][7:0]}` (upper byte all zeros) |
| invalid `01…` | `{~twoscomp, 15'b0}` |

### 3.2 Pipeline delay

**Result for a command appears two CS cycles later** (pipelined). i.e. the
16-bit word returned during CS-cycle *n* is the result of the command received
during CS-cycle *n−2*. The emulator holds a 2-deep result FIFO/shift.

### 3.3 CONVERT(63) auto-increment

CONVERT with C=63 increments an internal MUX pointer to the next amplifier
channel each call, rolling 0→1→…→ and wrapping after the end of the amplifier
array back to 0. (Datasheet: send at least one CONVERT(0) first; pointer state
is undefined at power-up.) We model the pointer; the returned value is the BRAM
data for the current pointer channel.

### 3.4 CALIBRATE dummy window

After a CALIBRATE command, the next **nine** commands are ignored by the chip
(not executed); during the entire calibration window the chip returns MSB-only
results. We model this as a 9-command "calibration busy" counter, during which
CONVERT/READ/WRITE results are suppressed to `{~twoscomp,15'b0}`.

---

## 4. DDR MISO scheme (RHD2164-specific, page 10)

The RHD2164 has two internal 32-channel cores:
- **Module A** = amplifier channels 0–31 (also hosts aux/temp/supply channels)
- **Module B** = amplifier channels 32–63

A CONVERT(X) converts channel **X** on module A and channel **X+32** on module B
simultaneously. The two 16-bit results are merged onto one MISO line by a DDR
mux:

- MISO bits for **module A** are presented on SCLK **falling** edges.
- MISO bits for **module B** are presented on SCLK **rising** edges.
- The data on the **first SCLK rising edge** of a word must be **ignored**
  (dummy). The B-module's 16 bits are conveyed on the *successive* rising edges
  (15 bits) plus the **16th bit (B LSB) on the rising edge of CS**.

So within one CS cycle (16 SCLK periods), the master samples:
- 16 A-bits on the 16 falling edges → `A[15:0]` (MSB on first falling edge),
- ignores the 1st rising edge,
- 15 B-bits on rising edges 2..16 → `B[15:1]`,
- B[0] on the CS rising edge.

Master should sample MISO on both SCLK edges **and** on the CS rising edge.

### 4.1 Non-amplifier channels
When CONVERT addresses a non-amplifier channel (aux/temp/supply, C>31 in the A
core), the B-module result is meaningless and ignored by the host — but the
emulator still drives *something*. We drive the B BRAM contents regardless;
validation just ignores B for those channels.

---

## 5. Register map

64 addressable 8-bit registers. RAM = writable, ROM = read-only.

### 5.1 RAM registers (0–21)
Identical to RHD2132/RHD2216 for 0–17; **18–21 are RHD2164-specific**
(individual amplifier power for channels 32–63).

| Reg | Contents |
|-----|----------|
| 0  | ADC config / amp fast settle |
| 1  | supply sensor + ADC buffer bias |
| 2  | MUX bias |
| 3  | MUX load, temp sensor, digout |
| 4  | ADC output format + DSP offset (bit6 = **twoscomp**, bit4 = DSPen) |
| 5  | impedance check control |
| 6  | impedance check DAC |
| 7  | impedance check amp select (Zcheck select[5:0], all 6 bits used on 2164) |
| 8–13 | amplifier bandwidth select |
| 14–17 | individual amp power, channels 0–31 (`apwr[31:0]`) |
| 18–21 | individual amp power, channels 32–63 (`apwr[63:32]`) — 2164 only |

Power-up value of RAM is indeterminate on real silicon; we reset them to 0 for
deterministic simulation (documented deviation).

**READ of regs 18–21 quirk:** results appear correctly only on the **MISO B**
stream; all other registers read back on **MISO A**. The emulator routes reg
18–21 read data onto B and 0s (or the standard A read) onto A accordingly.

### 5.2 ROM registers (read-only)

| Reg | Value | Meaning |
|-----|-------|---------|
| 40 | 'I' = 0x49 | company designation |
| 41 | 'N' = 0x4E | |
| 42 | 'T' = 0x54 | |
| 43 | 'A' = 0x41 | |
| 44 | 'N' = 0x4E | |
| 59 | A: 0x35 (53) / B: 0x3A (58) | **MISO A/B marker** — different per module |
| 60 | die revision (Intan-defined; we use 0x01) | |
| 61 | 0x01 | unipolar amplifiers (1 = unipolar+common ref) |
| 62 | 0x40 = 64 | number of amplifiers |
| 63 | 0x04 | **chip ID = 4** (RHD2164) |

Register 59 is the key cross-check that A and B streams are distinct: A core
returns 0x35, B core returns 0x3A for READ(59).

---

## 6. ADC sample-on-CS-falling

The selected channel is "sampled" on the falling edge of CS. For the emulator,
the BRAM read for a CONVERT can be registered on CS-falling so the value is
ready to shift out two cycles later. CS must pulse high between every word.

---

## 7. Channel data source (emulator-specific)

No real electrodes. Per user decision, each module's 32 channels read their
16-bit "ADC value" from a **BRAM initialized from a `.mem` file at synthesis
time** (`$readmemh`). This lets the host validate that every channel returns a
known, distinct value. Layout:

- `mem/chip0_A.mem` — 32 words, channels 0–31 of chip 0
- `mem/chip0_B.mem` — 32 words, channels 32–63 of chip 0
- `mem/chip1_A.mem` — 32 words, channels 0–31 of chip 1
- `mem/chip1_B.mem` — 32 words, channels 32–63 of chip 1

Non-amplifier channels (32–62 in A addressing space: aux/temp/supply) can be
backed by a small ROM or fixed constants; for v1 we return a fixed pattern.

---

## 8. Clocking (emulator-specific)

Board oscillator: **100 MHz**. An MMCM generates a fast sample/launch clock
(target ~400 MHz, well within the XC7S25's >450 MHz fabric capability) used to
oversample the asynchronous SCLK/CS/MOSI and to launch MISO edges with low
latency so tMISO ≤ 12 ns is met at 24 MHz SCLK. Exact MMCM ratio finalized in
the constraints/timing task.

---

## 9. Documented deviations from silicon

1. RAM registers reset to 0 at power-up (silicon = indeterminate).
2. Channel "ADC" data comes from BRAM, not real amplifiers.
3. Analog blocks (impedance DAC, temp sensor, bandwidth filters) are not
   modeled physically — their registers are writable/readable but have no
   analog effect.
4. Calibration is modeled only as the 9-command busy window + MSB-only results;
   no real ADC trimming occurs.
