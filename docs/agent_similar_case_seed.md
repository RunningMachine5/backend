# 시연용 유사 완료 사건 Seed 데이터

유사 사건 조회와 대시보드 Top 3 시연에 사용할 완료 사건을 적재한다.

- 사기 유형 4개별 9건, 총 36건 생성
- 유형별 대표 근거뿐 아니라 단독 근거·복합 근거·HIGH·VERY_HIGH 조합 포함
- `AGENT_CASES.execution_status=COMPLETED` 저장
- `AGENT_REVIEWS.decision=CONFIRMED_FRAUD` 저장
- 현재 활성 Rule Set의 실제 구성요소 키 사용
- 고정 `case_id`를 사용하여 재실행 시 중복 사건을 만들지 않고 기존 사건의 위험도·검토 결과를 현재 Seed 값으로 동기화
- Agent 워크플로를 실행하지 않고 완료 사건을 직접 저장하므로 이메일은 발송하지 않음

백엔드와 PostgreSQL을 실행하고 마이그레이션을 적용한 뒤 다음 명령을 실행한다.

```powershell
uv run python -m app.scripts.seed_agent_similar_cases
```

최초 실행 결과 예시이다.

```json
{
  "rule_set_id": 1,
  "created_count": 36,
  "skipped_count": 0
}
```

같은 데이터로 다시 실행하면 중복 생성하지 않는다.

```json
{
  "rule_set_id": 1,
  "created_count": 0,
  "skipped_count": 36
}
```

단위 및 통합 테스트 명령이다.

```powershell
uv run python -m unittest tests.test_seed_agent_similar_cases -v
```
