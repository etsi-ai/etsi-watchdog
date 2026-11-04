# tests/streaming/test_ks_wasserstein_streaming.py

import os
import math
import numpy as np
import pandas as pd
import pytest
from scipy.stats import ks_2samp, wasserstein_distance, chisquare

import etsi.watchdog.streaming  # noqa: F401 registers algorithms
from etsi.watchdog.drift_check import DriftCheck
from etsi.watchdog.drift.factory import get_drift_function
from etsi.watchdog.drift.base import DriftResult
from etsi.watchdog.streaming.stats.rng import SplitMix64
from etsi.watchdog.streaming.stats.reservoir import ReservoirSample
from etsi.watchdog.streaming.stats.ks_streaming import StreamingKsStat
from etsi.watchdog.streaming.stats.wasserstein_streaming import StreamingWassersteinStat
from etsi.watchdog.streaming.stats.reference import ReferenceProfile
from etsi.watchdog.streaming.state import codec
from etsi.watchdog.streaming.state.memory_store import InMemoryStateStore
from etsi.watchdog.streaming.config import StreamingConfig
from etsi.watchdog.streaming.source import FakeEventSource, RawMessage
from etsi.watchdog.streaming.streaming_monitor import StreamingMonitor


class TestSplitMix64:
    def test_determinism_and_reproducibility(self):
        rng1 = SplitMix64(42)
        rng2 = SplitMix64(42)
        seq1 = [rng1.next() for _ in range(100)]
        seq2 = [rng2.next() for _ in range(100)]
        assert seq1 == seq2
        assert len(set(seq1)) == 100

    def test_next_int_bounds(self):
        rng = SplitMix64(12345)
        for bound in [2, 10, 100, 2000]:
            vals = [rng.next_int(bound) for _ in range(500)]
            assert min(vals) >= 0
            assert max(vals) < bound

    def test_blake2b_component_seeding(self):
        seed1 = SplitMix64.seed(0, "sensor-1", "temperature", 1600000000.0)
        seed2 = SplitMix64.seed(0, "sensor-1", "temperature", 1600000000.0)
        seed3 = SplitMix64.seed(0, "sensor-2", "temperature", 1600000000.0)
        assert seed1 == seed2
        assert seed1 != seed3
        assert 0 <= seed1 < (1 << 64)


class TestReservoirSampling:
    def test_fill_and_capacity_bound(self):
        sample = ReservoirSample(k=100, seed=42)
        assert sample.k == 100
        assert len(sample.values) == 0

        # Fill below capacity
        for i in range(50):
            sample.update(float(i))
        assert len(sample.values) == 50
        assert sample.n == 50
        assert sample.values == [float(i) for i in range(50)]

        # Exceed capacity
        for i in range(50, 1000):
            sample.update(float(i))
        assert len(sample.values) == 100
        assert sample.n == 1000

    def test_algorithm_r_uniform_selection(self):
        # Over many trials, each element of stream N has equal probability k/N of being in reservoir
        N = 200
        k = 40
        trials = 2000
        counts = [0] * N

        for t in range(trials):
            sample = ReservoirSample(k=k, seed=t)
            for i in range(N):
                sample.update(float(i))
            for val in sample.values:
                counts[int(val)] += 1

        expected = trials * (k / N)
        # Chi-square goodness-of-fit test against uniform expected frequencies
        stat, pval = chisquare(counts, f_exp=[expected] * N)
        assert pval > 0.01  # Uniformity not rejected

    def test_state_roundtrip_and_identical_continuation(self):
        res1 = ReservoirSample(k=100, seed=99)
        for i in range(250):
            res1.update(float(i * 1.5))

        # Serialize state through msgpack codec
        state = res1.to_state()
        encoded = codec.encode(state)
        decoded = codec.decode(encoded)
        res2 = ReservoirSample.from_state(decoded)

        assert res2.k == res1.k
        assert res2.n == res1.n
        assert res2.values == res1.values
        assert res2.rng.state == res1.rng.state

        # Feed 100 more events into both; continuation must be bit-for-bit identical
        for i in range(250, 350):
            res1.update(float(i * 1.5))
            res2.update(float(i * 1.5))

        assert res1.values == res2.values
        assert res1.rng.state == res2.rng.state

    def test_hypergeometric_merge(self):
        # Reservoir A seen 800 items from uniform [0, 1)
        res_a = ReservoirSample(k=200, seed=1)
        rng_a = np.random.default_rng(10)
        for x in rng_a.uniform(0.0, 1.0, size=800):
            res_a.update(float(x))

        # Reservoir B seen 200 items from uniform [0, 1)
        res_b = ReservoirSample(k=200, seed=2)
        rng_b = np.random.default_rng(20)
        for x in rng_b.uniform(0.0, 1.0, size=200):
            res_b.update(float(x))

        merged = res_a.merge(res_b, rng_seed=42)
        assert merged.n == 1000
        assert len(merged.values) == 200

        # KS test against true uniform distribution
        ks_res = ks_2samp(merged.values, rng_a.uniform(0.0, 1.0, size=5000))
        assert ks_res.pvalue > 0.05


