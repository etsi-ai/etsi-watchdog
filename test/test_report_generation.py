import pandas as pd
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


from etsi.watchdog.report.generate import generate_drift_report


def test_generate_html_report(tmp_path):
    ref = pd.DataFrame({
        "feature1": [1, 2, 3, 4],
        "feature2": [5, 6, 7, 8]
    })
    live = pd.DataFrame({
        "feature1": [1, 1, 1, 1],
        "feature2": [9, 10, 11, 12]
    })
    results = {
        "feature1": {"P-Value": 0.01, "Statistic": 0.3, "Drift": True},
        "feature2": {"P-Value": 0.5, "Statistic": 0.1, "Drift": False}
    }
    output_path = tmp_path / "report.html"
    generate_drift_report(ref, live, results, str(output_path), format="html")

    assert output_path.exists()
    assert "Drift Overview" in output_path.read_text()
