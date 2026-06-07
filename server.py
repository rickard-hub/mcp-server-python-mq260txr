from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send
import hmac
import httpx
import logging
import os

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

MCP_API_TOKEN = os.environ.get("MCP_API_TOKEN")
RENDER_EXTERNAL_HOSTNAME = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
ANDFRANKLY_API_TOKEN = os.environ.get("ANDFRANKLY_API_TOKEN")

AF_BASE_URL = "https://data.api.andfrankly.com/v1"

mcp = FastMCP(
    "simployer-employee-surveys",
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=bool(RENDER_EXTERNAL_HOSTNAME),
        allowed_hosts=[RENDER_EXTERNAL_HOSTNAME] if RENDER_EXTERNAL_HOSTNAME else [],
    ),
)


def _af_headers() -> dict:
    if not ANDFRANKLY_API_TOKEN:
        raise ValueError("ANDFRANKLY_API_TOKEN environment variable is not set")
    return {"Authorization": ANDFRANKLY_API_TOKEN}


# ── Resources (semi-static reference data, browseable by LLMs) ───────────────


@mcp.resource("andfrankly://groups")
async def resource_groups() -> str:
    """All currently active groups in the Simployer Employee Surveys account."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{AF_BASE_URL}/groups", headers=_af_headers())
        resp.raise_for_status()
        return resp.text


@mcp.resource("andfrankly://kpis")
async def resource_kpis() -> str:
    """All KPI definitions for which values have ever been created in the account."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{AF_BASE_URL}/kpis", headers=_af_headers())
        resp.raise_for_status()
        return resp.text


# ── Tools ─────────────────────────────────────────────────────────────────────


@mcp.tool()
async def list_groups() -> str:
    """List all currently active groups in the Simployer Employee Surveys account.
    Returns id, name, description, externalId and subgroup ids for each group.
    Use the group id with other tools to fetch KPIs, response rates and results."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{AF_BASE_URL}/groups", headers=_af_headers())
        resp.raise_for_status()
        return resp.text


@mcp.tool()
async def list_kpis() -> str:
    """List all KPI definitions for which values have ever been created in the account.
    Returns KPI id, name and image URL. Use the kpi_id with get_kpi_values."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{AF_BASE_URL}/kpis", headers=_af_headers())
        resp.raise_for_status()
        return resp.text


@mcp.tool()
async def get_kpi_values(
    kpi_id: str,
    group_id: str,
    include_subgroups: bool = False,
    from_date: str = "",
    to_date: str = "",
) -> str:
    """Fetch weekly KPI values (0.0–1.0) for a group. Returns the latest values if no dates given.
    Use list_kpis to get a valid kpi_id and list_groups to get a valid group_id.
    Dates must be in YYYY-MM-DD format and must be provided together (both or neither)."""
    params: dict = {"groupId": group_id, "includeSubgroups": include_subgroups}
    if from_date:
        params["fromDate"] = from_date
    if to_date:
        params["toDate"] = to_date
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{AF_BASE_URL}/kpis/{kpi_id}/values",
            headers=_af_headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.text


@mcp.tool()
async def get_response_rates(
    group_id: str,
    include_subgroups: bool = False,
    from_date: str = "",
    to_date: str = "",
) -> str:
    """Fetch weekly survey response rates (0.0–1.0) for a group. Returns the latest values if no dates given.
    Use list_groups to get a valid group_id.
    Dates must be in YYYY-MM-DD format and must be provided together (both or neither)."""
    params: dict = {"groupId": group_id, "includeSubgroups": include_subgroups}
    if from_date:
        params["fromDate"] = from_date
    if to_date:
        params["toDate"] = to_date
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{AF_BASE_URL}/responserates/values",
            headers=_af_headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.text


@mcp.tool()
async def get_asked_questions(
    group_id: str,
    include_subgroups: bool = False,
    from_date: str = "",
    to_date: str = "",
    language: str = "",
) -> str:
    """Fetch which survey questions were asked to a group (defaults to last 5 weeks).
    Returns question definitions and a map of group → yearweek → question ids.
    Call this before get_results to discover available question ids for a group.
    language: ISO-639-1 code, e.g. 'sv' or 'en'. Dates in YYYY-MM-DD format."""
    params: dict = {"groupId": group_id, "includeSubgroups": include_subgroups}
    if from_date:
        params["fromDate"] = from_date
    if to_date:
        params["toDate"] = to_date
    if language:
        params["language"] = language
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{AF_BASE_URL}/askedquestions",
            headers=_af_headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.text


@mcp.tool()
async def get_results(
    group_id: str,
    question_ids: str = "",
    include_subgroups: bool = False,
    include_comments: bool = False,
    granular_data: bool = False,
    from_date: str = "",
    to_date: str = "",
    language: str = "",
) -> str:
    """Fetch survey result data for a group (defaults to last 5 weeks).
    Returns question definitions and result values per question/group/week.
    question_ids: comma-separated question ids, e.g. '98,144'. Use get_asked_questions to discover valid ids.
    granular_data: return individual answers instead of aggregated averages where the question type supports it.
    include_comments: include free-text comments submitted with answers.
    language: ISO-639-1 code, e.g. 'sv' or 'en'. Dates in YYYY-MM-DD format."""
    params: dict = {
        "groupId": group_id,
        "includeSubgroups": include_subgroups,
        "includeComments": include_comments,
        "granularData": granular_data,
    }
    if question_ids:
        params["questionIds"] = question_ids
    if from_date:
        params["fromDate"] = from_date
    if to_date:
        params["toDate"] = to_date
    if language:
        params["language"] = language
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{AF_BASE_URL}/results",
            headers=_af_headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.text


# custom_route bypasses auth — use only for public endpoints
@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> Response:
    return JSONResponse({"status": "ok"})


# Simple bearer token auth. For multi-user or production setups,
# consider upgrading to the MCP SDK's built-in OAuth 2.1 support.
class BearerAuthMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or scope["path"] == "/health":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        auth = headers.get(b"authorization", b"").decode()
        logger.debug("AUTH path=%s header=%r", scope["path"], auth)
        # constant-time comparison to prevent timing side-channel attacks
        if hmac.compare_digest(auth, f"Bearer {MCP_API_TOKEN}"):
            await self.app(scope, receive, send)
            return

        response = JSONResponse(
            {
                "jsonrpc": "2.0",
                "error": {"code": -32001, "message": "Unauthorized"},
                "id": None,
            },
            status_code=401,
        )
        await response(scope, receive, send)


def create_app():
    app = mcp.streamable_http_app()
    # When no token is set (local dev), auth is disabled entirely
    if MCP_API_TOKEN:
        app.add_middleware(BearerAuthMiddleware)
    return app


if __name__ == "__main__":
    import uvicorn

    if not MCP_API_TOKEN:
        print(
            "WARNING: MCP_API_TOKEN is not set."
            " The server is running without authentication."
        )

    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(create_app(), host="0.0.0.0", port=port)
