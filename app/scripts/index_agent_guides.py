"""Markdown 대응 가이드 코퍼스를 실제 임베딩하여 pgvector에 적재한다."""

from __future__ import annotations

import json
from dataclasses import asdict

from dotenv import load_dotenv

load_dotenv()

from sqlmodel import Session  # noqa: E402

from app.core.db import engine  # noqa: E402
from app.repositories.agent_guide import AgentGuideRepository  # noqa: E402
from app.services.agent.guide_embedder import OpenAIGuideEmbedder  # noqa: E402
from app.services.agent.guide_indexing import GuideIndexingService  # noqa: E402


def main() -> None:
    with Session(engine) as session:
        result = GuideIndexingService(
            AgentGuideRepository(session),
            OpenAIGuideEmbedder(),
        ).index_corpus()
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
