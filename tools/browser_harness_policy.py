"""Safety policy for Hermes browser-harness execution.

The upstream browser-harness executes arbitrary Python against a CDP browser
session.  This module keeps the first Hermes integration deliberately narrow:
CDP-backed/disposable sessions are allowed, real local browser profile attach is
blocked unless explicitly approved by the caller, and obvious credential/browser
storage extraction is rejected before subprocess execution.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Iterable
from urllib.parse import urlparse


REAL_BROWSER_ATTACH_MODES = {
    "real_brave",
    "local_brave",
    "real_chrome",
    "local_chrome",
    "local_profile",
    "local_browser",
}

SAFE_MODES = {"doctor", "cdp", "disposable", "dedicated_brave", "ssh_bridge"}

SENSITIVE_STORAGE_PATTERNS = (
    re.compile(r"\bdocument\.cookie\b", re.IGNORECASE),
    re.compile(r"\blocalStorage\b", re.IGNORECASE),
    re.compile(r"\bsessionStorage\b", re.IGNORECASE),
    re.compile(r"\bNetwork\.(getAllCookies|getCookies)\b", re.IGNORECASE),
    re.compile(r"\bStorage\.(getCookies|clearCookies)\b", re.IGNORECASE),
    re.compile(r"\bcookieStore\b", re.IGNORECASE),
)

MUTATING_ACTION_HINTS = (
    "place bet",
    "submit bet",
    "deposit",
    "withdraw",
    "send message",
    "post tweet",
    "submit form",
    "account settings",
    "buy now",
    "checkout",
    "confirm order",
    "purchase",
    "save changes",
    "delete account",
)

PAGE_NAVIGATION_PATTERN = re.compile(
    r"\b(?:new_tab|goto_url|goto|(?:page|browser|tab)\.goto|cdp)\s*\([^)]*(?:url\s*=\s*)?['\"](?P<url>https?://[^'\"]+)",
    re.IGNORECASE | re.DOTALL,
)

LOGIN_SURFACE_PATTERNS = (
    re.compile(r"input\s*\[\s*type\s*=\s*['\"]?password", re.IGNORECASE),
    re.compile(r"\btype\s*=\s*['\"]password['\"]", re.IGNORECASE),
    re.compile(r"\bpassword\b.{0,80}\bfill\s*\(", re.IGNORECASE | re.DOTALL),
)

FILE_TRANSFER_PATTERNS = (
    re.compile(r"\bset_input_files\s*\(", re.IGNORECASE),
    re.compile(r"input\s*\[\s*type\s*=\s*['\"]?file", re.IGNORECASE),
    re.compile(r"\baccept_downloads\b", re.IGNORECASE),
    re.compile(r"\bdownload\b", re.IGNORECASE),
    re.compile(r"\bupload\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class PolicyVerdict:
    allowed: bool
    reason: str
    mode: str
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "mode": self.mode,
            "warnings": list(self.warnings),
        }


def _code_contains_any(code: str, patterns: Iterable[re.Pattern[str]]) -> bool:
    return any(pattern.search(code or "") for pattern in patterns)


def _normalize_domain(domain: str) -> str:
    raw = (domain or "").strip().lower()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"//{raw}")
    host = (parsed.hostname or raw).strip(".")
    return host.lower()


def _host_matches_allowed_domain(host: str, allowed_domain: str) -> bool:
    normalized_host = _normalize_domain(host)
    normalized_allowed = _normalize_domain(allowed_domain)
    return bool(
        normalized_host
        and normalized_allowed
        and (normalized_host == normalized_allowed or normalized_host.endswith(f".{normalized_allowed}"))
    )


def _blocked_goto_hosts(code: str, allowed_domains: Iterable[str] | None) -> tuple[str, ...]:
    allowed = tuple(_normalize_domain(domain) for domain in (allowed_domains or ()) if _normalize_domain(domain))
    if not allowed:
        return ()
    blocked: list[str] = []
    for match in PAGE_NAVIGATION_PATTERN.finditer(code or ""):
        host = _normalize_domain(match.group("url"))
        if host and not any(_host_matches_allowed_domain(host, domain) for domain in allowed):
            blocked.append(host)
    return tuple(dict.fromkeys(blocked))


def evaluate_browser_harness_request(
    *,
    mode: str,
    code: str = "",
    allow_real_browser_attach: bool = False,
    allowed_domains: Iterable[str] | None = None,
) -> PolicyVerdict:
    """Return whether a browser-harness request is allowed to execute.

    Modes intentionally distinguish explicitly supplied CDP sessions from local
    profile attach.  Without a supplied CDP endpoint, upstream browser-harness
    tries profile autodiscovery and can attach to a real logged-in browser; this
    is blocked unless the caller explicitly approves it.
    """
    normalized_mode = (mode or "cdp").strip().lower()

    if normalized_mode in REAL_BROWSER_ATTACH_MODES and not allow_real_browser_attach:
        return PolicyVerdict(
            allowed=False,
            reason="real_browser_attach_requires_explicit_approval",
            mode=normalized_mode,
        )

    if normalized_mode not in SAFE_MODES and normalized_mode not in REAL_BROWSER_ATTACH_MODES:
        return PolicyVerdict(
            allowed=False,
            reason="unknown_browser_harness_mode",
            mode=normalized_mode,
        )

    if _code_contains_any(code, SENSITIVE_STORAGE_PATTERNS):
        return PolicyVerdict(
            allowed=False,
            reason="sensitive_browser_storage_access_blocked",
            mode=normalized_mode,
        )

    blocked_hosts = _blocked_goto_hosts(code, allowed_domains)
    if blocked_hosts:
        return PolicyVerdict(
            allowed=False,
            reason="domain_not_allowed",
            mode=normalized_mode,
            warnings=blocked_hosts,
        )

    if _code_contains_any(code, LOGIN_SURFACE_PATTERNS):
        return PolicyVerdict(
            allowed=False,
            reason="sensitive_login_surface_requires_explicit_approval",
            mode=normalized_mode,
        )

    if _code_contains_any(code, FILE_TRANSFER_PATTERNS):
        return PolicyVerdict(
            allowed=False,
            reason="file_transfer_requires_explicit_approval",
            mode=normalized_mode,
        )

    lowered = (code or "").lower()
    mutating_matches = tuple(hint for hint in MUTATING_ACTION_HINTS if hint in lowered)
    if mutating_matches:
        return PolicyVerdict(
            allowed=False,
            reason="mutating_browser_action_requires_explicit_approval",
            mode=normalized_mode,
            warnings=mutating_matches,
        )

    return PolicyVerdict(allowed=True, reason="allowed", mode=normalized_mode)