class TestStreamingKsStat:
    def test_dkw_bound_vs_full_window_batch(self):
        rng = np.random.default_rng(123)
        ref_data = rng.normal(0, 1, 10000)
        cur_data = rng.normal(0.4, 1, 5000)

        ref_prof = ReferenceProfile(ref_data, feature="f1")
        stat = StreamingKsStat(capacity=2000, seed=42)

        for x in cur_data:
            stat.update(float(x))

        result = stat.finalize(ref_prof, threshold=0.95)
        assert isinstance(result, DriftResult)
        assert result.method == "ks"
        assert result.details["reservoir_size"] == 2000
        assert result.details["n_seen"] == 5000

        # Exact full batch statistic
        batch_stat, batch_p = ks_2samp(ref_prof.sorted_sample, cur_data)

        # DKW bound at 95% confidence for k=2000: eps ~= 0.0304 + ref eps ~= 0.015
        diff = abs(result.details["ks_statistic"] - batch_stat)
        dkw_tolerance = result.details["dkw_epsilon_95"] + 0.02
        assert diff <= dkw_tolerance

    def test_ks_stationary_vs_shifted_drift_detection(self):
        rng = np.random.default_rng(999)
        ref_data = rng.normal(0, 1, 5000)
        ref_prof = ReferenceProfile(ref_data, feature="x")

        # 1. Stationary stream (no drift)
        stat_null = StreamingKsStat(capacity=2000, seed=1)
        for x in rng.normal(0, 1, 1500):
            stat_null.update(float(x))
        res_null = stat_null.finalize(ref_prof, threshold=0.95)
        # Score is 1 - pvalue; with threshold 0.95, null distribution should not flag drift
        assert res_null.is_drifted is False

        # 2. Shifted stream (significant drift)
        stat_shift = StreamingKsStat(capacity=2000, seed=2)
        for x in rng.normal(2.5, 1, 1500):
            stat_shift.update(float(x))
        res_shift = stat_shift.finalize(ref_prof, threshold=0.95)
        assert res_shift.is_drifted is True
        assert res_shift.details["p_value"] < 1e-10
        assert res_shift.score > 0.999

    def test_ks_state_roundtrip(self):
        stat1 = StreamingKsStat(capacity=500, seed=77)
        for i in range(1200):
            stat1.update(float(i % 17))

        state = stat1.to_state()
        encoded = codec.encode(state)
        decoded = codec.decode(encoded)
        stat2 = StreamingKsStat.from_state(decoded)

        assert stat2.capacity == stat1.capacity
        assert stat2.n == stat1.n
        assert stat2.reservoir.values == stat1.reservoir.values


class TestStreamingWassersteinStat:
    def test_wasserstein_distance_properties(self):
        rng = np.random.default_rng(333)
        ref_data = rng.normal(10.0, 1.0, 5000)
        ref_prof = ReferenceProfile(ref_data, feature="metric")

        # 1. Stationary
        stat_stat = StreamingWassersteinStat(capacity=2000, seed=1)
        for x in rng.normal(10.0, 1.0, 2000):
            stat_stat.update(float(x))
        res_stat = stat_stat.finalize(ref_prof, threshold=0.5)
        assert res_stat.is_drifted is False
        assert res_stat.score < 0.2  # distance near 0

        # 2. Shifted mean by +3.0
        stat_shift = StreamingWassersteinStat(capacity=2000, seed=2)
        for x in rng.normal(13.0, 1.0, 2000):
            stat_shift.update(float(x))
        res_shift = stat_shift.finalize(ref_prof, threshold=0.5)
        assert res_shift.is_drifted is True
        # For a shift of +3.0 on normal distribution, Wasserstein-1 distance is approximately 3.0
        assert abs(res_shift.score - 3.0) < 0.2

    def test_wasserstein_state_roundtrip(self):
        stat1 = StreamingWassersteinStat(capacity=500, seed=88)
        for i in range(600):
            stat1.update(float(i * 0.1))

        state = stat1.to_state()
        encoded = codec.encode(state)
        decoded = codec.decode(encoded)
        stat2 = StreamingWassersteinStat.from_state(decoded)

        assert stat2.capacity == stat1.capacity
        assert stat2.n == stat1.n
        assert stat2.reservoir.values == stat1.reservoir.values


