# 001 — LMS+Rice+xchan_adaptive: backward-adaptive integer cross-channel gain

- **Cycle:** 1
- **Date:** 2026-07-08
- **Branch:** `compression-cycle-2026-07-08`
- **Candidate:** `LMS+Rice+xchan_adaptive`
- **Primary real dataset:** `otb_hdsemg_vl`

## Hypothesis

Replace the whole-signal float cross-channel gain (shipped as header side-info) with
a backward-adaptive per-block integer gain — beta for block i is the integer
least-squares ratio `<x_c,x_p>/<x_p,x_p>` over block i-1's already-reconstructed
samples (block 0 = beta 0) — so the decoder recomputes the identical gain causally
with zero side-info and zero look-ahead, while keeping the family's order-8 sign-sign
LMS + adaptive Golomb-Rice back-end unchanged.

## Implementation

Only the cross-channel front-end's beta-derivation changed inside
`research/registry.py`; the order-8 sign-sign LMS temporal predictor and the adaptive
Golomb-Rice entropy back-end are byte-identical to the incumbent `LMS+Rice+xchan`.

Implementer's report:

```json
{
  "codec_name": "LMS+Rice+xchan_adaptive",
  "hypothesis": "Replace the whole-signal float cross-channel gain (shipped as header side-info) with a backward-adaptive per-block integer gain — beta for block i is the integer least-squares ratio <x_c,x_p>/<x_p,x_p> over block i-1's already-reconstructed samples (block 0 = beta 0) — so the decoder recomputes the identical gain causally with zero side-info and zero look-ahead, while keeping the family's order-8 sign-sign LMS + adaptive Golomb-Rice back-end unchanged.",
  "selftest_command": "PYTHONPATH=host_tools ./.venv/bin/python research/registry.py --selftest",
  "selftest_output": "registry self-test on random int16 [32 x 2500], 6 codecs\n\ncodec                 ratio  round-trip  emb_ok  neural    cost\n---------------------------------------------------------------\ndelta+Rice            2.52x          OK      OK      OK   0.008\nLMS+Rice              2.73x          OK      OK      OK   0.052\ndelta+Rice+xchan      2.52x          OK      OK      OK   0.013\nLMS+Rice+xchan        2.72x          OK      OK      OK   0.057\nLMS+Rice+xchan_adaptive  2.72x          OK      OK      OK   0.065\nfixed0-3+Rice         2.72x          OK      OK      OK   0.019\n\nregistry self-test: ALL round-trips bit-exact",
  "selftest_passed": true,
  "files_changed": [
    "/home/user/RHD2164-FPGA-Emulator/research/registry.py"
  ]
}
```

Self-test command + output (proves bit-exactness of all 6 registered codecs incl. the
new one):

```
registry self-test on random int16 [32 x 2500], 6 codecs

codec                 ratio  round-trip  emb_ok  neural    cost
---------------------------------------------------------------
delta+Rice            2.52x          OK      OK      OK   0.008
LMS+Rice              2.73x          OK      OK      OK   0.052
delta+Rice+xchan      2.52x          OK      OK      OK   0.013
LMS+Rice+xchan        2.72x          OK      OK      OK   0.057
LMS+Rice+xchan_adaptive  2.72x          OK      OK      OK   0.065
fixed0-3+Rice         2.72x          OK      OK      OK   0.019

registry self-test: ALL round-trips bit-exact
```

## Measurement

Full raw measure-phase output, pasted verbatim.

Command 1: `PYTHONPATH=host_tools ./.venv/bin/python research/registry.py --selftest`
Exit code: 0
```
registry self-test on random int16 [32 x 2500], 6 codecs

codec                 ratio  round-trip  emb_ok  neural    cost
---------------------------------------------------------------
delta+Rice            2.52x          OK      OK      OK   0.008
LMS+Rice              2.73x          OK      OK      OK   0.052
delta+Rice+xchan      2.52x          OK      OK      OK   0.013
LMS+Rice+xchan        2.72x          OK      OK      OK   0.057
LMS+Rice+xchan_adaptive  2.72x          OK      OK      OK   0.065
fixed0-3+Rice         2.72x          OK      OK      OK   0.019

registry self-test: ALL round-trips bit-exact
```

