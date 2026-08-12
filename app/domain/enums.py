from enum import Enum


class RiskGrade(str, Enum):
    """거래의 최종 위험등급."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    VERY_HIGH = "VERY_HIGH"


class FraudType(str, Enum):
    """패턴 점수로 계산하는 사기유형."""

    LARGE_AMOUNT_PAYMENT = "고액 결제 사기"
    STOLEN_CARD_PAYMENT = "도난 카드 결제"
    UNUSUAL_USER_BEHAVIOR = "비정상 사용자 거래"


class AgentAction(str, Enum):
    """위험등급 분기 후 Agent가 수행한 작업."""

    NO_ACTION = "추가 조치 없음"
    EMAIL_SENT = "피해자 안내 이메일 전송"
    DASHBOARD_REPORTED = "모니터링 대시보드 보고"
    EMAIL_AND_DASHBOARD_REPORTED = "피해자 이메일 전송 및 모니터링 대시보드 보고"
