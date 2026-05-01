#!/usr/bin/env python3
"""Plan or probe a Windows Brave SSH/CDP bridge for Hermes browser-harness.

Default behavior is non-mutating: it prints the Windows Brave command, SSH tunnel
command, and Hermes follow-up steps.  Use --probe only after the user has already
started Brave and the SSH tunnel; it reads the local loopback /json/version
endpoint and prints the VM-side BU_CDP_WS value.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.browser_harness_bridge import (  # noqa: E402
    build_bridge_plan,
    extract_cdp_websocket_url,
    fetch_cdp_version_payload,
)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh-target", help="SSH destination, e.g. user@windows-host; required unless --probe is used")
    parser.add_argument("--local-port", type=int, default=9333, help="VM loopback port for the forwarded CDP endpoint")
    parser.add_argument("--remote-port", type=int, default=9222, help="Windows loopback CDP port used by Brave")
    parser.add_argument("--profile-dir", help="Windows Brave dedicated profile path")
    parser.add_argument("--brave-path", help="Windows Brave executable path")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument("--probe", action="store_true", help="Probe an already-forwarded local /json/version endpoint")
    args = parser.parse_args(argv)
    if not args.probe and not args.ssh_target:
        parser.error("--ssh-target is required unless --probe is used")
    return args


def _render_text(payload: dict) -> str:
    if "windows_brave_command" not in payload:
        lines = [
            "Windows Brave SSH/CDP bridge probe",
            "",
            "CDP version URL:",
            payload["cdp_version_url"],
            "",
            "Safety notes:",
        ]
        lines.extend(f"- {note}" for note in payload["safety_notes"])
        if "probe" in payload:
            lines.extend(["", "Probe:", json.dumps(payload["probe"], indent=2)])
        return "\n".join(lines) + "\n"

    lines = [
        "Windows Brave SSH/CDP bridge plan",
        "",
        "Windows PowerShell command:",
        payload["windows_brave_command"],
        "",
        "VM SSH tunnel command:",
        " ".join(payload["ssh_command"]),
        "",
        "CDP version URL:",
        payload["cdp_version_url"],
        "",
        "Next steps:",
    ]
    lines.extend(f"- {step}" for step in payload["next_steps"])
    lines.extend(["", "Safety notes:"])
    lines.extend(f"- {note}" for note in payload["safety_notes"])
    if "probe" in payload:
        lines.extend(["", "Probe:", json.dumps(payload["probe"], indent=2)])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    plan_kwargs = {
        "local_port": args.local_port,
        "remote_port": args.remote_port,
    }
    if args.ssh_target:
        plan_kwargs["ssh_target"] = args.ssh_target
    if args.profile_dir:
        plan_kwargs["profile_dir"] = args.profile_dir
    if args.brave_path:
        plan_kwargs["brave_path"] = args.brave_path
    if args.ssh_target:
        payload = build_bridge_plan(**plan_kwargs)
    else:
        payload = {
            "starts_processes": False,
            "browser_harness_mode": "ssh_bridge",
            "local_port": args.local_port,
            "remote_port": args.remote_port,
            "cdp_version_url": f"http://127.0.0.1:{args.local_port}/json/version",
            "safety_notes": [
                "Probe-only mode does not start processes, open SSH tunnels, or attach browser-harness.",
                "Only probe loopback forwarded CDP endpoints; never expose CDP to the LAN.",
            ],
        }
    if args.probe:
        version_payload = fetch_cdp_version_payload(local_port=args.local_port)
        payload["probe"] = {
            "version_payload": version_payload,
            "BU_CDP_WS": extract_cdp_websocket_url(version_payload, local_port=args.local_port),
        }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        sys.stdout.write(_render_text(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
