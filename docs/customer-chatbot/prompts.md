# 챗봇 LLM 계약

## 유형 판별

LLM을 사용하지 않는다. 서버가 현재 `question_id`와 `ANSWER_YES|ANSWER_NO`를 검증해
결정적으로 분기한다.

유형 확정 직후에는 유형별로 미리 정제한 검색 질의를 `GuideResponder`에 직접 전달한다.
이 시스템 질의는 고객 원문에서 추출한 것이 아니므로 `chat_guide_search_queries`에 가짜
고객 근거로 저장하지 않는다.

## 자유 대화

`FREE_CHAT`과 `HANDOFF_PENDING`에서 고객 자유 텍스트를 `AnswerAnalyzer`가 판정하고
검색 질의로 분해한다. `SUFFICIENT`이면 `GuideResponder`, `TOO_VAGUE` 또는 분석 실패면
구체화 안내, `WANT_END`이면 종료 안내로 간다.

사기 정황 추출 프롬프트와 채점 계약은 폐기했다.
