from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from partner_agent import human_api
from partner_agent.app import app
from partner_agent.memory import SESSION_STATES


def _reset_session(session_id: str) -> None:
    SESSION_STATES.clear(session_id)


async def _always_relevant(**kwargs) -> bool:
    return True


async def _passthrough_postprocess(**kwargs) -> str:
    return kwargs["partner_response"]


@pytest.fixture(autouse=True)
def _mock_leader_complete(monkeypatch):
    async def _fake_complete(**kwargs):
        return {
            "final_state": "completed",
            "task_id": kwargs["task_id"],
            "partner_sender_id": "partner-a",
            "binding_ok": True,
            "product_texts": [],
            "status_texts": [],
            "trace": [{"step": "complete"}],
            "call_proof": {"invoked": True, "trace_steps": ["complete"]},
        }

    monkeypatch.setattr(human_api, "leader_complete_task", _fake_complete)


@pytest.fixture(autouse=True)
def _disable_auto_discovery_by_default(monkeypatch):
    monkeypatch.setattr(human_api, "AUTO_DISCOVERY_ENABLED", False)


@pytest.fixture(autouse=True)
def _disable_force_remote_by_default(monkeypatch):
    monkeypatch.setattr(human_api, "HUMAN_FORCE_REMOTE_COLLAB", False)


def test_human_chat_page_available() -> None:
    client = TestClient(app)
    response = client.get("/human")
    assert response.status_code == 200
    assert "ACPs 智能体助手" in response.text
    assert "POST /human/chat" in response.text


def test_human_chat_returns_answer(monkeypatch) -> None:
    _reset_session("human-session-1")

    async def _fake_decision(
        _: str,
        *,
        router_history_key: str,
        candidate_rpc_url: str | None,
        has_active_task: bool,
        active_task_state: str,
    ):
        assert router_history_key == "human-router:human-session-1"
        assert candidate_rpc_url is None
        assert has_active_task is False
        assert active_task_state == ""
        return {"action": "local_reply"}

    async def _fake_answer(_: str, *, conversation_key: str | None = None) -> str:
        assert conversation_key == "human-chat:human-session-1"
        return "这是测试回复"

    monkeypatch.setattr(human_api, "decide_human_action", _fake_decision)
    monkeypatch.setattr(human_api, "build_chat_answer", _fake_answer)

    client = TestClient(app)
    response = client.post("/human/chat", json={"text": "你好", "session_id": "human-session-1"})
    assert response.status_code == 200
    data = response.json()
    assert data["answer"] == "这是测试回复"
    assert data["mode"] == "local-chat"
    assert data["sessionId"] == "human-session-1"
    assert data["memoryTurns"] >= 0
    assert data["collaboration"]["taskId"] == ""


