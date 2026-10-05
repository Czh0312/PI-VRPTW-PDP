# M4 平衡价格供列闭环

2026-10-05T10:56:00.647434+08:00：新用户授权；候选和24次根对照预登记。保留旧夜间证据和暂停状态。

1/24 zhou_Set2_100-5-1b_subset20 budget=15 seed=42 baseline: E2=0.0, guided_added=0, TIME_LIMIT

2/24 zhou_Set2_100-5-1b_subset20 budget=15 seed=42 balanced: E2=0.0, guided_added=19, TIME_LIMIT

3/24 zhou_Set2_100-5-1b_subset20 budget=30 seed=42 balanced: E2=0.0, guided_added=37, TIME_LIMIT

4/24 zhou_Set2_100-5-1b_subset20 budget=30 seed=42 baseline: E2=0.0, guided_added=0, TIME_LIMIT

5/24 zhou_Set2_100-5-1b_subset20 budget=15 seed=43 balanced: E2=0.0, guided_added=14, PRICING_LIMIT

6/24 zhou_Set2_100-5-1b_subset20 budget=15 seed=43 baseline: E2=0.0, guided_added=0, TIME_LIMIT

7/24 zhou_Set2_100-5-1b_subset20 budget=30 seed=43 baseline: E2=0.0, guided_added=0, TIME_LIMIT

8/24 zhou_Set2_100-5-1b_subset20 budget=30 seed=43 balanced: E2=0.0, guided_added=16, TIME_LIMIT

9/24 zhou_Set2_100-5-1b_subset20 budget=15 seed=44 baseline: E2=0.0, guided_added=0, PRICING_LIMIT

10/24 zhou_Set2_100-5-1b_subset20 budget=15 seed=44 balanced: E2=0.0, guided_added=23, PRICING_LIMIT

11/24 zhou_Set2_100-5-1b_subset20 budget=30 seed=44 balanced: E2=0.0, guided_added=23, TIME_LIMIT

Correctness: attempt1 101 pass/1 fixture failure; fixed by lifting fixed-assignment start, no production change. Attempt2 102 pass in5.38s. Preparation attempt1 rejected raw fingerprint after two shared null Order metadata additions; complete legacy payload bridge passed and current pairs use new fingerprint. No timed job rerun. Process inventory denied. Baseline/candidate differ only owned bound module and added test. Raw solver logging identical in both arms.

12/24 zhou_Set2_100-5-1b_subset20 budget=30 seed=44 baseline: E2=0.0, guided_added=0, TIME_LIMIT

13/24 zhou_Set2_100-5-2_subset20 budget=15 seed=42 balanced: E2=0.0, guided_added=8, TIME_LIMIT

14/24 zhou_Set2_100-5-2_subset20 budget=15 seed=42 baseline: E2=0.0, guided_added=0, TIME_LIMIT

15/24 zhou_Set2_100-5-2_subset20 budget=30 seed=42 baseline: E2=0.0, guided_added=0, TIME_LIMIT

16/24 zhou_Set2_100-5-2_subset20 budget=30 seed=42 balanced: E2=0.0, guided_added=18, PRICING_LIMIT

17/24 zhou_Set2_100-5-2_subset20 budget=15 seed=43 baseline: E2=0.0, guided_added=0, TIME_LIMIT

18/24 zhou_Set2_100-5-2_subset20 budget=15 seed=43 balanced: E2=0.0, guided_added=8, TIME_LIMIT

19/24 zhou_Set2_100-5-2_subset20 budget=30 seed=43 balanced: E2=0.0, guided_added=11, TIME_LIMIT

20/24 zhou_Set2_100-5-2_subset20 budget=30 seed=43 baseline: E2=0.0, guided_added=0, PRICING_LIMIT

21/24 zhou_Set2_100-5-2_subset20 budget=15 seed=44 balanced: E2=0.0, guided_added=13, TIME_LIMIT

22/24 zhou_Set2_100-5-2_subset20 budget=15 seed=44 baseline: E2=0.0, guided_added=0, TIME_LIMIT

23/24 zhou_Set2_100-5-2_subset20 budget=30 seed=44 baseline: E2=0.0, guided_added=0, TIME_LIMIT

24/24 zhou_Set2_100-5-2_subset20 budget=30 seed=44 balanced: E2=0.0, guided_added=10, TIME_LIMIT

Finished 2026-10-05T11:16:04.357708+08:00: all 24 frozen jobs complete, 0 failures and no timing reruns. Independent verification: 236 guided routes, 24 original audits/replays/projections, 290 original certificate arithmetic checks; all 12 E2 pairs remain 0 -> 0, guided added 200. 565 related frozen-source regressions passed in 23.02s, including 102 targeted tests. Gate FAILED, production default remains false, full integer matrix not started per original plan. Nominal 540s / measured 541.100983s; shared load 14.09–40.42, no speed claim. Report and final source ownership check saved; no active worker or automatic continuation.
