# tests/test_batch_psi_zero_mass.py

import os
import pytest
import numpy as np
import pandas as pd
from etsi.watchdog.drift.psi import compute_psi, psi_drift
from etsi.watchdog import DriftCheck


def test_batch_psi_vacated_bin_contributes_positive_score():
    """
    Issue #50: In legacy compute_psi, vacated bins contributed 0.0 to PSI,
    causing real distribution shifts to be under-reported.
    With epsilon smoothing (eps=1e-4), vacated bins must contribute a large
    positive value reflecting the loss of mass.
    """
    # 5 bins spanning 1 to 10
    ref = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
    # Current data only spans upper half (6 to 10). Bins 1-2 and 3-4 are completely vacated.
    cur = np.array([6.0, 7.0, 8.0, 9.0, 10.0])

    # Legacy zero-mass rule (eps=0)
    legacy_res = compute_psi(ref, cur, buckets=5, eps=0.0)
    assert legacy_res.details["psi_per_bin"][0] == 0.0
    assert legacy_res.details["psi_per_bin"][1] == 0.0

    # Modern zero-mass fix (default eps=1e-4)
    fixed_res = compute_psi(ref, cur, buckets=5)
    # Vacated bins now contribute > 1.0 each
    assert fixed_res.details["psi_per_bin"][0] > 1.0
    assert fixed_res.details["psi_per_bin"][1] > 1.0
    # Overall score is significantly higher, properly capturing the shift
    assert fixed_res.score > legacy_res.score
    assert fixed_res.score > 2.0
    assert fixed_res.is_drifted is True


def test_batch_psi_new_mass_in_empty_bin():
    """
    Verify that actual mass appearing in an empty reference bin
    produces a positive drift contribution.
    """
    # Reference only spans 6 to 10
    ref = np.array([6.0, 7.0, 8.0, 9.0, 10.0])
    # Current has mass across 1 to 10
    cur = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])

    legacy_res = compute_psi(ref, cur, buckets=5, eps=0.0)
    assert legacy_res.details["psi_per_bin"][0] == 0.0

    fixed_res = compute_psi(ref, cur, buckets=5)
    assert fixed_res.details["psi_per_bin"][0] > 0.5
    assert fixed_res.score > legacy_res.score


def test_batch_psi_jointly_empty_bin_contributes_zero():
    """
    If neither reference nor current has mass in a bin (both are 0%),
    the contribution must be 0.0 (no spurious drift).
    """
    # Data with a gap in the middle
    ref = np.array([1.0, 1.1, 1.2, 9.8, 9.9, 10.0])
    cur = np.array([1.0, 1.1, 1.3, 9.7, 9.9, 10.0])

    res = compute_psi(ref, cur, buckets=10)
    # Intermediate bins should have 0 expected and 0 actual, contributing 0.0
    for e, a, p in zip(res.details["expected_percents"], res.details["actual_percents"], res.details["psi_per_bin"]):
        if e == 0.0 and a == 0.0:
            assert p == 0.0


def test_batch_psi_configurable_epsilon():
    """
    Verify eps parameter configurability: None/0 restores legacy zero-mass,
    while custom floats scale sensitivity.
    """
    ref = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    cur = np.array([4.0, 5.0])

    res_none = compute_psi(ref, cur, buckets=5, eps=None)
    res_zero = compute_psi(ref, cur, buckets=5, eps=0.0)
    assert res_none.score == res_zero.score

    res_e3 = compute_psi(ref, cur, buckets=5, eps=1e-3)
    res_e5 = compute_psi(ref, cur, buckets=5, eps=1e-5)
    # Smaller epsilon penalizes empty bins more severely
    assert res_e5.score > res_e3.score


def test_drift_check_integration_with_vacated_bins():
    """
    Verify DriftCheck API with algorithm='psi' detects drift on vacated categories.
    """
    ref_df = pd.DataFrame({"score": [10.0, 20.0, 30.0, 40.0, 50.0] * 20})
    # Current data only contains values >= 40.0
    cur_df = pd.DataFrame({"score": [40.0, 50.0] * 30})

    check = DriftCheck(ref_df, algorithm="psi", threshold=0.2)
    results = check.run(cur_df, features=["score"])

    res = results["score"]
    assert res.is_drifted is True
    assert res.score > 1.0


def test_real_dataset_california_housing_vacated_bins():
    """
    Empirical test using the California Housing Census dataset.
    Simulates demographic/income shift where low-income tracts are vacated.
    """
    from sklearn.datasets import fetch_california_housing

    data = fetch_california_housing(as_frame=True).frame
    med_inc = data["MedInc"].dropna().values

    # Baseline: full distribution (includes low, medium, and high income)
    ref = med_inc[:2000]

    # Current: affluent-only subset where low income bins (< 3.0) are completely vacated
    cur = med_inc[med_inc >= 3.5][:1000]

    legacy_res = compute_psi(ref, cur, buckets=10, eps=0.0)
    fixed_res = compute_psi(ref, cur, buckets=10, eps=1e-4)

    # Legacy zero-mass rule under-reported the shift because lowest bins had 0 actual count
    assert legacy_res.details["psi_per_bin"][0] == 0.0

    # Fixed compute_psi properly captures the vacated low-income bins
    assert fixed_res.details["psi_per_bin"][0] > 0.0
    assert fixed_res.score > legacy_res.score
    assert fixed_res.is_drifted is True


def test_real_dataset_nasa_climate_vacated_cold_bins():
    """
    Empirical test using the NASA Global Surface Temperature Anomaly dataset.
    Historical climate baseline (1880-1940) contains many negative temperature anomalies.
    Modern era (>1990) contains exclusively positive anomalies; cold bins are completely vacated.
    """
    import scipy

    nasa_csv_path = os.path.join(os.path.dirname(scipy.__file__), "signal", "tests", "data", "GLB.Ts+dSST.csv")
    if not os.path.exists(nasa_csv_path):
        pytest.skip(f"NASA dataset not found at {nasa_csv_path}")

    df_nasa = pd.read_csv(nasa_csv_path, skiprows=1)
    df_nasa["Year"] = pd.to_numeric(df_nasa["Year"], errors="coerce")
    df_nasa["J-D"] = pd.to_numeric(df_nasa["J-D"], errors="coerce")
    df_clean = df_nasa.dropna(subset=["Year", "J-D"]).copy()

    # Historical era (1880-1940)
    ref = df_clean[df_clean["Year"] <= 1940]["J-D"].values
    # Modern warming era (1990 onwards)
    cur = df_clean[df_clean["Year"] >= 1990]["J-D"].values

    legacy_res = compute_psi(ref, cur, buckets=10, eps=0.0)
    fixed_res = compute_psi(ref, cur, buckets=10)

    # In modern era, negative anomaly bins are vacated
    assert fixed_res.score > legacy_res.score
    assert fixed_res.is_drifted is True
    # The coldest bin (which has negative anomalies in reference) is vacated in modern era
    assert fixed_res.details["psi_per_bin"][0] > 0.0
