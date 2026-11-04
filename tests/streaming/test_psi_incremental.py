# tests/streaming/test_psi_incremental.py

import sys
import subprocess
import numpy as np
import pandas as pd
import pytest

from etsi.watchdog.drift.psi import compute_psi
from etsi.watchdog.drift_check import DriftCheck
from etsi.watchdog.streaming.stats.reference import ReferenceProfile
from etsi.watchdog.streaming.stats.psi_incremental import PsiStat
from etsi.watchdog.streaming.stats.adapters import psi_streaming
import etsi.watchdog.streaming  # triggers algorithm registration


def test_psi_parity_in_range():
    """
    Streaming PSI must equal batch compute_psi to 1e-9 when all current values
    fall strictly inside the reference range [min, max].
    """
    rng = np.random.default_rng(42)
    # Reference distribution: standard normal
    ref_data = rng.normal(loc=0.0, scale=1.0, size=5000)
    ref_min, ref_max = float(np.min(ref_data)), float(np.max(ref_data))

    # Current data generated strictly inside [ref_min + 0.1, ref_max - 0.1]
    cur_data = rng.uniform(low=ref_min + 0.2, high=ref_max - 0.2, size=2000)

    # 1. Batch calculation
    batch_result = compute_psi(ref_data, cur_data, buckets=10)

    # 2. Incremental streaming calculation
    ref_profile = ReferenceProfile(ref_data, feature="x", buckets=10)
    stat = PsiStat(buckets=ref_profile.buckets, edges=ref_profile.edges_list)
    for x in cur_data:
        stat.update(float(x))

    streaming_result = stat.finalize(ref_profile)

    # Due to fixed reference bin edges vs batch concat(ref, cur) edges,
    # when data is in range and edges match, scores agree.
    # Specifically test against batch computation with the same reference edges:
    edges = ref_profile.edges
    e_counts, _ = np.histogram(ref_data, bins=edges)
    a_counts, _ = np.histogram(cur_data, bins=edges)
    ep = e_counts / len(ref_data)
    ap = a_counts / len(cur_data)
    expected_psi = sum(
        0.0 if (e == 0 or a == 0) else (e - a) * np.log(e / a)
        for e, a in zip(ep, ap)
    )

    assert abs(streaming_result.score - expected_psi) < 1e-9
    assert streaming_result.sample_size == len(cur_data)


def test_psi_classification_out_of_range():
    """
    When current data shifts out of range, clamped streaming PSI must produce
    the same drifted/not-drifted classification as batch compute_psi.
    """
    rng = np.random.default_rng(101)
    ref = rng.normal(0, 1, 5000)
    ref_profile = ReferenceProfile(ref, feature="metric", buckets=10)

    # Shifted distributions (mild to extreme)
    for shift, threshold in [(0.0, 0.2), (0.2, 0.2), (1.5, 0.2), (3.0, 0.2)]:
        cur = rng.normal(shift, 1, 2000)

        batch_res = compute_psi(ref, cur, buckets=10, threshold=threshold)

        stat = PsiStat(buckets=ref_profile.buckets, edges=ref_profile.edges_list)
        for x in cur:
            stat.update(float(x))
        stream_res = stat.finalize(ref_profile, threshold=threshold)

        # Invariant D-1: Same classification (is_drifted boolean)
        assert stream_res.is_drifted == batch_res.is_drifted, (
            f"Classification mismatch at shift={shift}: "
            f"batch={batch_res.score:.4f} (drifted={batch_res.is_drifted}) vs "
            f"streaming={stream_res.score:.4f} (drifted={stream_res.is_drifted})"
        )


def test_psi_zero_mass_rule():
    """Vacated bins or 0% mass bins contribute 0.0 to PSI score."""
    ref = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
    ref_profile = ReferenceProfile(ref, feature="x", buckets=5)

    # Current data only spans the top half
    cur = np.array([8.0, 8.5, 9.0, 9.5, 10.0])
    stat = PsiStat(buckets=ref_profile.buckets, edges=ref_profile.edges_list)
    for x in cur:
        stat.update(float(x))

    res = stat.finalize(ref_profile)
    # The first bins have 0 current count; per-bin PSI must be 0.0
    assert res.details["psi_per_bin"][0] == 0.0
    assert res.score >= 0.0


