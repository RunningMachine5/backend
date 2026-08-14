"""
챗봇 관련 프롬프트 템플릿

- 사기정황 추출 프롬프트 render_fraud_circumstance_extraction_prompt
- 유저행동 추출 프롬프트 render_customer_action_extraction_prompt
- 유저 응답 평가 프롬프트 render_quality_check_prompt

추후 enum등이 바뀌었을때 대응할 수 있도록 
app.domain.customer_action_codes
app.domain.fraud_circumstance_codes 들은 따로 빼서 조립하는 형식입니다

외부에서 사용할 핵심 함수는 위의 3개의 템플릿입니다
"""

from __future__ import annotations

from collections.abc import Mapping
from string import Template

from app.domain.customer_action_codes import CUSTOMER_ACTION_DESCRIPTIONS
from app.domain.fraud_circumstance_codes import FRAUD_CIRCUMSTANCE_DESCRIPTIONS


QUALITY_CHECK_PROMPT_TEMPLATE = Template("""당신은 금융 이상거래 상담 챗봇에서 고객 응답의 충실도를 평가합니다.

직전 질문: $question_text
고객 응답: $customer_answer

다음 중 하나로 분류하세요.

- SUFFICIENT: 질문 목적에 맞는 구체적 사실이 확인됨
- TOO_VAGUE: 답변은 했으나 목적 정보를 특정할 수 없을 만큼 모호함
- NON_ANSWER: 질문과 무관하거나 판단 불가한 응답
- REFUSAL: 답변을 명시적으로 거부하거나 회피 의사를 밝힘
- WANT_END: 상담을 종료하기를 원함

규칙:

- "모름", "기억 안 남"은 NON_ANSWER로 분류합니다.
- 고객이 되묻는 경우, 질문 목적과 관련된 되물음이면 NON_ANSWER,
  회피성 되물음("그건 왜 물어봐요?")이면 REFUSAL로 분류합니다.
- 침묵/무응답은 이 노드에 들어오지 않으므로 고려하지 않습니다.

출력은 JSON만 반환하세요.""")


CUSTOMER_ACTION_EXTRACTION_PROMPT_TEMPLATE = Template("""당신은 금융 이상거래 상담에서 고객이 실제로 수행한 행동을 추출하는 분류기입니다.

다음 규칙을 따르세요.

- 사용자가 실제로 했다고 명확하게 말한 행동만 추출합니다.
- 상대방에게 요청만 받은 행동, 하지 않았다고 부정한 행동, 언급하지 않은 행동은 추출하지 않습니다.
- 거래 성공 여부만으로 고객이 직접 실행하거나 승인했다고 추정하지 않습니다.
- 고객이 거래를 직접 입력하고 실행했다면 detected_transaction_initiated입니다.
- 다른 사람이나 시스템이 준비한 거래를 고객이 인증·승인만 했다면 detected_transaction_approved입니다.
- 위 두 항목을 같은 거래에 동시에 적용하지 않습니다.
- 탐지된 거래와 관련 없는 과거 행동은 추출하지 않습니다.
- evidence는 사용자 답변에 실제로 존재하는 연속된 원문 문자열이어야 합니다.
- 모호하거나 충돌하는 내용은 추출하지 않습니다.
- 사용자가 이전 답변을 정정하면 최신 답변을 따릅니다.
- 허용된 enum 이외의 값은 생성하지 않습니다.

customer_action 정의:

$customer_action_definitions

사용자 답변:
$user_answers

출력 형식:
{
  "customer_actions": [
    {
      "type": "customer_action enum",
      "evidence": "사용자 답변의 정확한 원문"
    }
  ]
}""")


