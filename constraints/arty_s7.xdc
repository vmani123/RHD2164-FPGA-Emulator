# ============================================================================
# arty_s7.xdc  —  RHD2164 emulator switchable bench top (rhd2164_top_arty) on
#                 Digilent Arty S7. Pin sites from Digilent's Arty-S7 master XDC.
# ----------------------------------------------------------------------------
# Board: Arty S7-25  -> part xc7s25csga324-1  (the RTL's native target)
#        Arty S7-50  -> part xc7s50csga324-1  (same CSGA324 pinout, also works)
#
# SW0 selects the active SPI interface at runtime (LD4 lit = LVDS selected):
#   SW0 = 0  SINGLE-ENDED on Pmod JA (3.3 V CMOS)
#   SW0 = 1  DIFFERENTIAL on Pmod JB (+ JC pair for MISO1)
#
# Pmod JA — single-ended (physical pins; 5/11 = GND, 6/12 = 3V3):
#   JA1  (L17) -> se_cs     JA3 (M14) -> se_sclk    JA7 (M16) -> se_mosi
#   JA9  (M18) -> se_miso0  JA10 (N18) -> se_miso1
#   JA2/JA4/JA8 stay unused so each input keeps a quiet pair-neighbour.
#
# Pmod JB / JC — differential (JB is routed as 4 true pairs):
#   JB1/JB2  (P17/P18) -> lv_cs_p   / lv_cs_n     (LVDS_25 input)
#   JB3/JB4  (R18/T18) -> lv_sclk_p / lv_sclk_n   (LVDS_25 input)
#   JB7/JB8  (P14/P15) -> lv_mosi_p / lv_mosi_n   (LVDS_25 input)
#   JB9/JB10 (N15/P16) -> lv_miso0_p / lv_miso0_n (pseudo-diff LVCMOS33 out)
#   JC1/JC2  (U15/V16) -> lv_miso1_p / lv_miso1_n (pseudo-diff LVCMOS33 out;
#                         shared with shield pins ck_io41/40 — don't use both)
#
# Electrical notes for the differential side (Arty S7 has no 2.5 V bank):
#   * The three input pairs use true LVDS_25 receivers with DIFF_TERM FALSE —
#     permitted in an HR bank at VCCO = 3.3 V per UG471 (Vivado may still note
#     it with a warning). Terminate each pair with an EXTERNAL 100 ohm resistor
#     across P/N at the Pmod, and keep the driver's common mode ~1.2 V (any
#     real LVDS driver does this).
#   * The MISO pairs are complementary 3.3 V CMOS (pseudo-differential), not
#     current-mode LVDS. To feed a true LVDS receiver, attenuate to LVDS
#     levels with a resistor network, or use rhd2164_top on a 2.5 V-bank board.
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
# Reset (BTN0, active high while pressed) and mode switch (SW0).
# ----------------------------------------------------------------------------
set_property -dict {PACKAGE_PIN G15 IOSTANDARD LVCMOS33} [get_ports rst_btn]
set_property -dict {PACKAGE_PIN H14 IOSTANDARD LVCMOS33} [get_ports sw_mode]
set_false_path -from [get_ports {rst_btn sw_mode}]

# ----------------------------------------------------------------------------
# Single-ended SPI on Pmod JA. Pulls keep the emulator idle (CS deselected,
# SCLK/MOSI low) when nothing is connected or the LVDS side is in use.
# ----------------------------------------------------------------------------
set_property -dict {PACKAGE_PIN L17 IOSTANDARD LVCMOS33 PULLTYPE PULLUP}   [get_ports se_cs]
set_property -dict {PACKAGE_PIN M14 IOSTANDARD LVCMOS33 PULLTYPE PULLDOWN} [get_ports se_sclk]
set_property -dict {PACKAGE_PIN M16 IOSTANDARD LVCMOS33 PULLTYPE PULLDOWN} [get_ports se_mosi]
set_property -dict {PACKAGE_PIN M18 IOSTANDARD LVCMOS33} [get_ports se_miso0]
set_property -dict {PACKAGE_PIN N18 IOSTANDARD LVCMOS33} [get_ports se_miso1]

