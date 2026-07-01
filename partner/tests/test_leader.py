from __future__ import annotations

import asyncio
from dataclasses import dataclass

from acps_sdk.aip.aip_base_model import TaskState, TextDataItem

from partner_agent import leader as leader_module


@dataclass
class FakeStatus:
    state: TaskState
    dataItems: list[object]


@dataclass
class FakeProduct:
    dataItems: list[object]


@dataclass
class FakeTask:
    status: FakeStatus
    products: list[FakeProduct]
    senderId: str = "fake-partner"


class FakeClient:
    def __init__(self, partner_url: str, leader_id: str):
        self.partner_url = partner_url
        self.leader_id = leader_id
        self.closed = False
        self.calls: list[str] = []
        self._task = FakeTask(
            status=FakeStatus(TaskState.AwaitingInput, [TextDataItem(text="请补充信息")]),
            products=[],
        )

    async def start_task(self, session_id: str, task_id: str, user_input: str):
        self.calls.append("start")
        return self._task

    async def get_task(self, task_id: str, session_id: str):
        self.calls.append("get")
        return self._task

    async def continue_task(self, task_id: str, session_id: str, user_input: str):
        self.calls.append("continue")
        self._task = FakeTask(
            status=FakeStatus(TaskState.AwaitingCompletion, []),
            products=[FakeProduct(dataItems=[TextDataItem(text=f"回应: {user_input}")])],
        )
        return self._task

    async def complete_task(self, task_id: str, session_id: str):
        self.calls.append("complete")
        self._task.status.state = TaskState.Completed
        return self._task

    async def close(self):
        self.closed = True


def test_run_leader_partner_chat_continue_and_complete(monkeypatch) -> None:
    created_clients: list[FakeClient] = []

    def _factory(partner_url: str, leader_id: str) -> FakeClient:
        client = FakeClient(partner_url, leader_id)
        created_clients.append(client)
        return client

    monkeypatch.setattr(leader_module, "AipRpcClient", _factory)

    start_result = asyncio.run(
        leader_module.leader_start_task(
            partner_rpc_url="http://127.0.0.1:5000/rpc",
            leader_id="leader-aic",
            session_id="leader-session-1",
            user_input="你好",
        )
    )
    assert start_result["final_state"] == "awaiting-input"
    assert start_result["task_id"]
    assert start_result["trace"][0]["step"] == "start"
    assert start_result["call_proof"]["invoked"] is True
    assert "start" in start_result["call_proof"]["trace_steps"]

    continue_result = asyncio.run(
        leader_module.leader_continue_task(
            partner_rpc_url="http://127.0.0.1:5000/rpc",
            leader_id="leader-aic",
            session_id="leader-session-1",
            task_id=str(start_result["task_id"]),
            continue_input="请详细回答",
        )
    )
    assert continue_result["final_state"] == "awaiting-completion"
    assert continue_result["product_texts"] == ["回应: 请详细回答"]
    assert continue_result["call_proof"]["source_text_hash"]

    complete_result = asyncio.run(
        leader_module.leader_complete_task(
            partner_rpc_url="http://127.0.0.1:5000/rpc",
            leader_id="leader-aic",
            session_id="leader-session-1",
            task_id=str(start_result["task_id"]),
        )
    )
    assert complete_result["final_state"] == "completed"
    assert complete_result["timed_out"] is False

    assert len(created_clients) == 3
    assert created_clients[0].calls == ["start"]
    assert created_clients[1].calls == ["continue"]
    assert created_clients[2].calls == ["complete"]
    assert all(client.closed for client in created_clients)
