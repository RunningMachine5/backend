"""
챗봇 관련 프롬프트 템플릿

- 사기정황 추출 프롬프트 render_fraud_circumstance_extraction_prompt
- 가이드 검색 질의 분해 프롬프트 render_guide_search_query_extraction_prompt
- 유저 응답 평가 프롬프트 render_quality_check_prompt

사기 정황 enum이 바뀌었을 때 대응할 수 있도록
app.domain.fraud_circumstance_codes의 정의를 조립하는 형식입니다.

외부에서 사용할 핵심 함수는 위의 3개의 템플릿입니다
"""

from __future__ import annotations

from collections.abc import Mapping
from string import Template

from app.domain.fraud_circumstance_codes import FRAUD_CIRCUMSTANCE_DESCRIPTIONS


QUALITY_CHECK_PROMPT_TEMPLATE = Template("""당신은 금융 이상거래 상담 챗봇에서 고객 응답의 충실도를 평가합니다.

직전 질문: $question_text
고객 응답: $customer_answer

다음 중 하나로 분류하세요.

- SUFFICIENT: 질문 목적에 맞는 구체적 사실이 확인됨
- TOO_VAGUE: 질문 목적에 필요한 정보를 확인할 수 없는 응답
- WANT_END: 상담을 종료하기를 원함

규칙:

- 모호하거나 질문과 무관한 답변, "모름"·"기억 안 남" 같은 판단 불가 응답은 TOO_VAGUE로 분류합니다.
- 현재 질문에 대한 답변만 거부하거나 회피하는 경우와 고객이 되묻는 경우도 TOO_VAGUE로 분류합니다.
- 전체 상담을 그만두거나 종료하겠다는 의사가 명확한 경우에만 WANT_END로 분류합니다.
""")


GUIDE_SEARCH_QUERY_EXTRACTION_PROMPT_TEMPLATE = Template("""당신은 금융 이상거래 상담에서 고객 답변을 RAG로 독립 검색할 수 있는 가이드 검색 질의로 분해합니다.

다음 규칙을 따르세요.

- 금융사기 대응에 의미 있는 행동, 정보 노출, 상대방과의 접촉을 가이드 검색 질의로 변환합니다.
- 그런 행동이 없더라도 계좌·카드·이체·결제·대출·금융 보안 서비스처럼 금융과 관련된 질문이나 걱정이 있으면 가이드 검색 질의로 변환합니다.
- 금융과 무관한 배경 사실만 있으면 가이드 검색 질의로 만들지 않습니다.
- 각 가이드 검색 질의는 다른 대화 문맥 없이도 검색 가능한 완결된 한국어 질의여야 합니다.
- 한 가이드 검색 질의에는 하나의 대응 주제만 포함하고, 서로 독립적으로 검색할 수 있게 분리합니다.
- 고객이 실제로 묻거나 말한 것만 질의로 만듭니다. 신청 방법·조건·자격·절차처럼 고객이 묻지 않은 하위 항목을 새로 덧붙이지 않습니다.
- 한 질의는 물음 하나로 씁니다. 두 물음을 한 문장에 이어 붙이지 않습니다.
- 같은 의미의 요구를 중복하거나 겹쳐 만들지 않습니다.
- 5개는 상한일 뿐 목표가 아닙니다. 답변에서 실제로 확인되는 것만 만들고, 개수를 채우려고 일반적인 신고·증거보존·절차 안내를 덧붙이지 않습니다.
- 대응할 가이드 검색 질의가 하나뿐이면 하나만, 없으면 빈 배열을 반환합니다.
- title은 고객에게 보여줄 간결한 한국어 소제목입니다.
- search_query는 대응 가이드 문서를 찾기 위한 한국어 질문이며, 고객이 말한 범위를 벗어나지 않습니다.
- evidence는 그 질의의 근거가 된 사용자 답변 부분입니다.

사용자 답변:
$user_answers""")


