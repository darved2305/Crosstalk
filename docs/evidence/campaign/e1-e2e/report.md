# E1 report: `e1-e2e`

> **DEVELOPMENT RESULT, NOT REPORTABLE.** At least one session is not `VALID` or was collected from a dirty tree. These numbers exercise the pipeline; they are not evidence.

Feature schema v1 · bootstrap B = 200 (family clusters, shared by every arm and setting) · outer folds = 5 (family-disjoint, one map for all arms) · seeds = 1.

## Sessions

| Session | Status | Dirty tree | Reasons |
|---|---|---|---|
| `01M4QMMZMNV5XGH7YG5J535BAX` | PARTIAL | True | Reconstruction and independent validation pending |

## Data

| Regime | Arm | Traces | Families |
|---|---|---|---|
| NAT | a0 | 48 | 48 |
| NAT | a1 | 48 | 48 |
| NAT | a2 | 48 | 48 |
| NAT | a3 | 48 | 48 |
| NAT | a4 | 48 | 48 |

Excluded traces: none.

## Primary comparisons (NAT; lr, r1)

Δ is balanced accuracy minus D0 static (0.104); chance = 0.167. p is the two-sided paired bootstrap p-value; Holm-Bonferroni at alpha = 0.05 across the 8 pre-declared tests. rho = (m - chance) / (m_D0 - chance) is the retained leakage (n/a when D0 is not above chance).

![Balanced accuracy per arm](figures/e1_balanced_accuracy.png)

| Arm | Attacker | Balanced accuracy | Δ vs D0 [95% CI] | p | p (Holm) | Reject H0 | rho [95% CI] |
|---|---|---|---|---|---|---|---|
| a1 | static | 0.146 | 0.042 [-0.083, 0.146] | 0.560 | 1.000 | no | n/a |
| a1 | adaptive | 0.188 | 0.083 [-0.021, 0.188] | 0.150 | 1.000 | no | n/a |
| a2 | static | 0.188 | 0.083 [-0.021, 0.188] | 0.210 | 1.000 | no | n/a |
| a2 | adaptive | 0.125 | 0.021 [-0.104, 0.146] | 0.860 | 1.000 | no | n/a |
| a3 | static | 0.167 | 0.062 [-0.021, 0.125] | 0.190 | 1.000 | no | n/a |
| a3 | adaptive | 0.042 | -0.062 [-0.167, 0.021] | 0.280 | 1.000 | no | n/a |
| a4 | static | 0.146 | 0.042 [-0.083, 0.146] | 0.560 | 1.000 | no | n/a |
| a4 | adaptive | 0.188 | 0.083 [-0.021, 0.188] | 0.190 | 1.000 | no | n/a |

Static-to-adaptive recovery (rho adaptive - rho static):

| Arm | rho static | rho adaptive | Recovery [95% CI] |
|---|---|---|---|
| a1 | n/a | n/a | n/a |
| a2 | n/a | n/a | n/a |
| a3 | n/a | n/a | n/a |
| a4 | n/a | n/a | n/a |

## All attackers

| Regime | Arm | Attacker | Representation | Model | Balanced accuracy | Macro-F1 |
|---|---|---|---|---|---|---|
| NAT | a0 | static | r1 | chance | 0.146 [0.062, 0.230] | 0.127 [0.047, 0.215] |
| NAT | a0 | static | r1 | lr | 0.104 [0.042, 0.188] | 0.096 [0.033, 0.160] |
| NAT | a1 | static | r1 | chance | 0.125 [0.042, 0.229] | 0.123 [0.042, 0.199] |
| NAT | a1 | static | r1 | lr | 0.146 [0.062, 0.209] | 0.073 [0.030, 0.108] |
| NAT | a1 | adaptive | r1 | chance | 0.125 [0.042, 0.229] | 0.123 [0.042, 0.199] |
| NAT | a1 | adaptive | r1 | lr | 0.188 [0.104, 0.271] | 0.166 [0.090, 0.242] |
| NAT | a2 | static | r1 | chance | 0.125 [0.062, 0.208] | 0.114 [0.051, 0.191] |
| NAT | a2 | static | r1 | lr | 0.188 [0.104, 0.292] | 0.164 [0.076, 0.256] |
| NAT | a2 | adaptive | r1 | chance | 0.125 [0.062, 0.208] | 0.114 [0.051, 0.191] |
| NAT | a2 | adaptive | r1 | lr | 0.125 [0.042, 0.209] | 0.121 [0.045, 0.199] |
| NAT | a3 | static | r1 | chance | 0.146 [0.062, 0.250] | 0.129 [0.056, 0.224] |
| NAT | a3 | static | r1 | lr | 0.167 [0.167, 0.167] | 0.048 [0.048, 0.048] |
| NAT | a3 | adaptive | r1 | chance | 0.146 [0.062, 0.250] | 0.129 [0.056, 0.224] |
| NAT | a3 | adaptive | r1 | lr | 0.042 [0.000, 0.104] | 0.046 [0.000, 0.109] |
| NAT | a4 | static | r1 | chance | 0.104 [0.041, 0.188] | 0.093 [0.025, 0.158] |
| NAT | a4 | static | r1 | lr | 0.146 [0.062, 0.209] | 0.073 [0.029, 0.106] |
| NAT | a4 | adaptive | r1 | chance | 0.104 [0.041, 0.188] | 0.093 [0.025, 0.158] |
| NAT | a4 | adaptive | r1 | lr | 0.188 [0.083, 0.271] | 0.184 [0.082, 0.268] |

## Cost (client view, same runs)

TTFT and inter-token latency are measured at the client; bandwidth is TCP payload in both directions per request; overhead and ΔTTFT are paired against D0 with family cluster-bootstrap intervals.

| Regime | Arm | n | TTFT p50/p95/p99 ms | ITL p50/p95/p99 ms | Bytes / request | Bandwidth overhead % | Δ TTFT p50 ms | Pad bytes | Dummy records | Timer lateness p99 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| NAT | a0 | 48 | 134.9/326.0/939.2 | 17.8/55.0/110.3 | 10324 | - | - | 0 | 0.0 | - |
| NAT | a1 | 48 | 151.4/319.6/922.4 | 16.6/50.6/94.5 | 12031 | 16.5 [16.3, 16.8] | 16.7 [-10.7, 36.2] | 1708 | 0.0 | - |
| NAT | a2 | 48 | 156.1/324.6/484.7 | 27.1/71.7/124.4 | 8658 | -16.1 [-17.6, -14.3] | 21.9 [-3.7, 40.5] | 0 | 0.0 | 58.39 |
| NAT | a3 | 48 | 143.7/286.0/303.0 | 18.3/63.6/132.4 | 72337 | 600.7 [507.4, 724.5] | 5.4 [-13.4, 30.8] | 59894 | 99.5 | 247.34 |
| NAT | a4 | 48 | 139.6/318.8/398.4 | 25.5/75.3/161.1 | 10028 | -2.9 [-5.1, -0.6] | 6.7 [-23.4, 20.5] | 1483 | 0.0 | 82.49 |

## Reading this report

The adaptive attacker is the headline (Kerckhoffs: the observer knows the deployed defense). A defense that only defeats the static attacker has not been shown to protect anything. Costs come from the same runs as the leakage numbers.
