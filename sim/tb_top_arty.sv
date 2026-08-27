// ============================================================================
// tb_top_arty.sv — top-level smoke test for rhd2164_top_arty (the Arty S7
// switch-selectable bench top).
//
// What it proves, per interface (SW0 = 0 single-ended, then SW0 = 1 LVDS):
//   * clocking/reset come up (waits for led_locked),
//   * the selected interface reaches the cores through the input mux,
//   * ROM identity ('I', chip ID 4, 64 amps) and the reg-59 A/B DDR markers
//     (0x35/0x3A) come back on BOTH chips' MISO, at the 2-command pipeline
//     offset, sampled with the datasheet DDR scheme,
//   * WRITE echo + readback works,
//   * in LVDS mode the pseudo-differential N legs track ~P on every sample.
//
// Protocol depth (all commands, all channels, CALIBRATE, coverage) lives in
// sim/tb_rhd2164.sv against the cores; this bench only smokes the top level.
//
// Runs under Vivado xsim with the real unisims (sim/run_xsim.sh) and under
// Icarus Verilog with sim/iverilog_stubs.sv (sim/run_sim.sh).
// ============================================================================

`timescale 1ns/1ps
`default_nettype none

module tb_top_arty;

    // ---- board clock: 100 MHz ----
    reg clk100 = 1'b0;
    always #5 clk100 = ~clk100;

    reg rst_btn = 1'b1;
    reg sw_mode = 1'b0;

    // ---- single-ended interface ----
    reg  se_cs = 1'b1, se_sclk = 1'b0, se_mosi = 1'b0;
    wire se_miso0, se_miso1;

    // ---- differential interface ----
    reg  lv_cs_p = 1'b1,   lv_cs_n = 1'b0;
    reg  lv_sclk_p = 1'b0, lv_sclk_n = 1'b1;
    reg  lv_mosi_p = 1'b0, lv_mosi_n = 1'b1;
    wire lv_miso0_p, lv_miso0_n, lv_miso1_p, lv_miso1_n;

    wire led_locked, led_alive, led_mode;

    rhd2164_top_arty #(
        .MEM_C0A ("mem/chip0_A.mem"), .MEM_C0B ("mem/chip0_B.mem"),
        .MEM_C1A ("mem/chip1_A.mem"), .MEM_C1B ("mem/chip1_B.mem")
    ) dut (
        .clk_100mhz (clk100), .rst_btn (rst_btn), .sw_mode (sw_mode),
        .se_cs (se_cs), .se_sclk (se_sclk), .se_mosi (se_mosi),
        .se_miso0 (se_miso0), .se_miso1 (se_miso1),
        .lv_cs_p (lv_cs_p), .lv_cs_n (lv_cs_n),
        .lv_sclk_p (lv_sclk_p), .lv_sclk_n (lv_sclk_n),
        .lv_mosi_p (lv_mosi_p), .lv_mosi_n (lv_mosi_n),
        .lv_miso0_p (lv_miso0_p), .lv_miso0_n (lv_miso0_n),
        .lv_miso1_p (lv_miso1_p), .lv_miso1_n (lv_miso1_n),
        .led_locked (led_locked), .led_alive (led_alive), .led_mode (led_mode)
    );

    // ---- SPI timing (ns): slow (5 MHz SCLK) — this bench checks wiring and
    //      muxing, not speed; the core TB runs at the 24 MHz spec rate. ----
    localparam real TSCLK_HALF = 100.0;
    localparam real TCS1       = 100.0;
    localparam real TCSOFF     = 400.0;
    localparam real TSAMP      = 10.0;

    integer errors = 0;
    reg     tb_lvds = 1'b0;   // which interface this bench is driving

    // ------------------------------------------------------------------
    // Interface-agnostic drive/sample helpers.
    // ------------------------------------------------------------------
    task drv_cs(input v);
        begin
            if (tb_lvds) begin lv_cs_p = v; lv_cs_n = ~v; end
            else se_cs = v;
        end
    endtask
    task drv_sclk(input v);
        begin
            if (tb_lvds) begin lv_sclk_p = v; lv_sclk_n = ~v; end
            else se_sclk = v;
        end
    endtask
    task drv_mosi(input v);
        begin
            if (tb_lvds) begin lv_mosi_p = v; lv_mosi_n = ~v; end
            else se_mosi = v;
        end
    endtask

    // Sample the active MISOs; in LVDS mode also check N === ~P every sample.
    task samp_miso(output m0, output m1);
        begin
            if (tb_lvds) begin
                m0 = lv_miso0_p; m1 = lv_miso1_p;
                if (lv_miso0_n !== ~lv_miso0_p) begin
                    errors = errors + 1;
                    $display("  FAIL lvds miso0 N leg not complementary at %0t", $time);
                end
                if (lv_miso1_n !== ~lv_miso1_p) begin
                    errors = errors + 1;
                    $display("  FAIL lvds miso1 N leg not complementary at %0t", $time);
                end
            end else begin
                m0 = se_miso0; m1 = se_miso1;
            end
        end
    endtask

    // ------------------------------------------------------------------
    // One SPI transfer on the active interface (same DDR sampling scheme as
    // the core TB: A bits in the SCLK-high phase, B bits in the low phase,
    // B[0] covered by the final low-phase sample before CS rises).
    // ------------------------------------------------------------------
    localparam int N = 32;
    reg [15:0] ret_a0 [0:N-1];  reg [15:0] ret_b0 [0:N-1];
    reg [15:0] ret_a1 [0:N-1];  reg [15:0] ret_b1 [0:N-1];
    integer idx = 0;

    task spi_xfer(input [15:0] cmd);
        integer k;
        reg [15:0] a0, b0, a1, b1;
        reg m0, m1;
        begin
            a0 = 0; b0 = 0; a1 = 0; b1 = 0;
            drv_cs(1'b0);
            #(TCS1);
            for (k = 0; k < 16; k = k + 1) begin
                drv_mosi(cmd[15 - k]);           // MSB first, set up before rising
                #2;
                drv_sclk(1'b1);
                #(TSCLK_HALF - TSAMP - 2);
                samp_miso(m0, m1);               // A in the HIGH phase
                a0 = {a0[14:0], m0}; a1 = {a1[14:0], m1};
                #(TSAMP);
                drv_sclk(1'b0);
                #(TSCLK_HALF - TSAMP);
                samp_miso(m0, m1);               // B in the LOW phase
                b0 = {b0[14:0], m0}; b1 = {b1[14:0], m1};
                #(TSAMP);
            end
            drv_cs(1'b1);
            #(TCSOFF);
            ret_a0[idx] = a0; ret_b0[idx] = b0;
            ret_a1[idx] = a1; ret_b1[idx] = b1;
            idx = idx + 1;
        end
    endtask

    task check(input [15:0] got, input [15:0] exp, input [8*24-1:0] what);
        begin
            if (got !== exp) begin
                errors = errors + 1;
                $display("  FAIL %0s: got %04h exp %04h", what, got, exp);
            end
        end
    endtask

    function [15:0] READ (input [5:0] r); READ  = {2'b11, r, 8'h00}; endfunction
    function [15:0] WRITE(input [5:0] r, input [7:0] d); WRITE = {2'b10, r, d}; endfunction

    // ------------------------------------------------------------------
    // Identity + echo sequence on the currently selected interface.
    // Slot i returns the result of command i-2 (2-command pipeline).
    // ------------------------------------------------------------------
    task run_smoke(input lvds, input [7:0] wrval);
        integer base;
        begin
            tb_lvds = lvds;
            base    = idx;
            $display("--- %0s interface (SW0=%0d) ---", lvds ? "LVDS" : "single-ended", lvds);

            spi_xfer(READ(63));            // 0: dummy (also checked at slot 2)
            spi_xfer(READ(63));            // 1: dummy
            spi_xfer(READ(40));            // 2: 'I'
            spi_xfer(READ(59));            // 3: A/B marker
            spi_xfer(READ(62));            // 4: number of amplifiers
            spi_xfer(WRITE(8, wrval));     // 5: write echo
            spi_xfer(READ(8));             // 6: readback
            spi_xfer(READ(63));            // 7: chip ID
            spi_xfer(READ(63));            // 8: flush
            spi_xfer(READ(63));            // 9: flush

            check(ret_a0[base+2], 16'h0004, "chip0 chipID.A");
            check(ret_b0[base+2], 16'h0004, "chip0 chipID.B");
            check(ret_a1[base+2], 16'h0004, "chip1 chipID.A");
            check(ret_a0[base+4], 16'h0049, "chip0 INTAN 'I'");
            check(ret_a1[base+4], 16'h0049, "chip1 INTAN 'I'");
            check(ret_a0[base+5], 16'h0035, "chip0 reg59.A");
            check(ret_b0[base+5], 16'h003A, "chip0 reg59.B");
            check(ret_a1[base+5], 16'h0035, "chip1 reg59.A");
            check(ret_b1[base+5], 16'h003A, "chip1 reg59.B");
            check(ret_a0[base+6], 16'h0040, "chip0 nAmps");
            check(ret_a0[base+7], {8'hFF, wrval}, "chip0 WRITE echo");
            check(ret_b1[base+7], {8'hFF, wrval}, "chip1 WRITE echo.B");
            check(ret_a0[base+8], {8'h00, wrval}, "chip0 reg8 readback");
            check(ret_a1[base+9], 16'h0004, "chip1 chipID again");
        end
    endtask

    // ------------------------------------------------------------------
    // Stimulus.
    // ------------------------------------------------------------------
    initial begin
        $dumpfile("sim/tb_top_arty.vcd");
        $dumpvars(0, tb_top_arty);

        #200 rst_btn = 1'b0;
        wait (led_locked === 1'b1);
        #2000;

        sw_mode = 1'b0;                 // single-ended on Pmod JA
        #2000;
        run_smoke(1'b0, 8'hA5);

        sw_mode = 1'b1;                 // LVDS on Pmod JB/JC
        #2000;
        if (led_mode !== 1'b1) begin
            errors = errors + 1;
            $display("  FAIL led_mode not lit in LVDS mode");
        end
        run_smoke(1'b1, 8'h5A);

        $display("\n=== %0d error(s) over %0d top-level transfers ===", errors, idx);
        if (errors == 0) $display("TOP SMOKE PASSED");
        else             $display("TOP SMOKE FAILED");
        $finish;
    end

    // Safety timeout (MMCM lock in xsim takes a few microseconds).
    initial begin
        #5_000_000;
        $display("TIMEOUT waiting for lock or transfers");
        $display("TOP SMOKE FAILED");
        $finish;
    end

endmodule

`default_nettype wire
