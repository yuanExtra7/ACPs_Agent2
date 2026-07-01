"""FastAPI routes exposing Leader capability."""

from __future__ import annotations

from pydantic import BaseModel, Field
from fastapi import APIRouter

from .leader import run_leader_partner_chat
from .settings import LEADER_AIC

router = APIRouter(prefix="/leader", tags=["leader"])


class LeaderChatRequest(BaseModel):
    partner_rpc_url: str = Field(..., description="Target Partner AIP RPC endpoint URL")
    user_input: str = Field(..., min_length=1, description="Leader start input text")
    continue_input: str = Field("请补充更细节的说明。", description="Used when partner asks for more input")
    conversation_id: str | None = Field(
        default=None,
        description="Optional conversation id for leader memory continuity",
    )
    leader_id: str = Field(default=LEADER_AIC, description="Leader senderId")
    poll_seconds: float = Field(default=1.0, ge=0.1, le=10.0)
    max_polls: int = Field(default=20, ge=1, le=120)
    auto_complete: bool = Field(default=True)


@router.get("/health")
async def leader_health() -> dict[str, str]:
    return {"status": "ok", "leaderAic": LEADER_AIC}


@router.post("/chat")
async def leader_chat(payload: LeaderChatRequest) -> dict[str, object]:
    return await run_leader_partner_chat(
        partner_rpc_url=payload.partner_rpc_url,
        leader_id=payload.leader_id,
        user_input=payload.user_input,
        continue_input=payload.continue_input,
        conversation_id=payload.conversation_id,
        poll_seconds=payload.poll_seconds,
        max_polls=payload.max_polls,
        auto_complete=payload.auto_complete,
    )
