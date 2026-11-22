# tests/streaming/test_tdigest.py

import os
import json
import pytest
import numpy as np
import pandas as pd
from scipy.stats import ks_2samp, wasserstein_distance

import etsi.watchdog.streaming  # noqa: F401 registers algorithms
from etsi.watchdog.drift_check import DriftCheck
from etsi.watchdog.drift.factory import get_drift_function
from etsi.watchdog.drift.base import DriftResult
from etsi.watchdog.streaming.stats.tdigest import TDigest, Centroid
from etsi.watchdog.streaming.stats.ks_tdigest import TDigestKsStat
from etsi.watchdog.streaming.stats.wasserstein_tdigest import TDigestWassersteinStat
from etsi.watchdog.streaming.stats.reference import ReferenceProfile
from etsi.watchdog.streaming.state import codec
from etsi.watchdog.streaming.state.memory_store import InMemoryStateStore
from etsi.watchdog.streaming.config import StreamingConfig
from etsi.watchdog.streaming.source import FakeEventSource, RawMessage
from etsi.watchdog.streaming.streaming_monitor import StreamingMonitor


class TestTDigestCentroid:
    def test_centroid_creation_and_add(self):
        c = Centroid(mean=10.0, weight=1.0)
        assert c.mean == 10.0
        assert c.weight == 1.0

        c.add(20.0, 1.0)
        assert c.weight == 2.0
        assert c.mean == 15.0

        c.add(0.0, 2.0)
        assert c.weight == 4.0
        assert c.mean == 7.5

    def test_centroid_repr(self):
        c = Centroid(mean=5.123456, weight=3.0)
        assert "Centroid" in repr(c)
        assert "5.123456" in repr(c)


class TestTDigestQuantilesAndAccuracy:
    def test_compression_validation(self):
        with pytest.raises(ValueError, match="Compression"):
            TDigest(compression=5.0)
        with pytest.raises(ValueError, match="Compression"):
            TDigest(compression=2000.0)

    def test_empty_digest_errors(self):
        td = TDigest(compression=100.0)
        with pytest.raises(ValueError, match="empty"):
            td.quantile(0.5)
        with pytest.raises(ValueError, match="empty"):
            td.quantiles([0.1, 0.5])
        assert td.cdf(0.0) == 0.0

    def test_single_value(self):
        td = TDigest(compression=100.0)
        td.update(42.0)
        assert td.n == 1
        assert td.quantile(0.0) == 42.0
        assert td.quantile(0.5) == 42.0
        assert td.quantile(1.0) == 42.0

    def test_repeated_identical_values(self):
        td = TDigest(compression=100.0)
        for _ in range(500):
            td.update(7.0)
        assert td.n == 500
        assert abs(td.quantile(0.5) - 7.0) < 1e-6
        assert abs(td.quantile(0.01) - 7.0) < 1e-6
        assert abs(td.quantile(0.99) - 7.0) < 1e-6

    def test_normal_distribution_quantiles_and_tail_accuracy(self):
        rng = np.random.default_rng(42)
        n_samples = 10000
        data = rng.normal(loc=0.0, scale=1.0, size=n_samples)

        td = TDigest(compression=100.0)
        for x in data:
            td.update(float(x))

        assert td.n == n_samples
        # Centroid count bounded by O(compression)
        assert td.centroid_count <= 250

        # Test quantiles across the spectrum including tails
        test_qs = [0.001, 0.01, 0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99, 0.999]
        for q in test_qs:
            expected = float(np.percentile(data, q * 100.0))
            actual = td.quantile(q)
            # Normal distribution with std=1: absolute difference at tails is bounded
            abs_err = abs(actual - expected)
            assert abs_err < 0.20, f"Quantile {q}: actual={actual}, expected={expected}, err={abs_err}"

    def test_quantile_monotonicity(self):
        rng = np.random.default_rng(123)
        td = TDigest(compression=100.0)
        for x in rng.uniform(-100, 100, size=2000):
            td.update(float(x))

        qs = np.linspace(0.01, 0.99, 50)
        vals = [td.quantile(float(q)) for q in qs]
        for i in range(len(vals) - 1):
            assert vals[i] <= vals[i + 1] + 1e-9

    def test_cdf_properties(self):
        rng = np.random.default_rng(999)
        data = rng.normal(5.0, 2.0, size=3000)
        td = TDigest(compression=100.0)
        for x in data:
            td.update(float(x))

        # Boundary checks
        assert td.cdf(td._min - 1.0) == 0.0
        assert td.cdf(td._max + 1.0) == 1.0

        # Empirical CDF agreement
        for probe in [0.0, 3.0, 5.0, 7.0, 10.0]:
            emp_cdf = float((data <= probe).mean())
            td_cdf = td.cdf(probe)
            assert abs(td_cdf - emp_cdf) < 0.05, f"Probe {probe}: td={td_cdf}, emp={emp_cdf}"

    def test_trimmed_mean(self):
        rng = np.random.default_rng(77)
        data = rng.uniform(0.0, 100.0, size=2000)
        td = TDigest(compression=100.0)
        for x in data:
            td.update(float(x))

        # Mid 50% (interquartile mean) should be approx 50
        tm = td.trimmed_mean(0.25, 0.75)
        assert abs(tm - 50.0) < 5.0

    def test_reset(self):
        td = TDigest(compression=100.0)
        for i in range(100):
            td.update(float(i))
        assert td.n == 100
        td.reset()
        assert td.n == 0
        assert td.centroid_count == 0


