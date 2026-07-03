"""Leader-side task execution manager extracted from human API flow."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, Awaitable, Callable


@dataclass
class ExecutionResult:
    """Outcome of one leader execution round."""

    leader_result: dict[str, object]
    timings_ms: dict[str, int]


class TaskExecutionManager:
    """Encapsulates remote leader task execution state machine."""

    def __init__(
        self,
        *,
        leader_id: str,
        leader_call_timeout_seconds: float,
        normalize_state: Callable[[str], str],
        remaining_budget: Callable[[float], float],
        dynamic_max_polls: Callable[[float], int],
        proof_allows_remote_claim: Callable[[dict[str, object]], bool],
        call_proof_for_failure: Callable[..., dict[str, object]],
        leader_start_task: Callable[..., Awaitable[dict[str, object]]],
        leader_get_task: Callable[..., Awaitable[dict[str, object]]],
        leader_continue_task: Callable[..., Awaitable[dict[str, object]]],
        leader_complete_task: Callable[..., Awaitable[dict[str, object]]],
    ) -> None:
        self._leader_id = leader_id
        self._leader_call_timeout_seconds = leader_call_timeout_seconds
        self._normalize_state = normalize_state
        self._remaining_budget = remaining_budget
        self._dynamic_max_polls = dynamic_max_polls
        self._proof_allows_remote_claim = proof_allows_remote_claim
        self._call_proof_for_failure = call_proof_for_failure
        self._leader_start_task = leader_start_task
        self._leader_get_task = leader_get_task
        self._leader_continue_task = leader_continue_task
        self._leader_complete_task = leader_complete_task

    async def execute(
        self,
        *,
        action: str,
        decision_query: str,
        text: str,
        rpc_url: str,
        state: Any,
        start_total: float,
        timings_ms: dict[str, int],
    ) -> ExecutionResult:
        """Run one complete remote execution cycle with stabilization."""
        start_leader = perf_counter()
        remaining_before_leader = self._remaining_budget(start_total)
        if remaining_before_leader <= 0:
            raise TimeoutError("end-to-end budget exhausted before leader call")

        call_timeout = min(self._leader_call_timeout_seconds, remaining_before_leader)
        dynamic_polls = self._dynamic_max_polls(remaining_before_leader)
        leader_query = decision_query.strip() or text

        if action == "call_start":
            leader_result = await self._leader_start_task(
                partner_rpc_url=rpc_url,
                leader_id=self._leader_id,
                session_id=state.aip_session_id,
                user_input=leader_query,
                task_id=None,
                max_polls=dynamic_polls,
                timeout_seconds=call_timeout,
            )
        elif action == "call_continue":
            task_id = state.active_task_id
            if not task_id:
                leader_result = await self._leader_start_task(
                    partner_rpc_url=rpc_url,
                    leader_id=self._leader_id,
                    session_id=state.aip_session_id,
                    user_input=leader_query,
                    max_polls=dynamic_polls,
                    timeout_seconds=call_timeout,
                )
            else:
                try:
                    await self._leader_get_task(
                        partner_rpc_url=rpc_url,
                        leader_id=self._leader_id,
                        session_id=state.aip_session_id,
                        task_id=task_id,
                        max_polls=min(2, dynamic_polls),
                        timeout_seconds=call_timeout,
                    )
                except Exception:
                    state.active_task_id = ""
                    state.last_state = ""
                    leader_result = await self._leader_start_task(
                        partner_rpc_url=rpc_url,
                        leader_id=self._leader_id,
                        session_id=state.aip_session_id,
                        user_input=leader_query,
                        max_polls=dynamic_polls,
                        timeout_seconds=call_timeout,
                    )
                else:
                    leader_result = await self._leader_continue_task(
                        partner_rpc_url=rpc_url,
                        leader_id=self._leader_id,
                        session_id=state.aip_session_id,
                        task_id=task_id,
                        continue_input=leader_query,
                        max_polls=dynamic_polls,
                        timeout_seconds=call_timeout,
                    )
        elif action == "call_complete":
            task_id = state.active_task_id
            if not task_id:
                raise RuntimeError("no active task for call_complete")
            leader_result = await self._leader_complete_task(
                partner_rpc_url=rpc_url,
                leader_id=self._leader_id,
                session_id=state.aip_session_id,
                task_id=task_id,
                timeout_seconds=call_timeout,
            )
        else:  # call_get
            task_id = state.active_task_id
            if not task_id:
                leader_result = await self._leader_start_task(
                    partner_rpc_url=rpc_url,
                    leader_id=self._leader_id,
                    session_id=state.aip_session_id,
                    user_input=leader_query,
                    max_polls=dynamic_polls,
                    timeout_seconds=call_timeout,
                )
            else:
                leader_result = await self._leader_get_task(
                    partner_rpc_url=rpc_url,
                    leader_id=self._leader_id,
                    session_id=state.aip_session_id,
                    task_id=task_id,
                    max_polls=dynamic_polls,
                    timeout_seconds=call_timeout,
                )

        transient_state = self._normalize_state(str(leader_result.get("final_state", "")))
        transient_task_id = str(leader_result.get("task_id", "")).strip()
        if transient_state in {"accepted", "working"} and transient_task_id:
            remaining_for_stabilize = self._remaining_budget(start_total)
            if remaining_for_stabilize > 1.5:
                stabilize_start = perf_counter()
                stabilize_timeout = min(self._leader_call_timeout_seconds, remaining_for_stabilize)
                stabilize_polls = self._dynamic_max_polls(remaining_for_stabilize)
                leader_result = await self._leader_get_task(
                    partner_rpc_url=rpc_url,
                    leader_id=self._leader_id,
                    session_id=state.aip_session_id,
                    task_id=transient_task_id,
                    max_polls=stabilize_polls,
                    timeout_seconds=stabilize_timeout,
                )
                timings_ms["leader_stabilize"] = int((perf_counter() - stabilize_start) * 1000)

        # Demo-leader style extra settle pass: if task is still processing, use a short
        # follow-up polling round before returning to reduce visible "working" responses.
        post_stabilize_state = self._normalize_state(str(leader_result.get("final_state", "")))
        post_stabilize_task_id = str(leader_result.get("task_id", "")).strip()
        if post_stabilize_state in {"accepted", "working"} and post_stabilize_task_id:
            remaining_for_settle = self._remaining_budget(start_total)
            if remaining_for_settle > 2.5:
                settle_start = perf_counter()
                settle_timeout = min(self._leader_call_timeout_seconds, remaining_for_settle)
                settle_polls = max(3, min(8, self._dynamic_max_polls(remaining_for_settle)))
                leader_result = await self._leader_get_task(
                    partner_rpc_url=rpc_url,
                    leader_id=self._leader_id,
                    session_id=state.aip_session_id,
                    task_id=post_stabilize_task_id,
                    max_polls=settle_polls,
                    timeout_seconds=settle_timeout,
                )
                timings_ms["leader_settle"] = int((perf_counter() - settle_start) * 1000)

        final_state = self._normalize_state(str(leader_result.get("final_state", "")))
        initial_proof = leader_result.get("call_proof") or self._call_proof_for_failure(
            state=state,
            reason="pre-complete-missing-proof",
        )
        if (
            final_state == "awaiting-completion"
            and action != "call_complete"
            and self._proof_allows_remote_claim(initial_proof)
        ):
            remaining_before_complete = self._remaining_budget(start_total)
            if remaining_before_complete > 1.0:
                complete_timeout = min(self._leader_call_timeout_seconds, remaining_before_complete)
                complete_start = perf_counter()
                complete_task_id = str(leader_result.get("task_id", "")).strip()
                if complete_task_id:
                    complete_result = await self._leader_complete_task(
                        partner_rpc_url=rpc_url,
                        leader_id=self._leader_id,
                        session_id=state.aip_session_id,
                        task_id=complete_task_id,
                        timeout_seconds=complete_timeout,
                    )
                    combined_trace = [
                        *(leader_result.get("trace", []) or []),
                        *(complete_result.get("trace", []) or []),
                    ]
                    if not complete_result.get("product_texts"):
                        complete_result["product_texts"] = leader_result.get("product_texts", [])
                    if not complete_result.get("status_texts"):
                        complete_result["status_texts"] = leader_result.get("status_texts", [])
                    if not complete_result.get("call_proof"):
                        complete_result["call_proof"] = leader_result.get("call_proof", {})
                    complete_result["trace"] = combined_trace
                    leader_result = complete_result
                    timings_ms["leader_complete"] = int((perf_counter() - complete_start) * 1000)

        timings_ms["leader"] = int((perf_counter() - start_leader) * 1000)
        trace_items = leader_result.get("trace", []) or []
        trace_steps = [str(item.get("step", "")) for item in trace_items if isinstance(item, dict)]
        leader_result["execution_progress"] = {
            "traceSteps": len(trace_steps),
            "pollRounds": sum(1 for step in trace_steps if step == "get"),
            "lastRemoteState": self._normalize_state(str(leader_result.get("final_state", ""))),
        }
        return ExecutionResult(leader_result=leader_result, timings_ms=timings_ms)