FRAUD_CIRCUMSTANCE_EXTRACTION_PROMPT_TEMPLATE = Template("""당신은 금융 이상거래 상담에서 고객 답변에 나타난 사기 식별 정황을 추출하는 분류기입니다.

다음 규칙을 따르세요.

- 사용자가 직접 경험했다고 명확하게 말한 정황만 추출합니다.
- 사용자 답변에 없는 내용은 거래 정보나 일반적인 사기 수법을 근거로 추정하지 않습니다.
- 상대방의 발언·요청·사칭·통화 회피도 해당 정황의 정의에 포함되면 추출할 수 있습니다.
- 실제 행동 완료가 필요한 정황은 사용자가 행동했다고 명확히 말한 경우에만 추출합니다.
- 단순히 상대방에게 행동을 요청받은 것만으로 고객이 실행했다고 추정하지 않습니다.
- 거래 성공 여부만으로 고객이 송금·승인·출금·전달했다고 추정하지 않습니다.
- 링크 클릭, 앱 설치, 인증번호 제공 등의 행동만으로 특정 사칭 방식이나 계정탈취 결과를 추정하지 않습니다.
- 계좌에 돈이 입금되었다는 사실만으로 재송금·출금·전달을 추정하지 않습니다.
- 본인 모르게 거래가 발생했다는 말만으로 계정이 탈취되었다고 단정하지 않습니다. 정의된 구체적 정황이 함께 확인되어야 합니다.
- 하나의 답변에서 여러 정황이 명확하게 확인되면 각각 추출할 수 있습니다.
- 동일한 enum은 한 번만 추출하며, 가장 직접적이고 명확한 evidence를 선택합니다.
- 사기유형을 먼저 결정한 뒤 그 유형에 맞춰 정황을 생성하지 않습니다.
- 후보 사기유형이나 거래 정보만으로 정황을 생성하지 않습니다.
- 탐지된 거래와 관계없는 과거 사건의 정황은 추출하지 않습니다.
- evidence는 사용자 답변에 실제로 존재하는 연속된 원문 문자열이어야 합니다.
- evidence를 요약하거나 문장을 새로 만들지 않습니다.
- 하나의 연속된 원문만으로 정황이 입증되지 않으면 추출하지 않습니다.
- 모호하거나 서로 충돌하는 내용은 추출하지 않습니다.
- 사용자가 이전 답변을 정정하면 가장 최신 답변을 따릅니다.
- 허용된 enum 이외의 값은 생성하지 않습니다.
- 확인되는 정황이 없으면 fraud_circumstances를 빈 배열로 반환합니다.
- JSON 이외의 설명이나 마크다운을 출력하지 않습니다.

fraud_circumstance 정의:

$fraud_circumstance_definitions

사용자 답변:
$user_answers

출력 형식:
{
  "fraud_circumstances": [
    {
      "type": "fraud_circumstance enum",
      "evidence": "사용자 답변에 존재하는 정확한 연속 원문"
    }
  ]
}""")


def _render_definition_block(descriptions: Mapping[str, str]) -> str:
    """도메인 코드와 설명을 프롬프트의 enum 정의 형식으로 변환한다."""

    definitions = []
    for code, description in descriptions.items():
        formatted_description = description.replace("\n", "\n: ")
        definitions.append(f"{code}\n: {formatted_description}")
    return "\n\n".join(definitions)


CUSTOMER_ACTION_DEFINITION_BLOCK = _render_definition_block(
    CUSTOMER_ACTION_DESCRIPTIONS
)
FRAUD_CIRCUMSTANCE_DEFINITION_BLOCK = _render_definition_block(
    FRAUD_CIRCUMSTANCE_DESCRIPTIONS
)


def render_quality_check_prompt(
    *,
    question_text: str,
    customer_answer: str,
) -> str:
    """고객 답변의 충실도를 판정하는 프롬프트를 만든다."""

    return QUALITY_CHECK_PROMPT_TEMPLATE.substitute(
        question_text=question_text,
        customer_answer=customer_answer,
    )


def render_customer_action_extraction_prompt(
    *,
    user_answers: str,
) -> str:
    """고객 행동을 추출하는 프롬프트를 만든다."""

    return CUSTOMER_ACTION_EXTRACTION_PROMPT_TEMPLATE.substitute(
        customer_action_definitions=CUSTOMER_ACTION_DEFINITION_BLOCK,
        user_answers=user_answers,
    )


def render_fraud_circumstance_extraction_prompt(
    *,
    user_answers: str,
) -> str:
    """사기 정황을 추출하는 프롬프트를 만든다."""

    return FRAUD_CIRCUMSTANCE_EXTRACTION_PROMPT_TEMPLATE.substitute(
        fraud_circumstance_definitions=FRAUD_CIRCUMSTANCE_DEFINITION_BLOCK,
        user_answers=user_answers,
    )
