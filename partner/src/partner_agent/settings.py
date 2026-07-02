"""Centralized runtime settings for the Partner service."""

from __future__ import annotations

import os

PARTNER_AIC = os.getenv("PARTNER_AIC", "edu.ustb.agent.partner.chat.v1")  # Partner identity published to peers
APP_TITLE = os.getenv("PARTNER_APP_TITLE", "USTB Text Chat Partner")
RPC_PATH = os.getenv("PARTNER_RPC_PATH", "/rpc")  # JSON-RPC endpoint path
LEADER_AIC = os.getenv("LEADER_AIC", "edu.ustb.agent.leader.chat.v1")  # Sender ID used by embedded Leader

DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "").strip()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()
DEEPSEEK_TIMEOUT_SECONDS = float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "30"))  # Single model call timeout
POSTPROCESS_WITH_LLM = os.getenv("POSTPROCESS_WITH_LLM", "0").strip().lower() in {"1", "true", "yes", "on"}
MEMORY_MAX_TURNS = int(os.getenv("MEMORY_MAX_TURNS", "30"))  # Max in-memory turns per key
LEADER_POLL_SECONDS = float(os.getenv("LEADER_POLL_SECONDS", "0.4"))
LEADER_MAX_POLLS = int(os.getenv("LEADER_MAX_POLLS", "8"))
LEADER_CALL_TIMEOUT_SECONDS = float(os.getenv("LEADER_CALL_TIMEOUT_SECONDS", "30"))  # RPC timeout per Leader call
HUMAN_TOTAL_BUDGET_SECONDS = float(os.getenv("HUMAN_TOTAL_BUDGET_SECONDS", "30"))  # End-to-end budget per /human/chat
HUMAN_FORCE_REMOTE_COLLAB = os.getenv("HUMAN_FORCE_REMOTE_COLLAB", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
AUTO_DISCOVERY_ENABLED = os.getenv("AUTO_DISCOVERY_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
ACPS_DISCOVERY_BASE_URL = os.getenv("ACPS_DISCOVERY_BASE_URL", "https://ioa.pub/discovery").strip()
ACPS_DISCOVERY_TIMEOUT_SECONDS = float(os.getenv("ACPS_DISCOVERY_TIMEOUT_SECONDS", "10"))
ACPS_DISCOVERY_LIMIT = int(os.getenv("ACPS_DISCOVERY_LIMIT", "10"))
ACPS_DISCOVERY_EXCLUDE_SELF = os.getenv("ACPS_DISCOVERY_EXCLUDE_SELF", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}

