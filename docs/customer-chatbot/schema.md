# 챗봇 스키마

## `chat_sessions` 대화 상태 컬럼

| 컬럼 | 설명 |
|---|---|
| `status` | 이메일 발송부터 상담사 요청·종료까지의 생명주기 |
| `conversation_phase` | `DISCRIMINATION`, `FREE_CHAT`, `HANDOFF_PENDING`, `NORMAL_GUIDE` |
| `discrimination_question_id` | 현재 퀵리플라이 질문. 판별 단계가 아니면 `NULL` |
| `ownership_answer` | 최초 본인 거래 응답 `ANSWER_YES` 또는 `ANSWER_NO` |
| `confirmed_fraud_type` | 네/아니요 판별로 확정된 상위 사기유형 |
| `top_fraud_types` | Rule Engine 1·2순위 유형 |
| `top_fraud_type_scores` | 위 유형에 대응하는 원본 0~1 점수 |

## `chat_discrimination_actions`

퀵리플라이 재전송을 멱등 처리한다.

- 유니크 키: `(chat_session_id, request_id)`
- 입력 원본: `question_id`, `action`
- 재전송 결과: `response_payload`

판별 액션 처리 중에는 `chat_sessions` 행을 `FOR UPDATE`로 잠가 서로 다른 동시 클릭도
같은 질문을 중복 진행하지 못하게 한다.

사기 정황과 챗봇 재채점 테이블은 사용하지 않는다.
