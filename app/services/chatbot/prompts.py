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
- 침묵/무응답은 이 노드에 들어오지 않으므로 고려하지 않습니다.

출력은 JSON만 반환하세요.""")


GUIDE_SEARCH_QUERY_EXTRACTION_PROMPT_TEMPLATE = Template("""당신은 금융 이상거래 상담에서 고객 답변을 RAG로 독립 검색할 수 있는 가이드 검색 질의로 분해합니다.

다음 규칙을 따르세요.

- 금융사기 대응에 의미 있는 행동, 정보 노출, 상대방과의 접촉을 가이드 검색 질의로 변환합니다.
- 단순 배경 사실은 답변 안에서 사기 위험과 연결된 경우에만 포함합니다.
- 각 가이드 검색 질의는 다른 대화 문맥 없이도 검색 가능한 완결된 한국어 질의여야 합니다.
- 한 가이드 검색 질의에는 하나의 대응 주제만 포함하고, 서로 독립적으로 검색할 수 있게 분리합니다.
- 사용자가 언급한 순서를 유지하고, 같은 의미의 요구를 중복하거나 겹쳐 만들지 않습니다.
- 사용자가 하지 않았다고 부정한 행동, 요청만 받은 행동, 원문에 없는 위험은 만들지 않습니다.
- title은 고객에게 보여줄 간결한 한국어 소제목이며 120자를 넘기지 않습니다.
- search_query는 대응 가이드 문서를 찾기 위한 구체적인 한국어 질문이며 500자를 넘기지 않습니다.
- evidence는 사용자 답변에 실제로 존재하는 연속된 원문 문자열이어야 합니다.
- 최대 5개만 반환하며, 대응할 가이드 검색 질의가 없으면 빈 배열을 반환합니다.
- JSON 이외의 설명이나 마크다운을 출력하지 않습니다.

사용자 답변:
$user_answers

출력 형식:
{
  "guide_search_queries": [
    {
      "title": "고객에게 보여줄 짧은 소제목",
      "search_query": "독립적으로 검색 가능한 한국어 질문",
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


GUIDE_RESPONSE_PROMPT_TEMPLATE = Template("""당신은 금융사기가 의심되는 거래의 고객에게 대응 방법을 안내하는 상담 챗봇입니다.

고객 답변에서 분해한 가이드 검색 질의와, 질의별로 검색된 대응 가이드 근거가 아래에 있습니다.

다음 규칙을 따르세요.

- 주어진 위치마다 안내를 하나씩 작성합니다. 위치를 빠뜨리거나 여러 요구를 합치지 않습니다.
- 각 안내는 그 위치에 붙은 근거에서 확인되는 내용만으로 작성합니다.
- 다른 위치의 근거나 사전지식으로 답하지 않습니다.
- 근거에 없는 기관명·연락처·금액·기한·절차를 만들어내지 않습니다.
- 근거에 고객이 지금 할 수 있는 조치가 있으면 그 조치를 먼저 안내합니다.
- 피해가 이미 확정되었다고 단정하거나 고객의 책임을 지적하는 표현을 쓰지 않습니다.
- 존댓말로 쓰고 한 요구당 3문장을 넘기지 않습니다.
- 소제목·번호·목록 기호를 붙이지 않습니다. 애플리케이션이 붙입니다.
- 주어진 근거만으로 안내를 쓸 수 없으면 해당 위치의 guidance를 빈 문자열로 둡니다.
- 주어지지 않은 위치를 출력에 추가하지 않습니다.

가이드 검색 질의와 근거:

$guide_search_query_context_block

출력 형식:
{
  "guides": [
    {
      "position": 1,
      "guidance": "해당 가이드 검색 질의에 대한 안내"
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
) -> str:
    """근거를 찾은 가이드 검색 질의의 대응 가이드를 한 번에 생성하는 프롬프트를 만든다.

    guide_search_query_context_block은 질의별 블록이며 형식은 A.4 문서에 있다.
    """

    return GUIDE_RESPONSE_PROMPT_TEMPLATE.substitute(
        guide_search_query_context_block=guide_search_query_context_block,
    )
