"""고객 데이터 없이 Cohere 리랭커 연결 오류를 진단한다."""

from __future__ import annotations

import logging
from typing import Any

import cohere


logger = logging.getLogger(__name__)


def diagnose_cohere_reranker(
    *,
    api_key: str,
    model: str,
    timeout_seconds: float,
    client: Any | None = None,
) -> bool:
    """고정된 비민감 예제로 Cohere를 직접 호출하고 원본 오류 정보를 기록한다."""

    normalized_key = api_key.strip()
    logger.info(
        "Cohere 리랭커 진단 시작: sdk=%s model=%s key_present=%s "
        "key_length=%s timeout=%ss",
        getattr(cohere, "__version__", "unknown"),
        model,
        bool(normalized_key),
        len(normalized_key),
        timeout_seconds,
    )
    if not normalized_key:
        logger.error("Cohere 리랭커 진단 실패: COHERE_API_KEY가 비어 있습니다")
        return False

    try:
        active_client = client or cohere.ClientV2(
            api_key=normalized_key,
            timeout=timeout_seconds,
            client_name="fdshield-backend-diagnostics",
        )
        response = active_client.rerank(
            model=model,
            query="계좌 지급정지 방법",
            documents=["금융회사에 피해 사실을 신고하고 계좌 지급정지를 요청합니다."],
            top_n=1,
        )
    except Exception as exc:
        _log_exception_chain(exc, api_key=normalized_key)
        return False

    results = getattr(response, "results", None)
    if not isinstance(results, list) or len(results) != 1:
        logger.error(
            "Cohere 리랭커 진단 실패: 응답 results 계약이 올바르지 않습니다: type=%s",
            type(results).__name__,
        )
        return False

    billed_units = getattr(getattr(response, "meta", None), "billed_units", None)
    logger.info(
        "Cohere 리랭커 진단 성공: index=%s relevance_score=%s search_units=%s",
        getattr(results[0], "index", None),
        getattr(results[0], "relevance_score", None),
        getattr(billed_units, "search_units", None),
    )
    return True


def _log_exception_chain(exc: Exception, *, api_key: str) -> None:
    """키를 가린 상태로 SDK 예외와 연결된 HTTP 응답을 순서대로 기록한다."""

    current: BaseException | None = exc
    seen: set[int] = set()
    depth = 0
    while current is not None and id(current) not in seen and depth < 5:
        seen.add(id(current))
        response = getattr(current, "response", None)
        status_code = getattr(current, "status_code", None)
        if status_code is None and response is not None:
            status_code = getattr(response, "status_code", None)

        logger.error(
            "Cohere 리랭커 진단 예외[%s]: type=%s status=%s message=%s",
            depth,
            f"{type(current).__module__}.{type(current).__name__}",
            status_code,
            _redact(str(current), api_key),
        )
        response_text = _response_text(response)
        if response_text:
            logger.error(
                "Cohere 리랭커 HTTP 응답[%s]: %s",
                depth,
                _redact(response_text, api_key)[:2000],
            )

        current = current.__cause__ or current.__context__
        depth += 1


def _response_text(response: Any | None) -> str:
    if response is None:
        return ""
    try:
        return str(response.text)
    except Exception:
        return ""


def _redact(value: str, api_key: str) -> str:
    return value.replace(api_key, "***") if api_key else value


__all__ = ["diagnose_cohere_reranker"]
