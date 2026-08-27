// ============================================================================
// iverilog_stubs.sv — behavioral stand-ins for the Xilinx primitives used by
// the synthesis tops, so tb_top_arty can run under Icarus Verilog (CI).
//
// DO NOT compile this file under Vivado xsim: there the real models come from
// the precompiled unisims library (xelab -L unisims_ver ... glbl). run_sim.sh
// (iverilog) uses this file; run_xsim.sh does not.
// ============================================================================

`timescale 1ns/1ps

module IBUF (input wire I, output wire O);
    assign O = I;
endmodule

module BUFG (input wire I, output wire O);
    assign O = I;
endmodule

module IBUFDS (input wire I, input wire IB, output wire O);
    // True differential receive: resolve from the pair, X on invalid drive.
    assign O = (I === ~IB) ? I : 1'bx;
endmodule

module OBUFDS (input wire I, output wire O, output wire OB);
    assign O  = I;
    assign OB = ~I;
endmodule

// Free-running 400 MHz CLKOUT0 (the ratio the tops request); LOCKED follows
// RST with a short delay, like a fast-locking MMCM.
module MMCME2_BASE #(
    parameter CLKIN1_PERIOD    = 10.0,
    parameter CLKFBOUT_MULT_F  = 8.0,
    parameter DIVCLK_DIVIDE    = 1,
    parameter CLKOUT0_DIVIDE_F = 2.0
) (
    input  wire CLKIN1, CLKFBIN, PWRDWN, RST,
    output wire CLKFBOUT, CLKFBOUTB, LOCKED,
    output reg  CLKOUT0,
    output wire CLKOUT1, CLKOUT2, CLKOUT3, CLKOUT4, CLKOUT5, CLKOUT6,
    output wire CLKOUT0B, CLKOUT1B, CLKOUT2B, CLKOUT3B
);
    initial CLKOUT0 = 1'b0;
    always #1.25 CLKOUT0 = ~CLKOUT0;      // 400 MHz
    assign #100 LOCKED = ~RST;
    assign CLKFBOUT = CLKIN1;
endmodule

module ODDR #(
    parameter DDR_CLK_EDGE = "SAME_EDGE",
    parameter INIT         = 1'b0,
    parameter SRTYPE       = "ASYNC"
) (
    input  wire C, CE, D1, D2, R, S,
    output reg  Q
);
    initial Q = INIT;
    always @(posedge C or posedge R or posedge S) begin
        if      (R)  Q <= 1'b0;
        else if (S)  Q <= 1'b1;
        else if (CE) Q <= D1;   // both phases carry the same bit in this design
    end
endmodule
