#!/usr/bin/env bash
# Sanity-check the design with Vivado xsim (xvlog/xelab/xsim command line):
#   1. tb_rhd2164   — full reference-model + coverage TB against the cores
#   2. tb_top_arty  — smoke test of the Arty S7 switchable top, elaborated
#                     against the REAL Xilinx unisim models (MMCM/IBUFDS/ODDR)
# Exits non-zero if either bench fails, so it can gate a synthesis run.
#
# Prereq: a Vivado install on PATH — `source /opt/Xilinx/Vivado/<ver>/settings64.sh`
# Usage:  ./sim/run_xsim.sh        (run from anywhere; cd's to repo root)
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v xvlog >/dev/null 2>&1; then
    echo "ERROR: xvlog not found. Source Vivado first:"
    echo "  source /opt/Xilinx/Vivado/<version>/settings64.sh"
    exit 1
fi

rm -rf xsim.dir

# ---- compile everything once (rtl + both benches + glbl for unisims) ----
xvlog -sv \
    rtl/spi_frontend.sv rtl/command_decoder.sv rtl/register_file.sv \
    rtl/ddr_miso.sv rtl/rhd2164_emulator.sv rtl/rhd2164_top_arty.sv \
    sim/tb_rhd2164.sv sim/tb_top_arty.sv
xvlog "$XILINX_VIVADO/data/verilog/src/glbl.v"

# ---- 1. core reference-model testbench (pure RTL, no unisims needed) ----
xelab -debug typical tb_rhd2164 -s core_tb
xsim core_tb -runall | tee sim/xsim_core.log
grep -q "ALL CHECKS PASSED" sim/xsim_core.log || {
    echo "ERROR: core testbench failed under xsim"; exit 1; }

# ---- 2. Arty top smoke test against the real unisim primitives ----
xelab -debug typical -L unisims_ver tb_top_arty glbl -s top_tb
xsim top_tb -runall | tee sim/xsim_top.log
grep -q "TOP SMOKE PASSED" sim/xsim_top.log || {
    echo "ERROR: top-level smoke test failed under xsim"; exit 1; }

echo
echo "XSIM: all sanity checks passed (waveforms: sim/tb_rhd2164.vcd, sim/tb_top_arty.vcd)"
