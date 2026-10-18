from epipilot.research.synthetic import benchmark, make_world


def test_paired_worlds_and_reproducible_non_oracle_feedback():
    a, b = make_world(7, "cheap_gate"), make_world(7, "cheap_gate")
    assert a == b
    result = benchmark(seeds=3)
    assert len(result["summary"]) == 24
    assert all(row["runs"] == 3 for row in result["summary"])
    assert {row["strategy"] for row in result["summary"]} == {
        "fifo",
        "existing",
        "carts",
        "no_information",
        "no_cost",
        "no_unlock",
    }
    assert all(row["spent_mean"] <= row["budget"] + 1e-8 for row in result["summary"])
