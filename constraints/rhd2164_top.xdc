# ============================================================================
# rhd2164_top.xdc  —  Vivado constraints for the RHD2164 emulator on XC7S25
# ----------------------------------------------------------------------------
# TARGET: XC7S25 (Spartan-7), Vivado.
#
# >>> YOU MUST EDIT TWO THINGS BEFORE IMPLEMENTATION <<<
#   1. PACKAGE_PIN assignments below — replace every <PIN> with the real pin
#      from your board schematic. They are placeholders.
#   2. The LVDS bank VCCO / IOSTANDARD. This file uses LVDS_25 (2.5 V bank),
#      the usual choice on Spartan-7. If your SPI pins sit in a 3.3 V bank,
#      change IOSTANDARD to a 3.3 V differential standard (e.g. DIFF_HSTL or
#      TMDS_33 / use an external LVDS buffer) — true LVDS receivers/drivers
#      require a 2.5 V VCCO on 7-series. Confirm your bank voltage first.
# ============================================================================

# ----------------------------------------------------------------------------
# Primary clock: 100 MHz board oscillator
# ----------------------------------------------------------------------------
set_property -dict {PACKAGE_PIN <CLK_PIN> IOSTANDARD LVCMOS33} [get_ports clk_100mhz]
create_clock -name clk_100mhz -period 10.000 [get_ports clk_100mhz]

# The MMCM derives the 400 MHz fast clock; Vivado auto-derives it from the MMCM.
# Give it a friendly name for downstream constraints/reports.
create_generated_clock -name clk_fast [get_pins u_mmcm/CLKOUT0]

# ----------------------------------------------------------------------------
# Reset
# ----------------------------------------------------------------------------
set_property -dict {PACKAGE_PIN <RST_PIN> IOSTANDARD LVCMOS33} [get_ports rst_btn]

# ----------------------------------------------------------------------------
# SPI inputs — LVDS differential pairs (shared bus to both emulated chips)
# Only the _p pin needs a LOC; Vivado infers the _n pin of the diff pair.
# ----------------------------------------------------------------------------
set_property -dict {PACKAGE_PIN <CS_P_PIN>   IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports cs_p]
set_property -dict {PACKAGE_PIN <CS_N_PIN>   IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports cs_n]
set_property -dict {PACKAGE_PIN <SCLK_P_PIN> IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports sclk_p]
set_property -dict {PACKAGE_PIN <SCLK_N_PIN> IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports sclk_n]
set_property -dict {PACKAGE_PIN <MOSI_P_PIN> IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports mosi_p]
set_property -dict {PACKAGE_PIN <MOSI_N_PIN> IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports mosi_n]

# DIFF_TERM TRUE enables the on-die 100 ohm differential termination so you do
# not need external resistors at the FPGA end. Remove if your board already has
# external termination on these pairs.

# ----------------------------------------------------------------------------
# MISO outputs — LVDS differential pairs (one per chip)
# ----------------------------------------------------------------------------
set_property -dict {PACKAGE_PIN <MISO0_P_PIN> IOSTANDARD LVDS_25} [get_ports miso0_p]
set_property -dict {PACKAGE_PIN <MISO0_N_PIN> IOSTANDARD LVDS_25} [get_ports miso0_n]
set_property -dict {PACKAGE_PIN <MISO1_P_PIN> IOSTANDARD LVDS_25} [get_ports miso1_p]
set_property -dict {PACKAGE_PIN <MISO1_N_PIN> IOSTANDARD LVDS_25} [get_ports miso1_n]

# ----------------------------------------------------------------------------
# Timing: SCLK / CS / MOSI are sampled asynchronously by the 400 MHz fast clock
# through 3-stage synchronizers (see spi_frontend.sv). They are NOT treated as
# launch clocks, so we declare them as asynchronous datapaths to the fast clock
# rather than trying to meet source-synchronous setup/hold. The synchronizers
# absorb the metastability; the only real requirement is the ~12 ns tMISO,
# which is met because the master samples a half SCLK period (~20.8 ns at
# 24 MHz) after each launch edge.
# ----------------------------------------------------------------------------
set_max_delay -datapath_only -from [get_ports {cs_p sclk_p mosi_p}] \
    -to [get_clocks clk_fast] 5.000

# Constrain MISO output skew/delay so the differential pair launches cleanly.
# Reference the fast clock; the host samples relative to its own SCLK so a tight
# absolute output delay is not required, but bound it to keep the IOB tidy.
set_output_delay -clock clk_fast -max  2.000 [get_ports {miso0_p miso1_p}]
set_output_delay -clock clk_fast -min -1.000 [get_ports {miso0_p miso1_p}]

# ----------------------------------------------------------------------------
# Bitstream / config conveniences (optional — uncomment as desired)
# ----------------------------------------------------------------------------
# set_property CFGBVS VCCO        [current_design]
# set_property CONFIG_VOLTAGE 3.3 [current_design]
