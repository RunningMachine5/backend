"""8월 테스트 거래 데이터를 읽어 /transactions 엔드포인트로 실시간 전송하는 스트리머 스크립트.

기본적으로 초당 1개(1.0초 간격)씩 POST /transactions 로 전송하며,
실시간 FDS 탐지 결과(ML 예측 확률, 룰 점수, 승인/거절 상태)를 콘솔에 출력합니다.

사용법:
    # 기본 실행 (초당 1건, http://localhost:8000/transactions)
    python3 scripts/stream_transactions_api.py

    # 옵션 지정 실행 (0.5초 간격, 최대 100건만 전송)
    python3 scripts/stream_transactions_api.py --interval 0.5 --max-count 100

    # 현재 시각으로 transaction_datetime을 실시간 갱신하며 전송
    python3 scripts/stream_transactions_api.py --use-current-time --loop
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime


# ─── DTO 타입 변환기 ──────────────────────────────────────────────

INT_FIELDS = {
    "customer_id",
    "transaction_amount",
    "num_connection_failure",
}

FLOAT_FIELDS = {
    "location_lat",
    "location_lon",
}

BOOL_FIELDS = {
    "customer_rooting_jailbreak_indicator",
    "customer_mobile_roaming_indicator",
    "customer_vpn_indicator",
    "customer_flag_terminal_malicious_behavior_1",
    "customer_flag_terminal_malicious_behavior_2",
    "customer_flag_terminal_malicious_behavior_3",
    "customer_flag_terminal_malicious_behavior_5",
    "customer_flag_terminal_malicious_behavior_6",
}


def parse_csv_row_to_payload(row: dict[str, str], use_current_time: bool = False) -> dict:
    """CSV 한 행을 TransactionRequestDTO JSON payload 로 변환."""
    payload: dict = {}
    for key, value in row.items():
        if value is None or value == "":
            payload[key] = None
        elif key in INT_FIELDS:
            payload[key] = int(value)
        elif key in FLOAT_FIELDS:
            payload[key] = float(value)
        elif key in BOOL_FIELDS:
            payload[key] = value in ("True", "true", "1", 1, True)
        else:
            payload[key] = value

    if use_current_time:
        payload["transaction_datetime"] = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S%z")

    return payload


# ─── API 전송 함수 ───────────────────────────────────────────────

def send_transaction(url: str, payload: dict, timeout: float = 5.0) -> tuple[int, dict | str, float]:
    """HTTP POST 요청 전송 및 응답 반환."""
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data_bytes,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )

    start_time = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            status_code = response.getcode()
            body_text = response.read().decode("utf-8")
            try:
                body_json = json.loads(body_text)
                return status_code, body_json, latency_ms
            except json.JSONDecodeError:
                return status_code, body_text, latency_ms
    except urllib.error.HTTPError as e:
        latency_ms = (time.perf_counter() - start_time) * 1000.0
        error_body = e.read().decode("utf-8")
        try:
            return e.code, json.loads(error_body), latency_ms
        except json.JSONDecodeError:
            return e.code, error_body, latency_ms
    except Exception as e:
        latency_ms = (time.perf_counter() - start_time) * 1000.0
        return 0, str(e), latency_ms


# ─── 메인 실행 루프 ───────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="FDS 실시간 거래 데이터 전송 시뮬레이터 (초당 N개 스트리밍)"
    )
    parser.add_argument(
        "--file",
        type=str,
        default="./dummy_data/transactions_august.csv",
        help="전송할 CSV 파일 경로 (기본: ./dummy_data/transactions_august.csv)",
    )
    parser.add_argument(
        "--url",
        type=str,
        default="http://localhost:8000/transactions",
        help="FDS 거래 API 엔드포인트 URL (기본: http://localhost:8000/transactions)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="전송 주기 (초 단위, 기본: 1.0초 = 초당 1개)",
    )
    parser.add_argument(
        "--max-count",
        type=int,
        default=None,
        help="전송할 최대 건수 (기본: 전체)",
    )
    parser.add_argument(
        "--use-current-time",
        action="store_true",
        help="transaction_datetime을 실제 현재 시간으로 갱신하여 전송",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="파일 끝에 도달하면 처음부터 무한 반복 전송",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not os.path.exists(args.file):
        print(f"❌ 파일을 찾을 수 없습니다: {args.file}", file=sys.stderr)
        sys.exit(1)

    print("================================================================================")
    print(" 🚀 FDS Real-Time Transaction Streamer Started")
    print(f" - Target Endpoint : {args.url}")
    print(f" - Source CSV File : {args.file}")
    print(f" - Send Interval   : {args.interval}s (TPS: {1.0 / args.interval:.1f}/sec)")
    print(f" - Use Current Time: {args.use_current_time}")
    print(f" - Loop Mode       : {args.loop}")
    print("================================================================================")

    sent_count = 0
    approved_count = 0
    declined_count = 0
    error_count = 0

    try:
        while True:
            with open(args.file, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row_idx, raw_row in enumerate(reader, start=1):
                    if args.max_count is not None and sent_count >= args.max_count:
                        print("\n🏁 지정된 최대 전송 건수에 도달하여 종료합니다.")
                        return

                    payload = parse_csv_row_to_payload(raw_row, use_current_time=args.use_current_time)
                    status_code, response, latency = send_transaction(args.url, payload)

                    sent_count += 1
                    amount_str = f"{payload['transaction_amount']:,}원"
                    src_acc = payload["source_account_number"]
                    channel = payload["channel"]

                    if status_code in (200, 201) and isinstance(response, dict):
                        pred_status = response.get("prediction_status", "UNKNOWN")
                        tx_id = response.get("transaction_id", "-")
                        proba = response.get("predict_proba")
                        proba_str = f"{proba * 100:.1f}%" if proba is not None else "N/A"
                        rule_scores = response.get("rule_scores")

                        # 상태별 포맷팅
                        if pred_status == "DECLINED":
                            declined_count += 1
                            tag = "\033[91m[🚨 DECLINED / 차단]\033[0m"
                        else:
                            approved_count += 1
                            tag = "\033[92m[✅ APPROVED / 정상]\033[0m"

                        rule_summary = ""
                        if rule_scores and isinstance(rule_scores, dict):
                            active_rules = [f"{k}:{v:.2f}" for k, v in rule_scores.items() if v > 0]
                            if active_rules:
                                rule_summary = f" | Rules: {', '.join(active_rules)}"

                        print(
                            f"[{sent_count:04d}] {tag} ID:{tx_id} | {src_acc} | {amount_str:>11s} | "
                            f"{channel:<8s} | Prob: {proba_str:<6s}{rule_summary} ({latency:.1f}ms)"
                        )
                    else:
                        error_count += 1
                        print(
                            f"[{sent_count:04d}] \033[93m[⚠️ HTTP {status_code}]\033[0m {src_acc} | "
                            f"{amount_str:>11s} | Response: {response} ({latency:.1f}ms)"
                        )

                    time.sleep(args.interval)

            if not args.loop:
                break

    except KeyboardInterrupt:
        print("\n\n⏹️ 사용자에 의해 전송이 중단되었습니다.")

    print("\n================================================================================")
    print(" 📊 Transmission Summary")
    print(f" - Total Sent : {sent_count:,} requests")
    print(f" - Approved   : {approved_count:,} (정상 승인)")
    print(f" - Declined   : {declined_count:,} (이상 탐지 차단)")
    print(f" - Errors     : {error_count:,} (연결/요청 실패)")
    print("================================================================================")


if __name__ == "__main__":
    main()
