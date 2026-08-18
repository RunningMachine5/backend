from __future__ import annotations
from datetime import datetime

from app.dto.dashboard import(
    CaseAgentView,
    CaseDetailResponse,
    CaseListItemResponse,
    ChatMessageView,
    ChatView,
    MLView,
    SectionResult,
    SectionStatus,
    TransactionView
)
from app.repositories.case_query import CaseListRow, CaseQueryRepository

class CaseQueryService:
    def __init__(self, repository: CaseQueryRepository) -> None:
        self.repository = repository

    def list_cases(
        self,
        *,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        customer_id: str | None = None,
        ip_address: str | None = None,
        recipient_account_number: str | None = None,
        min_amount: int | None = None, # amount가 금액인가?
        max_amount: int | None = None,
        risk_grades: list[str] | None = None, # risk_grades 이거 덕현님 dto에서 받는 값 아님?
        page: int = 1,
        page_size: int = 50
    ) -> tuple[list[CaseListItemResponse], int]:
        if page < 1:
            raise ValueError("page는 1 이상이어야 함")
        if not 1<= page_size <= 100:
            raise ValueError("page_size는 1~100 사이어야 함")

        rows, total_count = self.repository.list_suspicious_cases(
            period_start=period_start,
            period_end=period_end,
            customer_id=customer_id,
            ip_address=ip_address,
            recipient_account_number=recipient_account_number,
            min_amount=min_amount,
            max_amount=max_amount,
            risk_grades=risk_grades,
            offset=(page-1)*page_size,
            limit=page_size,
        )

        return (
            [self._to_case_list_item(row) for row in rows],
            total_count
        )

    def get_case_detail(
            self,
            transaction_id: int
    ) -> CaseDetailResponse | None:
        transaction = self.repository.get_transaction(transaction_id)

        if transaction is None:
            return None

        prediction = self.repository.get_latest_prediction(transaction_id)
        score_result = self.repository.get_score_result(transaction_id)
        agent_case = self.repository.get_agent_case(transaction_id)
        review = self.repository.get_review(
            agent_case.case_id if agent_case is not None else None
        )
        chat_session = self.repository.get_chat_session(transaction_id)

        # 근데 이거 메시지 sse 보내는 거 아니엇나..
        chat_messages = (
            self.repository.list_chat_messages(chat_session.chat_session_id)
            if chat_session is not None
            else []
        )

        return CaseDetailResponse(
            case_id=(
                agent_case.case_id
                if agent_case is not None
                else f"UNASSIGNED-{transaction.id}"
            ),
            transaction_id=transaction.id,
            transaction=self._to_transaction_section(transaction),
            ml=self._to_ml_section(prediction),
            case_agent=self._to_agent_section(
                agent_case=agent_case,
                score_result=score_result,
            ),
            chat=self._to_chat_section(
                chat_session=chat_session,
                chat_messages=chat_messages,
            ),
            review=self._to_review_section(review),
        )

    def _to_case_list_item(
        self,
        row: CaseListRow
    ) -> CaseListItemResponse:
        if row.review is not None:
            review_status = "COMPLETED"
        elif row.agent_case is None:
            review_status = "NOT_AVAILABLE"
        elif row.agent_case.execution_status == "PROCESSING":
            review_status = "PROCESSING"
        else:
            review_status = "PENDING"

        return CaseListItemResponse(
            case_id=(
                row.agent_case.case_id
                if row.agent_case is not None
                else f"UNASSINGED={row.transaction.id}"
            ),
            transaction_id=row.transaction.id,
            execution_status=(
                row.agent_case.execution_status
                if row.agent_case is not None
                else "NOT_AVAILABLE"
            ),
            risk_score=(
                row.agent_case.risk_score
                if row.agent_case is not None
                else None
            ),
            risk_grade=(
                row.agent_case.risk_grade
                if row.agent_case is not None
                else None
            ),
            primary_fraud_type=(
                row.score_result.primary_fraud_type
                if row.score_result is not None
                else None
            ),
            transaction_amount=row.transaction.transaction_amount,
            transaction_datetime=row.transaction.transaction_datetime.isoformat(),
            review_status=review_status
        )

    def _to_transaction_section(
        self,
        transaction,
    ) -> SectionResult[TransactionView]:
        return SectionResult(
            status=SectionStatus.AVAILABLE,
            data=TransactionView(
                transaction_id=transaction.id,
                transaction_datetime=transaction.transaction_datetime.isoformat(),
                transaction_amount=transaction.transaction_amount,
                channel=transaction.channel,
                location=(
                    f"{transaction.location_lat}, {transaction.location_lon}"
                    if(
                        transaction.location_lat is not None
                        and transaction.location_lon is not None
                    )
                    else "데이터 없음"
                ),
                customer_id=transaction.customer_id or "데이터 없음",
                source_account_id=transaction.source_account_number,
                recipient_account_id=transaction.recipient_account_number,
                access_medium=transaction.access_medium,
                operating_system=transaction.operating_system,
                ip_address=(
                    str(transaction.ip_address)
                    if transaction.ip_address is not None
                    else None
                ),
                mac_address=(
                    str(transaction.mac_address)
                    if transaction.mac_address is not None
                    else None
                ),
                num_connection_failure=transaction.num_connection_failure,
                rooting_jailbreak_indicator=transaction.rooting_jailbreak_indicator,
                mobile_roaming_indicator=transaction.mobile_roaming_indicator,
                vpn_indicator=transaction.vpn_indicator,
                terminal_malicious_behavior_detected=any((
                    transaction.flag_terminal_malicious_behavior_1,
                    transaction.flag_terminal_malicious_behavior_2,
                    transaction.flag_terminal_malicious_behavior_3,
                    transaction.flag_terminal_malicious_behavior_5,
                    transaction.flag_terminal_malicious_behavior_6,
                )),
            )
        )

    def _to_ml_section(
        self,
        prediction,
    ) -> SectionResult[MLView]:
        if prediction is None:
            return SectionResult(
                status=SectionStatus.NOT_AVAILABLE,
                error_message="ML 예측 결과가 없습니다.",
            )

        return SectionResult(
            status=SectionStatus.AVAILABLE,
            data=MLView(
                prediction_status="COMPLETED",
                is_fraud=prediction.predict_result,
                fraud_probability=prediction.predict_proba,
                model_name=prediction.model_name,
                model_version=prediction.model_version,
            ),
        )

    def _to_agent_section(
        self,
        *,
        agent_case,
        score_result,
    ) -> SectionResult[CaseAgentView]:
        if agent_case is None:
            return SectionResult(
                status=SectionStatus.NOT_AVAILABLE,
                error_message="Agent 사건 분석 결과가 없습니다.",
            )

        rule_result = None

        if score_result is not None:
            rule_result = {
                "rule_filter_status": score_result.rule_filter_status,
                "primary_fraud_type": score_result.primary_fraud_type,
                "type_scores": score_result.type_scores,
                "matched_components": score_result.matched_components,
            }

        status = (
            SectionStatus.PROCESSING
            if agent_case.execution_status == "PROCESSING"
            else SectionStatus.AVAILABLE
        )

        return SectionResult(
            status=status,
            data=CaseAgentView(
                execution_status=agent_case.execution_status,
                failure_reason=agent_case.failure_reason,
                risk_score=agent_case.risk_score,
                risk_grade=agent_case.risk_grade,
                best_similar_case_id=agent_case.best_similar_case_id,
                rule_result=rule_result,
                investigation_result=agent_case.investigation_result,
                similar_case_results=agent_case.similar_case_results or [],
                response_result=agent_case.response_result,
            ),
        )

    def _to_chat_section(
        self,
        chat_session,
        chat_messages,
    ) -> SectionResult[ChatView]:
        if chat_session is None:
            return SectionResult(
                status=SectionStatus.EMPTY,
                error_message="연결된 채팅 세션이 없습니다.",
            )

        return SectionResult(
            status=SectionStatus.AVAILABLE,
            data=ChatView(
                chat_session_id=chat_session.chat_session_id,
                session_status=chat_session.status,
                started_at=chat_session.created_at.isoformat(),
                closed_at=(
                    chat_session.completed_at.isoformat()
                    if chat_session.completed_at is not None
                    else None
                ),
                messages=[
                    ChatMessageView(
                        message_id=str(message.message_id),
                        sender_type=message.sender_type,
                        message_text=message.message_text,
                        sent_at=message.sent_at.isoformat(),
                    )
                    for message in chat_messages
                ],
            ),
        )

    def _to_review_section(
        self,
        review,
    ) -> SectionResult[dict]:
        if review is None:
            return SectionResult(
                status=SectionStatus.EMPTY,
                error_message="최종 판정 및 처리 데이터가 없습니다.",
            )

        return SectionResult(
            status=SectionStatus.AVAILABLE,
            data={
                "decision": review.decision,
                "confirmed_fraud_type": review.confirmed_fraud_type,
                "performed_actions": review.performed_actions or [],
                "checklist_results": review.checklist_results or [],
                "resolution_summary": review.resolution_summary,
                "reviewed_at": review.reviewed_at.isoformat(),
            },
        )