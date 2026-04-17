def test_default_config_has_code_harness_section():
    from hermes_cli.config import DEFAULT_CONFIG

    code_harness = DEFAULT_CONFIG.get("code_harness", {})

    assert code_harness.get("enabled") is False
    assert code_harness.get("default") == ""
    omx_cfg = code_harness.get("omx", {})
    assert omx_cfg.get("command") == ""