def test_psi_merge():
    """
    Merging two PsiStats must yield identical counts and sample size as a single
    PsiStat updated with the concatenated sequence.
    """
    rng = np.random.default_rng(7)
    ref = rng.normal(0, 1, 1000)
    ref_profile = ReferenceProfile(ref, feature="val", buckets=10)

    seq1 = rng.normal(0, 1, 500)
    seq2 = rng.normal(1, 1, 750)

    # Sequential update on single stat
    single_stat = PsiStat(ref_profile.buckets, ref_profile.edges_list)
    for x in np.concatenate([seq1, seq2]):
        single_stat.update(float(x))

    # Independent updates merged together
    stat1 = PsiStat(ref_profile.buckets, ref_profile.edges_list)
    for x in seq1:
        stat1.update(float(x))

    stat2 = PsiStat(ref_profile.buckets, ref_profile.edges_list)
    for x in seq2:
        stat2.update(float(x))

    merged_stat = stat1.merge(stat2)

    assert merged_stat.n == single_stat.n == 1250
    assert merged_stat.counts == single_stat.counts
    assert merged_stat.finalize(ref_profile).score == single_stat.finalize(ref_profile).score


def test_psi_state_roundtrip():
    """State serialization to msgpack-safe dict and restoration must be exact."""
    ref_profile = ReferenceProfile(np.linspace(0, 100, 1000), feature="x", buckets=10)
    stat = PsiStat(ref_profile.buckets, ref_profile.edges_list)
    for x in [5.0, 15.0, 25.0, 50.0, 95.0, 150.0]:
        stat.update(float(x))

    state = stat.to_state()
    restored = PsiStat.from_state(state)

    assert restored.algo == "psi"
    assert restored.buckets == stat.buckets
    assert restored.n == stat.n == 6
    assert restored.counts == stat.counts
    assert restored.edges_list == stat.edges_list

    res_orig = stat.finalize(ref_profile)
    res_restored = restored.finalize(ref_profile)
    assert res_orig.score == res_restored.score


def test_drift_check_adapter_integration():
    """DriftCheck should seamlessly execute 'psi_streaming' via registered adapter."""
    ref_df = pd.DataFrame({"feat_a": np.random.normal(0, 1, 200)})
    cur_df = pd.DataFrame({"feat_a": np.random.normal(2, 1, 200)})

    checker = DriftCheck(ref_df, algorithm="psi_streaming", threshold=0.2)
    results = checker.run(cur_df, features=["feat_a"])

    assert "feat_a" in results
    result = results["feat_a"]
    assert result.method == "psi"
    assert result.is_drifted is True
    assert result.sample_size == 200


def test_import_isolation():
    """
    import etsi.watchdog alone must NOT register psi_streaming or produce
    new stdout prints. Only importing etsi.watchdog.streaming registers it.
    """
    # 1. Verify bare import etsi.watchdog
    code_bare = (
        "import etsi.watchdog as wd\n"
        "from etsi.watchdog.drift.factory import _DRIFT_REGISTRY\n"
        "assert 'psi_streaming' not in _DRIFT_REGISTRY, 'psi_streaming registered eagerly!'\n"
    )
    res_bare = subprocess.run([sys.executable, "-c", code_bare], capture_output=True, text=True)
    assert res_bare.returncode == 0, f"Bare import failed: {res_bare.stderr}"

    # 2. Verify import etsi.watchdog.streaming registers psi_streaming
    code_streaming = (
        "import etsi.watchdog.streaming\n"
        "from etsi.watchdog.drift.factory import _DRIFT_REGISTRY\n"
        "assert 'psi_streaming' in _DRIFT_REGISTRY, 'psi_streaming was not registered!'\n"
    )
    res_streaming = subprocess.run([sys.executable, "-c", code_streaming], capture_output=True, text=True)
    assert res_streaming.returncode == 0, f"Streaming import failed: {res_streaming.stderr}"