FRAUD_CIRCUMSTANCE_EXTRACTION_PROMPT_TEMPLATE = Template("""당신은 금융 이상거래 상담에서 고객 답변에 나타난 사기 식별 정황을 추출하는 분류기입니다.

다음 규칙을 따르세요.

- 사용자가 직접 경험했다고 명확하게 말한 정황만 추출합니다.
- 사용자 답변에 없는 내용은 거래 정보나 일반적인 사기 수법을 근거로 추정하지 않습니다.
- 상대방의 발언·요청·사칭·통화 회피도 해당 정황의 정의에 포함되면 추출할 수 있습니다.
- 실제 행동 완료가 필요한 정황은 사용자가 행동했다고 명확히 말한 경우에만 추출합니다.
- 하나의 답변에서 여러 정황이 명확하게 확인되면 각각 추출할 수 있습니다.
- 동일한 enum은 한 번만 추출하며, 가장 직접적이고 명확한 evidence를 선택합니다.
- 탐지된 거래와 관계없는 과거 사건의 정황은 추출하지 않습니다.
- evidence는 사용자 답변에 실제로 존재하는 연속된 원문 문자열이어야 합니다.
- evidence를 요약하거나 문장을 새로 만들지 않습니다.
- 하나의 연속된 원문만으로 정황이 입증되지 않으면 추출하지 않습니다.
- 모호하거나 서로 충돌하는 내용은 추출하지 않습니다.
- 사용자가 이전 답변을 정정하면 가장 최신 답변을 따릅니다.
- 확인되는 정황이 없으면 fraud_circumstances를 빈 배열로 반환합니다.
- JSON 이외의 설명이나 마크다운을 출력하지 않습니다.

fraud_circumstance 정의:

$fraud_circumstance_definitions

사용자 답변:
$user_answers""")


GUIDE_RESPONSE_PROMPT_TEMPLATE = Template("""당신은 금융사기가 의심되는 거래의 고객에게 대응 방법을 안내하는 상담 챗봇입니다.

고객 답변에서 분해한 가이드 검색 질의와, 질의별로 검색된 대응 가이드 근거가 아래에 있습니다.

다음 규칙을 따르세요.

- 주어진 위치마다 섹션을 하나씩 작성합니다. 위치를 빠뜨리거나 여러 요구를 합치지 않습니다.
- 입력 위치의 오름차순을 그대로 유지합니다.
- 각 섹션의 첫 줄은 입력에 주어진 소제목을 바꾸지 말고 `■ 소제목` 형식으로 씁니다.
- 소제목 다음 줄부터 그 위치에 붙은 근거에서 확인되는 안내만 작성합니다.
- 다른 위치의 근거나 사전지식으로 답하지 않습니다.
- 근거에 없는 기관명·연락처·금액·기한·절차를 만들어내지 않습니다.
- 근거에 고객이 지금 할 수 있는 조치가 있으면 그 조치를 먼저 안내합니다.
- 피해가 이미 확정되었다고 단정하거나 고객의 책임을 지적하는 표현을 쓰지 않습니다.
- 존댓말로 쓰고 한 요구당 3문장을 넘기지 않습니다.
- `근거: 없음`인 위치의 본문은 다른 말을 만들지 말고 아래 두 줄만 정확히 씁니다.

$ungrounded_message

- 섹션 사이는 빈 줄 하나로 구분합니다.
- 주어지지 않은 위치를 추가하지 않습니다.
- JSON, 마크다운 코드 블록, 앞뒤 설명은 출력하지 않습니다. 고객에게 보여줄 섹션 본문만 출력합니다.

가이드 검색 질의와 근거:

$guide_search_query_context_block""")


def _render_definition_block(descriptions: Mapping[str, str]) -> str:
    """도메인 코드와 설명을 프롬프트의 enum 정의 형식으로 변환한다."""

    definitions = []
    for code, description in descriptions.items():
        formatted_description = description.replace("\n", "\n: ")
        definitions.append(f"{code}\n: {formatted_description}")
    return "\n\n".join(definitions)


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


def render_guide_search_query_extraction_prompt(
    *,
    user_answers: str,
) -> str:
    """고객 답변을 독립적인 가이드 검색 질의로 분해하는 프롬프트를 만든다."""

    return GUIDE_SEARCH_QUERY_EXTRACTION_PROMPT_TEMPLATE.substitute(
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


def render_guide_response_prompt(
    *,
    guide_search_query_context_block: str,
    ungrounded_message: str,
) -> str:
    """검색 질의 전체의 최종 고객 안내 본문을 한 번에 생성하는 프롬프트를 만든다.

    guide_search_query_context_block은 질의별 블록이며 형식은 A.4 문서에 있다.
    """

    return GUIDE_RESPONSE_PROMPT_TEMPLATE.substitute(
        guide_search_query_context_block=guide_search_query_context_block,
        ungrounded_message=ungrounded_message,
    )