---

Command 2: `PYTHONPATH=host_tools ./.venv/bin/python research/bench.py --datasets otb_hdsemg_vl synth_sc0.6 synth_sc0.9 --csv results/cycle_bench.csv --max-samples 15000`
Exit code: 0
```
### synth_sc0.6  [128 ch x 15000 samp @ 30000 Hz, grid (8, 16), synthetic]  3.84 MB raw
Compressing:   0%|          | 0/1 [00:00<?, ?it/s]Compressing: 100%|██████████| 1/1 [00:00<00:00,  2.36it/s]Compressing: 100%|██████████| 1/1 [00:00<00:00,  2.36it/s]
  codec                 ratio MB/s enc  %FLAC  emb   cost
  -------------------------------------------------------
  LMS+Rice+xchan        2.57x      3.3   180%   OK  0.057
  LMS+Rice+xchan_adaptive  2.57x      3.0   180%   OK  0.065
  delta+Rice+xchan      2.38x      4.3   167%   OK  0.013
  LMS+Rice              2.35x      3.2   165%   OK  0.052
  wavpack               2.35x      8.8   165%  ref    -  
  fixed0-3+Rice         2.35x      1.2   164%   OK  0.019
  delta+Rice            2.20x      3.7   154%   OK  0.008
  lzma                  2.16x      1.6   152%  ref    -  
  zstd-19               2.01x      1.6   141%  ref    -  
  gzip-9                1.81x      9.0   127%  ref    -  
  mtscomp               1.72x      8.8   120%  ref    -  
  flac                  1.43x     10.4   100%  ref    -  
  xchan gain: LMS+Rice+xchan +9.4%, delta+Rice+xchan +8.3%

### synth_sc0.9  [128 ch x 15000 samp @ 30000 Hz, grid (8, 16), synthetic]  3.84 MB raw
Compressing:   0%|          | 0/1 [00:00<?, ?it/s]Compressing: 100%|██████████| 1/1 [00:00<00:00,  2.99it/s]Compressing: 100%|██████████| 1/1 [00:00<00:00,  2.98it/s]
  codec                 ratio MB/s enc  %FLAC  emb   cost
  -------------------------------------------------------
  LMS+Rice+xchan        2.53x      3.1   189%   OK  0.057
  LMS+Rice+xchan_adaptive  2.52x      3.0   188%   OK  0.065
  delta+Rice+xchan      2.36x      4.1   176%   OK  0.013
  LMS+Rice              2.13x      2.9   159%   OK  0.052
  wavpack               2.13x      8.4   159%  ref    -  
  fixed0-3+Rice         2.12x      1.2   158%   OK  0.019
  delta+Rice            2.01x      3.9   150%   OK  0.008
  lzma                  1.95x      1.4   146%  ref    -  
  zstd-19               1.83x      1.3   136%  ref    -  
  gzip-9                1.62x     11.7   121%  ref    -  
  mtscomp               1.55x     11.3   116%  ref    -  
  flac                  1.34x     10.4   100%  ref    -  
  xchan gain: LMS+Rice+xchan +19.1%, delta+Rice+xchan +17.2%

### otb_hdsemg_vl  [64 ch x 15000 samp @ 2048 Hz, grid (5, 13), REAL]  1.92 MB raw
Compressing:   0%|          | 0/1 [00:00<?, ?it/s]Compressing: 100%|██████████| 1/1 [00:00<00:00,  6.65it/s]Compressing: 100%|██████████| 1/1 [00:00<00:00,  6.64it/s]
  codec                 ratio MB/s enc  %FLAC  emb   cost
  -------------------------------------------------------
  LMS+Rice+xchan        2.14x      2.6   175%   OK  0.057
  LMS+Rice+xchan_adaptive  2.13x      2.5   174%   OK  0.065
  delta+Rice+xchan      2.04x      3.9   166%   OK  0.013
  wavpack               1.85x      8.8   151%  ref    -  
  LMS+Rice              1.83x      2.5   149%   OK  0.052
  fixed0-3+Rice         1.82x      1.1   149%   OK  0.019
  delta+Rice            1.76x      3.5   143%   OK  0.008
  lzma                  1.58x      2.4   129%  ref    -  
  mtscomp               1.49x     12.3   121%  ref    -  
  zstd-19               1.44x      3.0   117%  ref    -  
  gzip-9                1.33x     20.5   108%  ref    -  
  flac                  1.23x     10.5   100%  ref    -  
  xchan gain: LMS+Rice+xchan +17.4%, delta+Rice+xchan +16.2%

wrote results/cycle_bench.csv  (36 rows)
```

