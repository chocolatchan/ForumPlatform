# Task 023: Baseline Run Matrix

This document records the exact individual benchmark executions comprising the Task 023 Reproducible Baseline (`run_id: baseline_20261021_122538`).

## 1. Execution Matrix

| Scenario Name | Run # | Concurrency | Requests | Duration (s) | Achieved RPS | p50 (ms) | p90 (ms) | p95 (ms) | p99 (ms) | Min (ms) | Max (ms) | Success / Fail |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **WL1: Categories Read** | 1 | 10 | 200 | 0.33 | 599.9 | 14.6 | 23.3 | 28.8 | 34.7 | 4.8 | 45.1 | 200 / 0 |
| **WL1: Categories Read** | 2 | 10 | 200 | 0.32 | 620.8 | 14.6 | 20.8 | 24.1 | 29.4 | 4.6 | 32.5 | 200 / 0 |
| **WL1: Categories Read** | 3 | 10 | 200 | 0.31 | 645.3 | 14.3 | 18.9 | 21.0 | 23.5 | 5.3 | 24.9 | 200 / 0 |
| **WL2: Threads Read** | 1 | 10 | 200 | 0.39 | 515.4 | 17.8 | 26.6 | 31.4 | 46.6 | 6.7 | 51.3 | 200 / 0 |
| **WL2: Threads Read** | 2 | 10 | 200 | 0.40 | 500.2 | 18.1 | 27.5 | 31.7 | 42.8 | 6.3 | 48.9 | 200 / 0 |
| **WL2: Threads Read** | 3 | 10 | 200 | 0.39 | 509.0 | 17.5 | 27.8 | 33.8 | 48.5 | 6.4 | 55.4 | 200 / 0 |
| **WL3: Keyword Search** | 1 | 10 | 200 | 0.36 | 557.9 | 16.5 | 23.7 | 27.0 | 34.0 | 5.5 | 37.1 | 200 / 0 |
| **WL3: Keyword Search** | 2 | 10 | 200 | 0.36 | 557.4 | 16.7 | 22.8 | 25.7 | 34.2 | 5.7 | 39.8 | 200 / 0 |
| **WL3: Keyword Search** | 3 | 10 | 200 | 0.34 | 582.5 | 15.7 | 21.6 | 25.0 | 31.3 | 5.6 | 36.2 | 200 / 0 |
| **WL4: Auth Profile** | 1 | 10 | 200 | 0.43 | 462.7 | 19.2 | 28.5 | 33.0 | 38.9 | 6.8 | 41.5 | 200 / 0 |
| **WL4: Auth Profile** | 2 | 10 | 200 | 0.43 | 467.0 | 20.1 | 26.5 | 30.5 | 36.5 | 6.9 | 43.1 | 200 / 0 |
| **WL4: Auth Profile** | 3 | 10 | 200 | 0.42 | 474.8 | 19.7 | 24.6 | 27.8 | 35.2 | 6.7 | 39.9 | 200 / 0 |
| **WL5: Thread Create** | 1 | 5 | 50 | 0.25 | 196.9 | 22.3 | 32.1 | 38.5 | 39.2 | 11.2 | 41.0 | 50 / 0 |
| **WL5: Thread Create** | 2 | 5 | 50 | 0.23 | 220.1 | 21.0 | 26.8 | 29.2 | 31.6 | 10.9 | 34.2 | 50 / 0 |
| **WL5: Thread Create** | 3 | 5 | 50 | 0.24 | 207.6 | 22.5 | 29.1 | 31.3 | 40.3 | 11.5 | 42.1 | 50 / 0 |
| **WL6: Mixed Journey** | 1 | 15 | 200 | 0.49 | 405.6 | 34.0 | 51.5 | 58.6 | 73.5 | 6.2 | 81.2 | 200 / 0 |
| **WL6: Mixed Journey** | 2 | 15 | 200 | 0.49 | 407.4 | 32.8 | 49.6 | 56.5 | 71.2 | 5.9 | 78.4 | 200 / 0 |
| **WL6: Mixed Journey** | 3 | 15 | 200 | 0.49 | 406.9 | 34.0 | 48.9 | 55.9 | 79.8 | 6.1 | 84.7 | 200 / 0 |

---

## 2. Statistical Consistency & Coefficient of Variation

Across all 3 runs for each scenario:
- **WL1 Throughput Std Dev:** 22.8 RPS (CV: 3.6%)
- **WL2 Throughput Std Dev:** 7.6 RPS (CV: 1.5%)
- **WL3 Throughput Std Dev:** 14.4 RPS (CV: 2.5%)
- **WL4 Throughput Std Dev:** 6.1 RPS (CV: 1.3%)
- **WL5 Throughput Std Dev:** 11.7 RPS (CV: 5.6%)
- **WL6 Throughput Std Dev:** 0.9 RPS (CV: 0.2%)

The very low coefficient of variation (<6% across all workloads) demonstrates exceptional stability and test reproducibility in the local Docker environment.
