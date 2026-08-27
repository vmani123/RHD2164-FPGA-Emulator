# ============================================================================
# arty_s7.xdc  —  RHD2164 emulator bench top (rhd2164_top_se) on Digilent
#                 Arty S7. Pin sites taken from Digilent's Arty-S7 master XDC.
# ----------------------------------------------------------------------------
# Board: Arty S7-25  -> part xc7s25csga324-1  (the RTL's native target)
#        Arty S7-50  -> part xc7s50csga324-1  (same CSGA324 pinout, also works)
#
# This is the SINGLE-ENDED 3.3 V bring-up pinout over Pmod JA. The Arty S7's
# I/O banks run at 3.3 V (bank 34 at 1.35 V), so true LVDS_25 signaling is not
# available on this board; for the real LVDS headstage interface use
# rhd2164_top + rhd2164_top.xdc on a board with a 2.5 V bank.
#
# Pmod JA wiring (physical connector pins; 5/11 = GND, 6/12 = 3V3):
#   JA1  (L17) -> cs      (input,  master's active-low chip select)
#   JA3  (M14) -> sclk    (input,  CPOL=0, idle low)
#   JA7  (M16) -> mosi    (input,  command bits, MSB first)
#   JA9  (M18) -> miso0   (output, emulated chip 0 data)
#   JA10 (N18) -> miso1   (output, emulated chip 1 data)
# JA2/JA4/JA8 are left unused on purpose: JA is routed as differential pairs
# (JA1/JA2, JA3/JA4, JA7/JA8, JA9/JA10), so each critical input gets a quiet
# pair-neighbour instead of picking up crosstalk from SCLK. Tie your master's
# GND to JA5 or JA11 — a solid ground return matters more than wire length.
# ============================================================================

# ----------------------------------------------------------------------------
# Primary clock: 100 MHz oscillator on R2 (bank 34 — the 1.35 V DDR3 bank,
# hence SSTL135, per Digilent's master XDC).
# ----------------------------------------------------------------------------
set_property -dict {PACKAGE_PIN R2 IOSTANDARD SSTL135} [get_ports clk_100mhz]
create_clock -name clk_100mhz -period 10.000 [get_ports clk_100mhz]

# The MMCM derives the 400 MHz fast clock; give it a friendly name.
create_generated_clock -name clk_fast [get_pins u_mmcm/CLKOUT0]

# Bank 34 needs an internal VREF when its pins are used as ordinary I/O
# (Digilent sets this unconditionally in the master XDC).
set_property INTERNAL_VREF 0.675 [get_iobanks 34]

# ----------------------------------------------------------------------------
# Reset: BTN0 (G15), active high while pressed.
# ----------------------------------------------------------------------------
set_property -dict {PACKAGE_PIN G15 IOSTANDARD LVCMOS33} [get_ports rst_btn]
set_false_path -from [get_ports rst_btn]

# ----------------------------------------------------------------------------
# SPI over Pmod JA (see wiring table above). Pulls keep the emulator idle
# (CS deselected, SCLK/MOSI low) when nothing is connected.
# ----------------------------------------------------------------------------
set_property -dict {PACKAGE_PIN L17 IOSTANDARD LVCMOS33 PULLTYPE PULLUP}   [get_ports cs]
set_property -dict {PACKAGE_PIN M14 IOSTANDARD LVCMOS33 PULLTYPE PULLDOWN} [get_ports sclk]
set_property -dict {PACKAGE_PIN M16 IOSTANDARD LVCMOS33 PULLTYPE PULLDOWN} [get_ports mosi]
set_property -dict {PACKAGE_PIN M18 IOSTANDARD LVCMOS33} [get_ports miso0]
set_property -dict {PACKAGE_PIN N18 IOSTANDARD LVCMOS33} [get_ports miso1]

# ----------------------------------------------------------------------------
# Bring-up LEDs: LD2 (E18) = MMCM locked, LD3 (F13) = heartbeat.
# ----------------------------------------------------------------------------
set_property -dict {PACKAGE_PIN E18 IOSTANDARD LVCMOS33} [get_ports led_locked]
set_property -dict {PACKAGE_PIN F13 IOSTANDARD LVCMOS33} [get_ports led_alive]
set_false_path -to [get_ports {led_locked led_alive}]

# ----------------------------------------------------------------------------
# Timing: same approach as rhd2164_top.xdc — SCLK/CS/MOSI are sampled
# asynchronously by the 400 MHz clock through the spi_frontend synchronizers,
# so they are asynchronous datapaths, not launch clocks. The only hard
# requirement is the ~12 ns tMISO, met by construction at RHD2164 SPI rates.
# ----------------------------------------------------------------------------
set_max_delay -datapath_only -from [get_ports {cs sclk mosi}] \
    -to [get_clocks clk_fast] 5.000

set_output_delay -clock clk_fast -max  2.000 [get_ports {miso0 miso1}]
set_output_delay -clock clk_fast -min -1.000 [get_ports {miso0 miso1}]

# ----------------------------------------------------------------------------
# Bitstream / config (per Digilent master XDC).
# ----------------------------------------------------------------------------
set_property CFGBVS VCCO        [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]
