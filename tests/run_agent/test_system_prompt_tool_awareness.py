"""System prompt tests for active tool-awareness guidance."""

from unittest.mock import patch

from run_agent import AIAgent


def _make_tool_defs(*names: str) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": f"{name} tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for name in names
    ]


def test_system_prompt_lists_active_tool_names_and_forbids_false_unavailable_claims():
    with (
        patch("run_agent.get_tool_definitions", return_value=_make_tool_defs("terminal", "patch", "read_file")),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("hermes_cli.config.load_config", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            model="openai-codex/gpt-5.4",
        )

    prompt = agent._build_system_prompt()

    assert "# Active tool availability" in prompt
    assert "terminal" in prompt
    assert "patch" in prompt
    assert "read_file" in prompt
    assert "Do not claim a tool is unavailable if its name appears in this active tool list" in prompt
    assert "If unsure, inspect this active tool list first" in prompt


def test_system_prompt_warns_not_to_invent_vision_image_paths():
    with (
        patch("run_agent.get_tool_definitions", return_value=_make_tool_defs("vision_analyze", "browser_vision")),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("hermes_cli.config.load_config", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            model="openai-codex/gpt-5.4",
        )

    prompt = agent._build_system_prompt()

    assert "Vision input rule" in prompt
    assert "Do not invent, guess, or fuzz image paths" in prompt
    assert "/tmp/nonexistent" in prompt
