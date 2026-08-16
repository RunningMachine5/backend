"""실제 pgvector 검색과 LLM 호출로 대응 계획 생성 품질을 측정한다."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from sqlmodel import Session  # noqa: E402

from app.core.db import engine  # noqa: E402
from app.repositories.agent_guide import AgentGuideRepository  # noqa: E402
from app.services.agent.guide_embedder import OpenAIGuideEmbedder  # noqa: E402
from app.services.agent.guide_search import GuideSearchService  # noqa: E402
from app.services.agent.response_plan_evaluation import (  # noqa: E402
    evaluate_response_plans,
    load_response_plan_evaluation_cases,
)
from app.services.agent.response_plan_generator import (  # noqa: E402
    PolicyResponsePlanGenerator,
    RESPONSE_PLAN_MAX_COMPLETION_TOKENS,
    RESPONSE_PLAN_REASONING_EFFORT,
    RagResponsePlanGenerator,
)
from app.services.agent.response_policy import (  # noqa: E402
    get_default_policy_repository,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        help="평가 보고서를 저장할 JSON 파일 경로이다.",
    )
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument(
        "--reasoning-effort",
        default=RESPONSE_PLAN_REASONING_EFFORT,
    )
    parser.add_argument(
        "--max-completion-tokens",
        type=int,
        default=RESPONSE_PLAN_MAX_COMPLETION_TOKENS,
    )
    args = parser.parse_args()
    if args.repeat < 1 or args.top_k < 1:
        raise SystemExit("--repeat와 --top-k는 1 이상이어야 한다.")

    with Session(engine) as session:
        report = evaluate_response_plans(
            load_response_plan_evaluation_cases(),
            policy_repository=get_default_policy_repository(),
            guide_searcher=GuideSearchService(
                AgentGuideRepository(session),
                OpenAIGuideEmbedder(),
            ),
            policy_generator=PolicyResponsePlanGenerator(),
            rag_generator=RagResponsePlanGenerator(
                reasoning_effort=args.reasoning_effort,
                max_completion_tokens=args.max_completion_tokens,
            ),
            repeat=args.repeat,
            top_k=args.top_k,
        )

    output = json.dumps(
        {
            "configuration": {
                "model": os.getenv(
                    "AGENT_RESPONSE_PLAN_MODEL",
                    os.getenv("OPENAI_MODEL", "gpt-5"),
                ),
                "repeat": args.repeat,
                "top_k": args.top_k,
                "reasoning_effort": args.reasoning_effort,
                "max_completion_tokens": args.max_completion_tokens,
            },
            **report.to_dict(),
        },
        ensure_ascii=False,
        indent=2,
    )
    print(output)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")


if __name__ == "__main__":
    main()