class TestRegistryAndDriftCheckIntegration:
    def test_registry_contains_streaming_algorithms(self):
        fn_ks = get_drift_function("ks_streaming")
        fn_w = get_drift_function("wasserstein_streaming")
        fn_psi = get_drift_function("psi_streaming")

        assert fn_ks is not None
        assert fn_w is not None
        assert fn_psi is not None
        assert getattr(fn_ks, "stat_factory") is StreamingKsStat
        assert getattr(fn_w, "stat_factory") is StreamingWassersteinStat

    def test_drift_check_with_ks_streaming(self):
        rng = np.random.default_rng(42)
        ref_df = pd.DataFrame({"feat1": rng.normal(0, 1, 1000)})
        cur_df = pd.DataFrame({"feat1": rng.normal(3, 1, 500)})

        checker = DriftCheck(ref_df, algorithm="ks_streaming", threshold=0.95)
        results = checker.run(cur_df, features=["feat1"])

        assert "feat1" in results
        res = results["feat1"]
        assert isinstance(res, DriftResult)
        assert res.method == "ks"
        assert res.is_drifted is True
        assert res.details["approximation"] == "reservoir"

    def test_drift_check_with_wasserstein_streaming(self):
        rng = np.random.default_rng(42)
        ref_df = pd.DataFrame({"feat1": rng.normal(5, 1, 1000)})
        cur_df = pd.DataFrame({"feat1": rng.normal(10, 1, 500)})

        checker = DriftCheck(ref_df, algorithm="wasserstein_streaming", threshold=1.0)
        results = checker.run(cur_df, features=["feat1"])

        assert "feat1" in results
        res = results["feat1"]
        assert isinstance(res, DriftResult)
        assert res.method == "wasserstein"
        assert res.is_drifted is True
        assert res.score > 4.5


class TestStreamingMonitorWithReservoirAlgorithms:
    def test_monitor_e2e_ks_streaming(self, tmp_path):
        import json
        rng = np.random.default_rng(42)
        ref_data = rng.normal(0, 1, 2000)
        ref_path = str(tmp_path / "ref.csv")
        log_path = str(tmp_path / "logs" / "drift.csv")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        pd.DataFrame({"temp": ref_data}).to_csv(ref_path, index=False)

        cfg = StreamingConfig(
            reference_path=ref_path,
            features=["temp"],
            topic="telemetry",
            algorithm="ks_streaming",
            threshold=0.95,
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
            payload = json.dumps({"stream_id": "stream-1", "event_ts": curr_ts, "temp": float(val)}).encode("utf-8")
            msg = RawMessage("telemetry", 0, offset, b"stream-1", payload)
            offset += 1
            return msg

        # Window 0, 1, 2: Stationary Normal(0, 1) - 60 events each
        for w in range(3):
            for i in range(60):
                msgs.append(make_msg(ts, rng.normal(0, 1)))
                ts += 0.1
            ts += 5.0  # advance watermark past window_end + grace

        # Window 3: Shifted Normal(3.5, 1.0) - 60 events
        for i in range(60):
            msgs.append(make_msg(ts, rng.normal(3.5, 1.0)))
            ts += 0.1
        ts += 20.0
        # Final trailing event to flush window 3
        msgs.append(make_msg(ts, 0.0))

        source = FakeEventSource(msgs)
        store = InMemoryStateStore()
        monitor = StreamingMonitor(cfg, source=source, store=store)
        monitor.dispatcher.start()

        monitor.run_once()
        monitor.dispatcher.stop(timeout_s=5.0)

        assert monitor.engine.windows_flushed >= 4
        assert os.path.exists(log_path)
        log_df = pd.read_csv(log_path)
        assert len(log_df) >= 4
