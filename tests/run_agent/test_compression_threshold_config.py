import run_agent


def test_resolve_compression_threshold_defaults_to_recommended_target():
    assert run_agent._resolve_compression_threshold({}) == 0.85
    assert run_agent._resolve_compression_threshold(None) == 0.85


def test_resolve_compression_threshold_handles_invalid_input_without_crashing():
    assert run_agent._resolve_compression_threshold({"threshold": "not-a-number"}) == 0.85
    assert run_agent._resolve_compression_threshold({"threshold": 2}) == 0.85
    assert run_agent._resolve_compression_threshold({"threshold": -1}) == 0.85


def test_resolve_compression_threshold_accepts_exact_recommended_boundary():
    assert run_agent._resolve_compression_threshold({"threshold": 0.85}) == 0.85
