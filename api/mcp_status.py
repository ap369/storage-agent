from fastapi import APIRouter, Header, HTTPException, Request

from api.schemas import MCPServerStatus
from auth import parse_bearer_token, verify_token

router = APIRouter()


def _require_auth(request: Request, authorization: str | None) -> None:
    settings = request.app.state.settings
    if not verify_token(parse_bearer_token(authorization), settings.API_TOKEN):
        raise HTTPException(status_code=401, detail="unauthorized")


@router.get("/mcp/status", response_model=list[MCPServerStatus])
async def get_mcp_status(
    request: Request,
    authorization: str | None = Header(default=None),
) -> list[dict]:
    _require_auth(request, authorization)
    return request.app.state.mcp_status
