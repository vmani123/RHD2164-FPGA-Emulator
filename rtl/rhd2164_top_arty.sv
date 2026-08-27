// ============================================================================
// rhd2164_top_arty.sv  (SWITCH-SELECTABLE BENCH TOP — Digilent Arty S7)
// ----------------------------------------------------------------------------
// One bitstream, two SPI front doors into the same pair of emulated RHD2164
// chips, selected at runtime by slide switch SW0:
//
//   SW0 = 0 : SINGLE-ENDED interface on Pmod JA (3.3 V CMOS levels)
//   SW0 = 1 : DIFFERENTIAL interface on Pmod JB/JC — signals like the real
//             RHD2164's LVDS bus (CS/SCLK/MOSI received through IBUFDS
//             differential receivers, MISO driven as P/N pairs)
//
// I/O standards are fixed per pin at bitstream time, so the two interfaces
// live on different connectors and the switch selects which one feeds the
// cores. Both MISO interfaces are always driven; only the inputs are muxed.
// Flip the switch while the bus is idle (CS high).
//
// Electrical honesty (Arty S7 banks are 3.3 V — no 2.5 V bank):
//   * CS/SCLK/MOSI pairs use true LVDS_25 input buffers, which 7-series HR
//     banks permit at VCCO = 3.3 V with DIFF_TERM = FALSE (UG471): terminate
//     each pair with an external 100 ohm resistor at the Pmod.
//   * MISO pairs are PSEUDO-differential: two complementary LVCMOS33 outputs,
//     not a current-mode LVDS driver. Protocol-identical to the chip; to feed
//     a true LVDS receiver, add a resistor network or use rhd2164_top on a
//     board with a 2.5 V bank.
//
// Clocking is unchanged: 100 MHz board oscillator -> MMCM -> 400 MHz fast
// oversampling clock shared by both interfaces.
//
// LEDs: LD2 = MMCM locked, LD3 = ~0.7 s heartbeat, LD4 = LVDS mode selected.
// ============================================================================

