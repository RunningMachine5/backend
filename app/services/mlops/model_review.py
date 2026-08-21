"""후보 모델과 운영 모델의 성능 지표를 AI가 검토한다."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Any, Literal

from fastapi import Depends
from openai import OpenAI, OpenAIError

from app.core import config


ReviewDecision = Literal["RECOMMENDED", "NOT_RECOMMENDED"]

COMPARISON_METRICS = (
    ("PR-AUC", "validation_pr_auc", "higher"),
    ("ROC-AUC", "validation_roc_auc", "higher"),
    ("Recall", "validation_recall", "higher"),
    ("F1 Score", "validation_f1", "higher"),
    ("Precision", "validation_precision", "higher"),
    ("FPR", "validation_fpr", "lower"),
)


@dataclass(frozen=True)
class ModelReviewResult:
    decision: ReviewDecision
    summary: str


class ModelReviewError(RuntimeError):
    """AI 모델 검토 호출 또는 응답 해석 실패."""


class ModelReviewLLM:
    """ML 추천 태그를 제외하고 실제 성능 수치만 AI에 전달한다."""

    def __init__(self, client: Any | None = None, model: str | None = None) -> None:
        self.client = client or OpenAI(
            api_key=config.OPENAI_API_KEY,
            timeout=config.OPENAI_TIMEOUT_SECONDS,
            max_retries=config.OPENAI_MAX_RETRIES,
        )
        self.model = model or config.MLOPS_REVIEW_MODEL

    def review(
        self,
        *,
        candidate_run_id: int,
        candidate_details: dict[str, Any],
        production_run_id: int | None,
        production_details: dict[str, Any] | None,
    ) -> ModelReviewResult:
        prompt = self._build_prompt(
            candidate_run_id=candidate_run_id,
            candidate_details=candidate_details,
            production_run_id=production_run_id,
            production_details=production_details,
        )
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_completion_tokens=500,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "model_review",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {
                                "decision": {
                                    "type": "string",
                                    "enum": ["RECOMMENDED", "NOT_RECOMMENDED"],
                                },
                                "summary": {"type": "string"},
                            },
                            "required": ["decision", "summary"],
                            "additionalProperties": False,
                        },
                    },
                },
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "너는 금융 이상거래 탐지 모델의 MLOps 검토 보조자다. "
                            "제공된 후보 모델과 현재 운영 모델의 성능 수치만 근거로 "
                            "검증 후보 진행을 추천하거나 비추천한다. ML 또는 MLflow가 "
                            "기록한 기존 추천값은 입력에 없으며 추측해서도 안 된다. "
                            "PR-AUC, ROC-AUC, Recall, F1 Score, Precision은 높을수록 좋고 "
                            "FPR은 낮을수록 좋다. 특히 PR-AUC, Recall, FPR을 우선해서 "
                            "운영 모델 대비 핵심 성능 저하와 오탐 위험을 함께 판단한다. "
                            "근거가 부족하거나 핵심 지표가 뚜렷하게 악화되면 비추천한다. "
                            "원인, 데이터 분포, 통계적 유의성은 제공되지 않았으므로 "
                            "추측하지 않는다. summary는 결론부터 시작하는 자연스러운 "
                            "한국어 2~3문장으로 작성하고 중요한 수치 차이와 다음 확인 "
                            "사항을 포함한다. 최종 운영 배포를 확정하는 표현은 쓰지 않는다."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
            )
            content = response.choices[0].message.content or "{}"
            data = json.loads(content)
            decision = data["decision"]
            summary = data["summary"].strip()
        except (OpenAIError, json.JSONDecodeError, KeyError, AttributeError) as exc:
            raise ModelReviewError("AI 모델 검토 요청에 실패했습니다.") from exc
        if decision not in {"RECOMMENDED", "NOT_RECOMMENDED"} or not summary:
            raise ModelReviewError("AI 모델 검토 응답이 비어 있습니다.")
        return ModelReviewResult(
            decision=decision,
            summary=summary,
        )

    @staticmethod
    def _build_prompt(
        *,
        candidate_run_id: int,
        candidate_details: dict[str, Any],
        production_run_id: int | None,
        production_details: dict[str, Any] | None,
    ) -> str:
        candidate_metrics = candidate_details.get("metrics", {})
        production_metrics = (
            production_details.get("metrics", {}) if production_details else {}
        )
        metrics = []
        for name, key, direction in COMPARISON_METRICS:
            candidate = candidate_metrics.get(key)
            production = production_metrics.get(key)
            metrics.append(
                {
                    "name": name,
                    "direction": direction,
                    "candidate": candidate,
                    "production": production,
                    "delta": (
                        candidate - production
                        if isinstance(candidate, (int, float))
                        and isinstance(production, (int, float))
                        else None
                    ),
                }
            )

        payload = {
            "candidate": {
                "training_run_id": candidate_run_id,
                "model_version": candidate_details.get("model_version"),
            },
            "production": {
                "training_run_id": production_run_id,
                "model_version": (
                    production_details.get("model_version")
                    if production_details
                    else None
                ),
            },
            "metrics": metrics,
        }
        return json.dumps(payload, ensure_ascii=False)


@lru_cache(maxsize=1)
def get_model_review_llm() -> ModelReviewLLM:
    return ModelReviewLLM()


ModelReviewLLMDep = Annotated[ModelReviewLLM, Depends(get_model_review_llm)]


__all__ = [
    "ModelReviewLLM",
    "ModelReviewLLMDep",
    "ModelReviewError",
    "ModelReviewResult",
    "ReviewDecision",
    "get_model_review_llm",
]
