#!/usr/bin/env bash
# Compile + run the RHD2164 emulator testbench with Icarus Verilog.
# Exits non-zero if the self-checking testbench reports any failure, so it can
# be used directly in CI.
#
# Usage:  ./sim/run_sim.sh        (run from anywhere; cd's to repo root)
set -euo pipefail

cd "$(dirname "$0")/.."

iverilog -g2012 -o sim/tb.vvp -s tb_rhd2164 \
    sim/tb_rhd2164.sv \
    rtl/rhd2164_emulator.sv \
    rtl/spi_frontend.sv \
    rtl/command_decoder.sv \
    rtl/register_file.sv \
    rtl/ddr_miso.sv

# Capture output so we can both show it and inspect the result.
out="$(vvp sim/tb.vvp)"
echo "$out"

# Waveforms: sim/tb_rhd2164.vcd  (open with gtkwave)

if echo "$out" | grep -q "ALL CHECKS PASSED"; then
    exit 0
else
    echo "::error::Simulation reported failures (or did not complete)."
    exit 1
fi