---

Step 3 condition check: `LMS+Rice+xchan_adaptive` row for `otb_hdsemg_vl` showed ratio
2.13x, which is >= 2.10, so step 3 (search) was run.

Command 3: `PYTHONPATH=host_tools ./.venv/bin/python research/search.py --datasets otb_hdsemg_vl --csv results/cycle_search.csv`
Exit code: 0
```
searching on ['otb_hdsemg_vl']  (REAL)

  start lms8s8+x8/b256: ratio 2.2970  emb_ok=True
  step 1: lms8s8+x8/b256 -> lms4s8+x8/b256  ratio 2.2970 -> 2.3091  (+0.53%)
  step 2: lms4s8+x8/b256 -> lms4s7+x8/b256  ratio 2.3091 -> 2.3165  (+0.32%)
  step 3: lms4s7+x8/b256 -> lms4s7+x8/b512  ratio 2.3165 -> 2.3206  (+0.18%)
  step 4: lms4s7+x8/b512 -> lms4s7+x7/b512  ratio 2.3206 -> 2.3210  (+0.02%)
  converged at lms4s7+x7/b512: ratio 2.3210

=== BEST embeddable codec: lms4s7+x7/b512  mean ratio 2.321x ===
    embedded_ok=True neural_ok=True cost=0.027 enc=26 cyc/sample-ch

=== what mattered (ablation from best) ===
    cross          True -> False   costs +0.3710x ratio (+19.03%)
    order             4 -> 8       costs +0.0210x ratio (+0.91%)
    shift             7 -> 8       costs +0.0079x ratio (+0.34%)
    block           512 -> 256     costs +0.0042x ratio (+0.18%)

=== Pareto front (ratio vs cost, embedded_ok only, 60 configs evaluated) ===
    config                 ratio    cost  neural
    delta+x7/b512         2.279x   0.016      OK
    fixed+x7/b512         2.300x   0.025      OK
    lms4s7+x7/b512        2.321x   0.027      OK

wrote results/cycle_search.csv (60 configs)
```

**Headline measured number:** `LMS+Rice+xchan_adaptive` on REAL `otb_hdsemg_vl` =
**2.13×** (vs prior best `LMS+Rice+xchan` 2.14× → −0.01×, −0.48%). Every benched row
is bit-exact (`emb`/round-trip OK), and 2.13× is far below the ~6× leak sanity ceiling.

## Verification

Two independent verifiers reproduced the measure-phase claims. Full verdicts pasted
verbatim.

