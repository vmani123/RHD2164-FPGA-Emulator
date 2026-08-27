#!/usr/bin/env bash
# Compile + run both testbenches with Icarus Verilog:
#   1. tb_rhd2164   — full reference-model + coverage TB against the cores
#   2. tb_top_arty  — top-level smoke test of the Arty S7 switchable top
#      (Xilinx primitives replaced by sim/iverilog_stubs.sv)
# Exits non-zero if either reports a failure, so it can be used directly in CI.
#
# Usage:  ./sim/run_sim.sh        (run from anywhere; cd's to repo root)
set -euo pipefail

cd "$(dirname "$0")/.."

# ---- 1. core reference-model testbench ----
iverilog -g2012 -o sim/tb.vvp -s tb_rhd2164 \
    sim/tb_rhd2164.sv \
    rtl/rhd2164_emulator.sv \
    rtl/spi_frontend.sv \
    rtl/command_decoder.sv \
    rtl/register_file.sv \
    rtl/ddr_miso.sv

out="$(vvp sim/tb.vvp)"
echo "$out"

if ! echo "$out" | grep -q "ALL CHECKS PASSED"; then
    echo "::error::Core simulation reported failures (or did not complete)."
    exit 1
fi

# ---- 2. Arty top-level smoke test (stubbed Xilinx primitives) ----
iverilog -g2012 -o sim/tb_top.vvp -s tb_top_arty \
    sim/tb_top_arty.sv \
    sim/iverilog_stubs.sv \
    rtl/rhd2164_top_arty.sv \
    rtl/rhd2164_emulator.sv \
    rtl/spi_frontend.sv \
    rtl/command_decoder.sv \
    rtl/register_file.sv \
    rtl/ddr_miso.sv

out2="$(vvp sim/tb_top.vvp)"
echo "$out2"

# Waveforms: sim/tb_rhd2164.vcd, sim/tb_top_arty.vcd  (open with gtkwave)

if ! echo "$out2" | grep -q "TOP SMOKE PASSED"; then
    echo "::error::Top-level smoke simulation reported failures."
    exit 1
fi
