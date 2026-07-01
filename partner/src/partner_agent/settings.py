"""Runtime settings for Partner agent."""

from __future__ import annotations

import os

PARTNER_AIC = os.getenv("PARTNER_AIC", "edu.ustb.agent.partner.chat.v1")
APP_TITLE = os.getenv("PARTNER_APP_TITLE", "USTB Text Chat Partner")
RPC_PATH = os.getenv("PARTNER_RPC_PATH", "/rpc")

DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "").strip()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()
DEEPSEEK_TIMEOUT_SECONDS = float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "30"))