Verifier A:
```json
{
  "verdict": "PROMOTE",
  "embedded_ok": true,
  "round_trip_ok": true,
  "reproduced_ratio_otb": 2.13,
  "reasons": "All measure-phase claims reproduced independently and every hard gate passes. (1) Bit-exact round-trip: registry --selftest all 6 codecs OK incl LMS+Rice+xchan_adaptive; embedded_codec.py selftest OK; independent round-trip on REAL otb_hdsemg_vl (64x15000 int16) np.array_equal=True. (2) Ratio on REAL data re-measured 2.13x (2.1323), matches claim; honest embeddable xchan gain vs LMS+Rice(1.83x) = +16.4%. (3) embedded_cost.score(meta): embedded_ok=True, neural_ok=True, cost=0.065, enc=67.2 cyc (fits sEMG 1831 & neural 125), state=7424B (<<256KiB), integer_only, causal, lookahead=0, no failing reasons. (4) 2.13x << 6x sanity, no leak. (5) Causality verified adversarially: encoder vs decoder betas identical over all 3654 block-channels; beta(block i) unchanged when block i perturbed (depends only on block i-1); 12-byte header carries NO beta side-info; all int64/fixed-point (no float). Not a watch-list method. Only Python/docs changed (research/registry.py, SURVEY.md); no rtl/ or sim/ touched. Caveat for leaderboard maintainer: adaptive ratio 2.13x is marginally below baseline LMS+Rice+xchan 2.14x and declared cost is higher (0.065 vs 0.057), but the baseline's embeddability is a documented approximation (its actual code uses a float whole-signal beta shipped as side-info), whereas this codec is the genuinely streaming realization matching its meta exactly and validating the hypothesis.",
  "evidence": "$ PYTHONPATH=host_tools ./.venv/bin/python research/registry.py --selftest\nregistry self-test on random int16 [32 x 2500], 6 codecs\ncodec                 ratio  round-trip  emb_ok  neural    cost\nLMS+Rice+xchan_adaptive  2.72x          OK      OK      OK   0.065\n... registry self-test: ALL round-trips bit-exact\n\n$ PYTHONPATH=host_tools ./.venv/bin/python host_tools/embedded_codec.py\nembedded_codec self-test: ALL round-trips bit-exact\n\n$ bench.py --datasets otb_hdsemg_vl --max-samples 15000  (REAL, asserts bit-exact per codec)\n  LMS+Rice+xchan        2.14x  OK  0.057\n  LMS+Rice+xchan_adaptive  2.13x  OK  0.065\n  LMS+Rice              1.83x  OK  0.052\n  xchan gain: LMS+Rice+xchan +17.4%\n\nembedded_cost.score(meta): embedded_ok=True neural_ok=True cost=0.06502 enc_cyc=67.2 sram_bytes=7424 reasons=[]  (SEMG_BUDGET 1831, NEURAL_BUDGET 125, SRAM_LIMIT 262144, MAX_LOOKAHEAD 4096)\n\nIndependent REAL-data audit (otb 64x15000 int16):\n  REAL round-trip bit-exact: True  ratio 2.1322956\n  encoder vs decoder betas identical: True (n betas: 3654)\n  beta(block 5) unchanged when block 5 perturbed: True (203)\n  header: magic 0x5841 cols 13 C 64 N 15000 -> 12-byte header, no beta array\n  xadapt_forward output dtype: int64 int-only: True\n\n$ git status --short\n M SURVEY.md\n M research/registry.py   (no rtl/ or sim/ changes)\n\nverify_compressed.py --selftest: FileNotFoundError sim_data/ground_truth.npy (fixture absent from repo; not a codec defect; round-trip otherwise proven via registry selftest + bench.py real-data assert + independent np.array_equal)."
}
```

