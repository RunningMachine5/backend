"""고정 평가 질의로 대응 가이드 검색 품질 개선 전후를 측정한다."""

from __future__ import annotations

import json

from dotenv import load_dotenv

load_dotenv()

from sqlmodel import Session  # noqa: E402

from app.core.db import engine  # noqa: E402
from app.repositories.agent_guide import AgentGuideRepository  # noqa: E402
from app.services.agent.guide_embedder import OpenAIGuideEmbedder  # noqa: E402
from app.services.agent.guide_evaluation import (  # noqa: E402
    evaluate_guide_search,
    load_guide_evaluation_cases,
)
from app.services.agent.guide_search import GuideSearchService  # noqa: E402


def main() -> None:
    with Session(engine) as session:
        service = GuideSearchService(
            AgentGuideRepository(session),
            OpenAIGuideEmbedder(),
        )
        report = evaluate_guide_search(load_guide_evaluation_cases(), service)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
