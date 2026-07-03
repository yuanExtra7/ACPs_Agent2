from __future__ import annotations

import asyncio

from partner_agent import chat_service


class _FakeBrain:
    def __init__(self, *, enabled: bool, output: str) -> None:
        self._enabled = enabled
        self._output = output

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def chat(
        self,
        user_text: str,
        history: list[dict[str, str]] | None = None,
        system_prompt: str | None = None,
        timeout_seconds: float | None = None,
    ) -> str:
        return self._output


class _RaisingBrain:
    @property
    def enabled(self) -> bool:
        return True

    async def chat(
        self,
        user_text: str,
        history: list[dict[str, str]] | None = None,
        system_prompt: str | None = None,
        timeout_seconds: float | None = None,
    ) -> str:
        raise RuntimeError("401 Unauthorized")


def test_postprocess_keeps_partner_text_when_valid_proof_but_llm_claims_no_evidence(monkeypatch) -> None:
    monkeypatch.setattr(
        chat_service,
        "_BRAIN",
        _FakeBrain(enabled=True, output="当前未得到有效远端响应证据。"),
    )
    result = asyncio.run(
        chat_service.postprocess_collaboration_result(
            user_request="请总结英伟达股价",
            partner_response="英伟达近期股价走势偏强，波动较大。",
            leader_result={"final_state": "completed"},
            call_proof={"invoked": True, "trace_steps": ["start", "get"]},
            conversation_key=None,
        )
    )
    assert result == "英伟达近期股价走势偏强，波动较大。"


def test_postprocess_returns_status_text_directly_when_still_working(monkeypatch) -> None:
    monkeypatch.setattr(
        chat_service,
        "_BRAIN",
        _FakeBrain(enabled=True, output="当前未得到有效远端响应证据。"),
    )
    status_text = "协作已执行，当前任务状态：working"
    result = asyncio.run(
        chat_service.postprocess_collaboration_result(
            user_request="请继续",
            partner_response=status_text,
            leader_result={"final_state": "working"},
            call_proof={"invoked": True, "trace_steps": ["start"]},
            conversation_key=None,
        )
    )
    assert result == status_text


def test_build_chat_answer_does_not_echo_user_input_when_brain_disabled(monkeypatch) -> None:
    monkeypatch.setattr(chat_service, "_BRAIN", _FakeBrain(enabled=False, output="unused"))
    answer = asyncio.run(chat_service.build_chat_answer("xyz", conversation_key=None))
    assert "已收到文本咨询" not in answer
    assert "模型暂不可用" in answer


def test_build_chat_answer_surfaces_llm_error_reason_when_chat_fails(monkeypatch) -> None:
    monkeypatch.setattr(chat_service, "_BRAIN", _RaisingBrain())
    answer = asyncio.run(chat_service.build_chat_answer("xyz", conversation_key=None))
    assert "LLM调用失败" in answer
    assert "401 Unauthorized" in answer