Verifier B:
```json
{
  "verdict": "PROMOTE",
  "embedded_ok": true,
  "round_trip_ok": true,
  "reproduced_ratio_otb": 2.13,
  "evidence": "Reproduced independently (exact commands + outputs):\n\nCMD1: PYTHONPATH=host_tools ./.venv/bin/python research/registry.py --selftest\n -> \"registry self-test: ALL round-trips bit-exact\"\n    LMS+Rice+xchan_adaptive  2.72x  round-trip OK  emb_ok OK  neural OK  cost 0.065\n\nCMD2: PYTHONPATH=host_tools ./.venv/bin/python host_tools/embedded_codec.py\n -> \"embedded_codec self-test: ALL round-trips bit-exact\" (shared library intact)\n\nCMD3: PYTHONPATH=host_tools ./.venv/bin/python research/bench.py --datasets otb_hdsemg_vl --csv /tmp/verify_B_bench.csv --max-samples 15000\n -> otb_hdsemg_vl [64 ch x 15000 @ 2048 Hz, grid (5,13), REAL]\n    LMS+Rice+xchan       2.14x  OK  0.057\n    LMS+Rice+xchan_adaptive  2.13x  OK  0.065   (matches measure-phase claim 2.13x)\n    xchan gain: LMS+Rice+xchan +17.4%, delta+Rice+xchan +16.2%\n\nIndependent real-data round-trip (datasets.corpus() -> otb_hdsemg_vl.load(15000), shape (64,15000)):\n -> REAL round-trip bit-exact: True   ratio=2.132   (< 6x, no leak)\n\nCost re-derived from embedded_cost.score(meta): enc_ops=56 -> enc_cycles=67.2; state 58 B/ch x128 = 7424 B; cost = 67.2/1831.1 + 7424/262144 = 0.0650. integer_only=True, causal=True, lookahead=0 (<=4096), 7424 B << 256 KiB SRAM, 67 cyc fits sEMG(1831) AND neural(125) -> embedded_ok=True, neural_ok=True. Hand-recompute matches model exactly.\n\nNo-side-info / causality check: blob header = 12 bytes (<HHII magic=0x5841 cols=13 C=64 N=15000); bytes consumed by header + per-channel Rice bodies = 900438 = total blob length -> ZERO beta side-info transmitted. Code trace of _xadapt_inverse: block i beta computed from x[c,(i-1)*B:i*B] (prev reconstructed block) and x[p,...] (parent, parent[c]<c so fully reconstructed first); block 0 uses beta=0 both sides; no future/encoder-only data. 6 random-seed round-trips (varied C,N,cols) all bit-exact.\n\nNo rtl/ or sim/ files changed (git diff HEAD: only SURVEY.md, research/registry.py + untracked CSVs).",
  "reasons": "All correctness gates reproduce and the hypothesis is confirmed. The codec is bit-exact lossless on real otb data, on the registry synthetic self-test, and across 6 random seeds. Re-measured real-data ratio (2.13x) matches the claim and is far below the 6x leak threshold. embedded_ok=True is honest and code-verified: integer-only, causal, genuinely lookahead=0 with ZERO beta side-info (blob header is 12 bytes then Rice bodies only; consumed bytes == blob length), state 7424 B well under SRAM, 67 enc cyc/sample-ch fits even the tight 30 kS/s neural budget; cost 0.065 hand-recomputed. The decoder truly recomputes beta causally from the previous already-reconstructed block and the fully-reconstructed parent channel (parent[c]<c), with block-0 bootstrap beta=0 on both sides -- no encoder-only leak. Not a watch-list method. Flag (non-disqualifying): on the raw (ratio,cost) plane it is a hair behind the existing LMS+Rice+xchan (2.14x/0.057), but the hypothesis promised parity while removing side-info + look-ahead, which is exactly what reproduced -- and unlike the incumbent's aspirational whole-signal beta, this variant's streaming/zero-side-info realization is verified true-to-code."
}
```

**Combined verdict: PROMOTE** (double-PROMOTE — both verifiers independently
reproduced bit-exact round-trip, `embedded_ok=True`, and otb ratio 2.13×).

## Analyst attribution

