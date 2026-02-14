"""DNS TXT record resolver for API server address discovery (Phase 3 support).

The ikabot third-party API server publishes its current address as a DNS
TXT record on ikagod.twilightparadox.com.  This module resolves that record
via raw UDP socket (no external DNS library needed) with fallback to nslookup.

The resolved address is cached for the duration of the process — we only
need to look it up once per session.
"""

import os
import socket
import struct
from typing import Optional

from autoIkabot.config import CUSTOM_API_ADDRESS_ENV, PUBLIC_API_DOMAIN
from autoIkabot.utils.logging import get_logger

logger = get_logger(__name__)

# Module-level cache: once resolved, we reuse the address all session.
_cached_address: Optional[str] = None


def _build_dns_query(domain: str) -> bytes:
    """Build a raw DNS query packet for a TXT record.

    Parameters
    ----------
    domain : str
        The domain to query (e.g. "ikagod.twilightparadox.com").

    Returns
    -------
    bytes
        A DNS query packet ready to send over UDP.
    """
    # Header: ID, flags (standard query + recursion desired), 1 question
    header = struct.pack(">HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0)

    # Question section: encode domain labels
    question = b""
    for label in domain.split("."):
        question += struct.pack("B", len(label)) + label.encode("utf-8")
    question += struct.pack("B", 0)              # root label terminator
    question += struct.pack(">HH", 0x0010, 1)    # QTYPE=TXT, QCLASS=IN

    return header + question


def _parse_txt_response(response: bytes) -> str:
    """Extract the TXT record value from a DNS response packet.

    Parameters
    ----------
    response : bytes
        Raw DNS response bytes.

    Returns
    -------
    str
        The concatenated TXT record value.

    Raises
    ------
    ValueError
        If no TXT record is found in the response.
    """
    offset = 12  # skip the 12-byte header

    # Skip the question section (one question)
    while True:
        length = response[offset]
        if length == 0:
            break
        offset += length + 1
    offset += 5  # skip zero byte + QTYPE (2) + QCLASS (2)

    # Parse answer records
    while offset < len(response):
        # Name field: either a pointer (0xC0) or a sequence of labels
        if response[offset] & 0xC0 == 0xC0:
            offset += 2  # compressed pointer
        else:
            while True:
                length = response[offset]
                if length == 0:
                    offset += 1
                    break
                offset += length + 1

        rtype = struct.unpack(">H", response[offset:offset + 2])[0]
        # Skip TYPE(2) + CLASS(2) + TTL(4) = 8 bytes to reach RDLENGTH
        rdlength = struct.unpack(">H", response[offset + 8:offset + 10])[0]
        offset += 10  # now pointing at RDATA

        if rtype == 16:  # TXT record
            txt_data = response[offset:offset + rdlength]
            # TXT RDATA is one or more length-prefixed strings
            parts = []
            pos = 0
            while pos < len(txt_data):
                slen = txt_data[pos]
                parts.append(txt_data[pos + 1:pos + 1 + slen].decode("utf-8"))
                pos += 1 + slen
            return "".join(parts)
        else:
            offset += rdlength

    raise ValueError("No TXT record found in DNS response")


def _dns_txt_via_socket(domain: str, dns_server: str = "8.8.8.8") -> str:
    """Resolve a DNS TXT record using a raw UDP socket.

    Parameters
    ----------
    domain : str
        Domain to look up.
    dns_server : str
        DNS server IP to query.

    Returns
    -------
    str
        The TXT record value (typically an IP address or hostname).
    """
    query = _build_dns_query(domain)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(5)
        sock.sendto(query, (dns_server, 53))
        data, _ = sock.recvfrom(512)
    return _parse_txt_response(data)


def get_api_address(domain: str = PUBLIC_API_DOMAIN) -> str:
    """Resolve the public API server address.

    Checks in order:
    1. CUSTOM_API_ADDRESS environment variable (overrides everything)
    2. Module-level cache (already resolved this session)
    3. DNS TXT lookup via socket against multiple DNS servers

    The result is cached so subsequent calls are instant.

    Parameters
    ----------
    domain : str
        The domain whose TXT record contains the API server address.

    Returns
    -------
    str
        Full URL prefix, e.g. "http://1.2.3.4:5000"

    Raises
    ------
    RuntimeError
        If the address cannot be resolved from any source.
    """
    global _cached_address

    # 1. Environment override
    custom = os.environ.get(CUSTOM_API_ADDRESS_ENV)
    if custom:
        logger.info("Using custom API address from env: %s", custom)
        return custom

    # 2. Cache hit
    if _cached_address is not None:
        return _cached_address

    # 3. DNS TXT lookup — try multiple DNS servers
    dns_servers = ["ns2.afraid.org", "8.8.8.8", "1.1.1.1"]
    last_error = None
    for dns_server in dns_servers:
        try:
            txt = _dns_txt_via_socket(domain, dns_server)
            # The TXT record contains an address like "1.2.3.4:5000"
            # Strip any path suffix (ikabot reference does .replace("/ikagod/ikabot", ""))
            address = "http://" + txt.replace("/ikagod/ikabot", "")
            # Basic validation: must contain a dot or colon (IPv4, IPv6, hostname)
            bare = address.replace("http://", "")
            if "." not in bare and ":" not in bare:
                raise ValueError(f"Bad address from DNS: {address}")
            _cached_address = address
            logger.info(
                "Resolved API address via DNS (%s): %s", dns_server, address
            )
            return address
        except Exception as e:
            logger.warning(
                "DNS TXT lookup failed via %s: %s", dns_server, e
            )
            last_error = e

    raise RuntimeError(
        f"Could not resolve API server address for {domain}: {last_error}"
    )
