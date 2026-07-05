// ============================================================================
// tb_rhd2164.sv  —  Testbench for the RHD2164 emulator (two chips)
// ----------------------------------------------------------------------------
// Self-checking, reference-model-driven verification environment.
//
//   * An SPI master task mirrors the host controller: CS framing, 16 SCLK
//     pulses per transfer at ~24 MHz (CPOL=0, MOSI MSB-first), and samples the
//     DDR MISO exactly as the datasheet specifies (A on the 16 falling edges,
//     B on the 16 low phases: B[15..1] on rising edges 2..16, B[0] on CS rise).
//
//   * An INDEPENDENT reference model (ref_step / ref_read / chanval) re-derives
//     the expected A/B result for BOTH chips from the command stream, tracking
//     RAM registers, twoscomp, the CALIBRATE busy window, and the CONVERT(63)
//     MUX pointer. It is a separate implementation of the spec, so agreement
//     with the DUT is a real cross-check, not a tautology.
//
//   * The scoreboard auto-compares every transfer at the 2-command pipeline
//     offset: the word returned during transfer i is the result of command i-2.
//
// Directed coverage: ROM identity, reg-59 A/B marker, WRITE+readback, twoscomp
// MSB-only flip, CALIBRATE 9-command ignore window (writes suppressed),
// CONVERT(63) auto-increment, per-chip/module CONVERT data. A constrained-
// random tail exercises additional command mixes.
//
// Run:  ./sim/run_sim.sh
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
    localparam int N = 256;
    reg [15:0] ret_a0 [0:N-1];   reg [15:0] ret_b0 [0:N-1];
    reg [15:0] ret_a1 [0:N-1];   reg [15:0] ret_b1 [0:N-1];
    reg [15:0] exp_a0 [0:N-1];   reg [15:0] exp_b0 [0:N-1];
    reg [15:0] exp_a1 [0:N-1];   reg [15:0] exp_b1 [0:N-1];
    integer    errors = 0;
    integer    idx    = 0;       // next transfer slot

    // ================================================================
    // FUNCTIONAL COVERAGE (manual — iverilog has no covergroups).
    // Each bin records "did the stimulus exercise this case at least once".
    // ================================================================
    reg     cov_op       [0:5];   // CONVERT, CALIBRATE, CLEAR, WRITE, READ, invalid
    reg     cov_conv_ch  [0:31];  // each amplifier channel converted
    reg     cov_wr_reg   [0:21];  // each RAM register written
    reg     cov_rd_reg   [0:63];  // each register read (RAM + ROM)
    reg     cov_twoscomp [0:1];   // MSB-only result seen under twoscomp=0 and =1
    integer cov_calib  = 0;       // times the CALIBRATE window was armed
    integer cov_conv63 = 0;       // times CONVERT(63) auto-increment used
    integer ci, hit, tot;
    reg [8*10-1:0] opname [0:5];

    // ================================================================
    // REFERENCE MODEL state (an independent re-implementation of the spec)
    // ================================================================
    reg [7:0] ref_ram [0:21];
    reg [3:0] ref_calib;         // >0 => inside CALIBRATE dummy window
    reg [4:0] ref_mux;           // CONVERT(63) auto-increment pointer
    integer   ri;

    // ---- time-varying channel data: load the SAME .mem files the DUT plays ----
    localparam integer MEM_SAMPLES = 256;             // must match DUT parameter
    localparam integer DEPTH       = MEM_SAMPLES * 32;
    reg [15:0] mem_c0a [0:DEPTH-1];  reg [15:0] mem_c0b [0:DEPTH-1];
    reg [15:0] mem_c1a [0:DEPTH-1];  reg [15:0] mem_c1b [0:DEPTH-1];
    reg [$clog2(MEM_SAMPLES)-1:0] ref_sample_ptr;     // mirrors DUT sample_ptr

    // Expected CONVERT value = mem[sample_ptr*32 + channel], per chip/module.
    function [15:0] chanval(input integer chip, input is_b, input [4:0] cidx,
                            input integer sptr);
        integer a;
        begin
            a = sptr * 32 + cidx;
            if      (chip == 0 && !is_b) chanval = mem_c0a[a];
            else if (chip == 0 &&  is_b) chanval = mem_c0b[a];
            else if (chip == 1 && !is_b) chanval = mem_c1a[a];
            else                         chanval = mem_c1b[a];
        end
    endfunction

    // Register read value, mirroring register_file.sv (A vs B stream).
    function [7:0] ref_read(input [5:0] r, input is_b);
        begin
            if (r <= 6'd17)        ref_read = ref_ram[r];
            else if (r <= 6'd21)   ref_read = is_b ? ref_ram[r] : 8'h00; // 18..21 B-only
            else case (r)
                6'd40:   ref_read = 8'h49;
                6'd41:   ref_read = 8'h4E;
                6'd42:   ref_read = 8'h54;
                6'd43:   ref_read = 8'h41;
                6'd44:   ref_read = 8'h4E;
                6'd59:   ref_read = is_b ? 8'h3A : 8'h35;
                6'd60:   ref_read = 8'h01;
                6'd61:   ref_read = 8'h01;
                6'd62:   ref_read = 8'h40;
                6'd63:   ref_read = 8'h04;
                default: ref_read = 8'h00;
            endcase
        end
    endfunction

    // Compute expected results for a command and advance reference state.
    task automatic ref_step(input [15:0] cmd, input integer slot);
        reg        ignored;
        reg [15:0] msb_only;
        reg [1:0]  op;
        reg [5:0]  r_c;
        reg [7:0]  d;
        reg [4:0]  eff;
        begin
            ignored  = (ref_calib != 4'd0);
            msb_only = {~ref_ram[4][6], 15'b0};   // ref_ram[4][6] = twoscomp
            op       = cmd[15:14];
            r_c      = cmd[13:8];
            d        = cmd[7:0];
            eff      = (r_c == 6'd63) ? ((ref_mux == 5'd31) ? 5'd0 : ref_mux + 5'd1)
                                      : r_c[4:0];

            // Sample pointer advances on every CONVERT(0) (raw channel field 0),
            // unconditionally -- mirrors the DUT top, which acts on the command
            // stream regardless of the CALIBRATE ignore window.
            if (op == 2'b00 && r_c == 6'd0)
                ref_sample_ptr = (ref_sample_ptr == MEM_SAMPLES-1) ? 0 : ref_sample_ptr + 1;

            if (ignored) begin
                exp_a0[slot] = msb_only; exp_b0[slot] = msb_only;
                exp_a1[slot] = msb_only; exp_b1[slot] = msb_only;
            end else begin
                case (op)
                    2'b00: begin // CONVERT
                        exp_a0[slot] = chanval(0, 1'b0, eff, ref_sample_ptr);
                        exp_b0[slot] = chanval(0, 1'b1, eff, ref_sample_ptr);
                        exp_a1[slot] = chanval(1, 1'b0, eff, ref_sample_ptr);
                        exp_b1[slot] = chanval(1, 1'b1, eff, ref_sample_ptr);
                    end
                    2'b10: begin // WRITE echo
                        exp_a0[slot] = {8'hFF, d}; exp_b0[slot] = {8'hFF, d};
                        exp_a1[slot] = {8'hFF, d}; exp_b1[slot] = {8'hFF, d};
                    end
                    2'b11: begin // READ
                        exp_a0[slot] = {8'h00, ref_read(r_c, 1'b0)};
                        exp_b0[slot] = {8'h00, ref_read(r_c, 1'b1)};
                        exp_a1[slot] = exp_a0[slot];
                        exp_b1[slot] = exp_b0[slot];
                    end
                    default: begin // CALIBRATE / CLEAR / invalid
                        exp_a0[slot] = msb_only; exp_b0[slot] = msb_only;
                        exp_a1[slot] = msb_only; exp_b1[slot] = msb_only;
                    end
                endcase
            end

            // ---- state update ----
            if (ignored) begin
                ref_calib = ref_calib - 4'd1;
            end else begin
                if (op == 2'b00)
                    ref_mux = (r_c == 6'd63) ? eff : r_c[4:0];
                if (op == 2'b10 && r_c <= 6'd21)
                    ref_ram[r_c] = d;
                if (cmd == 16'h5500)
                    ref_calib = 4'd9;
            end
        end
    endtask

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
                mosi = cmd[15 - k];              // MSB first, set up before rising
                #2;
                sclk = 1'b1;                     // rising edge R_(k+1)
                #(TSCLK_HALF - TSAMP - 2);
                a0 = {a0[14:0], miso0};          // sample A in the HIGH phase
                a1 = {a1[14:0], miso1};
                #(TSAMP);
                sclk = 1'b0;                     // falling edge F_(k+1)
                #(TSCLK_HALF - TSAMP);
                b0 = {b0[14:0], miso0};          // sample B in the LOW phase
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

    // Sample functional coverage for a command (uses reference twoscomp state).
    task automatic cover_cmd(input [15:0] cmd);
        reg [1:0] op; reg [5:0] rc;
        begin
            op = cmd[15:14]; rc = cmd[13:8];
            case (op)
                2'b00: begin                              // CONVERT
                    cov_op[0] = 1'b1;
                    if (rc == 6'd63) cov_conv63 = cov_conv63 + 1;
                    else             cov_conv_ch[rc[4:0]] = 1'b1;
                end
                2'b01: begin                              // calibration family
                    if      (cmd == 16'h5500) begin cov_op[1] = 1'b1; cov_calib = cov_calib + 1; end
                    else if (cmd == 16'h6A00)        cov_op[2] = 1'b1;       // CLEAR
                    else                             cov_op[5] = 1'b1;       // invalid
                    cov_twoscomp[ref_ram[4][6]] = 1'b1;   // twoscomp at this moment
                end
                2'b10: begin                              // WRITE
                    cov_op[3] = 1'b1;
                    if (rc <= 6'd21) cov_wr_reg[rc] = 1'b1;
                end
                2'b11: begin                              // READ
                    cov_op[4] = 1'b1;
                    cov_rd_reg[rc] = 1'b1;
                end
            endcase
        end
    endtask

    // Issue a command: cover it, model it, then drive it on the bus.
    task automatic do_cmd(input [15:0] cmd);
        begin
            cover_cmd(cmd);
            ref_step(cmd, idx);
            spi_xfer(cmd);
        end
    endtask

    // ------------------------------------------------------------------
    // Command builders (encodings straight from the datasheet)
    // ------------------------------------------------------------------
    function [15:0] CONVERT(input [5:0] c); CONVERT = {2'b00, c, 8'h00};      endfunction
    function [15:0] READ   (input [5:0] r); READ    = {2'b11, r, 8'h00};      endfunction
    function [15:0] WRITE  (input [5:0] r, input [7:0] d); WRITE = {2'b10, r, d}; endfunction
    localparam [15:0] CALIBRATE = 16'h5500;
    localparam [15:0] CLEARCAL  = 16'h6A00;
    localparam [15:0] INVALID   = 16'h4000;   // 01.. but not CALIBRATE/CLEAR

    // ------------------------------------------------------------------
    // Stimulus
    // ------------------------------------------------------------------
    integer i, rseed, rop, rnd;
    reg [15:0] rcmd;

    initial begin
        $dumpfile("sim/tb_rhd2164.vcd");
        $dumpvars(0, tb_rhd2164);

        // init reference state to match DUT reset
        for (ri = 0; ri < 22; ri = ri + 1) ref_ram[ri] = 8'h00;
        ref_calib = 4'd0;
        ref_mux   = 5'd0;
        rseed     = 32'hC0FFEE01;

        // load the same channel data the DUT plays; mirror the sample pointer
        // (DUT resets sample_ptr to MEM_SAMPLES-1 so the first CONVERT(0) -> 0).
        $readmemh("mem/chip0_A.mem", mem_c0a);
        $readmemh("mem/chip0_B.mem", mem_c0b);
        $readmemh("mem/chip1_A.mem", mem_c1a);
        $readmemh("mem/chip1_B.mem", mem_c1b);
        ref_sample_ptr = MEM_SAMPLES - 1;

        // init coverage bins
        for (ci = 0; ci < 6;  ci = ci + 1) cov_op[ci]       = 1'b0;
        for (ci = 0; ci < 32; ci = ci + 1) cov_conv_ch[ci]  = 1'b0;
        for (ci = 0; ci < 22; ci = ci + 1) cov_wr_reg[ci]   = 1'b0;
        for (ci = 0; ci < 64; ci = ci + 1) cov_rd_reg[ci]   = 1'b0;
        cov_twoscomp[0] = 1'b0; cov_twoscomp[1] = 1'b0;
        opname[0]="CONVERT"; opname[1]="CALIBRATE"; opname[2]="CLEAR";
        opname[3]="WRITE";   opname[4]="READ";      opname[5]="invalid";

        // Reset
        repeat (10) @(posedge clk);
        rst_n = 1'b1;
        repeat (10) @(posedge clk);

        // ---- directed: dummy + full ROM sweep ----
        do_cmd(READ(63)); do_cmd(READ(63));          // power-up dummies
        do_cmd(READ(40)); do_cmd(READ(41)); do_cmd(READ(42));
        do_cmd(READ(43)); do_cmd(READ(44));          // INTAN
        do_cmd(READ(59));                            // A/B marker
        do_cmd(READ(60)); do_cmd(READ(61));
        do_cmd(READ(62)); do_cmd(READ(63));          // nAmps, chipID

        // ---- directed: RAM write + readback ----
        do_cmd(WRITE(8, 8'h16)); do_cmd(READ(8));    // bandwidth reg echoes + reads back

        // ---- directed: twoscomp flip (Register 4 bit 6) ----
        do_cmd(INVALID);                             // MSB-only, twoscomp=0 -> 0x8000
        do_cmd(WRITE(4, 8'h40));                     // set twoscomp=1
        do_cmd(READ(4));                             // confirm 0x40 stored
        do_cmd(INVALID);                             // MSB-only, twoscomp=1 -> 0x0000

        // ---- directed: CONVERT data + CONVERT(63) auto-increment ----
        do_cmd(CONVERT(0));                          // seed MUX pointer = 0
        do_cmd(CONVERT(5));
        do_cmd(CONVERT(31));
        do_cmd(CONVERT(0));                          // reseed pointer to 0
        do_cmd(CONVERT(63));                         // -> channel 1
        do_cmd(CONVERT(63));                         // -> channel 2
        do_cmd(CONVERT(63));                         // -> channel 3

        // ---- directed: CALIBRATE 9-command ignore window ----
        do_cmd(CALIBRATE);                           // arms 9-command window
        do_cmd(WRITE(2, 8'h3F));                     // ignored #1 (must NOT execute)
        do_cmd(READ(63)); do_cmd(READ(63)); do_cmd(READ(63)); do_cmd(READ(63));
        do_cmd(READ(63)); do_cmd(READ(63)); do_cmd(READ(63)); do_cmd(READ(63)); // ignored #2..9
        do_cmd(READ(2));                             // executes: should read 0x00 (write ignored)

        // ---- CLEAR command (covers the CLEAR opcode) ----
        do_cmd(CLEARCAL);

        // ---- coverage closure: sweep every channel and every RAM register ----
        for (i = 0; i < 32; i = i + 1) do_cmd(CONVERT(i[5:0]));        // all 32 channels
        for (i = 0; i < 22; i = i + 1) do_cmd(WRITE(i[5:0], 8'h80 + i[7:0])); // all 22 writes
        for (i = 0; i < 22; i = i + 1) do_cmd(READ(i[5:0]));          // read them back

        // ---- constrained-random tail ----
        // ($random returns SIGNED 32-bit; mask to stay non-negative before %.)
        for (i = 0; i < 40; i = i + 1) begin
            rop = ($random(rseed) & 32'h7FFF_FFFF) % 4;
            rnd = ($random(rseed) & 32'h7FFF_FFFF);
            case (rop)
                0: rcmd = CONVERT(rnd % 32);
                1: rcmd = READ(rnd % 24);                       // mix of RAM + a few ROM regs
                2: rcmd = WRITE(rnd % 22, ($random(rseed) & 32'h0000_00FF));
                default: rcmd = ((rnd % 8) == 0) ? INVALID : CONVERT(rnd % 32);
            endcase
            do_cmd(rcmd);
        end

        // ---- flush the 2-command pipeline ----
        do_cmd(READ(63)); do_cmd(READ(63));

        // --------------------------------------------------------------
        // Scoreboard: ret[i] must equal exp[i-2] on all four streams.
        // --------------------------------------------------------------
        $display("\n=== RHD2164 reference-model scoreboard (%0d transfers) ===", idx);
        for (i = 2; i < idx; i = i + 1) begin
            if (ret_a0[i] !== exp_a0[i-2]) begin errors=errors+1;
                $display("  FAIL t%0d chip0.A got %04h exp %04h", i, ret_a0[i], exp_a0[i-2]); end
            if (ret_b0[i] !== exp_b0[i-2]) begin errors=errors+1;
                $display("  FAIL t%0d chip0.B got %04h exp %04h", i, ret_b0[i], exp_b0[i-2]); end
            if (ret_a1[i] !== exp_a1[i-2]) begin errors=errors+1;
                $display("  FAIL t%0d chip1.A got %04h exp %04h", i, ret_a1[i], exp_a1[i-2]); end
            if (ret_b1[i] !== exp_b1[i-2]) begin errors=errors+1;
                $display("  FAIL t%0d chip1.B got %04h exp %04h", i, ret_b1[i], exp_b1[i-2]); end
        end

        // --------------------------------------------------------------
        // A few named milestone prints for human readability.
        // --------------------------------------------------------------
        $display("\n=== Milestones ===");
        $display("  chipID (reg63)      A=%04h  (exp 0004)", ret_a0[2]);   // res of READ(63) #0
        $display("  reg59 A/B marker    A=%04h B=%04h  (exp 0035/003A)", ret_a0[9], ret_b0[9]);
        $display("  twoscomp=0 MSB-only %04h  (exp 8000)", ret_a0[16]);    // INVALID #1
        $display("  twoscomp=1 MSB-only %04h  (exp 0000)", ret_a0[19]);    // INVALID #2

        // --------------------------------------------------------------
        // Functional coverage report.
        // --------------------------------------------------------------
        $display("\n=== Functional coverage ===");

        // Command opcodes
        hit = 0;
        for (ci = 0; ci < 6; ci = ci + 1) begin
            if (cov_op[ci]) hit = hit + 1;
            else $display("  MISS opcode: %0s", opname[ci]);
        end
        $display("  opcodes        : %0d/6 hit", hit);

        // CONVERT channels
        hit = 0;
        for (ci = 0; ci < 32; ci = ci + 1) if (cov_conv_ch[ci]) hit = hit + 1;
        $display("  CONVERT channels: %0d/32 hit", hit);

        // RAM register writes
        hit = 0;
        for (ci = 0; ci < 22; ci = ci + 1) if (cov_wr_reg[ci]) hit = hit + 1;
        $display("  RAM reg writes  : %0d/22 hit", hit);

        // Register reads: RAM (0..21) + the meaningful ROM regs
        hit = 0; tot = 0;
        for (ci = 0; ci < 22; ci = ci + 1) begin tot=tot+1; if (cov_rd_reg[ci]) hit=hit+1; end
        for (ci = 40; ci <= 44; ci = ci + 1) begin tot=tot+1; if (cov_rd_reg[ci]) hit=hit+1; end
        for (ci = 59; ci <= 63; ci = ci + 1) begin tot=tot+1; if (cov_rd_reg[ci]) hit=hit+1; end
        $display("  register reads  : %0d/%0d hit", hit, tot);

        // twoscomp states, CALIBRATE, CONVERT(63)
        $display("  twoscomp states : %0d/2 hit", (cov_twoscomp[0]?1:0)+(cov_twoscomp[1]?1:0));
        $display("  CALIBRATE window: armed %0d time(s)", cov_calib);
        $display("  CONVERT(63) used: %0d time(s)", cov_conv63);

        $display("\n=== %0d error(s) over %0d checked transfers ===", errors, idx-2);
        if (errors == 0) $display("ALL CHECKS PASSED");
        else             $display("THERE WERE FAILURES");
        $finish;
    end

    // Safety timeout
    initial begin
        #2000000;
        $display("TIMEOUT");
        $finish;
    end

endmodule

`default_nettype wire
