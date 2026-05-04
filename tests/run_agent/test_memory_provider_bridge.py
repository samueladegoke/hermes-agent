"""Regression tests for built-in memory writes mirrored to external providers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from run_agent import AIAgent


def test_memory_remove_notifies_external_memory_provider():
    """The memory bridge must forward remove events, not only add/replace."""
    agent = AIAgent.__new__(AIAgent)
    agent._todo_store = None
    agent._session_db = None
    agent.session_id = "test-session"
    agent._memory_store = object()
    agent._memory_manager = MagicMock()
    agent._build_memory_write_metadata = lambda **kwargs: kwargs

    with patch("tools.memory_tool.memory_tool", return_value='{"success": true}'):
        result = agent._invoke_tool(
            "memory",
            {"action": "remove", "target": "memory", "old_text": "old mirrored fact"},
            effective_task_id="task-1",
            tool_call_id="call-1",
        )

    assert result == '{"success": true}'
    agent._memory_manager.on_memory_write.assert_called_once_with(
        "remove",
        "memory",
        "old mirrored fact",
        metadata={"task_id": "task-1", "tool_call_id": "call-1", "old_text": "old mirrored fact"},
    )