`default_nettype none

module rhd2164_top_arty #(
    // .mem paths are overridable so simulation can point at mem/ from the
    // repo root; synthesis flows stage the files next to the project instead.
    parameter MEM_C0A = "chip0_A.mem",
    parameter MEM_C0B = "chip0_B.mem",
    parameter MEM_C1A = "chip1_A.mem",
    parameter MEM_C1B = "chip1_B.mem"
) (
    input  wire clk_100mhz,   // 100 MHz board oscillator (Arty S7: R2)
    input  wire rst_btn,      // active-high external reset (BTN0)
    input  wire sw_mode,      // SW0: 0 = single-ended (JA), 1 = LVDS (JB/JC)

    // ---- Single-ended SPI interface (Pmod JA, 3.3 V) ----
    input  wire se_cs,        // active-low chip select
    input  wire se_sclk,      // serial clock, CPOL=0 (idle low)
    input  wire se_mosi,      // command in, MSB first
    output wire se_miso0,     // chip 0 data out
    output wire se_miso1,     // chip 1 data out

    // ---- Differential SPI interface (Pmod JB + JC) ----
    input  wire lv_cs_p,   input wire lv_cs_n,
    input  wire lv_sclk_p, input wire lv_sclk_n,
    input  wire lv_mosi_p, input wire lv_mosi_n,
    output wire lv_miso0_p, output wire lv_miso0_n,   // chip 0 (pseudo-diff)
    output wire lv_miso1_p, output wire lv_miso1_n,   // chip 1 (pseudo-diff)

    // ---- Bring-up indicators ----
    output wire led_locked,   // MMCM locked
    output wire led_alive,    // ~0.7 s heartbeat
    output wire led_mode      // lit = LVDS interface selected
);

    // ------------------------------------------------------------------
    // Clock generation: 100 MHz -> 400 MHz (VCO = 800 MHz).
    // ------------------------------------------------------------------
    wire clk_ibuf, clk_fb, clk_fast_unbuf, clk_fast, mmcm_locked;

    IBUF u_clk_ibuf (.I(clk_100mhz), .O(clk_ibuf));

    MMCME2_BASE #(
        .CLKIN1_PERIOD   (10.000),   // 100 MHz
        .CLKFBOUT_MULT_F (8.000),    // VCO = 800 MHz
        .DIVCLK_DIVIDE   (1),
        .CLKOUT0_DIVIDE_F(2.000)     // 400 MHz
    ) u_mmcm (
        .CLKIN1   (clk_ibuf),
        .CLKFBIN  (clk_fb),
        .CLKFBOUT (clk_fb),
        .CLKOUT0  (clk_fast_unbuf),
        .CLKOUT1  (), .CLKOUT2(), .CLKOUT3(), .CLKOUT4(), .CLKOUT5(), .CLKOUT6(),
        .CLKOUT0B(), .CLKOUT1B(), .CLKOUT2B(), .CLKOUT3B(),
        .CLKFBOUTB(),
        .LOCKED   (mmcm_locked),
        .PWRDWN   (1'b0),
        .RST      (rst_btn)
    );

    BUFG u_clk_bufg (.I(clk_fast_unbuf), .O(clk_fast));

    // Reset synchronizer: held low until MMCM locks, released on clk_fast.
    reg [3:0] rst_sync = 4'h0;
    wire rst_n;
    always @(posedge clk_fast or negedge mmcm_locked) begin
        if (!mmcm_locked) rst_sync <= 4'h0;
        else              rst_sync <= {rst_sync[2:0], 1'b1};
    end
    assign rst_n = rst_sync[3];

    // ------------------------------------------------------------------
    // Differential input receivers (LVDS interface).
    // ------------------------------------------------------------------
    wire lv_cs_se, lv_sclk_se, lv_mosi_se;
    IBUFDS u_cs_ibufds   (.I(lv_cs_p),   .IB(lv_cs_n),   .O(lv_cs_se));
    IBUFDS u_sclk_ibufds (.I(lv_sclk_p), .IB(lv_sclk_n), .O(lv_sclk_se));
    IBUFDS u_mosi_ibufds (.I(lv_mosi_p), .IB(lv_mosi_n), .O(lv_mosi_se));

    // ------------------------------------------------------------------
    // Interface select. The switch is synchronized onto clk_fast; the muxed
    // signals are still asynchronous to clk_fast and are cleaned up by the
    // 3-stage synchronizers inside each core's spi_frontend, so a mid-flip
    // glitch is absorbed the same way as any other input noise.
    // ------------------------------------------------------------------
    reg [1:0] sw_sync = 2'b00;
    always @(posedge clk_fast) sw_sync <= {sw_sync[0], sw_mode};
    wire mode_lvds = sw_sync[1];

    wire cs_mux   = mode_lvds ? lv_cs_se   : se_cs;
    wire sclk_mux = mode_lvds ? lv_sclk_se : se_sclk;
    wire mosi_mux = mode_lvds ? lv_mosi_se : se_mosi;

    // ------------------------------------------------------------------
    // Two emulator cores (shared by both interfaces).
    // ------------------------------------------------------------------
    wire miso0_core, miso1_core;

    rhd2164_emulator #(
        .MEM_A_FILE (MEM_C0A),
        .MEM_B_FILE (MEM_C0B)
    ) u_chip0 (
        .clk (clk_fast), .rst_n (rst_n),
        .cs (cs_mux), .sclk (sclk_mux), .mosi (mosi_mux), .miso (miso0_core)
    );

    rhd2164_emulator #(
        .MEM_A_FILE (MEM_C1A),
        .MEM_B_FILE (MEM_C1B)
    ) u_chip1 (
        .clk (clk_fast), .rst_n (rst_n),
        .cs (cs_mux), .sclk (sclk_mux), .mosi (mosi_mux), .miso (miso1_core)
    );

    // ------------------------------------------------------------------
    // MISO outputs. Every output bit is registered in its IOB via ODDR (both
    // phases identical, so the bit is simply forwarded) for deterministic,
    // low output delay. The LVDS-side N legs are driven complementary from
    // their own ODDRs so P and N launch on the same clock edge.
    // ------------------------------------------------------------------
    ODDR #(.DDR_CLK_EDGE("SAME_EDGE"), .INIT(1'b0), .SRTYPE("ASYNC")) u_se_oddr0 (
        .Q(se_miso0), .C(clk_fast), .CE(1'b1),
        .D1(miso0_core), .D2(miso0_core), .R(~rst_n), .S(1'b0)
    );
    ODDR #(.DDR_CLK_EDGE("SAME_EDGE"), .INIT(1'b0), .SRTYPE("ASYNC")) u_se_oddr1 (
        .Q(se_miso1), .C(clk_fast), .CE(1'b1),
        .D1(miso1_core), .D2(miso1_core), .R(~rst_n), .S(1'b0)
    );

    ODDR #(.DDR_CLK_EDGE("SAME_EDGE"), .INIT(1'b0), .SRTYPE("ASYNC")) u_lv_oddr0_p (
        .Q(lv_miso0_p), .C(clk_fast), .CE(1'b1),
        .D1(miso0_core), .D2(miso0_core), .R(~rst_n), .S(1'b0)
    );
    ODDR #(.DDR_CLK_EDGE("SAME_EDGE"), .INIT(1'b1), .SRTYPE("ASYNC")) u_lv_oddr0_n (
        .Q(lv_miso0_n), .C(clk_fast), .CE(1'b1),
        .D1(~miso0_core), .D2(~miso0_core), .R(1'b0), .S(~rst_n)
    );
    ODDR #(.DDR_CLK_EDGE("SAME_EDGE"), .INIT(1'b0), .SRTYPE("ASYNC")) u_lv_oddr1_p (
        .Q(lv_miso1_p), .C(clk_fast), .CE(1'b1),
        .D1(miso1_core), .D2(miso1_core), .R(~rst_n), .S(1'b0)
    );
    ODDR #(.DDR_CLK_EDGE("SAME_EDGE"), .INIT(1'b1), .SRTYPE("ASYNC")) u_lv_oddr1_n (
        .Q(lv_miso1_n), .C(clk_fast), .CE(1'b1),
        .D1(~miso1_core), .D2(~miso1_core), .R(1'b0), .S(~rst_n)
    );

    // ------------------------------------------------------------------
    // Bring-up LEDs.
    // ------------------------------------------------------------------
    assign led_locked = mmcm_locked;
    assign led_mode   = mode_lvds;

    reg [27:0] alive_ctr = 28'd0;   // 2^28 / 400 MHz ~ 0.67 s per half period
    always @(posedge clk_fast) alive_ctr <= alive_ctr + 1'b1;
    assign led_alive = alive_ctr[27];

endmodule

`default_nettype wire
