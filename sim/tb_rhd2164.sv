// ============================================================================
// tb_rhd2164.sv  —  Testbench for the RHD2164 emulator (two chips)
// ----------------------------------------------------------------------------
// Drives a single-ended SPI master that mirrors the host controller:
//   * CS framing with tCS1 / tCSOFF gaps
//   * 16 SCLK pulses per transfer at ~24 MHz, CPOL=0, MOSI MSB-first
//   * samples the DDR MISO exactly as the datasheet specifies:
//       A[15:0] on the 16 SCLK FALLING edges,
//       B[15:0] on the 16 SCLK LOW phases (B[15..1] land on rising edges
//               2..16, B[0] on the CS rising edge).
//
// Two emulator cores share CS/SCLK/MOSI (like rhd2164_top), each with its own
// MISO. The scoreboard checks the 2-command pipeline: the word returned during
// transfer i is the result of the command issued in transfer i-2.
//
// Run:  see sim/run_sim.sh
// ============================================================================

`timescale 1ns/1ps
`default_nettype none

module tb_rhd2164;

    // ---- fast oversampling clock: 400 MHz (2.5 ns period) ----
    reg clk = 1'b0;
    always #1.25 clk = ~clk;

    reg rst_n = 1'b0;

    // ---- shared SPI bus ----
    reg  cs   = 1'b1;
    reg  sclk = 1'b0;
    reg  mosi = 1'b0;
    wire miso0, miso1;

    rhd2164_emulator #(.MEM_A_FILE("mem/chip0_A.mem"), .MEM_B_FILE("mem/chip0_B.mem"))
        dut0 (.clk(clk), .rst_n(rst_n), .cs(cs), .sclk(sclk), .mosi(mosi), .miso(miso0));

    rhd2164_emulator #(.MEM_A_FILE("mem/chip1_A.mem"), .MEM_B_FILE("mem/chip1_B.mem"))
        dut1 (.clk(clk), .rst_n(rst_n), .cs(cs), .sclk(sclk), .mosi(mosi), .miso(miso1));

    // ---- SPI timing (ns) ----
    localparam real TSCLK_HALF = 21.0;   // ~24 MHz (period 42 ns)
    localparam real TCS1       = 21.0;   // CS low -> first SCLK rising
    localparam real TCSOFF     = 160.0;  // CS high duration (>=154 ns)
    localparam real TSAMP      = 3.0;    // sample MISO this long before each edge

    // ---- scoreboard storage ----
    localparam int N = 32;
    reg [15:0] ret_a0 [0:N-1];
    reg [15:0] ret_b0 [0:N-1];
    reg [15:0] ret_a1 [0:N-1];
    reg [15:0] ret_b1 [0:N-1];
    integer    errors = 0;
    integer    idx    = 0;

    // ------------------------------------------------------------------
    // One SPI transfer: send 16-bit cmd, capture A/B from both chips.
    // ------------------------------------------------------------------
    task automatic spi_xfer(input [15:0] cmd);
        integer k;
        reg [15:0] a0, b0, a1, b1;
        begin
            a0 = 0; b0 = 0; a1 = 0; b1 = 0;
            cs = 1'b0;
            #(TCS1);
            for (k = 0; k < 16; k = k + 1) begin
                // Set up MOSI bit (MSB first) before the rising edge.
                mosi = cmd[15 - k];
                #2;
                sclk = 1'b1;                     // rising edge R_(k+1)
                #(TSCLK_HALF - TSAMP - 2);
                // Sample A near the end of the HIGH phase (the falling-edge value).
                a0 = {a0[14:0], miso0};
                a1 = {a1[14:0], miso1};
                #(TSAMP);
                sclk = 1'b0;                     // falling edge F_(k+1)
                #(TSCLK_HALF - TSAMP);
                // Sample B near the end of the LOW phase (rising-edge / CS-rising value).
                b0 = {b0[14:0], miso0};
                b1 = {b1[14:0], miso1};
                #(TSAMP);
            end
            cs = 1'b1;                           // CS rising (B[0] already captured)
            #(TCSOFF);

            ret_a0[idx] = a0; ret_b0[idx] = b0;
            ret_a1[idx] = a1; ret_b1[idx] = b1;
            idx = idx + 1;
        end
    endtask

    // ------------------------------------------------------------------
    // Check helpers
    // ------------------------------------------------------------------
    task automatic chk(input [8*24-1:0] name, input [15:0] got, input [15:0] exp);
        begin
            if (got !== exp) begin
                $display("  FAIL %0s: got %04h expected %04h", name, got, exp);
                errors = errors + 1;
            end else begin
                $display("  ok   %0s: %04h", name, got);
            end
        end
    endtask

    // ------------------------------------------------------------------
    // Stimulus
    // ------------------------------------------------------------------
    initial begin
        $dumpfile("sim/tb_rhd2164.vcd");
        $dumpvars(0, tb_rhd2164);

        // Reset
        repeat (10) @(posedge clk);
        rst_n = 1'b1;
        repeat (10) @(posedge clk);

        // idx: command            (result appears 2 transfers later)
        spi_xfer(16'hC000);   // 0  READ(0x00)? no -> READ(63): 11_111111_00000000
        // Correct READ(63) encoding: 11 + 111111 + 00000000 = 1111_1100_0000_0000 = 0xFC00
        // (the line above used a placeholder; real sequence below)
        // --- restart cleanly ---
        idx = 0;
        spi_xfer(16'hFC00);   // 0  READ(63)         -> chipID
        spi_xfer(16'hFC00);   // 1  READ(63)
        spi_xfer(16'hFA00);   // 2  READ(40) 'I'     ; returns res(0)=READ63=0x0004
        spi_xfer(16'hFB00);   // 3  READ(59) marker  ; returns res(1)=0x0004
        spi_xfer(16'hFE00);   // 4  READ(62) nAmps   ; returns res(2)=READ40=0x0049
        spi_xfer(16'h0000);   // 5  CONVERT(0)       ; returns res(3)=READ59 A=0x35 B=0x3A
        spi_xfer(16'h0100);   // 6  CONVERT(1)       ; returns res(4)=READ62=0x0040
        spi_xfer(16'h8816);   // 7  WRITE(8,0x16)    ; returns res(5)=CONVERT0 (BRAM ch0)
        spi_xfer(16'h1F00);   // 8  CONVERT(31)      ; returns res(6)=CONVERT1 (BRAM ch1)
        spi_xfer(16'h4000);   // 9  invalid (01..)   ; returns res(7)=WRITE echo 0xFF16
        spi_xfer(16'hFC00);   // 10 READ(63)         ; returns res(8)=CONVERT31 (BRAM ch31)
        spi_xfer(16'hFC00);   // 11 READ(63)         ; returns res(9)=invalid MSB-only 0x8000
        spi_xfer(16'hFC00);   // 12 READ(63)         ; returns res(10)=READ63=0x0004
        spi_xfer(16'hFC00);   // 13 READ(63)         ; returns res(11)=READ63=0x0004

        // --------------------------------------------------------------
        // Verify (chip0 unless noted). ret[i] = result of command (i-2).
        // --------------------------------------------------------------
        $display("\n=== RHD2164 emulator checks ===");

        // ROM: chip ID = 4 (reg 63)
        chk("READ63 chipID A", ret_a0[2], 16'h0004);
        chk("READ63 chipID B", ret_b0[2], 16'h0004);

        // ROM: 'I' of INTAN (reg 40)
        chk("READ40 'I'   A", ret_a0[4], 16'h0049);

        // A/B MARKER (reg 59): A=0x35, B=0x3A  -- proves DDR A/B separation
        chk("READ59 marker A", ret_a0[5], 16'h0035);
        chk("READ59 marker B", ret_b0[5], 16'h003A);

        // ROM: number of amps = 64 (reg 62)
        chk("READ62 nAmps A", ret_a0[6], 16'h0040);

        // CONVERT(0): chip0 A=0x1000 B=0x2000 ; chip1 A=0x3000 B=0x4000
        chk("CONV0 chip0 A", ret_a0[7], 16'h1000);
        chk("CONV0 chip0 B", ret_b0[7], 16'h2000);
        chk("CONV0 chip1 A", ret_a1[7], 16'h3000);
        chk("CONV0 chip1 B", ret_b1[7], 16'h4000);

        // CONVERT(1): channel index 1
        chk("CONV1 chip0 A", ret_a0[8], 16'h1001);
        chk("CONV1 chip0 B", ret_b0[8], 16'h2001);

        // WRITE(8,0x16) echo = {0xFF, data}
        chk("WRITE echo   A", ret_a0[9], 16'hFF16);

        // CONVERT(31): channel index 31
        chk("CONV31 chip0 A", ret_a0[10], 16'h101F);
        chk("CONV31 chip0 B", ret_b0[10], 16'h201F);
        chk("CONV31 chip1 A", ret_a1[10], 16'h301F);
        chk("CONV31 chip1 B", ret_b1[10], 16'h401F);

        // invalid command -> MSB-only result (twoscomp=0 => 0x8000)
        chk("invalid MSBonly", ret_a0[11], 16'h8000);

        $display("\n=== %0d error(s) ===", errors);
        if (errors == 0) $display("ALL CHECKS PASSED");
        else             $display("THERE WERE FAILURES");
        $finish;
    end

    // Safety timeout
    initial begin
        #500000;
        $display("TIMEOUT");
        $finish;
    end

endmodule

`default_nettype wire