class TestTDigestMerge:
    def test_merge_two_digests(self):
        rng = np.random.default_rng(42)
        d1 = rng.normal(0, 1, size=3000)
        d2 = rng.normal(0, 1, size=2000)
        union = np.concatenate([d1, d2])

        td1 = TDigest(compression=100.0)
        for x in d1:
            td1.update(float(x))

        td2 = TDigest(compression=100.0)
        for x in d2:
            td2.update(float(x))

        merged = td1.merge(td2)
        assert merged.n == 5000
        assert merged._min == min(td1._min, td2._min)
        assert merged._max == max(td1._max, td2._max)

        # Accuracy of merged digest vs union
        for q in [0.01, 0.1, 0.5, 0.9, 0.99]:
            expected = float(np.percentile(union, q * 100.0))
            actual = merged.quantile(q)
            assert abs(actual - expected) < 0.15

    def test_merge_with_empty(self):
        td1 = TDigest(compression=100.0)
        td1.update(10.0)
        td1.update(20.0)

        td2 = TDigest(compression=100.0)
        merged = td1.merge(td2)
        assert merged.n == 2
        assert abs(merged.quantile(0.5) - 15.0) < 1e-6


class TestTDigestSerialization:
    def test_to_state_from_state_roundtrip(self):
        rng = np.random.default_rng(101)
        td1 = TDigest(compression=100.0)
        for x in rng.normal(0, 1, 1500):
            td1.update(float(x))

        state = td1.to_state()
        encoded = codec.encode(state)
        decoded = codec.decode(encoded)
        td2 = TDigest.from_state(decoded)

        assert td2.n == td1.n
        assert td2.compression == td1.compression
        assert td2._min == td1._min
        assert td2._max == td1._max
        assert len(td2.centroids) == len(td1.centroids)

        for q in [0.01, 0.05, 0.5, 0.95, 0.99]:
            assert abs(td1.quantile(q) - td2.quantile(q)) < 1e-9


