"""Real client address behind a reverse proxy.

The API sits behind nginx and a docker port mapping, so the TCP peer is the docker gateway for
everybody. Forwarding headers are honoured ONLY when the peer is in `settings.trusted_proxies`;
from anyone else they are attacker-controlled and ignored. Implemented here (not via uvicorn's
`--proxy-headers`) so the trust rule is explicit, configurable and tested."""
from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from functools import lru_cache

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Network = ipaddress.IPv4Network | ipaddress.IPv6Network


@lru_cache(maxsize=8)
def parse_networks(spec: str) -> tuple[Network, ...]:
    """Comma-separated IPs / CIDRs; unparsable items are skipped."""
    nets: list[Network] = []
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            continue
    return tuple(nets)


def parse_ip(value: str) -> IPAddress | None:
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def is_trusted(ip: IPAddress | None, nets: tuple[Network, ...]) -> bool:
    return ip is not None and any(ip in net for net in nets)


def resolve_client_ip(peer: str, headers: Mapping[str, str], trusted_proxies: str) -> str:
    """The client address for a request whose TCP peer is `peer`.

    Trusted peer: X-Real-IP (nginx overwrites it, so the client cannot forge it), else the
    right-most X-Forwarded-For entry that is not itself a trusted proxy. Otherwise: the peer."""
    nets = parse_networks(trusted_proxies)
    if not is_trusted(parse_ip(peer), nets):
        return peer
    real = parse_ip(headers.get("x-real-ip", ""))
    if real is not None:
        return str(real)
    for hop in reversed(headers.get("x-forwarded-for", "").split(",")):
        ip = parse_ip(hop)
        if ip is not None and not is_trusted(ip, nets):
            return str(ip)
    return peer