# ----------------------------------------------------------------------------
# Differential SPI inputs on Pmod JB (true pair sites; P-side LOC would be
# enough for Vivado to infer N, but both are pinned for clarity).
# External 100 ohm termination across each pair at the connector.
# ----------------------------------------------------------------------------
set_property -dict {PACKAGE_PIN P17 IOSTANDARD LVDS_25} [get_ports lv_cs_p]
set_property -dict {PACKAGE_PIN P18 IOSTANDARD LVDS_25} [get_ports lv_cs_n]
set_property -dict {PACKAGE_PIN R18 IOSTANDARD LVDS_25} [get_ports lv_sclk_p]
set_property -dict {PACKAGE_PIN T18 IOSTANDARD LVDS_25} [get_ports lv_sclk_n]
set_property -dict {PACKAGE_PIN P14 IOSTANDARD LVDS_25} [get_ports lv_mosi_p]
set_property -dict {PACKAGE_PIN P15 IOSTANDARD LVDS_25} [get_ports lv_mosi_n]
set_property DIFF_TERM FALSE [get_ports {lv_cs_p lv_cs_n lv_sclk_p lv_sclk_n lv_mosi_p lv_mosi_n}]

# ----------------------------------------------------------------------------
# Pseudo-differential MISO outputs (complementary LVCMOS33 legs).
# ----------------------------------------------------------------------------
set_property -dict {PACKAGE_PIN N15 IOSTANDARD LVCMOS33} [get_ports lv_miso0_p]
set_property -dict {PACKAGE_PIN P16 IOSTANDARD LVCMOS33} [get_ports lv_miso0_n]
set_property -dict {PACKAGE_PIN U15 IOSTANDARD LVCMOS33} [get_ports lv_miso1_p]
set_property -dict {PACKAGE_PIN V16 IOSTANDARD LVCMOS33} [get_ports lv_miso1_n]

# ----------------------------------------------------------------------------
# Bring-up LEDs: LD2 (E18) = MMCM locked, LD3 (F13) = heartbeat,
# LD4 (E13) = LVDS mode selected.
# ----------------------------------------------------------------------------
set_property -dict {PACKAGE_PIN E18 IOSTANDARD LVCMOS33} [get_ports led_locked]
set_property -dict {PACKAGE_PIN F13 IOSTANDARD LVCMOS33} [get_ports led_alive]
set_property -dict {PACKAGE_PIN E13 IOSTANDARD LVCMOS33} [get_ports led_mode]
set_false_path -to [get_ports {led_locked led_alive led_mode}]

# ----------------------------------------------------------------------------
# Timing: same approach as rhd2164_top.xdc — CS/SCLK/MOSI (either interface)
# are sampled asynchronously by the 400 MHz clock through the spi_frontend
# synchronizers, so they are asynchronous datapaths, not launch clocks. The
# only hard requirement is the ~12 ns tMISO, met by construction at RHD2164
# SPI rates.
# ----------------------------------------------------------------------------
set_max_delay -datapath_only \
    -from [get_ports {se_cs se_sclk se_mosi lv_cs_p lv_sclk_p lv_mosi_p}] \
    -to [get_clocks clk_fast] 5.000

set_output_delay -clock clk_fast -max  2.000 \
    [get_ports {se_miso0 se_miso1 lv_miso0_p lv_miso0_n lv_miso1_p lv_miso1_n}]
set_output_delay -clock clk_fast -min -1.000 \
    [get_ports {se_miso0 se_miso1 lv_miso0_p lv_miso0_n lv_miso1_p lv_miso1_n}]

# ----------------------------------------------------------------------------
# Bitstream / config (per Digilent master XDC).
# ----------------------------------------------------------------------------
set_property CFGBVS VCCO        [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]
