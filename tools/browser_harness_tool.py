"""Hermes tool registration for browser-use/browser-harness."""
from __future__ import annotations

from tools.browser_harness_runtime import (
    cleanup_daemon_artifacts,
    find_browser_harness_command,
    run_browser_harness,
)
from tools.registry import registry, tool_result


BROWSER_HARNESS_RUN_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_harness_run",
        "description": (
            "Run pinned browser-use/browser-harness through the Hermes safety wrapper. "
            "Safe modes require an explicit CDP WebSocket; real browser attach is blocked "
            "unless allow_real_browser_attach=true. Returns redacted structured output."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code executed by browser-harness helpers. Required except mode=doctor.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["doctor", "cdp", "disposable", "dedicated_brave", "ssh_bridge", "real_brave", "local_profile"],
                    "default": "cdp",
                    "description": "Execution mode. Use cdp/disposable with cdp_ws for safe sessions; real modes require explicit approval.",
                },
                "cdp_ws": {
                    "type": "string",
                    "description": "Explicit Chrome DevTools Protocol WebSocket URL for safe CDP-backed modes.",
                },
                "bu_name": {
                    "type": "string",
                    "description": "Optional BU_NAME for daemon/socket isolation. Defaults to a Hermes-generated name.",
                },
                "timeout_seconds": {
                    "type": "number",
                    "default": 30,
                    "description": "Hard timeout for the browser-harness subprocess.",
                },
                "allow_real_browser_attach": {
                    "type": "boolean",
                    "default": False,
                    "description": "Explicitly allow attaching to a local real browser profile. Keep false unless the user approved this session.",
                },
                "allowed_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional domain allowlist for page.goto/browser.goto URLs. Exact domains and subdomains are allowed.",
                },
            },
            "additionalProperties": False,
        },
    },
}

BROWSER_HARNESS_CLEANUP_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_harness_cleanup",
        "description": "Remove browser-harness daemon socket/pid artifacts for a BU_NAME; log retained unless remove_log=true.",
        "parameters": {
            "type": "object",
            "properties": {
                "bu_name": {"type": "string", "description": "The BU_NAME to clean up."},
                "remove_log": {"type": "boolean", "default": False, "description": "Also remove /tmp/bu-$BU_NAME.log."},
            },
            "required": ["bu_name"],
            "additionalProperties": False,
        },
    },
}


def _browser_harness_available() -> bool:
    return bool(find_browser_harness_command())


def handle_browser_harness_run(args: dict, **kwargs) -> str:
    result = run_browser_harness(
        code=args.get("code", ""),
        mode=args.get("mode", "cdp"),
        cdp_ws=args.get("cdp_ws"),
        bu_name=args.get("bu_name"),
        timeout_seconds=float(args.get("timeout_seconds", 30)),
        allow_real_browser_attach=bool(args.get("allow_real_browser_attach", False)),
        allowed_domains=args.get("allowed_domains"),
    )
    return tool_result(result.to_dict())


def handle_browser_harness_cleanup(args: dict, **kwargs) -> str:
    removed = cleanup_daemon_artifacts(
        args.get("bu_name", ""),
        remove_log=bool(args.get("remove_log", False)),
    )
    return tool_result(success=True, removed=removed)


registry.register(
    name="browser_harness_run",
    toolset="browser_harness",
    schema=BROWSER_HARNESS_RUN_SCHEMA,
    handler=handle_browser_harness_run,
    check_fn=_browser_harness_available,
    description="Run browser-harness with Hermes safety gates and redaction",
    emoji="🌐",
    max_result_size_chars=12000,
)

registry.register(
    name="browser_harness_cleanup",
    toolset="browser_harness",
    schema=BROWSER_HARNESS_CLEANUP_SCHEMA,
    handler=handle_browser_harness_cleanup,
    check_fn=_browser_harness_available,
    description="Clean browser-harness daemon artifacts for a BU_NAME",
    emoji="🧹",
    max_result_size_chars=4000,
)