def test_human_chat_with_rpc_url_calls_leader(monkeypatch) -> None:
    _reset_session("human-session-2")

    async def _fake_decision(
        _: str,
        *,
        router_history_key: str,
        candidate_rpc_url: str | None,
        has_active_task: bool,
        active_task_state: str,
    ):
        assert candidate_rpc_url == "http://127.0.0.1:5000/rpc"
        assert has_active_task is False
        return {"action": "call_start", "query": "请调用远端"}

    async def _fake_start(**kwargs):
        assert kwargs["partner_rpc_url"] == "http://127.0.0.1:5000/rpc"
        assert kwargs["session_id"].startswith("aip-")
        return {
            "final_state": "awaiting-completion",
            "task_id": "task-1",
            "partner_sender_id": "partner-a",
            "product_texts": ["远端回复"],
            "status_texts": [],
            "trace": [{"step": "start"}],
            "call_proof": {"invoked": True, "trace_steps": ["start"]},
        }

    monkeypatch.setattr(human_api, "decide_human_action", _fake_decision)
    monkeypatch.setattr(human_api, "is_partner_response_relevant", _always_relevant)
    monkeypatch.setattr(human_api, "leader_start_task", _fake_start)
    monkeypatch.setattr(human_api, "postprocess_collaboration_result", _passthrough_postprocess)

    client = TestClient(app)
    response = client.post(
        "/human/chat",
        json={
            "text": "请调用远端",
            "session_id": "human-session-2",
            "rpc_url": "http://127.0.0.1:5000/rpc",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "leader-proxy"
    assert data["answer"] == "远端回复"
    assert data["collaboration"]["effectiveRpcUrl"] == "http://127.0.0.1:5000/rpc"
    assert data["collaboration"]["taskId"] == ""
    assert data["collaboration"]["state"] == "completed"


def test_human_chat_auto_extract_rpc_url_and_sticky(monkeypatch) -> None:
    _reset_session("s-3")
    calls: list[tuple[str, str]] = []

    async def _fake_decision(
        _: str,
        *,
        router_history_key: str,
        candidate_rpc_url: str | None,
        has_active_task: bool,
        active_task_state: str,
    ):
        if not has_active_task:
            assert candidate_rpc_url == "http://10.0.0.8:5000/rpc"
            return {"action": "call_start", "query": "请总结能力"}
        assert active_task_state == "awaiting-input"
        return {"action": "call_continue", "query": "继续补充"}

    async def _fake_start(**kwargs):
        calls.append(("start", kwargs["partner_rpc_url"]))
        return {
            "final_state": "awaiting-input",
            "task_id": "task-sticky",
            "partner_sender_id": "partner-a",
            "product_texts": [],
            "status_texts": ["请继续补充信息"],
            "trace": [{"step": "start"}],
            "call_proof": {"invoked": True, "trace_steps": ["start"]},
        }

    async def _fake_continue(**kwargs):
        calls.append(("continue", kwargs["partner_rpc_url"]))
        assert kwargs["task_id"] == "task-sticky"
        return {
            "final_state": "awaiting-completion",
            "task_id": "task-sticky",
            "partner_sender_id": "partner-a",
            "product_texts": ["自动提取URL调用成功"],
            "status_texts": [],
            "trace": [{"step": "continue"}],
            "call_proof": {"invoked": True, "trace_steps": ["continue"]},
        }

    async def _fake_get(**kwargs):
        return {
            "final_state": "awaiting-input",
            "task_id": "task-sticky",
            "partner_sender_id": "partner-a",
            "binding_ok": True,
            "product_texts": [],
            "status_texts": ["请继续补充信息"],
            "trace": [{"step": "get"}],
            "call_proof": {"invoked": True, "trace_steps": ["get"]},
        }

    monkeypatch.setattr(human_api, "decide_human_action", _fake_decision)
    monkeypatch.setattr(human_api, "leader_start_task", _fake_start)
    monkeypatch.setattr(human_api, "leader_continue_task", _fake_continue)
    monkeypatch.setattr(human_api, "leader_get_task", _fake_get)
    monkeypatch.setattr(human_api, "is_partner_response_relevant", _always_relevant)
    monkeypatch.setattr(human_api, "postprocess_collaboration_result", _passthrough_postprocess)

    client = TestClient(app)
    response1 = client.post(
        "/human/chat",
        json={"text": "请调用 http://10.0.0.8:5000/rpc 请总结能力", "session_id": "s-3"},
    )
    assert response1.status_code == 200
    data1 = response1.json()
    assert data1["mode"] == "leader-proxy"
    assert data1["collaboration"]["taskId"] == "task-sticky"

    response2 = client.post(
        "/human/chat",
        json={"text": "继续", "session_id": "s-3"},
    )
    assert response2.status_code == 200
    data2 = response2.json()
    assert data2["mode"] == "leader-proxy"
    assert data2["answer"] == "自动提取URL调用成功"
    assert calls == [("start", "http://10.0.0.8:5000/rpc"), ("continue", "http://10.0.0.8:5000/rpc")]


def test_human_chat_rpc_intent_without_url_returns_hint(monkeypatch) -> None:
    _reset_session("s-4")

    async def _fake_decision(
        _: str,
        *,
        router_history_key: str,
        candidate_rpc_url: str | None,
        has_active_task: bool,
        active_task_state: str,
    ):
        return {"action": "need_rpc_url"}

    monkeypatch.setattr(human_api, "decide_human_action", _fake_decision)

    client = TestClient(app)
    response = client.post(
        "/human/chat",
        json={"text": "请帮我调用RPC地址的智能体", "session_id": "s-4"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "rpc-missing-url"
    assert "请提供可访问的 RPC 地址" in data["answer"]


def test_human_chat_auto_discovery_calls_remote(monkeypatch) -> None:
    _reset_session("auto-discover-1")
    monkeypatch.setattr(human_api, "AUTO_DISCOVERY_ENABLED", True)

    async def _fake_decision(
        _: str,
        *,
        router_history_key: str,
        candidate_rpc_url: str | None,
        has_active_task: bool,
        active_task_state: str,
    ):
        assert candidate_rpc_url is None
        return {"action": "need_rpc_url", "query": "文本咨询伙伴智能体"}

    async def _fake_auto_discovery(*, text: str, decision_query: str, state):
        assert "文本咨询伙伴智能体" in decision_query
        state.discovered_partner_aic = "aic-discovered"
        state.discovered_partner_name = "发现到的智能体"
        state.discovery_query = decision_query
        state.discovery_total_candidates = 3
        state.discovery_error = ""
        return "http://127.0.0.1:5001/rpc", ""

    async def _fake_start(**kwargs):
        assert kwargs["partner_rpc_url"] == "http://127.0.0.1:5001/rpc"
        return {
            "final_state": "awaiting-completion",
            "task_id": "task-auto-1",
            "partner_sender_id": "aic-discovered",
            "binding_ok": True,
            "product_texts": ["自动发现后调用成功"],
            "status_texts": [],
            "trace": [{"step": "start"}],
            "call_proof": {"invoked": True, "trace_steps": ["start"]},
        }

    monkeypatch.setattr(human_api, "decide_human_action", _fake_decision)
    monkeypatch.setattr(human_api, "_try_auto_discovery", _fake_auto_discovery)
    monkeypatch.setattr(human_api, "leader_start_task", _fake_start)
    monkeypatch.setattr(human_api, "is_partner_response_relevant", _always_relevant)
    monkeypatch.setattr(human_api, "postprocess_collaboration_result", _passthrough_postprocess)

    client = TestClient(app)
    response = client.post(
        "/human/chat",
        json={"text": "帮我自动发现并调用", "session_id": "auto-discover-1"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "leader-proxy"
    assert data["answer"] == "自动发现后调用成功"
    assert data["collaboration"]["effectiveRpcUrl"] == "http://127.0.0.1:5001/rpc"
    assert data["collaboration"]["discoveredAgentAic"] == "aic-discovered"
    assert data["collaboration"]["discoveredAgentName"] == "发现到的智能体"


def test_manual_sidebar_rpc_forces_remote_call(monkeypatch) -> None:
    _reset_session("manual-rpc-1")

    async def _fake_decision(
        _: str,
        *,
        router_history_key: str,
        candidate_rpc_url: str | None,
        has_active_task: bool,
        active_task_state: str,
    ):
        # Even if router says local, manual sidebar RPC should force call_start.
        return {"action": "local_reply", "query": "请协作回答"}

    async def _fake_start(**kwargs):
        assert kwargs["partner_rpc_url"] == "https://www.ioa.pub/api/finance/aip"
        return {
            "final_state": "completed",
            "task_id": "task-manual-1",
            "partner_sender_id": "finance-aic",
            "binding_ok": True,
            "product_texts": ["手填地址强制协作成功"],
            "status_texts": [],
            "trace": [{"step": "start"}],
            "call_proof": {"invoked": True, "trace_steps": ["start"]},
        }

    monkeypatch.setattr(human_api, "decide_human_action", _fake_decision)
    monkeypatch.setattr(human_api, "leader_start_task", _fake_start)
    monkeypatch.setattr(human_api, "is_partner_response_relevant", _always_relevant)
    monkeypatch.setattr(human_api, "postprocess_collaboration_result", _passthrough_postprocess)

    client = TestClient(app)
    response = client.post(
        "/human/chat",
        json={
            "text": "请帮我协作回答英伟达股价",
            "session_id": "manual-rpc-1",
            "rpc_url": "https://www.ioa.pub/api/finance/aip",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "leader-proxy"
    assert data["answer"] == "手填地址强制协作成功"


def test_force_remote_collaboration_always_calls_start(monkeypatch) -> None:
    _reset_session("force-remote-1")
    monkeypatch.setattr(human_api, "HUMAN_FORCE_REMOTE_COLLAB", True)
    monkeypatch.setattr(human_api, "AUTO_DISCOVERY_ENABLED", True)

    starts: list[str] = []

    async def _fake_decision(
        _: str,
        *,
        router_history_key: str,
        candidate_rpc_url: str | None,
        has_active_task: bool,
        active_task_state: str,
    ):
        return {"action": "local_reply"}

    async def _fake_auto_discovery(*, text: str, decision_query: str, state):
        state.discovered_partner_aic = "aic-remote-force"
        state.discovered_partner_name = "远端智能体B"
        state.discovery_query = decision_query or text
        state.discovery_total_candidates = 1
        state.discovery_error = ""
        return "https://example.com/rpc", ""

    async def _fake_start(**kwargs):
        starts.append(str(kwargs["user_input"]))
        return {
            "final_state": "completed",
            "task_id": f"task-{len(starts)}",
            "partner_sender_id": "aic-remote-force",
            "binding_ok": True,
            "product_texts": [f"远端回复{len(starts)}"],
            "status_texts": [],
            "trace": [{"step": "start"}],
            "call_proof": {"invoked": True, "trace_steps": ["start"]},
        }

    monkeypatch.setattr(human_api, "decide_human_action", _fake_decision)
    monkeypatch.setattr(human_api, "_try_auto_discovery", _fake_auto_discovery)
    monkeypatch.setattr(human_api, "leader_start_task", _fake_start)
    monkeypatch.setattr(human_api, "is_partner_response_relevant", _always_relevant)
    monkeypatch.setattr(human_api, "postprocess_collaboration_result", _passthrough_postprocess)

    client = TestClient(app)
    r1 = client.post("/human/chat", json={"text": "第一问", "session_id": "force-remote-1"})
    r2 = client.post("/human/chat", json={"text": "第二问", "session_id": "force-remote-1"})

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["mode"] == "leader-proxy"
    assert r2.json()["mode"] == "leader-proxy"
    assert r1.json()["answer"] == "远端回复1"
    assert r2.json()["answer"] == "远端回复2"
    assert len(starts) == 2


def test_local_reply_hides_stale_remote_runtime_info(monkeypatch) -> None:
    SESSION_STATES.update(
        "local-clear-1",
        rpc_url="https://www.ioa.pub/api/finance/aip",
        aip_session_id="aip-local-clear",
        active_task_id="",
        last_state="completed",
        partner_sender_id="server",
    )

    async def _fake_decision(
        _: str,
        *,
        router_history_key: str,
        candidate_rpc_url: str | None,
        has_active_task: bool,
        active_task_state: str,
    ):
        return {"action": "local_reply"}

    async def _fake_answer(_: str, *, conversation_key: str | None = None) -> str:
        return "这是本地回复"

    monkeypatch.setattr(human_api, "decide_human_action", _fake_decision)
    monkeypatch.setattr(human_api, "build_chat_answer", _fake_answer)

    client = TestClient(app)
    response = client.post("/human/chat", json={"text": "你好", "session_id": "local-clear-1"})
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "local-chat"
    assert data["collaboration"]["effectiveRpcUrl"] == ""
    assert data["collaboration"]["state"] == ""


def test_force_collaboration_intent_overrides_local_reply(monkeypatch) -> None:
    _reset_session("force-collab-1")
    monkeypatch.setattr(human_api, "AUTO_DISCOVERY_ENABLED", True)

    async def _fake_decision(
        _: str,
        *,
        router_history_key: str,
        candidate_rpc_url: str | None,
        has_active_task: bool,
        active_task_state: str,
    ):
        return {"action": "local_reply"}

    async def _fake_auto_discovery(*, text: str, decision_query: str, state):
        state.discovered_partner_aic = "aic-remote-1"
        state.discovered_partner_name = "远端智能体A"
        state.discovery_query = decision_query
        state.discovery_total_candidates = 2
        state.discovery_error = ""
        return "https://example.com/rpc", ""

    async def _fake_start(**kwargs):
        assert kwargs["partner_rpc_url"] == "https://example.com/rpc"
        return {
            "final_state": "completed",
            "task_id": "task-force-1",
            "partner_sender_id": "aic-remote-1",
            "binding_ok": True,
            "product_texts": ["远端已返回结果"],
            "status_texts": [],
            "trace": [{"step": "start"}],
            "call_proof": {"invoked": True, "trace_steps": ["start"]},
        }

    monkeypatch.setattr(human_api, "decide_human_action", _fake_decision)
    monkeypatch.setattr(human_api, "_try_auto_discovery", _fake_auto_discovery)
    monkeypatch.setattr(human_api, "leader_start_task", _fake_start)
    monkeypatch.setattr(human_api, "is_partner_response_relevant", _always_relevant)
    monkeypatch.setattr(human_api, "postprocess_collaboration_result", _passthrough_postprocess)

    client = TestClient(app)
    resp = client.post(
        "/human/chat",
        json={"text": "现在我希望你和其它智能体合作介绍一下CSGO", "session_id": "force-collab-1"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "leader-proxy"
    assert data["answer"] == "远端已返回结果"


def test_state_guard_rebuilds_invalid_active_task(monkeypatch) -> None:
    SESSION_STATES.update(
        "guard-1",
        rpc_url="http://127.0.0.1:5000/rpc",
        aip_session_id="aip-guard-1",
        active_task_id="task-invalid",
        last_state="invalid-state",
    )

    async def _fake_decision(
        _: str,
        *,
        router_history_key: str,
        candidate_rpc_url: str | None,
        has_active_task: bool,
        active_task_state: str,
    ):
        assert has_active_task is False
        assert active_task_state == ""
        return {"action": "local_reply"}

    async def _fake_answer(_: str, *, conversation_key: str | None = None) -> str:
        return "重建后本地回复"

    monkeypatch.setattr(human_api, "decide_human_action", _fake_decision)
    monkeypatch.setattr(human_api, "build_chat_answer", _fake_answer)

    client = TestClient(app)
    response = client.post("/human/chat", json={"text": "你好", "session_id": "guard-1"})
    assert response.status_code == 200
    assert response.json()["answer"] == "重建后本地回复"


def test_binding_mismatch_returns_leader_error(monkeypatch) -> None:
    _reset_session("bind-1")

    async def _fake_decision(
        _: str,
        *,
        router_history_key: str,
        candidate_rpc_url: str | None,
        has_active_task: bool,
        active_task_state: str,
    ):
        return {"action": "call_start", "query": "发起协作"}

    async def _fake_start(**kwargs):
        return {
            "final_state": "awaiting-input",
            "task_id": "task-a",
            "actual_task_id": "task-b",
            "actual_session_id": kwargs["session_id"],
            "binding_ok": False,
            "partner_sender_id": "partner-a",
            "product_texts": [],
            "status_texts": ["需要补充"],
            "trace": [{"step": "start"}],
            "call_proof": {"invoked": True, "trace_steps": ["start"]},
        }

    monkeypatch.setattr(human_api, "decide_human_action", _fake_decision)
    monkeypatch.setattr(human_api, "is_partner_response_relevant", _always_relevant)
    monkeypatch.setattr(human_api, "leader_start_task", _fake_start)
    monkeypatch.setattr(human_api, "_leader_binding_ok", lambda **_: False)

    client = TestClient(app)
    response = client.post(
        "/human/chat",
        json={"text": "协作一下", "session_id": "bind-1", "rpc_url": "http://127.0.0.1:5000/rpc"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "leader-error"
    assert "绑定异常" in data["answer"]
    assert data["collaboration"]["recoveryHint"]


def test_leader_exception_returns_structured_error(monkeypatch) -> None:
    _reset_session("err-1")

    async def _fake_decision(
        _: str,
        *,
        router_history_key: str,
        candidate_rpc_url: str | None,
        has_active_task: bool,
        active_task_state: str,
    ):
        return {"action": "call_start", "query": "发起协作"}

    async def _fake_start(**kwargs):
        raise RuntimeError("HTTP 500")

    monkeypatch.setattr(human_api, "decide_human_action", _fake_decision)
    monkeypatch.setattr(human_api, "leader_start_task", _fake_start)

    client = TestClient(app)
    response = client.post(
        "/human/chat",
        json={"text": "发起", "session_id": "err-1", "rpc_url": "http://127.0.0.1:5000/rpc"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "leader-error"
    assert "调用失败" in data["answer"]
    assert "HTTP 500" in data["collaboration"]["error"]


def test_record2_flow_no_stale_repeat(monkeypatch) -> None:
    _reset_session("record2")
    decisions = iter(
        [
            {"action": "local_reply"},
            {"action": "call_start", "query": "问问这个智能体你对奶妈有何看法"},
            {"action": "local_reply"},
            {"action": "call_start", "query": "介绍特朗普"},
        ]
    )
    local_answers = iter(
        [
            "奶牛是乳用品种牛。",
            "你给的RPC地址是 http://123.249.107.155:5000/rpc",
        ]
    )
    remote_answers = iter(
        [
            "这是远端对奶妈问题的回答。",
            "这是远端对特朗普问题的回答。",
        ]
    )

    async def _fake_decision(
        _: str,
        *,
        router_history_key: str,
        candidate_rpc_url: str | None,
        has_active_task: bool,
        active_task_state: str,
    ):
        return next(decisions)

    async def _fake_answer(_: str, *, conversation_key: str | None = None) -> str:
        return next(local_answers)

    async def _fake_start(**kwargs):
        return {
            "final_state": "awaiting-completion",
            "task_id": f"task-{kwargs['session_id']}",
            "partner_sender_id": "partner-a",
            "binding_ok": True,
            "product_texts": [next(remote_answers)],
            "status_texts": [],
            "trace": [{"step": "start"}],
            "call_proof": {"invoked": True, "trace_steps": ["start"]},
        }

    monkeypatch.setattr(human_api, "decide_human_action", _fake_decision)
    monkeypatch.setattr(human_api, "build_chat_answer", _fake_answer)
    monkeypatch.setattr(human_api, "leader_start_task", _fake_start)
    monkeypatch.setattr(human_api, "is_partner_response_relevant", _always_relevant)
    monkeypatch.setattr(human_api, "postprocess_collaboration_result", _passthrough_postprocess)

    client = TestClient(app)
    r1 = client.post("/human/chat", json={"text": "介绍什么是奶牛", "session_id": "record2"})
    r2 = client.post(
        "/human/chat",
        json={"text": "问问这个智能体你对奶妈有何看法", "session_id": "record2", "rpc_url": "http://123.249.107.155:5000/rpc"},
    )
    r3 = client.post("/human/chat", json={"text": "你还记得地址是多少吗？", "session_id": "record2"})
    r4 = client.post(
        "/human/chat",
        json={"text": "介绍特朗普", "session_id": "record2", "rpc_url": "http://123.249.107.155:5000/rpc"},
    )

    assert r1.json()["answer"] == "奶牛是乳用品种牛。"
    assert r2.json()["answer"] == "这是远端对奶妈问题的回答。"
    assert "123.249.107.155" in r3.json()["answer"]
    assert r4.json()["answer"] == "这是远端对特朗普问题的回答。"
    assert r4.json()["answer"] != r2.json()["answer"]


def test_record3_flow_no_topic_cross_talk(monkeypatch) -> None:
    _reset_session("record3")
    decisions = iter(
        [
            {"action": "call_start", "query": "青年如何助力国家建设"},
            {"action": "local_reply"},
            {"action": "call_start", "query": "介绍特朗普"},
        ]
    )
    local_answers = iter(["地址是 http://123.249.107.155:5000/rpc"])
    remote_answers = iter(
        [
            "青年助力国家建设的讨论结果。",
            "特朗普介绍结果。",
        ]
    )

    async def _fake_decision(
        _: str,
        *,
        router_history_key: str,
        candidate_rpc_url: str | None,
        has_active_task: bool,
        active_task_state: str,
    ):
        return next(decisions)

    async def _fake_answer(_: str, *, conversation_key: str | None = None) -> str:
        return next(local_answers)

    async def _fake_start(**kwargs):
        return {
            "final_state": "awaiting-completion",
            "task_id": f"task-{kwargs['session_id']}",
            "partner_sender_id": "partner-a",
            "binding_ok": True,
            "product_texts": [next(remote_answers)],
            "status_texts": [],
            "trace": [{"step": "start"}],
            "call_proof": {"invoked": True, "trace_steps": ["start"]},
        }

    monkeypatch.setattr(human_api, "decide_human_action", _fake_decision)
    monkeypatch.setattr(human_api, "build_chat_answer", _fake_answer)
    monkeypatch.setattr(human_api, "leader_start_task", _fake_start)
    monkeypatch.setattr(human_api, "is_partner_response_relevant", _always_relevant)
    monkeypatch.setattr(human_api, "postprocess_collaboration_result", _passthrough_postprocess)

    client = TestClient(app)
    a = client.post(
        "/human/chat",
        json={"text": "请和这个地址协作青年话题", "session_id": "record3", "rpc_url": "http://123.249.107.155:5000/rpc"},
    )
    b = client.post("/human/chat", json={"text": "还记得地址吗？", "session_id": "record3"})
    c = client.post(
        "/human/chat",
        json={"text": "问它介绍特朗普", "session_id": "record3", "rpc_url": "http://123.249.107.155:5000/rpc"},
    )

    assert a.json()["answer"] == "青年助力国家建设的讨论结果。"
    assert "123.249.107.155" in b.json()["answer"]
    assert c.json()["answer"] == "特朗普介绍结果。"
    assert c.json()["answer"] != a.json()["answer"]


def test_collaboration_result_uses_model_postprocess(monkeypatch) -> None:
    _reset_session("record4-post")

    async def _fake_decision(
        _: str,
        *,
        router_history_key: str,
        candidate_rpc_url: str | None,
        has_active_task: bool,
        active_task_state: str,
    ):
        return {"action": "call_start", "query": "请讨论青年建设"}

    async def _fake_start(**kwargs):
        return {
            "final_state": "awaiting-completion",
            "task_id": "task-r4",
            "partner_sender_id": "partner-a",
            "binding_ok": True,
            "product_texts": ["远端原始回答"],
            "status_texts": [],
            "trace": [{"step": "start"}],
            "call_proof": {"invoked": True, "trace_steps": ["start"]},
        }

    async def _fake_postprocess(**kwargs):
        assert kwargs["partner_response"] == "远端原始回答"
        assert "讨论青年建设" in kwargs["user_request"]
        assert kwargs["call_proof"]["invoked"] is True
        return "按用户要求整合后的交付"

    monkeypatch.setattr(human_api, "decide_human_action", _fake_decision)
    monkeypatch.setattr(human_api, "leader_start_task", _fake_start)
    monkeypatch.setattr(human_api, "postprocess_collaboration_result", _fake_postprocess)
    monkeypatch.setattr(human_api, "is_partner_response_relevant", _always_relevant)

    client = TestClient(app)
    response = client.post(
        "/human/chat",
        json={"text": "请和对方讨论青年建设并给我整合结论", "session_id": "record4-post", "rpc_url": "http://123.249.107.155:5000/rpc"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "leader-proxy"
    assert data["answer"] == "按用户要求整合后的交付"
    assert data["collaboration"]["phase"] == "post-processed"
    assert "routing" in data["collaboration"]["timingsMs"]
    assert "leader" in data["collaboration"]["timingsMs"]
    assert "postprocess" in data["collaboration"]["timingsMs"]
    assert "total" in data["collaboration"]["timingsMs"]


def test_leader_timeout_returns_structured_error_with_timings(monkeypatch) -> None:
    _reset_session("record4-timeout")

    async def _fake_decision(
        _: str,
        *,
        router_history_key: str,
        candidate_rpc_url: str | None,
        has_active_task: bool,
        active_task_state: str,
    ):
        return {"action": "call_start", "query": "发起协作"}

    async def _timeout_start(**kwargs):
        raise TimeoutError("leader timeout")

    monkeypatch.setattr(human_api, "decide_human_action", _fake_decision)
    monkeypatch.setattr(human_api, "leader_start_task", _timeout_start)

    client = TestClient(app)
    response = client.post(
        "/human/chat",
        json={"text": "开始协作", "session_id": "record4-timeout", "rpc_url": "http://123.249.107.155:5000/rpc"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "leader-error"
    assert "调用失败" in data["answer"]
    assert data["collaboration"]["error"]
    assert "routing" in data["collaboration"]["timingsMs"]
    assert "leader" in data["collaboration"]["timingsMs"]
    assert "total" in data["collaboration"]["timingsMs"]


def test_record4_local_followup_not_repeating_collab_answer(monkeypatch) -> None:
    _reset_session("record4-follow")
    decisions = iter(
        [
            {"action": "call_start", "query": "青年如何助力国家建设"},
            {"action": "local_reply"},
        ]
    )
    local_answers = iter(["特朗普是美国第45任总统。"])

    async def _fake_decision(
        _: str,
        *,
        router_history_key: str,
        candidate_rpc_url: str | None,
        has_active_task: bool,
        active_task_state: str,
    ):
        return next(decisions)

    async def _fake_start(**kwargs):
        return {
            "final_state": "awaiting-completion",
            "task_id": "task-follow",
            "partner_sender_id": "partner-a",
            "binding_ok": True,
            "product_texts": ["青年可从多方面助力国家建设。"],
            "status_texts": [],
            "trace": [{"step": "start"}],
            "call_proof": {"invoked": True, "trace_steps": ["start"]},
        }

    async def _fake_postprocess(**kwargs):
        return kwargs["partner_response"]

    async def _fake_local_answer(_: str, *, conversation_key: str | None = None):
        return next(local_answers)

    monkeypatch.setattr(human_api, "decide_human_action", _fake_decision)
    monkeypatch.setattr(human_api, "leader_start_task", _fake_start)
    monkeypatch.setattr(human_api, "postprocess_collaboration_result", _fake_postprocess)
    monkeypatch.setattr(human_api, "build_chat_answer", _fake_local_answer)
    monkeypatch.setattr(human_api, "is_partner_response_relevant", _always_relevant)

    client = TestClient(app)
    first = client.post(
        "/human/chat",
        json={"text": "请你和远端讨论青年建设", "session_id": "record4-follow", "rpc_url": "http://123.249.107.155:5000/rpc"},
    ).json()
    second = client.post(
        "/human/chat",
        json={"text": "特朗普是谁？", "session_id": "record4-follow"},
    ).json()
    assert first["answer"] == "青年可从多方面助力国家建设。"
    assert second["answer"] == "特朗普是美国第45任总统。"
    assert second["answer"] != first["answer"]


def test_truth_guard_blocks_invalid_call_proof(monkeypatch) -> None:
    _reset_session("record5-proof")

    async def _fake_decision(
        _: str,
        *,
        router_history_key: str,
        candidate_rpc_url: str | None,
        has_active_task: bool,
        active_task_state: str,
    ):
        return {"action": "call_start", "query": "请协作回答"}

    async def _fake_start(**kwargs):
        return {
            "final_state": "awaiting-completion",
            "task_id": "task-proof",
            "partner_sender_id": "partner-a",
            "binding_ok": True,
            "product_texts": ["这是远端回答"],
            "status_texts": [],
            "trace": [{"step": "start"}],
            "call_proof": {"invoked": False, "trace_steps": []},
        }

    monkeypatch.setattr(human_api, "decide_human_action", _fake_decision)
    monkeypatch.setattr(human_api, "leader_start_task", _fake_start)

    client = TestClient(app)
    resp = client.post(
        "/human/chat",
        json={"text": "请你调用远端", "session_id": "record5-proof", "rpc_url": "http://123.249.107.155:5000/rpc"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "leader-error"
    assert data["collaboration"]["phase"] == "truth-guard"


def test_offtopic_recovery_retries_then_fallback(monkeypatch) -> None:
    _reset_session("record5-offtopic")
    start_calls: list[str] = []

    async def _fake_decision(
        _: str,
        *,
        router_history_key: str,
        candidate_rpc_url: str | None,
        has_active_task: bool,
        active_task_state: str,
    ):
        return {"action": "call_start", "query": "请回答特朗普简介"}

    async def _fake_start(**kwargs):
        start_calls.append(str(kwargs["user_input"]))
        return {
            "final_state": "awaiting-completion",
            "task_id": f"task-{len(start_calls)}",
            "partner_sender_id": "partner-a",
            "binding_ok": True,
            "product_texts": ["奶妈是辅助职业。"],
            "status_texts": [],
            "trace": [{"step": "start"}],
            "call_proof": {"invoked": True, "trace_steps": ["start"]},
        }

    async def _always_irrelevant(**kwargs) -> bool:
        return False

    monkeypatch.setattr(human_api, "decide_human_action", _fake_decision)
    monkeypatch.setattr(human_api, "leader_start_task", _fake_start)
    monkeypatch.setattr(human_api, "is_partner_response_relevant", _always_irrelevant)

    client = TestClient(app)
    resp = client.post(
        "/human/chat",
        json={"text": "请你和远端协作介绍特朗普", "session_id": "record5-offtopic", "rpc_url": "http://123.249.107.155:5000/rpc"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "leader-error"
    assert data["collaboration"]["phase"] == "offtopic-recovery"
    assert len(start_calls) == 2


def test_budget_timeout_returns_retryable(monkeypatch) -> None:
    _reset_session("record5-time-budget")

    async def _fake_decision(
        _: str,
        *,
        router_history_key: str,
        candidate_rpc_url: str | None,
        has_active_task: bool,
        active_task_state: str,
    ):
        return {"action": "local_reply"}

    monkeypatch.setattr(human_api, "decide_human_action", _fake_decision)
    monkeypatch.setattr(human_api, "HUMAN_TOTAL_BUDGET_SECONDS", 0.0)

    client = TestClient(app)
    resp = client.post("/human/chat", json={"text": "你好", "session_id": "record5-time-budget"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "leader-error"
    assert data["collaboration"]["phase"] == "timeout"
    assert data["collaboration"]["retryable"] is True
