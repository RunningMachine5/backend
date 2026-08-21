"""챗봇 분석·사기 정황 추출·가이드 생성 프롬프트."""

from __future__ import annotations

from collections.abc import Mapping
from string import Template

from app.domain.fraud_circumstance_codes import FRAUD_CIRCUMSTANCE_DESCRIPTIONS


GUIDE_SEARCH_QUERY_RULES = """- 금융사기 대응에 의미 있는 행동, 정보 노출, 상대방과의 접촉을 가이드 검색 질의로 변환합니다.
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
- evidence는 그 질의의 근거가 된 사용자 답변 부분입니다."""


ANSWER_ANALYSIS_PROMPT_TEMPLATE = Template(f"""당신은 금융 이상거래 상담 챗봇에서 고객 응답을 한 번에 분석합니다.

직전 질문: $question_text
고객 응답: $customer_answer

먼저 다음 중 하나로 verdict를 분류하세요.

- SUFFICIENT: 질문 목적에 맞는 구체적 사실이 확인됨
- TOO_VAGUE: 질문 목적에 필요한 정보를 확인할 수 없는 응답
- WANT_END: 상담을 종료하기를 원함

판정 규칙:

- 모호하거나 질문과 무관한 답변, "모름"·"기억 안 남" 같은 판단 불가 응답은 TOO_VAGUE로 분류합니다.
- 현재 질문에 대한 답변만 거부하거나 회피하는 경우와 고객이 되묻는 경우도 TOO_VAGUE로 분류합니다.
- 전체 상담을 그만두거나 종료하겠다는 의사가 명확한 경우에만 WANT_END로 분류합니다.
- verdict가 TOO_VAGUE 또는 WANT_END이면 guide_search_queries는 반드시 빈 배열입니다.
- verdict가 SUFFICIENT이면 아래 규칙에 따라 고객 응답을 가이드 검색 질의로 분해합니다. 대응할 질의가 없으면 빈 배열도 허용합니다.

가이드 검색 질의 규칙:

{GUIDE_SEARCH_QUERY_RULES}
""")


FRAUD_CIRCUMSTANCE_EXTRACTION_PROMPT_TEMPLATE = Template("""당신은 금융 이상거래 상담에서 고객 답변에 나타난 사기 식별 정황을 빠짐없이 추출하는 분류기입니다.

판정 원칙:

- 사용자 답변 전체를 읽고, 같은 사건을 설명하는 여러 문장이나 절에 나뉜 정보를 함께 판단합니다.
- 정의와 단어가 정확히 같지 않아도 같은 의미의 일상적인 표현이면 인정합니다. 정의에 든 대상이나 표현은 예시이며, 의미상 같은 사례를 포함합니다.
- 정의의 "명확히 말함"은 필수 사실이 답변에서 확인된다는 뜻입니다. 사용자가 직접 "사기"라고 판단하거나 법적 사실을 확정할 필요는 없습니다.
- 상대방의 발언·요청·사칭·통화 회피는 사용자가 그런 일을 겪었다고 말하면 사용자의 경험으로 봅니다. "아들이라면서", "친구라고 연락해" 같은 표현도 상대가 그 관계를 자처한 것으로 봅니다.
- 각 정의의 필수 조건을 모두 확인합니다. 정의가 실제 이체·입력·전달·게시 등 행동 완료를 요구할 때만 완료 사실이 필요합니다.
- 정의가 상대방의 요청이나 주장 자체를 정황으로 삼으면 사용자가 그 요청을 수행하지 않았어도 추출합니다.
- 하나의 답변에서 여러 정황이 확인되면 빠뜨리지 말고 각각 추출합니다.
- 정황들은 서로 배타적이지 않습니다. 더 구체적인 정황을 추출했더라도 그 전제가 다른 정의에도 해당하면 둘 다 추출합니다. 특히 가족·지인 사칭과 휴대폰 고장 설명·제3자 계좌 송금 요청·상품권 정보 요청은 함께 추출할 수 있습니다.
- urgent_transfer_to_third_party_account 또는 gift_card_pin_requested_by_impersonated_contact을 추출하면, 두 정의에 가족·지인 사칭이 필수로 포함되므로 family_or_friend_impersonated_in_messenger도 함께 추출합니다.
- 사용자 답변에 없는 사실을 일반적인 사기 수법만으로 보충하지 않습니다.
- 사용자가 하지 않았다고 부정한 내용, 가정·질문, 일반적인 설명, 사용자와 무관한 타인의 사건은 추출하지 않습니다.
- 모호하거나 서로 충돌하는 내용은 추출하지 않으며, 사용자가 이전 답변을 정정하면 가장 최신 답변을 따릅니다.
- 탐지된 거래와 관계없는 과거 사건의 정황은 추출하지 않습니다.

evidence 규칙:

- evidence는 사용자 답변에 실제로 존재하는 연속된 원문 문자열이어야 하며, 요약하거나 문장을 새로 만들지 않습니다.
- 근거가 여러 문장이나 절에 나뉘면 첫 근거부터 마지막 근거까지를 포함하는 가장 짧은 연속 구간을 그대로 선택합니다. 여러 문장을 포함해도 됩니다.
- 동일한 enum은 한 번만 추출하며, 해당 정황의 필수 조건을 가장 잘 보여주는 evidence를 선택합니다.
- 확인되는 정황이 없으면 fraud_circumstances를 빈 배열로 반환합니다.

판정 예시:

- "검찰청이라고 전화가 왔어요. 제 통장이 범죄 자금 세탁에 쓰였다고 했습니다."에서는 두 문장을 함께 판단해 criminal_involvement_claim_by_phone을 추출합니다.
- "아들이라고 온 문자에서 급하다며 다른 사람 계좌로 보내 달라고 했지만 송금하지 않았어요."에서는 family_or_friend_impersonated_in_messenger와 urgent_transfer_to_third_party_account을 추출합니다. 실제 송금은 두 정황의 필수 조건이 아닙니다.
- "검사가 안전계좌로 보내라고 했지만 송금하지 않았어요."에서는 실제 이체가 없으므로 safe_account_or_asset_inspection_transfer를 추출하지 않습니다.

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


def render_answer_analysis_prompt(
    *,
    question_text: str,
    customer_answer: str,
) -> str:
    """답변 판정과 가이드 검색 질의 분해를 한 호출에서 수행할 프롬프트."""

    return ANSWER_ANALYSIS_PROMPT_TEMPLATE.substitute(
        question_text=question_text,
        customer_answer=customer_answer,
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
