"""Small ACPs Discovery API client used by Human/Leader orchestration."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass
class DiscoveryCandidate:
    """Normalized candidate agent selected from Discovery result."""

    aic: str
    name: str
    rpc_url: str
    description: str
    total_candidates: int


def _extract_ranked_aics(result: dict[str, Any]) -> list[str]:
    ranked: list[str] = []
    routes = result.get("routes")
    if isinstance(routes, list):
        for route in routes:
            if not isinstance(route, dict):
                continue
            groups = route.get("agentGroups")
            if not isinstance(groups, list):
                continue
            for group in groups:
                if not isinstance(group, dict):
                    continue
                skills = group.get("agentSkills")
                if not isinstance(skills, list):
                    continue
                for item in skills:
                    if not isinstance(item, dict):
                        continue
                    aic = str(item.get("aic", "")).strip()
                    if aic and aic not in ranked:
                        ranked.append(aic)
    return ranked


def _append_path(base_url: str, path: str) -> str:
    """Append path suffix to URL while preserving query/fragment."""
    parsed = urlsplit(base_url)
    base_path = (parsed.path or "").rstrip("/")
    new_path = f"{base_path}{path}"
    return urlunsplit((parsed.scheme, parsed.netloc, new_path, parsed.query, parsed.fragment))


def _pick_rpc_endpoint(acs: dict[str, Any]) -> str:
    endpoints = acs.get("endPoints")
    if not isinstance(endpoints, list):
        return ""
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue
        url = str(endpoint.get("url", "")).strip()
        transport = str(endpoint.get("transport", "")).strip().upper()
        if not url:
            continue
        if not (url.startswith("http://") or url.startswith("https://")):
            continue
        if transport in {"", "JSONRPC"}:
            return url
        if transport == "HTTP_JSON":
            # Some agents expose base HTTP API endpoints. Try common ACPs AIP RPC routes.
            if url.rstrip("/").endswith(("/rpc", "/aip", "/aip/rpc", "/api/v1/aip/rpc")):
                return url
            candidates = (
                _append_path(url, "/rpc"),
                _append_path(url, "/aip/rpc"),
                _append_path(url, "/api/v1/aip/rpc"),
                url,
            )
            return candidates[0]
        # Unknown transport: keep searching next endpoint.
    return ""


def _discover_once(
    *,
    base_url: str,
    query: str,
    limit: int,
    timeout_seconds: float,
    exclude_aic: str = "",
) -> DiscoveryCandidate | None:
    endpoint = f"{base_url.rstrip('/')}/acps-adp-v2/discover"
    payload = json.dumps({"query": query, "limit": limit}).encode("utf-8")
    request = Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read().decode("utf-8", errors="ignore")
    data = json.loads(raw)
    if not isinstance(data, dict):
        return None
    result = data.get("result")
    if not isinstance(result, dict):
        return None
    acs_map = result.get("acsMap")
    if not isinstance(acs_map, dict):
        return None

    ranked_aics = _extract_ranked_aics(result)
    for aic in acs_map:
        key = str(aic).strip()
        if key and key not in ranked_aics:
            ranked_aics.append(key)

    total_candidates = len(ranked_aics)
    for aic in ranked_aics:
        if exclude_aic and aic == exclude_aic:
            continue
        acs = acs_map.get(aic)
        if not isinstance(acs, dict):
            continue
        rpc_url = _pick_rpc_endpoint(acs)
        if not rpc_url:
            continue
        return DiscoveryCandidate(
            aic=aic,
            name=str(acs.get("name", "")).strip(),
            rpc_url=rpc_url,
            description=str(acs.get("description", "")).strip(),
            total_candidates=total_candidates,
        )
    return None


async def discover_partner_candidate(
    *,
    base_url: str,
    query: str,
    limit: int,
    timeout_seconds: float,
    exclude_aic: str = "",
) -> tuple[DiscoveryCandidate | None, str]:
    """Try Discover API and return one callable candidate plus diagnostics text."""

    try:
        candidate = await asyncio.to_thread(
            _discover_once,
            base_url=base_url,
            query=query,
            limit=limit,
            timeout_seconds=timeout_seconds,
            exclude_aic=exclude_aic,
        )
        if candidate is None:
            return None, "discover returned no callable endpoint"
        return candidate, ""
    except HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="ignore").strip()
        except Exception:
            body = ""
        detail = f"discover http {exc.code}"
        if body:
            detail = f"{detail}: {body[:220]}"
        return None, detail
    except URLError as exc:
        return None, f"discover network error: {exc.reason}"
    except TimeoutError:
        return None, "discover timeout"
    except Exception as exc:
        return None, f"discover error: {exc}"
