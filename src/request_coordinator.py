"""Single entry point shared by Slack and HTTP command handlers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from src.agent import AnalysisAgent
from src.dispatch import dispatch_command
from src.execution_workflow import ExecutionWorkflow
from src.linear_workflow import LinearIntegrationWorkflow
from src.plan_first import PlanAnswer, parse_choice
from src.request_router import RequestIntent, RequestRouter, RoutedRequest, strip_mentions
from src.thread_context import ThreadContextStore
from src.thread_summary_workflow import ThreadSummaryWorkflow


@dataclass
class RequestCoordinator:
    router: RequestRouter
    execution_workflow: ExecutionWorkflow
    linear_workflow: LinearIntegrationWorkflow
    thread_summary_workflow: ThreadSummaryWorkflow | None = None

    def process(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        text: str,
        thread_context: ThreadContextStore,
        agent: AnalysisAgent,
        on_progress: Callable[[str], None] | None = None,
    ) -> tuple[RoutedRequest, str]:
        routed = self._plan_choice(text, channel_id, thread_ts) or self.router.route(text)
        if routed.text.casefold() in {"trace 요약", "trace summary"}:
            trace_store = getattr(agent, "thread_trace_store", None)
            trace = trace_store.get(channel_id, thread_ts) if trace_store is not None else None
            summary = trace.summary() if trace is not None else "기록된 실행이 없습니다."
            response = "Trace 요약:\n" + summary
        else:
            response = self._route(
                routed,
                channel_id=channel_id,
                thread_ts=thread_ts,
                text=text,
                thread_context=thread_context,
                agent=agent,
                on_progress=on_progress,
            )
        # One owner appends both messages. Workflows deliberately remain
        # context-free, so Slack and /debug/command cannot diverge.
        thread_context.append_user_message(channel_id, thread_ts, text)
        thread_context.append(channel_id, thread_ts, text)
        thread_context.append(channel_id, thread_ts, response)
        return routed, response

    def _plan_choice(self, text: str, channel_id: str, thread_ts: str) -> RoutedRequest | None:
        """A message that is only an option number answers an open plan review; with no review
        open it is an ordinary message, so numbers elsewhere are never swallowed."""
        command = strip_mentions(text)
        number = parse_choice(command)
        if number is None or not self.execution_workflow.has_plan_review(channel_id, thread_ts):
            return None
        return RoutedRequest(
            RequestIntent.PLAN_ANSWER, command, plan_answer=PlanAnswer("choice", str(number))
        )

    def _route(
        self,
        routed: RoutedRequest,
        *,
        channel_id: str,
        thread_ts: str,
        text: str,
        thread_context: ThreadContextStore,
        agent: AnalysisAgent,
        on_progress: Callable[[str], None] | None = None,
    ) -> str:
        if routed.intent is RequestIntent.CONTROL_CONFIRM:
            return self._confirm(
                routed,
                channel_id=channel_id,
                thread_ts=thread_ts,
                text=text,
                thread_context=thread_context,
                agent=agent,
            )
        if routed.intent is RequestIntent.CONTROL_CANCEL:
            return self._cancel(
                channel_id=channel_id,
                thread_ts=thread_ts,
                text=text,
                thread_context=thread_context,
                agent=agent,
            )
        if routed.intent is RequestIntent.PLAN_CONFIRM and routed.area is not None:
            return self.execution_workflow.confirm_plan(channel_id, thread_ts, routed.area)
        if routed.intent is RequestIntent.PLAN_ANSWER and routed.plan_answer is not None:
            return self.execution_workflow.answer_plan_review(
                channel_id, thread_ts, routed.plan_answer
            )
        if routed.intent is RequestIntent.THREAD_SUMMARY:
            if self.thread_summary_workflow is None:
                return "스레드 요약 기능이 설정되지 않았습니다."
            return self.thread_summary_workflow.process(channel_id=channel_id, thread_ts=thread_ts)
        if routed.intent is RequestIntent.CODE_WORK:
            return (
                self.execution_workflow.process(
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    text=text,
                    thread_context=thread_context,
                    agent=agent,
                    trusted_code_work=routed.mode_decided,
                    on_progress=on_progress,
                )
                or "코드 작업 요청을 이해하지 못했습니다."
            )
        if routed.intent in {RequestIntent.LINEAR_READ, RequestIntent.LINEAR_MUTATION}:
            return (
                self.linear_workflow.process(
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    text=text,
                    thread_context=thread_context,
                    agent=agent,
                )
                or "지원되는 Linear 작업을 지정해 주세요."
            )
        # Nothing else claimed this message. A thread with an open code-work
        # conversation — a pending plan, or a request stalled only for a
        # project name — gets one more chance to recognize this message
        # (a clarification, or the missing project name) before falling back
        # to analysis.
        if (
            self.execution_workflow.has_open_conversation(channel_id, thread_ts)
            and "?" not in routed.text
            and "？" not in routed.text
        ):
            clarification = self.execution_workflow.process(
                channel_id=channel_id,
                thread_ts=thread_ts,
                text=text,
                thread_context=thread_context,
                agent=agent,
            )
            if clarification is not None:
                return clarification
        return dispatch_command(
            channel_id,
            thread_ts,
            text,
            thread_context=thread_context,
            agent=agent,
            record_context=False,
        )

    def _confirm(
        self,
        routed: RoutedRequest,
        *,
        channel_id: str,
        thread_ts: str,
        text: str,
        thread_context: ThreadContextStore,
        agent: AnalysisAgent,
    ) -> str:
        # Try code execution directly rather than relying on a separate
        # ownership pre-check. A confirmation must never fall through to LLM
        # analysis merely because a pending-store observation races or expires.
        code_response = self.execution_workflow.process(
            channel_id=channel_id,
            thread_ts=thread_ts,
            text=text,
            thread_context=thread_context,
            agent=agent,
        )
        if code_response is not None and not code_response.startswith(
            "실행할 보류 계획이 없습니다"
        ):
            return code_response
        if self.linear_workflow.has_pending(channel_id, thread_ts):
            return (
                self.linear_workflow.process(
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    text=text,
                    thread_context=thread_context,
                    agent=agent,
                )
                or "실행할 보류 작업이 없습니다."
            )
        return "실행할 보류 작업이 없습니다. 먼저 코드 작업 또는 Linear 변경 요청을 보내 주세요."

    def _cancel(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        text: str,
        thread_context: ThreadContextStore,
        agent: AnalysisAgent,
    ) -> str:
        owners = [
            workflow
            for workflow in (self.execution_workflow, self.linear_workflow)
            if workflow.has_pending(channel_id, thread_ts)
        ]
        if not owners:
            return "취소할 보류 작업이 없습니다."
        if len(owners) > 1:
            return (
                "보류된 작업이 여러 개라 자동 취소하지 않았습니다. "
                "새 요청으로 원하는 작업을 다시 지정해 주세요."
            )
        return (
            owners[0].process(
                channel_id=channel_id,
                thread_ts=thread_ts,
                text=text,
                thread_context=thread_context,
                agent=agent,
            )
            or "취소할 보류 작업이 없습니다."
        )
