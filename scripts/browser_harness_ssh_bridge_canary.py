#!/usr/bin/env python3
"""Run a read-only Hermes browser-harness canary against an SSH-forwarded CDP endpoint.

This script assumes the Windows Brave dedicated profile and SSH tunnel already
exist. It does not start Brave, open SSH, or attach to any real/default browser
profile. It either uses an explicit --cdp-ws value or derives one from a local
loopback /json/version endpoint, then runs a page_info() canary through the
Hermes browser-harness runtime wrapper.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.browser_harness_bridge import (  # noqa: E402
    extract_cdp_websocket_url,
    fetch_cdp_version_payload,
)
from tools.browser_harness_runtime import cleanup_daemon_artifacts, run_browser_harness  # noqa: E402


DEFAULT_BU_NAME = "hermes-win-brave-bridge-canary"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cdp-ws", help="Explicit VM-side CDP websocket URL, e.g. ws://127.0.0.1:9333/devtools/browser/...")
    parser.add_argument("--probe-local-port", type=int, default=9333, help="VM loopback port to probe when --cdp-ws is omitted")
    parser.add_argument("--probe-local-host", default="127.0.0.1", help="Loopback host to probe when --cdp-ws is omitted")
    parser.add_argument("--bu-name", default=DEFAULT_BU_NAME, help="Isolated browser-harness BU_NAME")
    parser.add_argument("--browser-harness-command", help="Optional browser-harness executable path for tests/debugging")
    parser.add_argument("--timeout-seconds", type=float, default=30.0, help="browser-harness execution timeout")
    parser.add_argument("--screenshot", action="store_true", help="Capture a read-only screenshot artifact under BH_ARTIFACT_DIR")
    parser.add_argument("--public-url", help="Optional http(s) public URL to navigate to for an allowlisted canary")
    parser.add_argument("--allowed-domain", action="append", default=[], help="Allowed domain for --public-url navigation; repeatable. Defaults to the public URL host.")
    parser.add_argument("--extract-text", action="store_true", help="After navigation, print a redacted visible-text snippet from document.body.innerText")
    parser.add_argument("--text-max-chars", type=int, default=1000, help="Maximum visible-text snippet characters to print")
    parser.add_argument("--remove-log", action="store_true", help="Also remove /tmp/bu-<name>.log during cleanup")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    return parser.parse_args(argv)


def _public_url_host(public_url: str) -> str:
    parsed = urlsplit(public_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("--public-url must be an http(s) URL with a hostname")
    return parsed.hostname.lower()


def _allowed_domains(args: argparse.Namespace) -> list[str]:
    domains = [domain.strip().lower() for domain in (args.allowed_domain or []) if domain.strip()]
    if args.public_url and not domains:
        domains.append(_public_url_host(args.public_url))
    return domains


def _canary_code(*, screenshot: bool, public_url: str | None = None, extract_text: bool = False, text_max_chars: int = 1000) -> str:
    lines = [
        "import json",
        "import os",
        "import sys",
        "from pathlib import Path",
        "print('BRIDGE_CANARY_BEGIN')",
    ]
    if public_url:
        lines.extend(
            [
                f"public_url = {json.dumps(public_url)}",
                "print('NEW_TAB_PUBLIC_URL', public_url)",
                f"new_tab({json.dumps(public_url)})",
                "loaded = wait_for_load(timeout=15.0)",
                "print('WAIT_FOR_LOAD', loaded)",
            ]
        )
    lines.extend(
        [
            "info = page_info()",
            "print('PAGE_INFO_JSON', json.dumps(info, sort_keys=True))",
        ]
    )
    if extract_text:
        max_chars = max(0, int(text_max_chars))
        lines.extend(
            [
                "text = js(\"document.body && document.body.innerText ? document.body.innerText : ''\") or ''",
                "snippet = ' '.join(str(text).split())",
                f"snippet = snippet[:{max_chars}]",
                "print('TEXT_SNIPPET_JSON', json.dumps(snippet))",
            ]
        )
    if screenshot:
        lines.extend(
            [
                "out = Path(os.environ['BH_ARTIFACT_DIR']) / 'windows-brave-ssh-bridge-canary.png'",
                "capture_screenshot(str(out))",
                "print('SCREENSHOT_PATH', str(out))",
            ]
        )
    lines.extend(
        [
            "print('BRIDGE_CANARY_DONE')",
            "sys.stdout.flush()",
            "os._exit(0)",
        ]
    )
    return "\n".join(lines) + "\n"


def _derive_cdp_ws(args: argparse.Namespace) -> tuple[str, dict | None]:
    if args.cdp_ws:
        return args.cdp_ws, None
    version_payload = fetch_cdp_version_payload(
        local_port=args.probe_local_port,
        local_host=args.probe_local_host,
    )
    return (
        extract_cdp_websocket_url(
            version_payload,
            local_port=args.probe_local_port,
            local_host=args.probe_local_host,
        ),
        version_payload,
    )


def _render_text(payload: dict) -> str:
    result = payload["result"]
    lines = [
        "Hermes browser-harness SSH bridge canary",
        f"ok: {payload['ok']}",
        f"cdp_ws: {payload['cdp_ws']}",
        f"public_url: {payload.get('public_url')}",
        f"allowed_domains: {', '.join(payload.get('allowed_domains') or [])}",
        f"mode: {result['mode']}",
        f"bu_name: {result['bu_name']}",
        f"exit_code: {result['exit_code']}",
        f"timed_out: {result['timed_out']}",
        "",
        "stdout:",
        result["stdout_redacted"].rstrip(),
        "",
        "stderr:",
        result["stderr_redacted"].rstrip(),
        "",
        "artifacts:",
    ]
    lines.extend(f"- {artifact}" for artifact in result["artifacts"])
    lines.extend(["", "cleanup_removed:"])
    lines.extend(f"- {path}" for path in payload["cleanup_removed"])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    try:
        allowed_domains = _allowed_domains(args)
    except Exception as exc:  # noqa: BLE001 - CLI should return structured failure, not traceback
        payload = {
            "ok": False,
            "stage": "prepare_canary",
            "error": f"{type(exc).__name__}: {exc}",
            "cdp_ws": None,
            "version_payload": None,
            "allowed_domains": [],
        }
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            sys.stderr.write(f"Failed to prepare canary: {payload['error']}\n")
        return 1

    try:
        cdp_ws, version_payload = _derive_cdp_ws(args)
    except Exception as exc:  # noqa: BLE001 - CLI should return structured failure, not traceback
        payload = {
            "ok": False,
            "stage": "derive_cdp_ws",
            "error": f"{type(exc).__name__}: {exc}",
            "cdp_ws": None,
            "version_payload": None,
            "allowed_domains": allowed_domains,
        }
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            sys.stderr.write(f"Failed to derive BU_CDP_WS: {payload['error']}\n")
        return 1

    result = run_browser_harness(
        code=_canary_code(
            screenshot=args.screenshot,
            public_url=args.public_url,
            extract_text=args.extract_text,
            text_max_chars=args.text_max_chars,
        ),
        mode="ssh_bridge",
        cdp_ws=cdp_ws,
        bu_name=args.bu_name,
        timeout_seconds=args.timeout_seconds,
        command=args.browser_harness_command,
        allowed_domains=allowed_domains,
    )
    cleanup_removed = cleanup_daemon_artifacts(args.bu_name, remove_log=args.remove_log)
    payload = {
        "ok": result.ok,
        "cdp_ws": cdp_ws,
        "version_payload": version_payload,
        "allowed_domains": allowed_domains,
        "public_url": args.public_url,
        "extract_text": args.extract_text,
        "result": result.to_dict(),
        "cleanup_removed": cleanup_removed,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        sys.stdout.write(_render_text(payload))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