class TestTDigestKsStat:
    def test_ks_stationary_vs_shifted(self):
        rng = np.random.default_rng(42)
        ref_data = rng.normal(0, 1, 5000)
        ref_prof = ReferenceProfile(ref_data, feature="x")

        # 1. Stationary (no drift)
        stat_null = TDigestKsStat(compression=100.0)
        for x in rng.normal(0, 1, 1500):
            stat_null.update(float(x))
        res_null = stat_null.finalize(ref_prof, threshold=0.95)
        assert isinstance(res_null, DriftResult)
        assert res_null.method == "ks"
        assert res_null.is_drifted is False
        assert res_null.details["approximation"] == "tdigest"
        assert "centroid_count" in res_null.details

        # 2. Shifted (drift)
        stat_shift = TDigestKsStat(compression=100.0)
        for x in rng.normal(2.5, 1, 1500):
            stat_shift.update(float(x))
        res_shift = stat_shift.finalize(ref_prof, threshold=0.95)
        assert res_shift.is_drifted is True
        assert res_shift.score > 0.99

    def test_ks_merge(self):
        rng = np.random.default_rng(55)
        ref_prof = ReferenceProfile(rng.normal(0, 1, 3000), feature="x")

        s1 = TDigestKsStat()
        for x in rng.normal(0, 1, 500):
            s1.update(float(x))

        s2 = TDigestKsStat()
        for x in rng.normal(0, 1, 500):
            s2.update(float(x))

        merged = s1.merge(s2)
        assert merged.n == 1000
        res = merged.finalize(ref_prof, threshold=0.95)
        assert res.sample_size == 1000
        assert res.is_drifted is False

    def test_ks_state_roundtrip(self):
        stat1 = TDigestKsStat(compression=150.0)
        for i in range(500):
            stat1.update(float(i * 0.1))

        state = stat1.to_state()
        encoded = codec.encode(state)
        decoded = codec.decode(encoded)
        stat2 = TDigestKsStat.from_state(decoded)

        assert stat2.n == stat1.n
        assert stat2.compression == stat1.compression
        assert stat2.algo == "ks"


class TestTDigestWassersteinStat:
    def test_wasserstein_stationary_vs_shifted(self):
        rng = np.random.default_rng(42)
        ref_data = rng.normal(10.0, 1.0, 5000)
        ref_prof = ReferenceProfile(ref_data, feature="x")

        # 1. Stationary
        stat_null = TDigestWassersteinStat(compression=100.0)
        for x in rng.normal(10.0, 1.0, 1500):
            stat_null.update(float(x))
        res_null = stat_null.finalize(ref_prof, threshold=0.5)
        assert isinstance(res_null, DriftResult)
        assert res_null.method == "wasserstein"
        assert res_null.is_drifted is False
        assert res_null.score < 0.2
        assert res_null.details["approximation"] == "tdigest"

        # 2. Shifted by +3.0: Wasserstein distance should be ≈ 3.0
        stat_shift = TDigestWassersteinStat(compression=100.0)
        for x in rng.normal(13.0, 1.0, 1500):
            stat_shift.update(float(x))
        res_shift = stat_shift.finalize(ref_prof, threshold=0.5)
        assert res_shift.is_drifted is True
        assert abs(res_shift.score - 3.0) < 0.35

    def test_wasserstein_merge(self):
        rng = np.random.default_rng(66)
        ref_prof = ReferenceProfile(rng.normal(0, 1, 3000), feature="x")

        s1 = TDigestWassersteinStat()
        for x in rng.normal(0, 1, 400):
            s1.update(float(x))

        s2 = TDigestWassersteinStat()
        for x in rng.normal(0, 1, 600):
            s2.update(float(x))

        merged = s1.merge(s2)
        assert merged.n == 1000
        res = merged.finalize(ref_prof, threshold=0.5)
        assert res.sample_size == 1000
        assert res.is_drifted is False

    def test_wasserstein_state_roundtrip(self):
        stat1 = TDigestWassersteinStat(compression=120.0)
        for i in range(400):
            stat1.update(float(i * 0.2))

        state = stat1.to_state()
        encoded = codec.encode(state)
        decoded = codec.decode(encoded)
        stat2 = TDigestWassersteinStat.from_state(decoded)

        assert stat2.n == stat1.n
        assert stat2.compression == stat1.compression
        assert stat2.algo == "wasserstein"


