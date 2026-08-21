import json
import os
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

@dataclass(frozen=True)
class DashboardInsightLLMResult:
    selected_labels: list[str]
    title: str
    summary: str

class DashboardInsightLLM:
    def __init__(self) -> None:
        self.client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            timeout=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "15")),
            max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "0")),
        )

    def select_and_describe(
        self,
        *,
        current_count: int,
        previous_count: int,
        candidates: list[dict[str, Any]],
    ) -> DashboardInsightLLMResult:
        safe_candidates = self._to_safe_candidates(candidates)
        
        if not safe_candidates:
            return DashboardInsightLLMResult(
                selected_labels=[],
                title="주목할 이상징후 없음",
                summary="현재 기간에 분석할 이상 거래가 없습니다."
            )

        prompt = self._build_prompt(
            current_count = current_count,
            previous_count=previous_count,
            candidates=safe_candidates,
        )

        response = self.client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-5"),
            reasoning_effort="low",

            # json_object는 json 형식만 강제함. 필드 구조는 못 정함.
            # response_format={"type": "json_object"}, 

            # json_schema는 우리가 정해놓은 필드 구조만 통과 가능
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "dashboard_insight_selection",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "selected_labels": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "title": {"type": "string"},
                            "summary": {"type": "string"},
                        },
                        "required": [
                            "selected_labels",
                            "title",
                            "summary",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
        
            messages=[
                {
                    "role": "system",
                    "content": (
                        "너는 금융 이상거래 탐지 대시보드의 분석 에이전트다. "
                        "주어진 후보 데이터만 근거로 판단한다. "
                        "개별 거래, 고객, 계좌, IP, 위치 원문은 제공되지 않는다. "
                        "숫자, 건수, 금액은 절대 새로 만들지 않는다. "
                        "대시보드에 보여줄 중요한 이상징후 3~5개를 선택하고 "
                        "짧은 제목과 요약을 생성한다."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                }
            ]
        )

        content = response.choices[0].message.content or "{}"
        data = json.loads(content)

        return DashboardInsightLLMResult(
            selected_labels=data.get("selected_labels", [])[:5],
            title=data.get("title", "이상거래 증가 원인 분석"),
            summary=data.get("summary", "이상거래 증가 원인을 분석했습니다.")
        )

    def _to_safe_candidates(
            self,
            candidates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        safe_candidates: list[dict[str, Any]] = []

        for candidate in candidates:
            safe_candidates.append(
                {
                    "label": candidate.get("label"),
                    "current_count": candidate.get("current_count"),
                    "previous_count": candidate.get("previous_count"),
                    "increase_count": candidate.get("increase_count"),
                    "increase_rate": candidate.get("increase_rate"),
                    "suspicious_amount": candidate.get("suspicious_amount"),
                }
            )

        return safe_candidates

    def _build_prompt(
        self,
        *,
        current_count: int,
        previous_count: int,
        candidates: list[dict[str, Any]]
    ) -> str:
        payload = {
            "current_count": current_count,
            "previous_count": previous_count,
            "candidates": candidates[:20],
            "output_format": {
                "selected_labels": [
                    "후보 label 중 선택한 값만 넣기"
                ],
                "title": "대시보드 카드 제목",
                "summary": "2문장 이내 요약",
            },
        }

        return (
            "아래 JSON은 비식별 집계 결과다.\n"
            "대시보드에 표시할 핵심 이상징후 3~5개를 선택해.\n"
            "selected_labels에는 후보 label 중 선택한 값만 넣어.\n\n"
            
            f"{json.dumps(payload, ensure_ascii=False)}"
        )
