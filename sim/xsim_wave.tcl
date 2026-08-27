# Used by sim/run_xsim.sh batch mode: log EVERY signal in the design into the
# waveform database given via `xsim -wdb`, then run the bench to completion.
log_wave -recursive *
run all
quit