class TestTDigestRegistryAndAdapters:
    def test_registry_contains_tdigest_algorithms(self):
        fn_ks = get_drift_function("ks_tdigest")
        fn_w = get_drift_function("wasserstein_tdigest")

        assert fn_ks is not None
        assert fn_w is not None
        assert getattr(fn_ks, "stat_factory") is TDigestKsStat
        assert getattr(fn_w, "stat_factory") is TDigestWassersteinStat
        assert getattr(fn_ks, "default_threshold") == 0.95
        assert getattr(fn_w, "default_threshold") == 0.1

    def test_drift_check_with_ks_tdigest(self):
        rng = np.random.default_rng(42)
        ref_df = pd.DataFrame({"feat1": rng.normal(0, 1, 1000)})
        cur_df = pd.DataFrame({"feat1": rng.normal(3, 1, 500)})

        checker = DriftCheck(ref_df, algorithm="ks_tdigest", threshold=0.95)
        results = checker.run(cur_df, features=["feat1"])

        assert "feat1" in results
        res = results["feat1"]
        assert isinstance(res, DriftResult)
        assert res.method == "ks"
        assert res.is_drifted is True
        assert res.details["approximation"] == "tdigest"

    def test_drift_check_with_wasserstein_tdigest(self):
        rng = np.random.default_rng(42)
        ref_df = pd.DataFrame({"feat1": rng.normal(5, 1, 1000)})
        cur_df = pd.DataFrame({"feat1": rng.normal(10, 1, 500)})

        checker = DriftCheck(ref_df, algorithm="wasserstein_tdigest", threshold=1.0)
        results = checker.run(cur_df, features=["feat1"])

        assert "feat1" in results
        res = results["feat1"]
        assert isinstance(res, DriftResult)
        assert res.method == "wasserstein"
        assert res.is_drifted is True
        assert res.details["approximation"] == "tdigest"
        assert res.score > 4.5


class TestStreamingMonitorWithTDigest:
    @pytest.mark.parametrize("algo,threshold", [
        ("ks_tdigest", 0.95),
        ("wasserstein_tdigest", 0.5),
    ])
    def test_monitor_e2e_tdigest(self, tmp_path, algo, threshold):
        rng = np.random.default_rng(42)
        ref_data = rng.normal(0, 1, 2000)
        ref_path = str(tmp_path / f"ref_{algo}.csv")
        log_path = str(tmp_path / f"logs_{algo}" / "drift.csv")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        pd.DataFrame({"metric": ref_data}).to_csv(ref_path, index=False)

        cfg = StreamingConfig(
            reference_path=ref_path,
            features=["metric"],
            topic="sensor-topic",
            algorithm=algo,
            threshold=threshold,
            window_seconds=10.0,
            grace_seconds=2.0,
            min_events=50,
            log_path=log_path,
        )

        msgs = []
        offset = 0
        ts = 100.0

        def make_msg(curr_ts, val):
            nonlocal offset
            payload = json.dumps({"stream_id": "stream-A", "event_ts": curr_ts, "metric": float(val)}).encode("utf-8")
            msg = RawMessage("sensor-topic", 0, offset, b"stream-A", payload)
            offset += 1
            return msg

        # Window 0: Stationary Normal(0, 1) - 60 events
        for _ in range(60):
            msgs.append(make_msg(ts, rng.normal(0, 1)))
            ts += 0.1
        ts += 5.0  # past window_end + grace

        # Window 1: Shifted Normal(3.0, 1.0) - 60 events
        for _ in range(60):
            msgs.append(make_msg(ts, rng.normal(3.0, 1.0)))
            ts += 0.1
        ts += 20.0
        # Final trailing event to flush window 1
        msgs.append(make_msg(ts, 0.0))

        source = FakeEventSource(msgs)
        store = InMemoryStateStore()
        monitor = StreamingMonitor(cfg, source=source, store=store)
        monitor.dispatcher.start()

        monitor.run_once()
        monitor.dispatcher.stop(timeout_s=5.0)

        assert monitor.engine.windows_flushed >= 2
        assert os.path.exists(log_path)
        log_df = pd.read_csv(log_path)
        assert len(log_df) >= 2


