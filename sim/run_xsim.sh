#!/usr/bin/env bash
# Sanity-check the design with Vivado xsim (xvlog/xelab/xsim command line):
#   1. tb_rhd2164   — full reference-model + coverage TB against the cores
#   2. tb_top_arty  — smoke test of the Arty S7 switchable top, elaborated
#                     against the REAL Xilinx unisim models (MMCM/IBUFDS/ODDR)
# Exits non-zero if either bench fails, so it can gate a synthesis run.
#
# Waveforms: batch mode logs EVERY signal of both benches to Vivado waveform
# databases (sim/xsim_core.wdb, sim/xsim_top.wdb). Open one in the Vivado
# waveform viewer from the Vivado Tcl console:
#     open_wave_database sim/xsim_top.wdb
# Both benches also write VCDs (sim/*.vcd) for GTKWave.
#
# Interactive instead (live xsim GUI, add signals, run/step yourself):
#     ./sim/run_xsim.sh gui          # top-level Arty bench
#     ./sim/run_xsim.sh gui core     # core reference-model bench
#
# Prereq: a Vivado install on PATH — `source /opt/Xilinx/Vivado/<ver>/settings64.sh`
# Usage:  ./sim/run_xsim.sh [gui [core|top]]     (run from anywhere)
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

xelab -debug typical tb_rhd2164 -s core_tb
xelab -debug typical -L unisims_ver tb_top_arty glbl -s top_tb

# ---- interactive mode: open the live xsim GUI on one bench ----
if [ "${1:-}" = "gui" ]; then
    if [ "${2:-top}" = "core" ]; then
        exec xsim core_tb -gui
    else
        exec xsim top_tb -gui
    fi
fi

# ---- batch mode: run both benches, logging all signals to .wdb ----
xsim core_tb -tclbatch sim/xsim_wave.tcl -wdb sim/xsim_core.wdb | tee sim/xsim_core.log
grep -q "ALL CHECKS PASSED" sim/xsim_core.log || {
    echo "ERROR: core testbench failed under xsim"; exit 1; }

xsim top_tb -tclbatch sim/xsim_wave.tcl -wdb sim/xsim_top.wdb | tee sim/xsim_top.log
grep -q "TOP SMOKE PASSED" sim/xsim_top.log || {
    echo "ERROR: top-level smoke test failed under xsim"; exit 1; }

echo
echo "XSIM: all sanity checks passed"
echo "  waveform databases: sim/xsim_core.wdb, sim/xsim_top.wdb"
echo "    -> Vivado Tcl console:  open_wave_database sim/xsim_top.wdb"
echo "  VCDs for GTKWave:   sim/tb_rhd2164.vcd, sim/tb_top_arty.vcd"
