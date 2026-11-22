# tests/test_lazy_init.py

import sys
import subprocess
import threading
import pickle
import inspect
import importlib
import pytest
import etsi.watchdog as wd


def test_bare_import_does_not_load_heavy_dependencies():
    """
    Verify that bare `import etsi.watchdog` does not eagerly import heavy libraries
    (pandas, scikit-learn, slack_sdk, scipy).
    """
    code = (
        "import sys\n"
        "import etsi.watchdog\n"
        "heavy = [m for m in sys.modules if any(k in m for k in ['slack', 'sklearn', 'pandas', 'scipy'])]\n"
        "assert not heavy, f'Heavy modules eagerly loaded: {heavy}'\n"
    )
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert res.returncode == 0, f"Bare import test failed:\nstdout: {res.stdout}\nstderr: {res.stderr}"


def test_import_drift_module_without_slack_sdk():
    """
    Verify that importing a drift module alone (e.g. psi, ks, wasserstein)
    succeeds when slack_sdk is not installed.
    """
    code = (
        "import sys\n"
        "sys.modules['slack_sdk'] = None\n"
        "from etsi.watchdog.drift.psi import psi_drift\n"
        "from etsi.watchdog.drift.ks import ks_drift\n"
        "from etsi.watchdog.drift.wasserstein import wasserstein_drift\n"
        "assert callable(psi_drift)\n"
        "assert callable(ks_drift)\n"
        "assert callable(wasserstein_drift)\n"
    )
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert res.returncode == 0, f"Drift module import failed without slack_sdk:\n{res.stderr}"


def test_lazy_top_level_attribute_access():
    """
    Verify that accessing attributes on etsi.watchdog dynamically loads them
    and caches them in the module's globals.
    """
    code = (
        "import sys\n"
        "import etsi.watchdog as wd\n"
        "assert 'sklearn' not in sys.modules\n"
        "# Accessing IsolationForestModel should load sklearn on demand\n"
        "model_cls = wd.IsolationForestModel\n"
        "assert model_cls is not None\n"
        "assert 'sklearn' in sys.modules\n"
    )
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert res.returncode == 0, f"Attribute access test failed:\n{res.stderr}"


def test_from_import_compatibility():
    """
    Verify that `from etsi.watchdog import <symbol>` functions correctly for all __all__ exports.
    """
    from etsi.watchdog import (
        DriftCheck,
        Monitor,
        DriftComparator,
        DriftResult,
        SlackNotifier,
        WatchdogConfig,
        quick_setup,
        IsolationForestModel,
        register_drift_algorithm,
        get_drift_function,
    )

    for item in [
        DriftCheck,
        Monitor,
        DriftComparator,
        DriftResult,
        SlackNotifier,
        WatchdogConfig,
        quick_setup,
        IsolationForestModel,
        register_drift_algorithm,
        get_drift_function,
    ]:
        assert item is not None


def test_dir_and_all_parity():
    """
    Verify that dir(etsi.watchdog) contains all entries declared in __all__.
    """
    wd_dir = dir(wd)
    for sym in wd.__all__:
        assert sym in wd_dir, f"Symbol {sym} missing from dir(etsi.watchdog)"


def test_attribute_error_on_nonexistent():
    """
    Verify that accessing a nonexistent attribute raises AttributeError.
    """
    with pytest.raises(AttributeError, match="has no attribute 'nonexistent_symbol'"):
        _ = getattr(wd, "nonexistent_symbol")


def test_wildcard_import_star():
    """
    Verify that `from etsi.watchdog import *` in an isolated process
    imports all declared public symbols into caller namespace.
    """
    code = (
        "from etsi.watchdog import *\n"
        "expected = [\n"
        "    'DriftCheck', 'Monitor', 'DriftComparator', 'DriftResult',\n"
        "    'SlackNotifier', 'WatchdogConfig', 'quick_setup',\n"
        "    'IsolationForestModel', 'register_drift_algorithm', 'get_drift_function'\n"
        "]\n"
        "for name in expected:\n"
        "    assert name in locals(), f'{name} not in locals after wildcard import'\n"
    )
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert res.returncode == 0, f"Wildcard import test failed:\n{res.stderr}"


def test_concurrent_multithreaded_lazy_access():
    """
    Verify thread safety during concurrent attribute access across multiple worker threads.
    """
    code = (
        "import threading\n"
        "import etsi.watchdog as wd\n"
        "errors = []\n"
        "symbols = [\n"
        "    'DriftCheck', 'Monitor', 'DriftComparator', 'DriftResult',\n"
        "    'SlackNotifier', 'WatchdogConfig', 'quick_setup',\n"
        "    'IsolationForestModel', 'register_drift_algorithm', 'get_drift_function'\n"
        "]\n"
        "def worker(sym):\n"
        "    try:\n"
        "        val = getattr(wd, sym)\n"
        "        assert val is not None\n"
        "    except Exception as e:\n"
        "        errors.append((sym, str(e)))\n"
        "\n"
        "threads = [threading.Thread(target=worker, args=(symbols[i % len(symbols)],)) for i in range(50)]\n"
        "for t in threads: t.start()\n"
        "for t in threads: t.join()\n"
        "assert not errors, f'Concurrent access errors: {errors}'\n"
    )
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert res.returncode == 0, f"Concurrent lazy access failed:\n{res.stderr}"


