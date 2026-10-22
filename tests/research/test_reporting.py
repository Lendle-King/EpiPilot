import json


def test_report_separates_engineering_and_scientific_assessment(tmp_path):
    from test_agent_cycle import discovered

    from epipilot.research.measurement import MeasurementAdapter
    from epipilot.research.reporting import report, write_report

    engine, root, _ = discovered(tmp_path)
    adapter = MeasurementAdapter(engine, root)
    engine.run_next(adapter, adapter)
    data = report(engine)
    assert data["engineering_validation"]["mergeable"] is False
    assert data["engineering_validation"]["release_accepted"] is False
    assert data["scientific_assessment"]["reviews"]
    observation = data["observations"][0]
    assert observation["engineering_validation"]["status"] == "measurement_execution"
    assert observation["scientific_assessment"]["outcome"] in {"support", "refute", "inconclusive"}

    path = tmp_path / "report.json"
    write_report(path, data)
    markdown = path.with_suffix(".md").read_text(encoding="utf8")
    assert "Engineering / code-delivery validation" in markdown
    assert "Scientific assessment" in markdown
    assert json.loads(path.read_text(encoding="utf8"))["reviews"]
