"""내부 정책과 검색 문서를 결합해 사건별 대응 계획을 생성한다."""

from __future__ import annotations

import json
import logging
import os
from collections import OrderedDict
from dataclasses import asdict
from hashlib import sha256
from threading import Lock
from typing import Any

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field

from app.domain.agent_status import InformationStatus
from app.domain.response_policy import ResponsePolicy
from app.dto.agent import (
    ChecklistItemDTO,
    RecommendedActionDTO,
    ResponsePlanDTO,
)
from app.dto.agent_guide import RetrievedGuideChunkDTO


RESPONSE_PLAN_REASONING_EFFORT = "low"
RESPONSE_PLAN_MAX_COMPLETION_TOKENS = 1200
GUIDE_CONTEXT_MAX_CHARS = 1200
RESPONSE_PLAN_PROMPT_VERSION = "1.0"
RESPONSE_PLAN_CACHE_SIZE = 64


class GeneratedActionDetail(BaseModel):
    """LLM이 정책 조치에 추가하는 구체적인 실행 정보."""

    model_config = ConfigDict(extra="forbid")

    action_code: str
    procedure_steps: list[str] = Field(max_length=3)
    cautions: list[str] = Field(max_length=2)


class GeneratedResponsePlan(BaseModel):
    """LLM이 정책에 보강할 조치별 상세정보 형식."""

    model_config = ConfigDict(extra="forbid")

    actions: list[GeneratedActionDetail]


class PolicyResponsePlanGenerator:
    """LLM을 사용할 수 없을 때 내부 정책만으로 대응 계획을 만든다."""

    def generate(
        self,
        *,
        fraud_type: str,
        policy: ResponsePolicy,
        guides: list[RetrievedGuideChunkDTO],
        metrics: dict[str, object] | None = None,
    ) -> ResponsePlanDTO:
        if metrics is not None:
            metrics.update(
                {
                    "response_plan_cache_hit": False,
                    "response_plan_cache_key": None,
                    "response_plan_llm_call_count": 0,
                    "response_plan_fallback_used": False,
                }
            )
        return ResponsePlanDTO(
            applied_fraud_type=fraud_type,
            information_status=(
                InformationStatus.SUFFICIENT
                if guides
                else InformationStatus.PARTIAL
            ),
            summary=(
                f"{fraud_type} 유형의 {policy.risk_grade} 위험 사건에 대한 "
                "기본 대응 계획이다."
            ),
            recommended_actions=[
                RecommendedActionDTO(
                    priority=action.priority,
                    action_code=action.action_code,
                    action=action.action,
                    reason=action.reason,
                    required=action.required,
                    procedure_steps=[],
                    cautions=[],
                )
                for action in policy.actions
            ],
            checklist=[
                ChecklistItemDTO(
                    item_code=item.item_code,
                    label=item.label,
                    required=item.required,
                )
                for item in policy.checklist
            ],
        )


