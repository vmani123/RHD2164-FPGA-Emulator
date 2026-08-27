// ============================================================================
// rhd2164_top_se.sv  (SINGLE-ENDED BENCH TOP — Digilent Arty S7 and similar)
// ----------------------------------------------------------------------------
// Same two-chip RHD2164 emulator as rhd2164_top.sv, but with single-ended
// 3.3 V SPI I/O instead of LVDS pairs. Intended for bench bring-up on boards
// whose I/O banks cannot do true LVDS_25 (the Arty S7's banks are 3.3 V /
// 1.35 V), talking to an MCU SPI master over a Pmod header. Use rhd2164_top
// (LVDS) for the real headstage interface on a board with a 2.5 V bank.
//
// Clocking is identical: 100 MHz board oscillator -> MMCM -> 400 MHz fast
// oversampling clock. The cores are unchanged; only the I/O ring differs.
//
// LEDs: led_locked = MMCM locked (should be lit whenever the board is up);
// led_alive blinks at ~0.7 s so a running bitstream is visible at a glance.
// ============================================================================

`default_nettype none

module rhd2164_top_se (
    input  wire clk_100mhz,   // 100 MHz single-ended board oscillator
    input  wire rst_btn,      // active-high external reset (Arty S7 BTN0)

    // Shared SPI inputs (single-ended, 3.3 V) — driven by the master to both chips
    input  wire cs,           // active-low chip select
    input  wire sclk,         // serial clock, CPOL=0 (idle low)
    input  wire mosi,         // command in, MSB first

    // Dedicated MISO outputs — one per emulated chip
    output wire miso0,        // chip 0
    output wire miso1,        // chip 1

    // Bring-up indicators
    output wire led_locked,   // MMCM locked
    output wire led_alive     // ~0.7 s heartbeat from the fast clock
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
    // Two emulator cores (single-ended inputs go straight in).
    // ------------------------------------------------------------------
    wire miso0_core, miso1_core;

    rhd2164_emulator #(
        .MEM_A_FILE ("chip0_A.mem"),
        .MEM_B_FILE ("chip0_B.mem")
    ) u_chip0 (
        .clk (clk_fast), .rst_n (rst_n),
        .cs (cs), .sclk (sclk), .mosi (mosi), .miso (miso0_core)
    );

    rhd2164_emulator #(
        .MEM_A_FILE ("chip1_A.mem"),
        .MEM_B_FILE ("chip1_B.mem")
    ) u_chip1 (
        .clk (clk_fast), .rst_n (rst_n),
        .cs (cs), .sclk (sclk), .mosi (mosi), .miso (miso1_core)
    );

    // ------------------------------------------------------------------
    // MISO outputs: register in the IOB via ODDR (both phases identical, so
    // the bit is simply forwarded) for deterministic, low output delay.
    // ------------------------------------------------------------------
    ODDR #(.DDR_CLK_EDGE("SAME_EDGE"), .INIT(1'b0), .SRTYPE("ASYNC")) u_oddr0 (
        .Q(miso0), .C(clk_fast), .CE(1'b1),
        .D1(miso0_core), .D2(miso0_core), .R(~rst_n), .S(1'b0)
    );
    ODDR #(.DDR_CLK_EDGE("SAME_EDGE"), .INIT(1'b0), .SRTYPE("ASYNC")) u_oddr1 (
        .Q(miso1), .C(clk_fast), .CE(1'b1),
        .D1(miso1_core), .D2(miso1_core), .R(~rst_n), .S(1'b0)
    );

    // ------------------------------------------------------------------
    // Bring-up LEDs.
    // ------------------------------------------------------------------
    assign led_locked = mmcm_locked;

    reg [27:0] alive_ctr = 28'd0;   // 2^28 / 400 MHz ~ 0.67 s per half period
    always @(posedge clk_fast) alive_ctr <= alive_ctr + 1'b1;
    assign led_alive = alive_ctr[27];

endmodule

`default_nettype wire
