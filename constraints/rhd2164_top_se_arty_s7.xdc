# ============================================================================
# rhd2164_top_se_arty_s7.xdc
#   Constraints for rhd2164_top_se.sv on a Digilent Arty S7.
#
# Pin numbers taken from Digilent's master XDC files:
#   github.com/Digilent/digilent-xdc -> Arty-S7-25-Master.xdc / Arty-S7-50-Master.xdc
# The two boards have IDENTICAL Pmod, clock, button and LED pinouts, so this
# one file works for the S7-25 and the S7-50 without edits. Only the part
# number in the Vivado project differs (xc7s25csga324-1 / xc7s50csga324-1).
#
# Everything here is LVCMOS33 on purpose. See the header of rhd2164_top_se.sv
# for why LVDS_25 cannot be used on this board.
# ============================================================================

# ----------------------------------------------------------------------------
# Clock — 100 MHz oscillator.
#
# NOTE: on the Arty S7 the 100 MHz clock arrives on R2, which sits in the DDR3
# bank (VCCO = 1.35 V), so its IOSTANDARD is SSTL135, NOT LVCMOS33. This is a
# board quirk, not a typo. (The 12 MHz oscillator on F14 is LVCMOS33 if you
# would rather start from that and change CLKIN1_PERIOD / CLKFBOUT_MULT_F.)
# ----------------------------------------------------------------------------
set_property -dict { PACKAGE_PIN R2  IOSTANDARD SSTL135 } [get_ports { clk_100mhz }]
create_clock -name clk_100mhz -period 10.000 [get_ports { clk_100mhz }]

# Name the MMCM output so the timing constraints below can reference it.
create_generated_clock -name clk_fast [get_pins u_mmcm/CLKOUT0]

# ----------------------------------------------------------------------------
# Reset — BTN0. Arty S7 buttons are active HIGH (pressed = 1), which matches
# the active-high rst_btn port.
# ----------------------------------------------------------------------------
set_property -dict { PACKAGE_PIN G15 IOSTANDARD LVCMOS33 } [get_ports { rst_btn }]

# ----------------------------------------------------------------------------
# SPI bus — Pmod JA, single-ended.
#
#   JA physical pin   net            direction on THIS board
#   ---------------   ------------   ------------------------
#   JA1  (L17)        cs_in          input   <- receiver
#   JA2  (L18)        sclk_in        input   <- receiver
#   JA3  (M14)        mosi_in        input   <- receiver
#   JA4  (N14)        miso0_out      output  -> receiver
#   JA7  (M16)        miso1_out      output  -> receiver
#   JA5, JA11         GND            tie at least one to the receiver's GND
#   JA6, JA12         VCC3V3         *** LEAVE DISCONNECTED ***
#
# The receiver board uses the same five positions with the directions
# reversed, so a straight-through cable works.
#
# *** Do not bridge JA6/JA12 between two separately powered boards. Use five
# jumpers plus a ground, or a 12-pin Pmod ribbon with the VCC conductors
# pulled. Tying two 3.3 V rails together is how a board dies. ***
# ----------------------------------------------------------------------------
set_property -dict { PACKAGE_PIN L17 IOSTANDARD LVCMOS33 } [get_ports { cs_in     }]
set_property -dict { PACKAGE_PIN L18 IOSTANDARD LVCMOS33 } [get_ports { sclk_in   }]
set_property -dict { PACKAGE_PIN M14 IOSTANDARD LVCMOS33 } [get_ports { mosi_in   }]
set_property -dict { PACKAGE_PIN N14 IOSTANDARD LVCMOS33 } [get_ports { miso0_out }]
set_property -dict { PACKAGE_PIN M16 IOSTANDARD LVCMOS33 } [get_ports { miso1_out }]

# Drive strength and slew on the MISO outputs. 8 mA / SLOW keeps the edges
# civil on an unterminated jumper; raise to FAST only if the receiver's eye
# scan (phase_select sweep) shows no clean setting at your SCLK rate.
set_property DRIVE 8     [get_ports { miso0_out miso1_out }]
set_property SLEW  SLOW  [get_ports { miso0_out miso1_out }]

# ----------------------------------------------------------------------------
# Bench sanity LEDs — LD2..LD4.
# Delete these five lines and the three ports if you do not want them.
# ----------------------------------------------------------------------------
set_property -dict { PACKAGE_PIN E18 IOSTANDARD LVCMOS33 } [get_ports { led_locked    }]
set_property -dict { PACKAGE_PIN F13 IOSTANDARD LVCMOS33 } [get_ports { led_heartbeat }]
set_property -dict { PACKAGE_PIN E13 IOSTANDARD LVCMOS33 } [get_ports { led_activity  }]

# ----------------------------------------------------------------------------
# Timing.
#
# CS / SCLK / MOSI are sampled asynchronously by clk_fast through 3-stage
# synchronizers in spi_frontend.sv. They are NOT launch clocks, so declaring
# source-synchronous setup/hold would be meaningless. Bound the datapath
# instead and let the synchronizers absorb metastability.
# ----------------------------------------------------------------------------
set_max_delay -datapath_only -from [get_ports { cs_in sclk_in mosi_in }] \
    -to [get_clocks clk_fast] 5.000

# MISO launches from an ODDR in the IOB. The receiver samples on its own SCLK
# with a runtime-selectable phase, so a tight absolute output delay is not
# required — just bound it so the placer keeps the flop in the IOB.
set_output_delay -clock clk_fast -max  2.000 [get_ports { miso0_out miso1_out }]
set_output_delay -clock clk_fast -min -1.000 [get_ports { miso0_out miso1_out }]

# The heartbeat and activity LEDs are human-speed; do not let them pull in
# timing closure effort.
set_false_path -to [get_ports { led_locked led_heartbeat led_activity }]

# ----------------------------------------------------------------------------
# Bitstream / configuration
# ----------------------------------------------------------------------------
set_property CFGBVS VCCO        [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]

# ----------------------------------------------------------------------------
# $readmemh search path for the channel BRAM contents.
# Add mem/chip0_A.mem, chip0_B.mem, chip1_A.mem, chip1_B.mem to the project as
# design sources, or point Vivado at the directory:
#
#   set_property include_dirs {<repo>/mem} [get_filesets sources_1]
#
# If the .mem files do not resolve, synthesis still succeeds and every channel
# reads back zero — a silent failure that looks exactly like a dead SPI link.
# Check the synthesis log for "Failed to open file" before blaming the wiring.
# ----------------------------------------------------------------------------
