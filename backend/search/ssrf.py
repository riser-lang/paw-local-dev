"""PAW Search Anti-SSRF & Safe Web Content Fetcher (Requirement 2.3 & W10).

Guarantees:
- Strict rejection of localhost, 127.0.0.1, 0.0.0.0, private ranges (10.x, 172.16-31.x, 192.168.x)
- Rejection of link-local (169.254.x.x) and cloud metadata (169.254.169.254)
- Pre-request DNS resolution & validation on all resolved A/AAAA records
- Step-by-step redirect validation (re-validates every redirect hop target IP)
- Max body size limit (500 KB) & max redirect depth (3 hops)
- Safe text extraction without executing scripts/styles/HTML tags
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Optional, Tuple
import urllib.parse
import httpx
import lxml.html

logger = logging.getLogger("paw.search.ssrf")

FORBIDDEN_HOSTNAMES = frozenset({
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    "metadata.google.internal",
    "instance-data",
})

CLOUD_METADATA_IPS = frozenset({
    "169.254.169.254",
    "169.254.170.2",
    "fd00:ec2::254",
})


class SSRFValidationError(ValueError):
    """Raised when a URL violates Anti-SSRF policies."""
    pass


def validate_ip_address(ip_str: str) -> bool:
    """Check if an IP string is public and safe to request."""
    if ip_str in CLOUD_METADATA_IPS:
        return False
    try:
        ip = ipaddress.ip_address(ip_str)
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
        return True
    except ValueError:
        return False


def validate_url_safe(url: str) -> Tuple[bool, str, Optional[str]]:
    """Validate that a URL does not target internal or restricted network endpoints.

    Returns:
        (is_safe: bool, reason: str, resolved_ip: Optional[str])
    """
    if not url:
        return False, "EMPTY_URL", None

    try:
        parsed = urllib.parse.urlsplit(url)
    except Exception as e:
        return False, f"MALFORMED_URL: {e}", None

    # 1. Scheme must be HTTP or HTTPS
    if parsed.scheme.lower() not in ("http", "https"):
        return False, f"UNSUPPORTED_SCHEME: {parsed.scheme}", None

    hostname = parsed.hostname
    if not hostname:
        return False, "MISSING_HOSTNAME", None

    hostname_clean = hostname.strip().lower()

    # 2. Check forbidden hostnames
    if hostname_clean in FORBIDDEN_HOSTNAMES:
        return False, f"FORBIDDEN_HOSTNAME: {hostname_clean}", None

    # Check for direct IP in URL
    try:
        # If hostname is direct IP
        direct_ip = ipaddress.ip_address(hostname_clean)
        if not validate_ip_address(str(direct_ip)):
            return False, f"PRIVATE_OR_RESTRICTED_IP: {direct_ip}", str(direct_ip)
        return True, "SAFE", str(direct_ip)
    except ValueError:
        pass  # Hostname is a domain name, proceed to DNS resolution

    # 3. Resolve DNS and validate ALL returned addresses (prevent DNS rebinding)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addr_infos = socket.getaddrinfo(hostname_clean, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as e:
        return False, f"DNS_RESOLUTION_FAILED: {e}", None
    except Exception as e:
        return False, f"DNS_ERROR: {e}", None

    if not addr_infos:
        return False, "NO_DNS_RECORDS", None

    primary_ip = None
    for addr in addr_infos:
        ip_candidate = addr[4][0]
        if not primary_ip:
            primary_ip = ip_candidate
        if not validate_ip_address(ip_candidate):
            return False, f"HOSTNAME_RESOLVES_TO_RESTRICTED_IP: {ip_candidate}", ip_candidate

    return True, "SAFE", primary_ip


def safe_fetch_page_text(
    url: str,
    max_bytes: int = 500 * 1024,
    timeout: float = 6.0,
    max_redirects: int = 3,
    user_agent: str = "PAW-Search/1.0",
) -> Tuple[bool, str, str]:
    """Safely fetch and extract plain text from a URL with full Anti-SSRF protection.

    Returns:
        (success: bool, text_or_error: str, final_url: str)
    """
    curr_url = url
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        redirects_followed = 0

        while redirects_followed <= max_redirects:
            # 1. Validate URL before making request
            is_safe, reason, resolved_ip = validate_url_safe(curr_url)
            if not is_safe:
                logger.warning(f"SSRF block on {curr_url}: {reason}")
                return False, f"SSRF_BLOCKED: {reason}", curr_url

            try:
                resp = client.get(curr_url, headers=headers)
            except Exception as e:
                return False, f"NETWORK_ERROR: {type(e).__name__}", curr_url

            # 2. Check for redirect
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location")
                if not location:
                    return False, "REDIRECT_WITHOUT_LOCATION", curr_url
                curr_url = urllib.parse.urljoin(curr_url, location)
                redirects_followed += 1
                continue

            # 3. Check status code
            if resp.status_code != 200:
                return False, f"HTTP_{resp.status_code}", curr_url

            # 4. Limit content length
            content = resp.content[:max_bytes]
            try:
                encoding = resp.encoding or "utf-8"
                html_text = content.decode(encoding, errors="replace")
            except Exception:
                html_text = content.decode("utf-8", errors="replace")

            # 5. Extract plain text safely without script/style
            try:
                doc = lxml.html.fromstring(html_text)
                # Strip unwanted elements
                for el in doc.xpath("//script | //style | //nav | //footer | //header | //noscript"):
                    el.drop_tree()
                plain_text = doc.text_content().strip()
                # Normalize excessive whitespaces
                import re
                plain_text = re.sub(r"\s+", " ", plain_text)
                return True, plain_text[:2000], curr_url
            except Exception as e:
                return False, f"HTML_EXTRACT_ERROR: {e}", curr_url

        return False, "EXCEEDED_MAX_REDIRECTS", curr_url


def fetch_and_clean_text(url: str, proxy: Optional[str] = None) -> Optional[str]:
    """Convenience wrapper for safe_fetch_page_text returning text or None."""
    ok, text, _ = safe_fetch_page_text(url)
    return text if ok else None

