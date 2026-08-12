# DB와 ML·Agent·Chat API가 완성될 때까지 가짜 원본 데이터를 공급하는 것

from copy import deepcopy
from typing import Any

class MockDashboardRepository:
    """DB와 외부 API가 준비되기 전 사용하는 Mock 저장소"""

    def __init__(self) -> None:
        # 유사 사건
        self.case_references = {
            "CASE-20260810-0001": {
                "case_id": "CASE-20260810-0001",
                "transaction_id": "TX-20260810-0001",
            }
        }

        # 거래 정보
        self.transactions = {
            "TX-20260810-0001": {
                "transaction_id": "TX-20260810-0001",
                "transaction_datetime": (
                    "2026-08-10T10:18:00+09:00"
                ),
                "transaction_amount": 50_000_000,
                "channel": "MOBILE",
                "location": "서울",
                "customer_id": "CUS-001",
                "source_account_id": "ACC-001",
                "recipient_account_id": "ACC-002",
            }
        }

        # 덕현님 에이전트 정보
        self.agent_results = {
            "CASE-20260810-0001": {
                "case_id": "CASE-20260810-0001",
                "transaction_id": "TX-20260810-0001",
                "execution_status": "COMPLETED",
                "failure_reason": None,
                "risk_score": 87,
                "risk_grade": "VERY_HIGH",
                "rule_result": {
                    "rule_filter_status": "APPLIED",
                    "primary_fraud_type": "ACCOUNT_TAKEOVER",
                    "type_scores": {
                        "VOICE_PHISHING": 0.20,
                        "MESSENGER_PHISHING": 0.58,
                        "ACCOUNT_TAKEOVER": 0.62,
                        "FRAUD_USED_ACCOUNT": 0.15,
                    },
                    "matched_components": [
                        {
                            "fraud_type": "ACCOUNT_TAKEOVER",
                            "evidence_code": (
                                "REMOTE_CONTROL_DETECTED"
                            ),
                            "observed_value": True,
                            "contribution": 0.25,
                        }
                    ],
                },
                "investigation_result": {
                    "classification_status": "AMBIGUOUS",
                    "score_margin": 0.04,
                    "investigation_status": "COMPLETED",
                    "recommended_fraud_type": (
                        "ACCOUNT_TAKEOVER"
                    ),
                    "recommendation_reason": (
                        "유사한 계정탈취 확정 사건이 "
                        "확인되었습니다."
                    ),
                },
                "similar_case_results": [
                    {
                        "similar_case_id": (
                            "CASE-20260728-0012"
                        ),
                        "similarity_rank": 1,
                        "similarity_score": 0.91,
                        "similarity_reason": (
                            "원격제어와 인증정보 변경 "
                            "근거가 공통입니다."
                        ),
                    }
                ],
                "response_result": {
                    "applied_fraud_type": "ACCOUNT_TAKEOVER",
                    "information_status": "SUFFICIENT",
                    "summary": (
                        "원격제어와 인증정보 변경 정황이 "
                        "확인된 고위험 계정탈취 의심 사건입니다."
                    ),
                    "recommended_actions": [
                        {
                            "priority": 1,
                            "action_code": (
                                "VERIFY_CUSTOMER_TRANSACTION"
                            ),
                            "action": (
                                "고객에게 본인 거래 여부를 "
                                "확인합니다."
                            ),
                            "reason": (
                                "원격제어 정황이 탐지되었습니다."
                            ),
                            "required": True,
                            "procedure_steps": [
                                "등록된 연락처로 고객에게 연락합니다."
                            ],
                            "cautions": [
                                "OTP나 인증번호를 요구하지 않습니다."
                            ],
                        }
                    ],
                    "checklist": [
                        {
                            "item_code": (
                                "CUSTOMER_TRANSACTION_CONFIRMED"
                            ),
                            "label": (
                                "고객 본인 거래 여부를 확인했는가"
                            ),
                            "required": True,
                        }
                    ],
                },
                "generation_metadata": {
                    "agent_version": "1.0.0",
                    "total_latency_ms": 6240,
                },
                "created_at": "2026-08-10T10:18:01+09:00",
                "completed_at": "2026-08-10T10:18:07+09:00",
            }
        }

        # 채팅 정보
        self.chat_sessions = {
            "CASE-20260810-0001": {
                "chat_session_id": "CHAT-001",
                "case_id": "CASE-20260810-0001",
                "status": "AI_ACTIVE",
                "assigned_reviewer_id": None,
                "started_at": "2026-08-10T10:31:00+09:00",
                "closed_at": None,
                "messages": [
                    {
                        "message_id": "MSG-001",
                        "sender_type": "AGENT",
                        "sender_id": None,
                        "message_text": (
                            "신규 수취인 고액 이체가 "
                            "탐지되었습니다."
                        ),
                        "sent_at": "2026-08-10T10:31:00+09:00",
                    },
                    {
                        "message_id": "MSG-002",
                        "sender_type": "REVIEWER",
                        "sender_id": "MONITOR-001",
                        "message_text": (
                            "고객 본인 확인을 진행합니다."
                        ),
                        "sent_at": "2026-08-10T10:32:00+09:00",
                    },
                ],
            }
        }

        self.reviews: dict[str, dict[str, Any]] = {}

    def get_case_reference(
            self,
            case_id: str,
    ) -> dict[str, Any] | None:
        """유사 사건 참조 정보 조회"""
        return self._copy(self.case_references.get(case_id))

    def get_transaction(
            self,
            transaction_id: str,
    ) -> dict[str, Any] | None:
        """거래 정보 조회"""
        return self._copy(self.transactions.get(transaction_id))

    def get_ml_result(
            self,
            transaction_id: str,
    ) -> dict[str, Any] | None:
        """ML 결과 조회"""
        return self._copy(self.ml_results.get(transaction_id))

    def get_agent_result(
        self,
        case_id: str,
    ) -> dict[str, Any] | None:
        return self._copy(self.agent_results.get(case_id))

    def get_chat_session(
        self,
        case_id: str,
    ) -> dict[str, Any] | None:
        return self._copy(self.chat_sessions.get(case_id))

    def get_review(
        self,
        case_id: str,
    ) -> dict[str, Any] | None:
        return self._copy(self.reviews.get(case_id))

    def list_cases(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []

        for case_id, reference in self.case_references.items():
            transaction_id = reference["transaction_id"]
            transaction = self.transactions[transaction_id]
            agent = self.agent_results.get(case_id) or {}
            rule_result = agent.get("rule_result") or {}

            rows.append(
                {
                    "case_id": case_id,
                    "transaction_id": transaction_id,
                    "execution_status": agent.get(
                        "execution_status",
                        "PROCESSING",
                    ),
                    "risk_score": agent.get("risk_score"),
                    "risk_grade": agent.get("risk_grade"),
                    "primary_fraud_type": rule_result.get(
                        "primary_fraud_type"
                    ),
                    "transaction_amount": transaction[
                        "transaction_amount"
                    ],
                    "transaction_datetime": transaction[
                        "transaction_datetime"
                    ],
                    "review_status": (
                        "COMPLETED"
                        if case_id in self.reviews
                        else "PENDING"
                    ),
                }
            )

        return deepcopy(rows)

    def get_summary(self) -> dict[str, int]:
        cases = self.list_cases()

        return {
            "pending_case_count": sum(
                item["review_status"] == "PENDING"
                for item in cases
            ),
            "very_high_case_count": sum(
                item["risk_grade"] == "VERY_HIGH"
                for item in cases
            ),
            "suspicious_amount": sum(
                item["transaction_amount"] for item in cases
            ),
            "completed_case_count": sum(
                item["review_status"] == "COMPLETED"
                for item in cases
            ),
            "email_required_count": sum(
                item["risk_grade"] == "VERY_HIGH"
                for item in cases
            ),
            "prevented_amount": 0,
        }

    @staticmethod
    def _copy(
        value: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        return deepcopy(value) if value is not None else None