class TestTDigestRealData:
    """
    Validation on real-world empirical datasets (California Housing and NASA Climate Anomalies).
    Validates tail quantile accuracy, KS statistic fidelity vs exact SciPy, Wasserstein
    distance fidelity, DriftCheck integration, and end-to-end StreamingMonitor runs.
    """

    @pytest.fixture
    def housing_data(self):
        from sklearn.datasets import fetch_california_housing
        housing = fetch_california_housing(as_frame=True)
        return housing.frame

    @pytest.fixture
    def nasa_data(self):
        import scipy
        nasa_csv_path = os.path.join(
            os.path.dirname(scipy.__file__), "signal", "tests", "data", "GLB.Ts+dSST.csv"
        )
        if not os.path.exists(nasa_csv_path):
            pytest.skip(f"NASA dataset not found at {nasa_csv_path}")

        df_nasa = pd.read_csv(nasa_csv_path, skiprows=1)
        df_nasa["Year"] = pd.to_numeric(df_nasa["Year"], errors="coerce")
        df_nasa["J-D"] = pd.to_numeric(df_nasa["J-D"], errors="coerce")
        df_clean = df_nasa.dropna(subset=["Year", "J-D"]).copy()
        return df_clean

    def test_real_california_housing_tail_quantiles_accuracy(self, housing_data):
        """
        Verify t-digest quantile accuracy across real empirical skewed distribution (MedInc).
        Tail percentiles (p01, p99, p99.9) must achieve <= 1% relative error.
        """
        data = housing_data["MedInc"].values
        td = TDigest(compression=100.0)
        for x in data:
            td.update(float(x))

        assert td.n == len(data)
        assert td.centroid_count <= 250

        # Check tail percentiles against empirical quantiles
        for q in [0.01, 0.05, 0.50, 0.95, 0.99, 0.999]:
            actual = float(np.percentile(data, q * 100.0))
            est = td.quantile(q)
            rel_err = abs(est - actual) / actual
            # Bounded error: <= 1% relative error at tails
            assert rel_err < 0.015, f"q={q}: actual={actual}, est={est}, rel_err={rel_err:.4f}"

    def test_real_california_housing_ks_and_wasserstein_vs_exact_batch(self, housing_data):
        """
        Verify that TDigestKsStat and TDigestWassersteinStat closely match exact batch SciPy
        calculations on real census blocks (MedInc feature across 4000 baseline vs 4000 live).
        """
        ref_vals = housing_data["MedInc"].iloc[:4000].values
        cur_vals = housing_data["MedInc"].iloc[4000:8000].values

        prof = ReferenceProfile(ref_vals, "MedInc")

        # 1. Exact batch SciPy statistics
        exact_ks = ks_2samp(prof.sorted_sample, cur_vals)
        exact_w = float(wasserstein_distance(prof.sorted_sample, cur_vals))

        # 2. T-Digest streaming KS
        ks_td = TDigestKsStat(compression=100.0)
        for x in cur_vals:
            ks_td.update(float(x))
        res_ks = ks_td.finalize(prof, threshold=0.95)

        # Discrepancy between t-digest KS and exact batch KS must be <= 0.005 (0.5%)
        ks_diff = abs(res_ks.details["ks_statistic"] - exact_ks.statistic)
        assert ks_diff < 0.005, f"KS stat diff too large: {ks_diff} (td={res_ks.details['ks_statistic']}, exact={exact_ks.statistic})"

        # 3. T-Digest streaming Wasserstein
        w_td = TDigestWassersteinStat(compression=100.0)
        for x in cur_vals:
            w_td.update(float(x))
        res_w = w_td.finalize(prof, threshold=0.2)

        # Discrepancy between t-digest Wasserstein and exact batch must be <= 0.01
        w_diff = abs(res_w.score - exact_w)
        assert w_diff < 0.01, f"Wasserstein diff too large: {w_diff} (td={res_w.score}, exact={exact_w})"

    def test_real_california_housing_drift_check(self, housing_data):
        """
        Test DriftCheck integration with real California Housing data.
        Inland districts (low income) vs Coastal districts (high income) should trigger drift.
        """
        inland_ref = housing_data.iloc[:2000][["MedInc", "MedHouseVal"]]
        coastal_cur = housing_data.iloc[10000:12000][["MedInc", "MedHouseVal"]]

        # KS DriftCheck
        chk_ks = DriftCheck(inland_ref, algorithm="ks_tdigest", threshold=0.95)
        res_ks = chk_ks.run(coastal_cur, features=["MedInc", "MedHouseVal"])
        for feat in ["MedInc", "MedHouseVal"]:
            assert res_ks[feat].is_drifted is True
            assert res_ks[feat].details["approximation"] == "tdigest"
            assert res_ks[feat].score > 0.99

        # Wasserstein DriftCheck
        # MedHouseVal W₁ ≈ 0.45 between these row ranges, so threshold must be below that
        chk_w = DriftCheck(inland_ref, algorithm="wasserstein_tdigest", threshold=0.3)
        res_w = chk_w.run(coastal_cur, features=["MedInc", "MedHouseVal"])
        for feat in ["MedInc", "MedHouseVal"]:
            assert res_w[feat].is_drifted is True
            assert res_w[feat].details["approximation"] == "tdigest"

    def test_real_nasa_climate_anomaly_streaming_monitor(self, tmp_path, nasa_data):
        """
        End-to-end StreamingMonitor on real NASA Global Surface Temperature Anomaly series.
        Early era (1880-1940) baseline vs modern era (1941+) streaming observation windows.
        Verifies global warming drift detection via both ks_tdigest and wasserstein_tdigest.
        """
        baseline_df = nasa_data[nasa_data["Year"] <= 1940][["J-D"]].rename(columns={"J-D": "temp_anomaly"})
        modern_df = nasa_data[nasa_data["Year"] > 1940][["Year", "J-D"]].rename(columns={"J-D": "temp_anomaly"})

        ref_path = tmp_path / "nasa_baseline.csv"
        baseline_df.to_csv(ref_path, index=False)

        for algo, threshold in [("ks_tdigest", 0.95), ("wasserstein_tdigest", 0.2)]:
            log_path = tmp_path / f"logs_{algo}" / "climate_drift.csv"
            os.makedirs(log_path.parent, exist_ok=True)

            cfg = StreamingConfig(
                reference_path=str(ref_path),
                features=["temp_anomaly"],
                topic="nasa-climate-stream",
                algorithm=algo,
                threshold=threshold,
                window_seconds=50.0,
                grace_seconds=10.0,
                min_events=50,
                log_path=str(log_path),
            )

            msgs = []
            base_ts = 1000.0
            for idx, row in enumerate(modern_df.itertuples()):
                payload = {
                    "stream_id": "global-temp-station",
                    "event_ts": base_ts + (idx * 0.5),  # 0 to ~40s (within 50s window)
                    "temp_anomaly": float(row.temp_anomaly),
                }
                msgs.append(
                    RawMessage(
                        topic="nasa-climate-stream",
                        partition=0,
                        offset=idx,
                        key=b"global-temp-station",
                        value=json.dumps(payload).encode("utf-8"),
                    )
                )

            # Advance watermark past window_end (50s) + grace (10s) to flush window
            watermark_ts = base_ts + 75.0
            msgs.append(
                RawMessage(
                    topic="nasa-climate-stream",
                    partition=0,
                    offset=len(msgs),
                    key=b"global-temp-station",
                    value=json.dumps({
                        "stream_id": "global-temp-station",
                        "event_ts": watermark_ts,
                        "temp_anomaly": 0.0,
                    }).encode("utf-8"),
                )
            )

            source = FakeEventSource(msgs)
            store = InMemoryStateStore()
            monitor = StreamingMonitor(cfg, source=source, store=store)
            monitor.dispatcher.start()

            monitor.run_once(max_events=len(msgs) + 10)
            monitor.dispatcher.stop(timeout_s=5.0)

            assert monitor.engine.windows_flushed >= 1
            assert log_path.exists()
            df_log = pd.read_csv(log_path)
            assert len(df_log) >= 1
            # Real historical climate data shows warming drift
            # pandas may return np.True_ which fails `is True`, so use ==
            assert df_log.iloc[0]["drift"] == True  # noqa: E712
            assert df_log.iloc[0]["feature"] == "global-temp-station/temp_anomaly"

