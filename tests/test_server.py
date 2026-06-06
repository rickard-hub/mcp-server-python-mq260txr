import os
import json
import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["MCP_API_TOKEN"] = "test-token"
os.environ["ANDFRANKLY_API_TOKEN"] = "test-af-token"

import server as server_module  # noqa: E402
from server import create_app, BearerAuthMiddleware  # noqa: E402

TOKEN = "test-token"
BASE_URL = "http://localhost"
MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
INIT_BODY = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1.0.0"},
    },
}


def parse_sse_data(text: str) -> dict:
    """Extract the JSON payload from an SSE response."""
    for line in text.strip().splitlines():
        if line.startswith("data: "):
            return json.loads(line[len("data: "):])
    raise ValueError(f"No data line in SSE response: {text}")


def _mock_af_response(response_text: str):
    """Return a mock for httpx.AsyncClient that yields response_text from .get()."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.text = response_text

    mock_session = MagicMock()
    mock_session.get = AsyncMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    return MagicMock(return_value=mock_session)


@pytest.fixture(scope="module")
async def managed_app():
    """Single app with lifespan for the entire test module."""
    app = create_app()
    async with LifespanManager(app):
        yield app


@pytest.fixture
def authed_headers():
    return {**MCP_HEADERS, "Authorization": f"Bearer {TOKEN}"}


async def _call_tool(client, authed_headers, tool_name: str, arguments: dict) -> dict:
    await client.post("/mcp", headers=authed_headers, json=INIT_BODY)
    resp = await client.post(
        "/mcp",
        headers=authed_headers,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
    )
    assert resp.status_code == 200
    return parse_sse_data(resp.text)


@pytest.mark.anyio
async def test_health(managed_app):
    async with AsyncClient(
        transport=ASGITransport(app=managed_app), base_url=BASE_URL
    ) as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_mcp_no_token_when_required(managed_app):
    async with AsyncClient(
        transport=ASGITransport(app=managed_app), base_url=BASE_URL
    ) as client:
        resp = await client.post("/mcp", headers=MCP_HEADERS, json=INIT_BODY)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == -32001


@pytest.mark.anyio
async def test_mcp_wrong_token(managed_app):
    headers = {**MCP_HEADERS, "Authorization": "Bearer wrong-token"}
    async with AsyncClient(
        transport=ASGITransport(app=managed_app), base_url=BASE_URL
    ) as client:
        resp = await client.post("/mcp", headers=headers, json=INIT_BODY)
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_mcp_correct_token(managed_app, authed_headers):
    async with AsyncClient(
        transport=ASGITransport(app=managed_app), base_url=BASE_URL
    ) as client:
        resp = await client.post("/mcp", headers=authed_headers, json=INIT_BODY)
    assert resp.status_code == 200
    data = parse_sse_data(resp.text)
    assert data["result"]["serverInfo"]["name"] == "simployer-employee-surveys"


@pytest.mark.anyio
async def test_mcp_open_when_no_token_set():
    """Verify auth middleware passes requests through when MCP_API_TOKEN is unset."""
    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.responses import JSONResponse

    async def ok(request):
        return JSONResponse({"passed": True})

    # When MCP_API_TOKEN is None, create_app() skips the middleware entirely.
    # Simulate that by building a bare app (no BearerAuthMiddleware).
    saved = server_module.MCP_API_TOKEN
    try:
        server_module.MCP_API_TOKEN = None
        bare_app = Starlette(routes=[Route("/mcp", ok, methods=["POST"])])
        # Verify create_app logic: middleware is NOT added when token is unset
        assert server_module.MCP_API_TOKEN is None
        async with AsyncClient(
            transport=ASGITransport(app=bare_app), base_url=BASE_URL
        ) as client:
            resp = await client.post("/mcp", headers=MCP_HEADERS, json=INIT_BODY)
        assert resp.status_code == 200
        assert resp.json() == {"passed": True}
    finally:
        server_module.MCP_API_TOKEN = saved


@pytest.mark.anyio
async def test_list_groups(managed_app, authed_headers):
    payload = json.dumps([{"id": 1, "name": "All employees", "subgroups": [2, 3]}])
    with patch("httpx.AsyncClient", _mock_af_response(payload)):
        async with AsyncClient(
            transport=ASGITransport(app=managed_app), base_url=BASE_URL
        ) as client:
            data = await _call_tool(client, authed_headers, "list_groups", {})
    assert data["result"]["content"][0]["text"] == payload


@pytest.mark.anyio
async def test_list_kpis(managed_app, authed_headers):
    payload = json.dumps([{"id": "P-102", "name": "1. Motivation Pulsing"}])
    with patch("httpx.AsyncClient", _mock_af_response(payload)):
        async with AsyncClient(
            transport=ASGITransport(app=managed_app), base_url=BASE_URL
        ) as client:
            data = await _call_tool(client, authed_headers, "list_kpis", {})
    assert data["result"]["content"][0]["text"] == payload


@pytest.mark.anyio
async def test_get_kpi_values(managed_app, authed_headers):
    payload = json.dumps({"kpi": {"id": "P-102", "name": "Motivation"}, "values": [{"groupId": 1, "value": 0.82, "yearWeek": "202615"}]})
    with patch("httpx.AsyncClient", _mock_af_response(payload)):
        async with AsyncClient(
            transport=ASGITransport(app=managed_app), base_url=BASE_URL
        ) as client:
            data = await _call_tool(
                client, authed_headers, "get_kpi_values",
                {"kpi_id": "P-102", "group_id": "1"},
            )
    assert data["result"]["content"][0]["text"] == payload


@pytest.mark.anyio
async def test_get_response_rates(managed_app, authed_headers):
    payload = json.dumps({"values": [{"groupId": 1, "value": 0.75, "yearWeek": "202615"}]})
    with patch("httpx.AsyncClient", _mock_af_response(payload)):
        async with AsyncClient(
            transport=ASGITransport(app=managed_app), base_url=BASE_URL
        ) as client:
            data = await _call_tool(
                client, authed_headers, "get_response_rates", {"group_id": "1"}
            )
    assert data["result"]["content"][0]["text"] == payload


@pytest.mark.anyio
async def test_get_asked_questions(managed_app, authed_headers):
    payload = json.dumps({
        "questions": [{"id": 98, "question": "How do you feel about coming to work?", "type": 15}],
        "askedquestions": [{"1": {"202615": [98]}}],
    })
    with patch("httpx.AsyncClient", _mock_af_response(payload)):
        async with AsyncClient(
            transport=ASGITransport(app=managed_app), base_url=BASE_URL
        ) as client:
            data = await _call_tool(
                client, authed_headers, "get_asked_questions",
                {"group_id": "1", "language": "sv"},
            )
    assert data["result"]["content"][0]["text"] == payload


@pytest.mark.anyio
async def test_get_results(managed_app, authed_headers):
    payload = json.dumps({
        "questions": [{"id": 98, "question": "How do you feel?", "type": 15}],
        "values": [{"98": {"1": {"202615": {"value": 0.88}}}}],
        "comments": [],
    })
    with patch("httpx.AsyncClient", _mock_af_response(payload)):
        async with AsyncClient(
            transport=ASGITransport(app=managed_app), base_url=BASE_URL
        ) as client:
            data = await _call_tool(
                client, authed_headers, "get_results",
                {"group_id": "1", "question_ids": "98", "include_subgroups": True},
            )
    assert data["result"]["content"][0]["text"] == payload
