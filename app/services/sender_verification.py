"""Official placement-cell sender verification.

Two checks, both required by default:
1. The From address is on the allowlist (exact address, or an allowed domain).
   The display name ("Placement Cell") is never trusted.
2. Gmail's Authentication-Results header shows the message really came from that domain
   (DMARC pass for the From domain, or a DKIM signature from that domain). This stops
   spoofed mails that merely *claim* to come from the placement cell.
"""

import json
import re
from dataclasses import dataclass, field
from email.utils import parseaddr
from functools import lru_cache
from pathlib import Path

from app.config import get_settings


@dataclass
class SenderAllowlist:
    addresses: set[str] = field(default_factory=set)
    domains: set[str] = field(default_factory=set)
    require_authentication: bool = True

    @classmethod
    def from_file(cls, path: str | Path) -> "SenderAllowlist":
        data = json.loads(Path(path).read_text())
        return cls(
            addresses={a.strip().lower() for a in data.get("addresses", []) if a.strip()},
            domains={d.strip().lower().lstrip("@") for d in data.get("domains", []) if d.strip()},
            require_authentication=bool(data.get("require_authentication", True)),
        )


@lru_cache
def get_allowlist() -> SenderAllowlist:
    return SenderAllowlist.from_file(get_settings().placement_senders_file)


@dataclass
class VerificationResult:
    allowed: bool
    address: str
    reason: str


def extract_address(from_header: str) -> str:
    return parseaddr(from_header or "")[1].strip().lower()


def _domain_of(address: str) -> str:
    return address.rsplit("@", 1)[-1] if "@" in address else ""


def is_allowlisted(address: str, allowlist: SenderAllowlist) -> bool:
    if not address or "@" not in address:
        return False
    return address in allowlist.addresses or _domain_of(address) in allowlist.domains


def is_authenticated(domain: str, auth_results: list[str]) -> bool:
    """True if Gmail recorded DMARC pass for this From domain, or a passing DKIM
    signature whose signing domain is this domain (or a parent of it)."""
    domain = domain.lower()
    for header in auth_results:
        h = header.lower()
        # dmarc=pass ... header.from=example.edu
        for m in re.finditer(r"dmarc=pass[^;]*?header\.from=([a-z0-9.\-]+)", h):
            if m.group(1) == domain:
                return True
        # dkim=pass header.i=@example.edu   or   header.d=example.edu
        for m in re.finditer(r"dkim=pass[^;]*?header\.(?:i|d)=@?([a-z0-9.\-]+)", h):
            signer = m.group(1)
            if domain == signer or domain.endswith("." + signer):
                return True
    return False


def verify_sender(
    from_header: str, auth_results: list[str], allowlist: SenderAllowlist | None = None
) -> VerificationResult:
    allowlist = allowlist or get_allowlist()
    address = extract_address(from_header)
    if not is_allowlisted(address, allowlist):
        return VerificationResult(False, address, "sender not on placement-cell allowlist")
    if allowlist.require_authentication and not is_authenticated(_domain_of(address), auth_results):
        return VerificationResult(False, address, "allowlisted address but DKIM/DMARC did not pass")
    return VerificationResult(True, address, "verified official sender")
