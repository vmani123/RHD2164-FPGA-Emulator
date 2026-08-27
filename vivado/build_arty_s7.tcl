# ============================================================================
# build_arty_s7.tcl — non-project batch build of the Arty S7 bench top.
#
#   vivado -mode batch -source vivado/build_arty_s7.tcl
#   vivado -mode batch -source vivado/build_arty_s7.tcl -tclargs xc7s50csga324-1
#
# Output lands in vivado/out/ (bitstream + timing/utilization reports).
# Default part is the Arty S7-25; pass the S7-50 part as the first tclarg.
# ============================================================================

set part "xc7s25csga324-1"
if {$argc > 0} { set part [lindex $argv 0] }

set root [file normalize [file join [file dirname [info script]] ..]]
set out  [file join $root vivado out]
file mkdir $out
cd $out

# $readmemh in rhd2164_emulator uses bare filenames ("chip0_A.mem"), which
# Vivado resolves against the current directory in non-project mode.
foreach f [glob [file join $root mem *.mem]] { file copy -force $f . }

read_verilog -sv [glob [file join $root rtl *.sv]]
read_xdc [file join $root constraints arty_s7.xdc]

synth_design -top rhd2164_top_se -part $part
opt_design
place_design
route_design

report_timing_summary -file timing_summary.rpt
report_utilization    -file utilization.rpt

# Fail loudly on a timing miss instead of shipping a broken bitstream.
set wns [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]]
if {$wns < 0} {
    puts "ERROR: setup timing not met (WNS = $wns ns) — see vivado/out/timing_summary.rpt"
    exit 1
}

write_bitstream -force rhd2164_top_se.bit
puts "DONE: [file join $out rhd2164_top_se.bit]  (WNS = $wns ns)"
