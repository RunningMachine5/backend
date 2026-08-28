"""ML로 보낼 피쳐들 다 조립하는 코드"""
from datetime import timedelta
from math import asin, cos, radians, sin, sqrt

from app.data.model import CustomerEventType, DerivedFeatures
from app.dto.ml_features import DerivedFeaturesCreateDTO, MLTransactionFeatures
from app.dto.transaction import TransactionCreateDTO, TransactionRequestDTO
from app.repositories.derived_features import DerivedFeaturesRepository
from app.repositories.feature_context import FeatureContext, FeatureContextRepository


def _calc_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371
    d_phi = radians(lat2 - lat1)
    d_lambda = radians(lon2 - lon1)
    a = sin(d_phi / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(d_lambda / 2) ** 2
    return 2 * r * asin(sqrt(a))


class DerivedFeatureService:
    def __init__(self, feature_context_repository: FeatureContextRepository, derived_features_repository: DerivedFeaturesRepository):
        self.feature_context_repository = feature_context_repository
        self.derived_features_repository = derived_features_repository

    def _fill_features(self, transaction: TransactionRequestDTO, context: FeatureContext) -> dict:
        repo = self.feature_context_repository

        customer = context.customer
        source_account = context.source_account
        recipient_account = context.recipient_account

        '''context만으로 채울 수 있는 것들 넣기'''
        # 고객 조회
        customer_birth_date = customer.birth_date
        customer_gender = customer.gender
        customer_registration_datetime = customer.registration_datetime
        customer_credit_rating = customer.credit_rating
        customer_loan_type = customer.loan_type

        # 계좌 조회
        account_account_type = source_account.account_type
        account_creation_datetime = source_account.creation_datetime
        account_initial_balance = source_account.current_balance
        account_amount_daily_limit = source_account.amount_daily_limit
        account_indicator_openbanking = source_account.indicator_openbanking

        # 수취인 계좌 조회
        another_person_account = 0 if recipient_account == source_account else 1
        recipient_account_suspend_status: bool = recipient_account.suspend_status

        agg_features = repo.get_aggregation_features(
            source_account_number=transaction.source_account_number,
            recipient_account_number=transaction.recipient_account_number,
            mac_address=transaction.mac_address,
            tx_datetime=transaction.transaction_datetime,
        )

        '''추가 DB 조회로 찾을 수 있는 것들 넣기'''
        # 3시간 거래
        number_of_transaction_with_the_account = agg_features.count_3h

        # 최근 일주일 거래
        flag_deposit_more_than_ten_million = 1 if agg_features.count_10m_in_week >= 1 else 0

        # 최근 1개월 거래
        one_month_max_amount = agg_features.one_month_max
        dawn_one_month_max_amount = agg_features.dawn_one_month_max
        one_month_std_dev = agg_features.one_month_std_dev
        dawn_one_month_std_dev = agg_features.dawn_one_month_std_dev

        # 기존 거래 전체
        transaction_history_with_the_account = agg_features.transaction_count_with
        unused_account_status = 1 if agg_features.transaction_count_with == 0 else 0
        unused_terminal_status = 1 if agg_features.tx_mac_count == 0 else 0

        # 기존 거래 채널 이력
        last_transaction_datetime = agg_features.last_atm_datetime
        last_bank_branch_transaction_datetime = agg_features.last_branch_datetime

        source_customer_id = customer.id if customer else None
        recipient_customer_id = recipient_account.customer_id if recipient_account else None

        events = repo.get_last_customer_both_events(
            source_account_number=source_account.account_number,
            recipient_account_number=recipient_account.account_number,
            source_customer_id=source_customer_id,
            recipient_customer_id=recipient_customer_id,
        )
        event_map = {(e.customer_id, e.event_type): e for e in events}

        inquery_atm_limit = (
            1
            if (event := event_map.get((source_customer_id, CustomerEventType.ATM_LIMIT_INQUIRY.value)))
            and event.occurred_at > transaction.transaction_datetime - timedelta(days=7)
            else 0
        )
        increase_atm_limit = (
            1
            if (event := event_map.get((source_customer_id, CustomerEventType.ATM_LIMIT_INCREASE.value)))
            and event.occurred_at > transaction.transaction_datetime - timedelta(days=7)
            else 0
        )
        account_indicator_release_limit_excess = (
            1
            if (event := event_map.get((source_customer_id, CustomerEventType.TRANSACTION_LIMIT_RELEASE.value)))
            and event.occurred_at > transaction.transaction_datetime - timedelta(days=7)
            else 0
        )

        recipient_suspend_event = event_map.get(
            (recipient_customer_id, CustomerEventType.SUSPENSION_RELEASE.value)
        )
        recipient_release_suspension = (
            1
            if recipient_suspend_event
            and recipient_suspend_event.occurred_at > transaction.transaction_datetime - timedelta(days=30)
            else 0
        )
        recipient_transaction_resumed_date = (
            recipient_suspend_event.occurred_at if recipient_suspend_event else None
        )

        flag_change_of_authentication_1 = (
            1
            if (event := event_map.get((source_customer_id, CustomerEventType.AUTH_1.value)))
            and event.occurred_at > transaction.transaction_datetime - timedelta(days=90)
            else 0
        )
        flag_change_of_authentication_2 = (
            1
            if (event := event_map.get((source_customer_id, CustomerEventType.AUTH_2.value)))
            and event.occurred_at > transaction.transaction_datetime - timedelta(days=90)
            else 0
        )
        flag_change_of_authentication_3 = (
            1
            if (event := event_map.get((source_customer_id, CustomerEventType.AUTH_3.value)))
            and event.occurred_at > transaction.transaction_datetime - timedelta(days=90)
            else 0
        )
        flag_change_of_authentication_4 = (
            1
            if (event := event_map.get((source_customer_id, CustomerEventType.AUTH_4.value)))
            and event.occurred_at > transaction.transaction_datetime - timedelta(days=90)
            else 0
        )

        account_remaining_amount_daily_limit_exceeded = (
            context.source_account.amount_daily_limit - agg_features.today_amount
        )

        return {
            "customer_birth_date": customer_birth_date,
            "customer_gender": customer_gender,
            "customer_registration_datetime": customer_registration_datetime,
            "customer_credit_rating": customer_credit_rating,
            "customer_loan_type": customer_loan_type,
            "account_account_type": account_account_type,
            "account_creation_datetime": account_creation_datetime,
            "account_initial_balance": account_initial_balance,
            "account_indicator_release_limit_excess": account_indicator_release_limit_excess,
            "account_amount_daily_limit": account_amount_daily_limit,
            "account_indicator_openbanking": account_indicator_openbanking,
            "another_person_account": another_person_account,
            "recipient_account_suspend_status": recipient_account_suspend_status,
            "number_of_transaction_with_the_account": number_of_transaction_with_the_account,
            "flag_deposit_more_than_ten_million": flag_deposit_more_than_ten_million,
            "one_month_max_amount": one_month_max_amount,
            "dawn_one_month_max_amount": dawn_one_month_max_amount,
            "one_month_std_dev": one_month_std_dev,
            "dawn_one_month_std_dev": dawn_one_month_std_dev,
            "transaction_history_with_the_account": transaction_history_with_the_account,
            "unused_account_status": unused_account_status,
            "unused_terminal_status": unused_terminal_status,
            "last_transaction_datetime": last_transaction_datetime,
            "last_bank_branch_transaction_datetime": last_bank_branch_transaction_datetime,
            "inquery_atm_limit": inquery_atm_limit,
            "increase_atm_limit": increase_atm_limit,
            "recipient_release_suspension": recipient_release_suspension,
            "flag_change_of_authentication_1": flag_change_of_authentication_1,
            "flag_change_of_authentication_2": flag_change_of_authentication_2,
            "flag_change_of_authentication_3": flag_change_of_authentication_3,
            "flag_change_of_authentication_4": flag_change_of_authentication_4,
            "recipient_transaction_resumed_date": recipient_transaction_resumed_date,
            "account_remaining_amount_daily_limit_exceeded": account_remaining_amount_daily_limit_exceeded,
        }

    def _calc_features(self, transaction: TransactionRequestDTO, context: FeatureContext) -> dict:
        # 직전 거래 데이터 조회
        last_transaction = self.feature_context_repository.get_last_transaction(transaction.source_account_number)

        # distance 계산
        distance = (
            0.0
            if last_transaction is None
            else _calc_distance(
                last_transaction.location_lat,
                last_transaction.location_lon,
                transaction.location_lat,
                transaction.location_lon,
            )
        )

        # time_difference 계산
        time_difference = (
            timedelta(seconds=0)
            if last_transaction is None
            else transaction.transaction_datetime
            - last_transaction.transaction_datetime
        )

        # 거래 후 잔고 account_balance 계산
        account_balance = (
            context.source_account.current_balance - transaction.transaction_amount
        )

        return {"distance": distance, "time_difference": time_difference, "account_balance": account_balance}

    def create_derived_features(self, transaction: TransactionRequestDTO) -> tuple:
        # DB 조회
        context = self.feature_context_repository.get_feature_context(
            transaction.source_account_number, transaction.recipient_account_number
        )

        # DB 조회만으로 채우기
        filled_features = self._fill_features(transaction, context)

        # DB 조회 후 계산해야 하는 녀석들 채우기
        calc_features = self._calc_features(transaction, context)

        result = (
            MLTransactionFeatures(
                # dto
                transaction_datetime=transaction.transaction_datetime,
                transaction_amount=transaction.transaction_amount,
                channel=transaction.channel,
                operating_system=transaction.operating_system,
                type_general_automatic=transaction.type_general_automatic,
                access_medium=transaction.access_medium,
                transaction_num_connection_failure=transaction.num_connection_failure,
                customer_rooting_jailbreak_indicator=transaction.customer_rooting_jailbreak_indicator,
                customer_mobile_roaming_indicator=transaction.customer_mobile_roaming_indicator,
                customer_vpn_indicator=transaction.customer_vpn_indicator,
                customer_flag_terminal_malicious_behavior_1=transaction.customer_flag_terminal_malicious_behavior_1,
                customer_flag_terminal_malicious_behavior_2=transaction.customer_flag_terminal_malicious_behavior_2,
                customer_flag_terminal_malicious_behavior_3=transaction.customer_flag_terminal_malicious_behavior_3,
                customer_flag_terminal_malicious_behavior_5=transaction.customer_flag_terminal_malicious_behavior_5,
                customer_flag_terminal_malicious_behavior_6=transaction.customer_flag_terminal_malicious_behavior_6,
                # filled
                customer_birth_date=filled_features["customer_birth_date"],
                customer_gender=filled_features["customer_gender"],
                customer_registration_datetime=filled_features[
                    "customer_registration_datetime"
                ],
                customer_credit_rating=filled_features["customer_credit_rating"],
                customer_loan_type=filled_features["customer_loan_type"],
                customer_flag_change_of_authentication_1=filled_features[
                    "flag_change_of_authentication_1"
                ],
                customer_flag_change_of_authentication_2=filled_features[
                    "flag_change_of_authentication_2"
                ],
                customer_flag_change_of_authentication_3=filled_features[
                    "flag_change_of_authentication_3"
                ],
                customer_flag_change_of_authentication_4=filled_features[
                    "flag_change_of_authentication_4"
                ],
                customer_inquery_atm_limit=filled_features["inquery_atm_limit"],
                customer_increase_atm_limit=filled_features["increase_atm_limit"],
                account_account_type=filled_features["account_account_type"],
                account_creation_datetime=filled_features["account_creation_datetime"],
                account_initial_balance=filled_features["account_initial_balance"],
                account_indicator_release_limit_excess=filled_features[
                    "account_indicator_release_limit_excess"
                ],
                account_amount_daily_limit=filled_features[
                    "account_amount_daily_limit"
                ],
                account_indicator_openbanking=filled_features[
                    "account_indicator_openbanking"
                ],
                recipient_release_suspension=filled_features[
                    "recipient_release_suspension"
                ],
                account_one_month_max_amount=filled_features["one_month_max_amount"],
                account_one_month_std_dev=filled_features["one_month_std_dev"],
                account_dawn_one_month_max_amount=filled_features[
                    "dawn_one_month_max_amount"
                ],
                account_dawn_one_month_std_dev=filled_features[
                    "dawn_one_month_std_dev"
                ],
                another_person_account=filled_features["another_person_account"],
                unused_terminal_status=filled_features["unused_terminal_status"],
                last_atm_transaction_datetime=filled_features[
                    "last_transaction_datetime"
                ],
                last_bank_branch_transaction_datetime=filled_features[
                    "last_bank_branch_transaction_datetime"
                ],
                flag_deposit_more_than_ten_million=filled_features[
                    "flag_deposit_more_than_ten_million"
                ],
                unused_account_status=filled_features["unused_account_status"],
                recipient_account_suspend_status=filled_features[
                    "recipient_account_suspend_status"
                ],
                number_of_transaction_with_the_account=filled_features[
                    "number_of_transaction_with_the_account"
                ],
                transaction_history_with_the_account=filled_features[
                    "transaction_history_with_the_account"
                ],
                recipient_transaction_resumed_date=filled_features[
                    "recipient_transaction_resumed_date"
                ],
                # clac
                distance=calc_features["distance"],
                time_difference=calc_features["time_difference"],
                account_balance=calc_features["account_balance"],
                account_remaining_amount_daily_limit_exceeded=filled_features[
                    "account_remaining_amount_daily_limit_exceeded"
                ],
            ),
            # 외부·ML의 customer_* 값을 Transaction DB 필드명으로 옮긴다.
            TransactionCreateDTO(
                customer_id=context.customer.id
                if context.customer
                else transaction.customer_id,
                source_account_number=transaction.source_account_number,
                recipient_account_number=transaction.recipient_account_number,
                transaction_datetime=transaction.transaction_datetime,
                transaction_amount=transaction.transaction_amount,
                channel=transaction.channel,
                type_general_automatic=transaction.type_general_automatic,
                access_medium=transaction.access_medium,
                num_connection_failure=transaction.num_connection_failure,
                initial_balance=filled_features[
                    "account_initial_balance"
                ],  # 출금 전 잔액
                balance=calc_features["account_balance"],  # 출금 후 잔액
                operating_system=transaction.operating_system,
                ip_address=transaction.ip_address,
                mac_address=transaction.mac_address,
                location_lat=transaction.location_lat,
                location_lon=transaction.location_lon,
                rooting_jailbreak_indicator=transaction.customer_rooting_jailbreak_indicator,
                mobile_roaming_indicator=transaction.customer_mobile_roaming_indicator,
                vpn_indicator=transaction.customer_vpn_indicator,
                flag_terminal_malicious_behavior_1=transaction.customer_flag_terminal_malicious_behavior_1,
                flag_terminal_malicious_behavior_2=transaction.customer_flag_terminal_malicious_behavior_2,
                flag_terminal_malicious_behavior_3=transaction.customer_flag_terminal_malicious_behavior_3,
                flag_terminal_malicious_behavior_5=transaction.customer_flag_terminal_malicious_behavior_5,
                flag_terminal_malicious_behavior_6=transaction.customer_flag_terminal_malicious_behavior_6,
            ),
            DerivedFeaturesCreateDTO(
                remaining_amount_daily_limit=filled_features[
                    "account_remaining_amount_daily_limit_exceeded"
                ],
                distance=calc_features["distance"],
                time_difference=calc_features["time_difference"],
                one_month_max_amount=filled_features["one_month_max_amount"],
                one_month_std_dev=filled_features["one_month_std_dev"],
                dawn_one_month_max_amount=filled_features["dawn_one_month_max_amount"],
                dawn_one_month_std_dev=filled_features["dawn_one_month_std_dev"],
                another_person_account=filled_features["another_person_account"],
                unused_terminal_status=filled_features["unused_terminal_status"],
                unused_account_status=filled_features["unused_account_status"],
                transaction_history_with_the_account=filled_features[
                    "transaction_history_with_the_account"
                ],
                flag_deposit_more_than_ten_million=filled_features[
                    "flag_deposit_more_than_ten_million"
                ],
                number_of_transaction_with_the_account=filled_features[
                    "number_of_transaction_with_the_account"
                ],
                last_atm_transaction_datetime=filled_features[
                    "last_transaction_datetime"
                ],
                last_bank_branch_transaction_datetime=filled_features[
                    "last_bank_branch_transaction_datetime"
                ],
                flag_change_of_authentication_1=filled_features[
                    "flag_change_of_authentication_1"
                ],
                flag_change_of_authentication_2=filled_features[
                    "flag_change_of_authentication_2"
                ],
                flag_change_of_authentication_3=filled_features[
                    "flag_change_of_authentication_3"
                ],
                flag_change_of_authentication_4=filled_features[
                    "flag_change_of_authentication_4"
                ],
                inquery_atm_limit=filled_features["inquery_atm_limit"],
                increase_atm_limit=filled_features["increase_atm_limit"],
                indicator_release_limit_excess=filled_features[
                    "account_indicator_release_limit_excess"
                ],
                recipient_release_suspension=filled_features[
                    "recipient_release_suspension"
                ],
                recipient_transaction_resumed_date=filled_features[
                    "recipient_transaction_resumed_date"
                ],
                recipient_account_suspend_status=filled_features[
                    "recipient_account_suspend_status"
                ],
            ),
        )

        # 엔티티로 만들어 반환
        return result

    def save_derived_features(self, df, tx_id) -> None:
        derived_features = DerivedFeatures(id=tx_id, **df.model_dump())
        self.derived_features_repository.save_derived_features(derived_features)

