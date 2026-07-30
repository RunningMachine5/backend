class FakeEmbedder:
    """외부 임베딩 모델 없이 질문을 고정 길이 숫자 벡터로 변환한다."""

    def embed(self, question: str) -> list[float]:
        """문자열 길이와 문자 코드 합을 사용해 재현 가능한 3차원 벡터를 만든다."""
        length_value = min(len(question) / 100, 1.0)
        code_value = sum(ord(character) for character in question) % 1000 / 1000
        keyword_value = 1.0 if "거래" in question else 0.0
        return [round(length_value, 3), round(code_value, 3), keyword_value]

