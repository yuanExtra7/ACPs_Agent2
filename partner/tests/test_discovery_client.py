from __future__ import annotations

from partner_agent import discovery_client


def test_pick_rpc_endpoint_accepts_jsonrpc_url() -> None:
    acs = {
        "endPoints": [
            {"url": "https://example.com/rpc", "transport": "JSONRPC"},
        ]
    }
    assert discovery_client._pick_rpc_endpoint(acs) == "https://example.com/rpc"


def test_pick_rpc_endpoint_derives_from_http_json_base() -> None:
    acs = {
        "endPoints": [
            {"url": "https://lab.ioa.pub:59002", "transport": "HTTP_JSON"},
        ]
    }
    assert discovery_client._pick_rpc_endpoint(acs) == "https://lab.ioa.pub:59002/rpc"


def test_pick_rpc_endpoint_keeps_existing_aip_path_for_http_json() -> None:
    acs = {
        "endPoints": [
            {"url": "https://www.ioa.pub/api/finance/aip", "transport": "HTTP_JSON"},
        ]
    }
    assert discovery_client._pick_rpc_endpoint(acs) == "https://www.ioa.pub/api/finance/aip"
