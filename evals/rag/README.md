# 고객 챗봇 RAG 골든셋

`datasets/golden_v1.jsonl`은 `docs/embed_target_pdfs/`의 PDF를 근거로 만든 평가 초안이다.
한 줄이 한 평가 사례이며 `golden_set.schema.json`이 필드 계약을 정의한다.

## 구성

| category | 건수 | 기대 동작 |
|---|---:|---|
| `single_hop` | 45 | 한 문서의 한 가이드 페이지 근거로 바로 답변 |
| `conditional` | 5 | 질문에 주어진 조건을 판별한 뒤 해당 분기의 답변을 선택 |
| `multi_document_reasoning` | 10 | 서로 다른 PDF의 근거를 연결해 하나의 결론과 조치 순서를 도출 |
| `multi_source` | 20 | 분해된 여러 요구를 근거별로 답하고 최종 응답으로 통합 |
| `ambiguous` | 10 | 내용을 추측하지 않고 추가 정보를 질문 |
| `unanswerable` | 10 | 문서 근거 또는 실행 권한이 없음을 밝히고 답변 거절 |

답변 가능한 80건은 `reference_contexts`에 PDF 파일명, 실제 PDF 페이지 번호와
해당 페이지에서 추출한 근거 문장을 저장한다. 모호 질문의 `derived_from`은 어떤
정상 사례에서 핵심 정보를 제거해 만들었는지 추적한다.

`multi_source`는 상위 `reference_answer`와 `required_claims`를 최종 통합 응답
평가에 사용한다. 하위 `subtasks`는 각 검색 질의를 해당 PDF 근거와 필수 주장에
연결하므로 질의별 검색 성능, 섹션별 답변 충실도와 다른 질의의 근거를 잘못 사용하는
교차 오염을 따로 평가할 수 있다.

41~45번은 행동 설명이 앞 페이지에 있고 대응 가이드가 바로 다음 페이지에 있는
페이지 경계 사례다. 기존 `single_hop`의 평가 기준과 `reference_contexts` 구조는
그대로 유지하고, 실제 답변 근거인 가이드 페이지 하나만 저장한다. 별도 평가 지표를
추가하지 않고 `page_boundary`, `behavior_page:<문서>:<페이지>`,
`guide_page:<문서>:<페이지>` 태그로 해당 5건의 성능만 슬라이스해 비교한다.

46~50번 `conditional`의 `conditions`는 조건별 기대 주장을 분리한다. 답변이 모든
분기의 내용을 나열했는지만 보지 말고, 사용자에게 해당하는 조건을 올바르게 판별해
그 분기의 조치를 선택했는지 평가한다.

51~60번 `multi_document_reasoning`의 `reasoning_steps`는 문서별 근거를 어떤
순서로 연결해야 최종 결론에 도달하는지 정의한다. 이 유형은 여러 독립 질문을 단순히
따로 답하는 `multi_source`와 달리, 둘 이상의 문서 근거가 하나의 판단이나 조치
순서에 실제로 사용됐는지를 평가한다.

## 생성과 검증

```bash
uv run python -m app.scripts.build_rag_golden_dataset
uv run python -m unittest tests.test_rag_golden_dataset -v
```

생성 스크립트는 각 근거의 anchor가 지정 PDF 페이지에 없으면 실패한다. 테스트는
수량, ID, 카테고리별 동작, 조건 분기, 문서 간 추론 단계와 모든 인용문의 실제 PDF
존재를 검증한다. 생성 시 100개 사례를 ID별로 설명한
[`datasets/golden_v1_catalog.md`](datasets/golden_v1_catalog.md)도 함께 갱신된다.

## 검수 상태

현재 100건은 모두 `review_status=DRAFT`다. PDF 근거 존재 여부는 자동 검증됐지만,
금융상담 골든셋으로 확정하려면 도메인 담당자가 모범 답변, 필수·금지 주장과 문서의
시점 유효성을 검토한 뒤 별도 승인 절차로 상태를 변경해야 한다. 이미지형인 `C01.pdf`는
텍스트 추출 결과가 없어 이번 초안의 근거 문서에서 제외했다.
