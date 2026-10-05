# M3 physical-site bound experiment

Registered before implementation. Existing M5 Benders is not directly portable; isolate fixed M3 physical-site floors first. No default or old sealed evidence changes.

38 targeted tests pass in1.24s; both 40-task original schedules fixed-project into all local models; 406 frozen-source related regressions pass in11.20s. Timed jobs still zero. Waiting for active ALNS/M5 work; process environment hash seed0 fixed before timing.

1/24 zhou_Set2_100-5-1b_subset20 seed=42 budget=15 baseline: UB=784.285170, LB=728.923884, gap=0.070588, physical=0.000000

2/24 zhou_Set2_100-5-1b_subset20 seed=42 budget=15 physical: UB=784.285170, LB=714.813425, gap=0.088580, physical=378.943224

3/24 zhou_Set2_100-5-1b_subset20 seed=42 budget=30 physical: UB=784.285170, LB=755.696227, gap=0.036452, physical=401.291401

4/24 zhou_Set2_100-5-1b_subset20 seed=42 budget=30 baseline: UB=784.285170, LB=729.747130, gap=0.069539, physical=0.000000

5/24 zhou_Set2_100-5-1b_subset20 seed=43 budget=15 physical: UB=784.285170, LB=715.117971, gap=0.088191, physical=86.510661

6/24 zhou_Set2_100-5-1b_subset20 seed=43 budget=15 baseline: UB=784.285170, LB=718.204969, gap=0.084255, physical=0.000000

7/24 zhou_Set2_100-5-1b_subset20 seed=43 budget=30 baseline: UB=784.285170, LB=723.702056, gap=0.077246, physical=0.000000

8/24 zhou_Set2_100-5-1b_subset20 seed=43 budget=30 physical: UB=784.285170, LB=742.032571, gap=0.053874, physical=401.322273

9/24 zhou_Set2_100-5-1b_subset20 seed=44 budget=15 baseline: UB=784.285170, LB=712.679659, gap=0.091300, physical=0.000000

10/24 zhou_Set2_100-5-1b_subset20 seed=44 budget=15 physical: UB=784.285170, LB=710.387590, gap=0.094223, physical=230.212516

11/24 zhou_Set2_100-5-1b_subset20 seed=44 budget=30 physical: UB=784.285170, LB=723.676883, gap=0.077278, physical=86.510661

12/24 zhou_Set2_100-5-1b_subset20 seed=44 budget=30 baseline: UB=784.285170, LB=721.671748, gap=0.079835, physical=0.000000

13/24 zhou_Set2_100-5-2_subset20 seed=42 budget=15 physical: UB=404.016983, LB=392.197062, gap=0.029256, physical=210.375918

14/24 zhou_Set2_100-5-2_subset20 seed=42 budget=15 baseline: UB=404.016983, LB=404.016983, gap=0.000000, physical=0.000000

15/24 zhou_Set2_100-5-2_subset20 seed=42 budget=30 baseline: UB=404.016983, LB=404.016983, gap=0.000000, physical=0.000000

16/24 zhou_Set2_100-5-2_subset20 seed=42 budget=30 physical: UB=404.016983, LB=404.016805, gap=0.000000, physical=222.195660

17/24 zhou_Set2_100-5-2_subset20 seed=43 budget=15 baseline: UB=404.016983, LB=404.016983, gap=0.000000, physical=0.000000

18/24 zhou_Set2_100-5-2_subset20 seed=43 budget=15 physical: UB=404.016983, LB=392.453594, gap=0.028621, physical=210.632449

19/24 zhou_Set2_100-5-2_subset20 seed=43 budget=30 physical: UB=404.016983, LB=404.016805, gap=0.000000, physical=222.195660

20/24 zhou_Set2_100-5-2_subset20 seed=43 budget=30 baseline: UB=404.016983, LB=404.016983, gap=0.000000, physical=0.000000

21/24 zhou_Set2_100-5-2_subset20 seed=44 budget=15 physical: UB=404.016983, LB=398.045819, gap=0.014779, physical=210.218408

22/24 zhou_Set2_100-5-2_subset20 seed=44 budget=15 baseline: UB=404.016983, LB=404.016983, gap=0.000000, physical=0.000000

23/24 zhou_Set2_100-5-2_subset20 seed=44 budget=30 baseline: UB=404.016983, LB=404.016983, gap=0.000000, physical=0.000000

24/24 zhou_Set2_100-5-2_subset20 seed=44 budget=30 physical: UB=404.016983, LB=404.016805, gap=0.000000, physical=222.195660

All 24 timed runs complete without failure or retry. First source30s three gap improvements; its15s three regressions. Second source baseline6/6 integer proofs, candidate0/6 (three tiny positive gaps still not proofs). Raw performance gate fails; independent positive site MPS replay and full output audits in progress.

Closed 2026-10-05T11:40:46.223776+08:00: 24 full integer controls complete; 24 output audits, periodic replays and all40task checks pass. 34 positive site MPS checks,31 supported and3 second-source30s S4 checks timed out without confirmation. All34 fixed original MPS projections pass. Gate failed, default remains off. Best independently supported first-source interval[755.696226955545,784.285170225458] approximately, exact recorded UB in best_verified_interval.json. Frozen406 regressions and final38 targeted tests passed; final code differs only by explicitly registered bound-source metadata lines. No timed reruns or active worker; all negative/inconclusive evidence retained.