```json
{
  "attribution": "Codec this cycle: LMS+Rice+xchan_adaptive. Only the cross-channel front-end's beta-derivation changed (offline whole-signal float beta shipped as side-info -> backward-adaptive per-block integer beta). Temporal predictor (order-8 sign-sign LMS) and entropy back-end (adaptive Golomb-Rice) are byte-identical to the incumbent, so neither is a mover this cycle.\n\nAttribution on the REAL set otb_hdsemg_vl (cycle_bench.csv rows 27,28,30,32), decomposed as chained ratios:\n- Temporal predictor: delta+Rice 1.757117 -> LMS+Rice 1.825352 = +3.88%. The order-8 LMS buys almost nothing over order-1 delta here.\n- Entropy back-end: unchanged (shared adaptive Rice) -> 0% this cycle.\n- Cross-channel front-end (adaptive beta): LMS+Rice 1.825352 -> LMS+Rice+xchan_adaptive 2.132296 = +16.82%. This is where essentially all the ratio comes from, as always for this family.\n\nNet effect of the cycle's actual change (offline float beta -> backward-adaptive integer beta), incumbent vs new on real data (rows 26 vs 27):\n- Ratio 2.142618 -> 2.132296 = -0.48% (0.995182x).\n- Compressed bytes 896100 -> 900438 = +4338 B = +0.484% larger file.\n- Cost 0.0572 -> 0.065 = +13.6% (enc 63.6->67.2 cyc/sample-ch; SRAM 5888->7424 B; verified by hand against embedded_cost.py).\nSo the mechanism change slightly HURT the ratio (a causal backward estimate is strictly less informed than the offline whole-signal least-squares optimum, plus the block-0 beta=0 bootstrap) while raising cost. The give-up is small and consistent on the synthetic sweeps too (rows 2-3: 9.4% -> 9.16% at sc0.6; rows 14-15: 19.1% -> 18.56% at sc0.9), i.e. it captures ~95-97% of the offline-beta gain everywhere.",
  "cross_channel_gain_isolated": "Isolated ACHIEVED cross-channel gain on real otb_hdsemg_vl (same predictor, xchan on vs off), from CSV cells, not the variance-R^2 ceiling:\n- New adaptive variant: LMS+Rice 1.8253518555842456 -> LMS+Rice+xchan_adaptive 2.1322956161334816 = +16.82%.\n- Incumbent offline-beta variant: LMS+Rice 1.8253518555842456 -> LMS+Rice+xchan 2.142618011382658 = +17.38% (matches the CSV xchan_gain cell = 17.4).\nThe backward-adaptive integer beta therefore recovers 16.82/17.38 = 96.8% of the offline-beta gain, giving up ~0.56 percentage points. Cross-channel remains the dominant lever by a wide margin (+16.8% vs the temporal predictor's +3.9% and the entropy back-end's 0%).",
  "headline_verdict_text": "Honest read: this is a slight ratio REGRESSION, not a ratio win. On the pure ratio-vs-cost plane the new codec (2.1323x, cost 0.065) is strictly DOMINATED by the incumbent LMS+Rice+xchan (2.1426x, cost 0.0572) on the only real set benched this cycle -- lower ratio AND higher cost. So by non-negotiable #2 (rank on the Pareto front, never ratio alone) it is not a Pareto win. Its value is entirely QUALITATIVE and it is real: it is the first realization of the +17% cross-channel gain that is genuinely embeddable AS BENCHMARKED -- integer-only, causal, lookahead=0, and zero beta side-info -- whereas the incumbent's 2.1426x was measured with an offline whole-signal float beta shipped in the header (the LEADERBOARD flags exactly this as the open port caveat). If the comparison must be apples-to-apples on truly streaming codecs, 2.1323x is the honest embeddable number and 2.1426x is not achievable on-node without the offline pass. Verifiers voted PROMOTE on that embeddability basis; I concur it is a sound engineering upgrade, but the orchestrator should record it as a causality/side-info win at a -0.48% ratio and +13.6% cost, NOT as a ratio improvement, and should not overwrite the OTB best-ratio number downward.",
  "pareto_check": "Dominated. Compare the two embedded_ok cross-channel points on real otb: LMS+Rice+xchan = (ratio 2.142618, cost 0.0572) vs LMS+Rice+xchan_adaptive = (ratio 2.132296, cost 0.065). The incumbent has both higher ratio and lower cost, so the new codec sits strictly inside the front and is dominated -- it does not join the ratio-vs-cost Pareto front. (Cost breakdown confirmed by hand from embedded_cost.py: adaptive adds _XADAPT_XTRA=3 ops -> 67.2 vs 63.6 enc cyc, and +18 state B/ch -> 7424 vs 5888 SRAM B.) The only axis on which it strictly beats the incumbent -- lookahead 0 vs BLOCK, and no side-info/no float -- is not a dimension of this cost model, which is why the model reports it as dominated. Both remain embedded_ok and neural_ok.",
  "sanity_gates": "Gates: (1) Bit-exact -- every row in cycle_bench.csv has ok=True, including the new codec (row 27); no FAIL rows. (2) Leak/degenerate ceiling -- max real ratio is 2.1426x (incumbent) and the new codec 2.1323x, both far below the ~6x sanity ceiling on broadband HD-sEMG; no leak. (3) Regression -- FLAG: the new codec's 2.1323x is a -0.48% ratio regression versus the current leaderboard best embeddable on OTB (LMS+Rice+xchan 2.14x), and it costs 13.6% more. It is not a regression in correctness or feasibility, but it is a regression in headline ratio, so it must not replace the incumbent's 2.14x figure on the leaderboard. Synthetic rows (real=False) are sweeps-only and were not used for any headline claim.",
  "next_hypotheses": [
    "Rank 1 (highest payoff, directly attacks both the -0.48% ratio and the +13.6% cost): apply the backward-adaptive integer beta on top of the order-4 predictor instead of order-8. Knob = predictor order 8->4 (keep cross-shift 7, block 512). cycle_search.csv shows lms4s7+x7/b512 = 2.321 tops lms8s8+x8/b512 = 2.3009 at ~half the cost (0.0271 vs 0.0402), and the LEADERBOARD ablation already found order-4 beats order-8 on two real sets. Pairing the cheaper/better predictor with the side-info-free adaptive beta could recover the give-up AND land below cost 0.0572, turning a dominated point into a genuine Pareto win.",
    "Rank 2 (targets the specific loss mechanism): sweep the adaptation block size XADAPT_BLOCK. The 0.48% real give-up (and the 19.1->18.56 synth sc0.9 gap) comes from (a) block-0 coded at beta=0 and (b) a stale previous-block beta on non-stationary EMG. Knob = XADAPT_BLOCK 256 -> {128, 512}: a smaller block tracks non-stationarity faster and dilutes the block-0 loss (at some added per-block k overhead); a warm-start of block-0 beta from block-0's own samples (still causal at block granularity) would remove the bootstrap penalty outright. Bench on real otb plus the synth corr sweep to confirm the lever.",
    "Rank 3 (highest ceiling, higher risk): extend the cross-channel front-end from a single grid-parent scalar beta to a 2-neighbour causal integer predictor (up + left), still backward-adaptive/side-info-free. Knob = channel-pairing topology / parents 1->2. Cross-channel is the dominant lever (+16.8% real), so residual spatial redundancy after one parent is the largest remaining pool; a second neighbour could add several points where neighbour |corr| is high (OTB/CEMHSEY/Hyser). Risk: two backward-adaptive betas must stay numerically stable and it raises cost, so gate hard on embedded_ok and the Pareto front."
  ]
}
```

