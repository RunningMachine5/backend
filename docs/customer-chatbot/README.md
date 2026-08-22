# 고객 대응 챗봇

고객 챗봇은 Rule Engine 상위 2개 유형을 네/아니요 퀵리플라이로 판별한 뒤 대응
가이드를 제공한다. 판별 단계에서는 LLM을 호출하지 않고, 가이드 이후 자유 대화에서만
`AnswerAnalyzer → GuideResponder`를 사용한다.

PDF 가이드의 청킹·인덱싱·평가·롤백 절차는
[RAG 인덱싱](./rag-indexing.md)을 참고한다.

## 상태

세션 생명주기 `status`와 고객 대화 단계 `conversation_phase`를 분리한다.

- 생명주기: `URL_SENT`, `IN_PROGRESS`, `HANDOFF_REQUESTED`, `DONE`, `FAILED`
- 대화 단계: `DISCRIMINATION`, `FREE_CHAT`, `HANDOFF_PENDING`, `NORMAL_GUIDE`

`DISCRIMINATION`의 현재 질문은 `OWNERSHIP`, `PRIMARY_CHECK`,
`SECONDARY_CHECK`로 저장한다. 본인 거래 응답과 확정 유형도 세션에 영속화한다.

## 판별 규칙

1·2위 Rule Engine 원본 점수 차이가 `0.15` 이상이면 확정 케이스, 미만이면 애매
케이스다.

- 확정: 본인 거래 `아니요`면 1순위 확정. `네`면 1순위 확인 질문을 하고,
  `네`는 1순위 확정, `아니요`는 정상 가이드다.
- 애매: 본인 거래 응답을 저장하고 1순위, 2순위 순서로 질문한다. 첫 `네` 유형을
  확정한다. 둘 다 `아니요`면 본인 거래 `네`는 정상 가이드, `아니요`는 상담사
  연결이다.

유형 확정 시 `fraud_type_confirmed` SSE 이벤트를 먼저 보내고, 유형별 고정 검색
질의로 `GuideResponder`를 호출한다. 이 경로에서는 `AnswerAnalyzer`를 호출하지 않는다.

## API

- `POST /chat/{id}/verify`: 본인 확인 후 안내와 `OWNERSHIP` 질문을 함께 생성
- `GET /chat/{id}`: 재접속 상태, `input_mode`, `quick_replies`, `question_id` 복원
- `POST /chat/{id}/discrimination-actions`: `ANSWER_YES|ANSWER_NO`, `question_id`,
  `request_id`를 받는 SSE 경로
- `POST /chat/{id}/messages`: `FREE_CHAT`과 `HANDOFF_PENDING` 자유 대화 SSE 경로
- `GET /transactions/{id}/chat-session`: 담당자용 생명주기 상태
- `GET /transactions/{id}/chat-session/detail`: 대화 전문과 `confirmed_fraud_type`

판별 액션은 세션 행 잠금과 `(chat_session_id, request_id)` 유니크 키로 중복 처리를
막는다. 같은 `request_id`와 같은 입력의 재전송은 저장된 완료 결과를 반환하고, 다른
입력에 같은 키를 사용하면 거부한다.

재접속 응답은 확정 유형을 포함하지만 `fraud_type_confirmed` 팝업 이벤트를 다시
전송하지 않는다.

## 제거된 기능

- 최초 `START_CHAT`, `REQUEST_HANDOFF`, `END_CHAT` 버튼
- 유형 조합별 자유 텍스트 판별 질문과 `GREETING`
- 사기 정황 추출 프롬프트와 백그라운드 작업
- `chat_fraud_circumstances`, `fraud_type_score_after_chat`
- 챗봇 재채점 점수 SSE와 담당자 상세 `type_scores`
- 챗봇 재채점 결과로 Agent 적용 유형을 덮어쓰는 처리