def test_pickle_roundtrip_of_lazy_objects():
    """
    Verify that objects instantiated from lazily imported classes support standard pickle serialization.
    """
    res = wd.DriftResult(method="psi", score=0.15, threshold=0.2, details={"test": 1}, sample_size=100)
    data = pickle.dumps(res)
    loaded = pickle.loads(data)
    assert loaded.method == "psi"
    assert loaded.score == 0.15
    assert loaded.sample_size == 100
    assert loaded.is_drifted is False


def test_inspect_getmembers_safety():
    """
    Verify introspection compatibility with inspect.getmembers.
    """
    members = dict(inspect.getmembers(wd))
    for sym in wd.__all__:
        assert sym in members, f"Expected symbol {sym} missing from inspect.getmembers"


def test_importlib_reload_safety():
    """
    Verify that reloading etsi.watchdog preserves lazy attribute resolution.
    """
    reloaded = importlib.reload(wd)
    assert hasattr(reloaded, "DriftCheck")
    assert reloaded.DriftCheck is not None


def test_slack_notifier_missing_dependency_informative_error():
    """
    Verify that if slack_sdk is absent, SlackNotifier instantiation
    raises a clear ImportError rather than failing on module import.
    """
    code = (
        "import sys\n"
        "sys.modules['slack_sdk'] = None\n"
        "from etsi.watchdog.slack_notifier import SlackNotifier\n"
        "try:\n"
        "    SlackNotifier(token='xoxb-test')\n"
        "    sys.exit(1)\n"
        "except ImportError as e:\n"
        "    assert 'slack_sdk is required' in str(e)\n"
    )
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert res.returncode == 0, f"SlackNotifier missing error test failed:\n{res.stderr}"


def test_real_dataset_california_housing_without_slack_sdk():
    """
    Integration test verifying batch DriftCheck on California Housing Census dataset
    when slack_sdk is omitted from environment.
    """
    code = (
        "import sys\n"
        "sys.modules['slack_sdk'] = None\n"
        "from sklearn.datasets import fetch_california_housing\n"
        "from etsi.watchdog import DriftCheck\n"
        "\n"
        "data = fetch_california_housing(as_frame=True).frame\n"
        "ref = data.iloc[:1000]\n"
        "curr = data.iloc[1000:2000]\n"
        "\n"
        "check = DriftCheck(ref, algorithm='psi')\n"
        "results = check.run(curr, features=['MedInc', 'HouseAge'])\n"
        "assert 'MedInc' in results\n"
        "assert 'HouseAge' in results\n"
        "assert results['MedInc'].sample_size == 1000\n"
    )
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert res.returncode == 0, f"Real dataset batch test without slack_sdk failed:\n{res.stderr}"


def test_real_dataset_nasa_climate_monitor_without_slack_sdk(tmp_path):
    """
    Integration test verifying Monitor rolling watch and CSV logging on NASA
    Global Surface Temperature Anomaly dataset when slack_sdk is omitted from environment.
    """
    import os
    import scipy
    nasa_csv_path = os.path.join(os.path.dirname(scipy.__file__), "signal", "tests", "data", "GLB.Ts+dSST.csv")
    if not os.path.exists(nasa_csv_path):
        pytest.skip(f"NASA dataset not found at {nasa_csv_path}")

    log_csv = tmp_path / "nasa_temperature_drift.csv"
    code = f"""
import sys
import os
import pandas as pd
sys.modules['slack_sdk'] = None
from etsi.watchdog import Monitor

df_nasa = pd.read_csv(r"{nasa_csv_path}", skiprows=1)
df_nasa['Year'] = pd.to_numeric(df_nasa['Year'], errors='coerce')
df_nasa['J-D'] = pd.to_numeric(df_nasa['J-D'], errors='coerce')
df_clean = df_nasa.dropna(subset=['Year', 'J-D']).copy()

ref_df = df_clean[df_clean['Year'] <= 1940][['J-D']].rename(columns={{'J-D': 'temp_anomaly'}})
curr_df = df_clean[df_clean['Year'] > 1940][['J-D']].rename(columns={{'J-D': 'temp_anomaly'}})

monitor = Monitor(ref_df)
monitor.enable_logging(r"{str(log_csv)}")
monitor.watch(curr_df, algorithm="ks")
assert os.path.exists(r"{str(log_csv)}")
df_logged = pd.read_csv(r"{str(log_csv)}")
assert len(df_logged) == 1
assert df_logged.iloc[0]["drift"] == True
assert df_logged.iloc[0]["method"] == "ks"
assert df_logged.iloc[0]["sample_size"] == len(curr_df)
"""
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert res.returncode == 0, f"NASA real climate data test without slack_sdk failed:\n{res.stderr}"