## Outcome

**Outcome: kept, registered, NOT promoted as a new best.** The combined verifier
verdict is double-PROMOTE on the **embeddability/correctness audit only**
(bit-exact round-trip, `embedded_ok`, cost model) — that audit gates whether a codec
is legitimate enough to stay registered, and `LMS+Rice+xchan_adaptive` clears it
cleanly. But on real `otb_hdsemg_vl` it measures **2.13×** at cost 0.065, against the
incumbent `LMS+Rice+xchan`'s 2.14× at cost 0.057 — a −0.48% ratio give-up **and**
higher cost, so it is strictly *dominated* on the ratio-vs-cost Pareto front and,
per this project's non-negotiable ("if the new candidate does not beat the current
best on real data, keep it in the registry but do not promote it"), it is **not**
promoted to `LEADERBOARD.md` as the new best-ratio embeddable codec for `otb_hdsemg_vl`
— `LMS+Rice+xchan` keeps that title, unchanged.

Its value is real and verified true-to-code, and is recorded as such rather than as a
ratio win: it is the first realization of the +17% cross-channel gain that is
genuinely embeddable *as benchmarked* — integer-only, causal, look-ahead = 0, and
zero beta side-info — whereas the incumbent's 2.14× depends on a float whole-signal
beta computed offline and shipped as header side-info, which cannot actually be
produced on a real streaming node. On that basis it is `LEADERBOARD.md`'s recommended
next port target for the cross-channel front-end (see "Port caveat"), and the ranked
next hypotheses above target closing the −0.48%/+13.6% gap so a future cycle can turn
it into a genuine Pareto win.
