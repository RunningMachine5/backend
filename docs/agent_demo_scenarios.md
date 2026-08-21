# Agent 최종 시연 시나리오

최종 시연에서는 모든 Seed 사건을 순서대로 보여주기보다, 유형 분류 상태가 다른 아래
시나리오를 선택해 Agent 동작 차이를 설명한다.

## 사전 준비

```powershell
uv run python -m app.scripts.seed_agent_similar_cases
uv run python -m app.scripts.index_agent_guides
```

유사 완료 사건 Seed는 4개 사기 유형별 9건, 총 36건이다. 각 사건은
`AGENT_CASES=COMPLETED`, `AGENT_REVIEWS.decision=CONFIRMED_FRAUD` 상태로 저장되므로,
대시보드 유사 사례 검색 후보로 사용된다.

## 명확한 유형 분류 시연

아래 사건은 1위 유형 점수 `0.90`, 2위 유형 점수 `0.30 이하`로 Rule 유형을 바로 적용한다.

| 사기 유형 | 사건 ID | 위험등급 | 대표 Rule 근거 |
| --- | --- | --- | --- |
| 보이스피싱 | `DEMO-CASE-01-09` | VERY_HIGH | 전화번호 조작, 한도 행동, 대출 맥락, 고액 거래, 신규 수취인, 원격제어 |
| 메신저피싱 | `DEMO-CASE-02-09` | VERY_HIGH | 원격제어, 강한 인증변경, 신규 수취인 이체, 취약 모바일 |
| 계정탈취 | `DEMO-CASE-03-09` | VERY_HIGH | 단말침해, 원격제어, 강한 인증변경, 불가능 이동, VPN·로밍, 접속 실패 |
| 사기이용계좌 | `DEMO-CASE-04-09` | VERY_HIGH | 수취계좌 정지, 고액입금, 반복 이체 |

발표 흐름은 다음과 같다.

```text
Rule 대표 유형 확정
→ 유형별 내부 정책 조회
→ 대응 가이드 RAG 검색
→ 대응 계획·체크리스트 생성
→ 유사 완료 사건 Top 3 표시
```

## 애매한 유형 분류·ReAct 조사 시연

아래 사건은 1위 점수 `0.57`, 2위 점수 `0.53`으로 점수 차이가 `0.04`다.
따라서 고정 임계값(1위 점수 0.60, 점수 차이 0.15)을 충족하지 못해 제한된 ReAct 조사 Agent가
과거 완료 사건을 비교한다.

| Rule 1위 유형 | 사건 ID | Rule 2위 유형 | 위험등급 |
| --- | --- | --- | --- |
| 보이스피싱 | `DEMO-CASE-01-06` | 메신저피싱 | VERY_HIGH |
| 메신저피싱 | `DEMO-CASE-02-06` | 계정탈취 | VERY_HIGH |
| 계정탈취 | `DEMO-CASE-03-06` | 사기이용계좌 | VERY_HIGH |
| 사기이용계좌 | `DEMO-CASE-04-06` | 보이스피싱 | VERY_HIGH |

발표 흐름은 다음과 같다.

```text
유형 점수 차이 작음
→ 유사 완료 사건 검색
→ 공통 Rule 근거·점수 분포·위험도 비교
→ 필요한 과거 사건만 상세조회
→ 추천 근거 또는 담당자 검토 필요 사유 생성
```

## 유형 미분류 시연

ML이 이상거래로 판단했지만 Rule 근거가 하나도 적중하지 않은 거래는 유사 완료 사건 Seed에
포함하지 않는다. 이런 사건을 사기 확정 완료 사건으로 저장하면 유사 사례 후보의 품질을
오염시킬 수 있기 때문이다.

해당 거래에서는 다음 결과를 확인한다.

```text
classification_status = UNCLASSIFIED
response_result.applied_fraud_type = UNCLASSIFIED
similar_case_results = []
information_status = INSUFFICIENT
```

대시보드에는 `유형 미분류`, `유형 판정 근거 부족으로 유사 사례 검색 생략`,
`공통 모니터링 검토 필요`로 표시한다.

## 데이터 재적재 확인

같은 명령을 다시 실행해도 고정 `case_id`를 기준으로 중복 생성하지 않는다.

```powershell
uv run python -m app.scripts.seed_agent_similar_cases
```

기대 결과이다.

```json
{
  "created_count": 0,
  "skipped_count": 36
}
```
