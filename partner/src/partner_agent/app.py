"""FastAPI application entrypoint for Partner, Leader, and Human routes."""

from __future__ import annotations

from acps_sdk.aip.aip_rpc_server import (
    CommandHandlers,
    DefaultHandlers,
    add_aip_rpc_router,
)
from fastapi import FastAPI

from .handlers import on_complete, on_continue, on_start
from .human_api import router as human_router
from .leader_api import router as leader_router
from .settings import APP_TITLE, PARTNER_AIC, RPC_PATH

app = FastAPI(title=APP_TITLE)

handlers = CommandHandlers(
    on_start=on_start,
    on_get=DefaultHandlers.get,
    on_cancel=DefaultHandlers.cancel,
    on_complete=on_complete,
    on_continue=on_continue,
)

add_aip_rpc_router(app, RPC_PATH, handlers)
app.include_router(leader_router)
app.include_router(human_router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Expose service liveness and current Partner identity."""
    return {"status": "ok", "aic": PARTNER_AIC}

