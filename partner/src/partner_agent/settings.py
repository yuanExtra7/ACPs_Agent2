"""Runtime settings for Partner agent."""

from __future__ import annotations

import os

PARTNER_AIC = os.getenv("PARTNER_AIC", "edu.ustb.agent.partner.chat.v1")
APP_TITLE = os.getenv("PARTNER_APP_TITLE", "USTB Text Chat Partner")
RPC_PATH = os.getenv("PARTNER_RPC_PATH", "/rpc")
LEADER_AIC = os.getenv("LEADER_AIC", "edu.ustb.agent.leader.chat.v1")

DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "").strip()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()
DEEPSEEK_TIMEOUT_SECONDS = float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "30"))
MEMORY_MAX_TURNS = int(os.getenv("MEMORY_MAX_TURNS", "30"))
LEADER_POLL_SECONDS = float(os.getenv("LEADER_POLL_SECONDS", "0.4"))
LEADER_MAX_POLLS = int(os.getenv("LEADER_MAX_POLLS", "8"))
LEADER_CALL_TIMEOUT_SECONDS = float(os.getenv("LEADER_CALL_TIMEOUT_SECONDS", "15"))
HUMAN_TOTAL_BUDGET_SECONDS = float(os.getenv("HUMAN_TOTAL_BUDGET_SECONDS", "20"))

