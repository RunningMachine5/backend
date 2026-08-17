# Backend 로컬 거래 E2E 데이터 주입

로컬 Backend와 ML Serving을 연결해 거래 저장, ML 판정, 룰 점수, 확정 라벨
저장까지 확인한다. 기본 샘플은 정상 1,800건과 이상거래 200건, 총 2,000건이다.

```text
train1 raw64 샘플
→ POST /transactions
→ Backend가 고객·계좌 조회 및 raw51 Feature 생성
→ ML Serving flat raw52 요청(transaction_id + raw51)
→ model79 추론
→ 이상거래만 ACTIVE 룰셋 적용
→ PUT /transactions/{id}/label
```

## 파일

```text
backend/test-data-injection/
├─ inject_transactions.ps1
└─ data/
   └─ transactions_raw64_2000.csv
```

| 라벨 | 건수 |
|---|---:|
| 정상 (`is_fraud=0`) | 1,800 |
| 이상 (`is_fraud=1`) | 200 |
| 합계 | 2,000 |

CSV는 Backend 저장소에 미리 포함한다. 이 90:10 비율은 E2E 확인용이며 운영
분포나 모델 성능을 대표하지 않는다.

## 실행

ML Serving과 Backend를 먼저 실행한 뒤 상위 `RunningMachine5` 폴더에서 실행한다.

```powershell
.\backend\test-data-injection\inject_transactions.ps1
```

기본 실행은 2,000건 모두를 처리한다. 스크립트는 순차 실행이므로
`-TransactionsPerSecond`는 요청 시작 속도의 상한일 뿐 실제 처리량을 보장하지 않는다.

```powershell
.\backend\test-data-injection\inject_transactions.ps1 `
  -TransactionsPerSecond 10
```

소량만 확인할 때는 정상·이상 건수를 함께 지정한다.

```powershell
.\backend\test-data-injection\inject_transactions.ps1 `
  -NormalRowLimit 2 `
  -FraudRowLimit 8 `
  -TransactionsPerSecond 1
```

Agent까지 기다리려면 소량 실행에만 `-WaitForAgent`를 추가한다. 이 옵션은 실제
이메일이나 OpenAI 호출을 발생시킬 수 있으므로 테스트 수신 주소를 먼저 확인한다.

## 현재 API 매핑

`POST /transactions`에는 raw64 전체가 아니라 외부에서 받는 거래 원천값만 보낸다.

| API 필드 | CSV 필드 |
|---|---|
| `customer_id` | 현재 테스트에서는 `null` |
| `source_account_number` | `account_account_number` |
| `recipient_account_number` | 같은 이름의 필수 문자열 |
| `num_connection_failure` | `transaction_num_connection_failure` |
| `location_lat`, `location_lon` | `location` 끝의 위도·경도 |
| 거래·채널·단말·보안 플래그 | 같은 의미의 컬럼 |

Backend가 고객·계좌 원장을 조회하고 없는 데이터는 로컬 임시값으로 보완한 뒤 ML
raw51 Feature를 계산한다. `is_fraud`는 거래 요청에 넣지 않고, Backend가 발급한
정수 거래 ID로 라벨 API에 따로 저장한다.

## 필수 확인만 수행

주입 스크립트는 다음만 실패 조건으로 본다.

- Backend health와 거래 API 호출 성공
- 필수 CSV 컬럼 존재
- 양의 정수 `transaction_id`
- `prediction_status=COMPLETED`와 유효한 판정 확률
- 이상거래의 룰 결과, 정상거래의 룰 미적용
- 확정 라벨 저장 성공

모델명·버전, SHAP 개수, CSV 64열 전체 일치 같은 검증은 이 거래 E2E의 책임이
아니므로 강제하지 않는다. 해당 계약은 ML 저장소 테스트에서 확인한다.

## 확인 URL

- Backend Swagger: <http://127.0.0.1:8000/docs>
- Backend health: <http://127.0.0.1:8000/health>
- ML Serving health: <http://127.0.0.1:8001/health>
- 최근 거래: <http://127.0.0.1:8000/transactions>

오류가 나면 거래 POST의 상태 코드와 Backend·ML Serving 컨테이너 로그를 먼저
확인한다. `422`는 요청 필드나 타입 불일치, `prediction_status=FAILED`는 Backend에서
ML Serving까지의 호출 실패를 뜻한다.