class RagResponsePlanGenerator:
    """RAG 문맥으로 정책 조치의 수행 절차와 주의사항을 보강한다."""

    def __init__(
        self,
        *,
        structured_llm: Any | None = None,
        fallback: PolicyResponsePlanGenerator | None = None,
        reasoning_effort: str = RESPONSE_PLAN_REASONING_EFFORT,
        max_completion_tokens: int = RESPONSE_PLAN_MAX_COMPLETION_TOKENS,
        cache_size: int = RESPONSE_PLAN_CACHE_SIZE,
        model_name: str | None = None,
    ) -> None:
        self.model_name = model_name or os.getenv(
            "AGENT_RESPONSE_PLAN_MODEL",
            os.getenv("OPENAI_MODEL", "gpt-5"),
        )
        self.reasoning_effort = reasoning_effort
        self.max_completion_tokens = max_completion_tokens
        model_options: dict[str, Any] = {
            "model": self.model_name,
            "api_key": os.getenv("OPENAI_API_KEY"),
            "timeout": float(os.getenv("OPENAI_TIMEOUT_SECONDS", "15")),
            "max_retries": int(os.getenv("OPENAI_MAX_RETRIES", "0")),
            "max_completion_tokens": max_completion_tokens,
        }
        if not self.model_name.startswith("gpt-4"):
            model_options["reasoning_effort"] = reasoning_effort
        self.structured_llm = structured_llm or ChatOpenAI(
            **model_options
        ).with_structured_output(
            GeneratedResponsePlan,
            method="json_schema",
            strict=True,
        )
        self.fallback = fallback or PolicyResponsePlanGenerator()
        self.cache_size = cache_size
        self._cache: OrderedDict[str, ResponsePlanDTO] = OrderedDict()
        self._cache_lock = Lock()
        self._key_locks: dict[str, Lock] = {}

    def generate(
        self,
        *,
        fraud_type: str,
        policy: ResponsePolicy,
        guides: list[RetrievedGuideChunkDTO],
        metrics: dict[str, object] | None = None,
    ) -> ResponsePlanDTO:
        # 검색 근거가 없으면 LLM을 호출하지 않고 정책 원문을 그대로 사용한다.
        if not guides:
            return self.fallback.generate(
                fraud_type=fraud_type,
                policy=policy,
                guides=guides,
                metrics=metrics,
            )

        cache_key = self._build_cache_key(fraud_type, policy, guides)
        if metrics is not None:
            metrics.update(
                {
                    "response_plan_cache_hit": False,
                    "response_plan_cache_key": cache_key,
                    "response_plan_llm_call_count": 0,
                    "response_plan_fallback_used": False,
                }
            )
        cached = self._get_cached(cache_key)
        if cached is not None:
            if metrics is not None:
                metrics["response_plan_cache_hit"] = True
            return cached

        with self._get_key_lock(cache_key):
            cached = self._get_cached(cache_key)
            if cached is not None:
                if metrics is not None:
                    metrics["response_plan_cache_hit"] = True
                return cached

            if metrics is not None:
                metrics["response_plan_llm_call_count"] = 1
            try:
                generated = self.structured_llm.invoke(
                    _build_messages(fraud_type, policy, guides)
                )
                details = {item.action_code: item for item in generated.actions}
                policy_codes = {item.action_code for item in policy.actions}
                if set(details) != policy_codes or len(details) != len(
                    generated.actions
                ):
                    raise ValueError("LLM 조치 코드가 내부 정책과 일치하지 않는다.")

                plan = ResponsePlanDTO(
                    applied_fraud_type=fraud_type,
                    information_status=InformationStatus.SUFFICIENT,
                    summary=(
                        f"{fraud_type} 유형의 {policy.risk_grade} 위험 사건에 대한 "
                        "대응 계획이다."
                    ),
                    recommended_actions=[
                        RecommendedActionDTO(
                            priority=action.priority,
                            action_code=action.action_code,
                            action=action.action,
                            reason=action.reason,
                            required=action.required,
                            procedure_steps=(
                                details[action.action_code].procedure_steps
                            ),
                            cautions=details[action.action_code].cautions,
                        )
                        for action in policy.actions
                    ],
                    checklist=[
                        ChecklistItemDTO(
                            item_code=item.item_code,
                            label=item.label,
                            required=item.required,
                        )
                        for item in policy.checklist
                    ],
                )
            except Exception as error:
                logging.getLogger(__name__).warning(
                    "RAG 대응 계획 생성 실패로 정책 fallback 적용: %s",
                    error,
                )
                if metrics is not None:
                    metrics["response_plan_fallback_used"] = True
                return self.fallback.generate(
                    fraud_type=fraud_type,
                    policy=policy,
                    guides=guides,
                )

            self._store_cached(cache_key, plan)
            return plan

    def _build_cache_key(
        self,
        fraud_type: str,
        policy: ResponsePolicy,
        guides: list[RetrievedGuideChunkDTO],
    ) -> str:
        payload = {
            "prompt_version": RESPONSE_PLAN_PROMPT_VERSION,
            "model": self.model_name,
            "reasoning_effort": self.reasoning_effort,
            "max_completion_tokens": self.max_completion_tokens,
            # 최종 DTO에 반영되는 정책값과 실제 LLM 입력이 바뀌면 키도 바뀐다.
            "policy": asdict(policy),
            "messages": _build_messages(fraud_type, policy, guides),
        }
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(serialized.encode("utf-8")).hexdigest()

    def _get_cached(self, cache_key: str) -> ResponsePlanDTO | None:
        if self.cache_size <= 0:
            return None
        with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                self._cache.move_to_end(cache_key)
            return cached

    def _store_cached(self, cache_key: str, plan: ResponsePlanDTO) -> None:
        if self.cache_size <= 0:
            return
        with self._cache_lock:
            self._cache[cache_key] = plan
            self._cache.move_to_end(cache_key)
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)

    def _get_key_lock(self, cache_key: str) -> Lock:
        with self._cache_lock:
            return self._key_locks.setdefault(cache_key, Lock())


def _build_messages(
    fraud_type: str,
    policy: ResponsePolicy,
    guides: list[RetrievedGuideChunkDTO],
) -> list[dict[str, str]]:
    payload = {
        "fraud_type": fraud_type,
        "risk_grade": policy.risk_grade,
        "policy_actions": [
            {
                "action_code": action.action_code,
                "action": action.action,
                "reason": action.reason,
            }
            for action in policy.actions
        ],
        "guide_contexts": [
            {
                "title": guide.title,
                "heading": guide.heading,
                "content": guide.content[:GUIDE_CONTEXT_MAX_CHARS],
            }
            for guide in guides
        ],
    }
    return [
        {
            "role": "system",
            "content": (
                "금융 이상거래 모니터링 담당자의 대응 계획을 작성한다. "
                "사기 유형과 정책 조치를 변경하지 말고, 검색 문서에 근거해 각 조치의 "
                "구체적인 수행 절차는 최대 3개, 주의사항은 최대 2개만 작성한다. "
                "요약과 정책 조치 설명은 작성하지 않는다. 개인정보나 인증정보를 "
                "요청하는 절차를 만들지 않는다."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False),
        },
    ]


__all__ = [
    "GeneratedActionDetail",
    "GeneratedResponsePlan",
    "GUIDE_CONTEXT_MAX_CHARS",
    "PolicyResponsePlanGenerator",
    "RESPONSE_PLAN_CACHE_SIZE",
    "RESPONSE_PLAN_MAX_COMPLETION_TOKENS",
    "RESPONSE_PLAN_PROMPT_VERSION",
    "RESPONSE_PLAN_REASONING_EFFORT",
    "RagResponsePlanGenerator",
]
