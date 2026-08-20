# 담당자 검토 기반 Agent 설정 후보 평가

이 도구는 `agent_reviews`에 누적된 담당자 검토 결과를 정답으로 사용해, Agent의 고정
유형 확실성 기준과 유사 사례 최소 유사도 후보를 비교한다. 평가 결과는 **자동 적용하지
않는다**. 팀이 보고서를 검토한 뒤 `TypeConfidenceThresholds`와
`CaseSimilarityConfig`의 값을 변경한다.

## 실행

```powershell
uv run --env-file .env python -m app.scripts.evaluate_agent_review_calibration `
  --output reports/agent_review_calibration.json `
  --csv-output reports/agent_review_calibration.csv
```

시연용 Seed 36건을 제외하고 실제 담당자 검토 사건만 보려면 다음을 사용한다.

```powershell
uv run --env-file .env python -m app.scripts.evaluate_agent_review_calibration `
  --exclude-seed
```

## 비교 후보

각 유형 확실성 기준과 유사 사례 최소 유사도를 조합해 9개 후보를 비교한다.

| 기준 | 1위 유형 최소 점수 | 1·2위 최소 점수 차이 |
| --- | ---: | ---: |
| 민감 | 0.50 | 0.10 |
| 기준 | 0.60 | 0.15 |
| 엄격 | 0.70 | 0.20 |

유사 사례 최소 유사도는 `0.55`, `0.60`, `0.65`를 각각 적용한다. 유사도 가중치 자체는
현재 운영 기준을 유지하고, 이번 평가에서는 후보가 늘어나는 것을 막기 위해 최소 유사도만
비교한다.

## 지표

- `ambiguous_rate`: 사기로 확정된 검토 사건 중 Agent 조사가 필요한 `AMBIGUOUS` 비율
- `confident_type_agreement_rate`: `CONFIDENT`로 분류된 사건 중 Rule 1위 유형이 담당자
  확정 유형과 일치한 비율
- `unsafe_confident_count`: `CONFIDENT`인데 Rule 1위 유형이 담당자 확정 유형과 다른 건수
- `unnecessary_investigation_rate`: Rule 1위 유형은 담당자 확정 유형과 같지만 `AMBIGUOUS`가
  되어 조사가 추가되는 비율
- `similarity_coverage_rate`: 다른 완료 사건 중 최소 유사도 이상 Top 1을 찾은 비율
- `similarity_precision_at_1`: Top 1 유사 완료 사건의 담당자 확정 유형이 현재 사건과 같은 비율

`CONFIRMED_FRAUD`와 `confirmed_fraud_type`이 모두 있는 사건만 유형·유사도 지표의 분모로
사용한다. 따라서 오탐(`FALSE_POSITIVE`)은 전체 검토 건수에는 포함되지만, 사기 유형 일치의
정답으로 사용하지 않는다.

## 해석 원칙

Seed 사건은 구조 검증과 시연에 유용하지만, 유형별 패턴이 의도적으로 정리되어 있어 실제
운영 임계값을 확정하는 근거로는 부족하다. 실제 담당자 검토 결과가 누적된 뒤 같은 명령을
반복 실행하고, `unsafe_confident_count`가 증가하지 않는 후보 중 불필요 조사율이 낮은 설정을
팀 검토 후 채택한다.
