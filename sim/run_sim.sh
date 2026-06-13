#!/usr/bin/env bash
# Compile + run the RHD2164 emulator testbench with Icarus Verilog.
# Usage:  ./sim/run_sim.sh        (run from the repo root)
set -euo pipefail

cd "$(dirname "$0")/.."

iverilog -g2012 -o sim/tb.vvp -s tb_rhd2164 \
    sim/tb_rhd2164.sv \
    rtl/rhd2164_emulator.sv \
    rtl/spi_frontend.sv \
    rtl/command_decoder.sv \
    rtl/register_file.sv \
    rtl/ddr_miso.sv

vvp sim/tb.vvp
# Waveforms: sim/tb_rhd2164.vcd  (open with gtkwave)
