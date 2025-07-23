# 🧭 Project Overview
  etsi-watchdog is a lightweight Python library for detecting data drift in tabular datasets.It helps identify when the distribution of features in a dataset changes over time or between environments — a key issue in ensuring machine learning models remain accurate and reliable.
  Whether you’re running batch pipelines, monitoring production models, or comparing model versions, etsi-watchdog makes drift detection simple and modular.

### This overview helps:

  - New contributors understand the project's structure and purpose.
  - Users unfamiliar with drift detection get a conceptual grounding.
  - Pipeline integrators know how to embed watchdog into their systems.

## What etsi-watchdog Does :-

At a high level, etsi-watchdog does the following:

- Detects feature-level drift between two datasets using PSI and other methods.
- Supports scheduled or rolling monitoring of time-series or batch data.
- Allows comparison of drift metrics across different model or data versions.


## ⚙️ Internal Workflow
### 🔹 DriftCheck – Static Drift Detection
Compares a new dataset against a reference to detect drift for selected features.

Workflow:
- css
- Copy
- Edit
- reference_df + current_df → DriftCheck.run() → {feature: DriftResult}
  
Used when:
- Validating a new dataset before model training
- Checking incoming production data against a training set

## 🔹 Monitor – Rolling Window Detection
Monitors drift over time in a time-indexed DataFrame using a sliding window.

Workflow:
time-indexed_df → Monitor.watch_rolling(window, freq) → DriftCheck → Logs or Alerts

## 🔹 DriftComparator – Version Comparison
Compares drift scores across two separate detection runs.

Used when:
- Comparing model version A vs. B
- Auditing feature stability across retraining cycles

## Usage
- Detects data drift early to prevent model performance degradation.
- Helps ensure data consistency across environments (dev, staging, prod).
- Compares feature stability across different model/data versions.
- Works in batch pipelines, streaming workflows, and CI/CD systems.
- Enables scheduled monitoring with rolling windows.
- Supports JSON and CSV logging for reporting and auditability.
- Lightweight and easy to integrate into existing ML pipelines.
- Suitable for both experimentation and production-grade monitoring


