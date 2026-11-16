"""Fetch only the configured ChatGPT CIMD, with pinned public IP and TLS hostname."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
from urllib.parse import urlsplit

import httpx

from sim.config import Settings


class OAuthError(Exception):
    def __init__(self, code: str = "invalid_request"):
        self.code = code
        super().__init__(code)


def configuration(cfg: Settings) -> tuple[str, str]:
    issuer = cfg.public_url.rstrip("/")
    base = urlsplit(issuer)
    client = urlsplit(cfg.mcp_oauth_client_id)
    redirect = urlsplit(cfg.mcp_oauth_redirect_uri)
    if (not cfg.mcp_oauth_enabled or base.scheme != "https" or not base.hostname
            or base.username or base.password or base.query or base.fragment or base.path
            or client.scheme != "https" or client.netloc != "chatgpt.com" or client.query or client.fragment
            or not re.fullmatch(r"/oauth/(?:[A-Za-z0-9_-]+/)?client\.json", client.path)
            or redirect.scheme != "https" or redirect.netloc != "chatgpt.com"
            or not redirect.path or redirect.query or redirect.fragment or "*" in redirect.path):
        raise OAuthError("temporarily_unavailable")
    return issuer, issuer + "/api/mcp"


async def public_addresses(host: str) -> list[str]:
    entries = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    addresses = sorted({e[4][0] for e in entries})
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise OAuthError("invalid_client")
    return addresses


def validate_document(doc: object, client_id: str, redirect_uri: str) -> str:
    if not isinstance(doc, dict):
        raise OAuthError("invalid_client")
    methods = doc.get("token_endpoint_auth_methods_supported", [doc.get("token_endpoint_auth_method", "none")])
    redirects = doc.get("redirect_uris")
    grants, responses = doc.get("grant_types", ["authorization_code"]), doc.get("response_types", ["code"])
    if (doc.get("client_id") != client_id or not isinstance(doc.get("client_name"), str)
            or not 1 <= len(doc["client_name"]) <= 200
            or not isinstance(redirects, list) or len(redirects) > 100
            or not all(isinstance(r, str) and "*" not in r for r in redirects)
            or redirect_uri not in redirects or not isinstance(methods, list) or "none" not in methods
            or not isinstance(grants, list) or "authorization_code" not in grants
            or not isinstance(responses, list) or "code" not in responses):
        raise OAuthError("invalid_client")
    return doc["client_name"]


async def fetch_client(cfg: Settings) -> str:
    configuration(cfg)
    parsed = urlsplit(cfg.mcp_oauth_client_id)
    try:
        async with asyncio.timeout(8):
            addresses = await public_addresses("chatgpt.com")
            ip = addresses[0]
            authority = f"[{ip}]" if ":" in ip else ip
            # No second DNS lookup: connect to the validated address but verify ChatGPT TLS.
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=5) as client:
                async with client.stream("GET", f"https://{authority}{parsed.path}",
                    headers={"Host": "chatgpt.com", "Accept": "application/json", "Accept-Encoding": "identity"},
                    extensions={"sni_hostname": "chatgpt.com"}) as response:
                    if (response.status_code != 200 or response.headers.get("content-type", "").split(";")[0]
                            != "application/json" or response.headers.get("content-encoding", "identity") != "identity"):
                        raise OAuthError("invalid_client")
                    data = bytearray()
                    async for chunk in response.aiter_raw():
                        data.extend(chunk)
                        if len(data) > 16_384:
                            raise OAuthError("invalid_client")
            return validate_document(json.loads(data), cfg.mcp_oauth_client_id, cfg.mcp_oauth_redirect_uri)
    except (TimeoutError, OSError, ValueError, httpx.HTTPError) as exc:
        raise OAuthError("invalid_client") from exc
