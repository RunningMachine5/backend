import unittest
from collections.abc import Sequence

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.data.model.document import Document
from app.data.model.document_chunk import EMBEDDING_DIM, DocumentChunk
from app.dto.agent_guide import GuideSearchRequestDTO
from app.repositories.agent_guide import AgentGuideRepository, GuideSearchRow
from app.services.agent.guide_corpus import DEFAULT_CORPUS_ROOT, load_guide_corpus
from app.services.agent.guide_embedder import (
    GuideEmbeddingError,
    validate_document_embeddings,
    validate_embedding,
)
from app.services.agent.guide_evaluation import calculate_retrieval_metrics
from app.services.agent.guide_indexing import GuideIndexingService
from app.services.agent.guide_search import GuideSearchService


class FakeGuideEmbedder:
    """외부 API 없이 적재·검색 계약을 검증하는 결정적 Fake이다."""

    def __init__(self) -> None:
        self.document_call_count = 0

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.document_call_count += 1
        return [self._vector(text) for text in texts]

    def embed_query(self, query: str) -> list[float]:
        return self._vector(query)

    @staticmethod
    def _vector(text: str) -> list[float]:
        vector = [0.0] * EMBEDDING_DIM
        keywords = {
            "고객": 0,
            "원격제어": 1,
            "보이스피싱": 2,
            "메신저": 3,
            "계좌": 4,
        }
        matched = False
        for keyword, index in keywords.items():
            if keyword in text:
                vector[index] += 1.0
                matched = True
        if not matched:
            vector[5] = 1.0
        return vector


class FakeGuideSearchRepository:
    """검색 서비스가 Repository에 전달한 조건을 기록하는 테스트 대역이다."""

    def __init__(self, rows: list[GuideSearchRow]) -> None:
        self.rows = rows
        self.last_search: dict[str, object] = {}

    def search_chunks(
        self,
        query_embedding: Sequence[float],
        **conditions: object,
    ) -> list[GuideSearchRow]:
        self.last_search = {
            "query_embedding": list(query_embedding),
            **conditions,
        }
        return self.rows


class AgentGuideVectorSearchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(
            self.engine,
            tables=[Document.__table__, DocumentChunk.__table__],
        )
        self.session = Session(self.engine)
        self.embedder = FakeGuideEmbedder()
        self.repository = AgentGuideRepository(self.session)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_corpus_is_indexed_and_unchanged_documents_are_skipped(self) -> None:
        documents = load_guide_corpus(DEFAULT_CORPUS_ROOT)
        service = GuideIndexingService(self.repository, self.embedder)

        first = service.index_documents(documents)
        second = service.index_documents(documents)

        self.assertEqual(first.document_count, 16)
        self.assertEqual(first.chunk_count, 84)
        self.assertEqual(first.embedded_chunk_count, 84)
        self.assertEqual(second.embedded_chunk_count, 0)
        self.assertEqual(second.unchanged_document_count, 16)
        self.assertEqual(self.embedder.document_call_count, 1)
        self.assertEqual(len(self.session.exec(select(Document)).all()), 16)
        self.assertEqual(len(self.session.exec(select(DocumentChunk)).all()), 84)

    def test_search_passes_filters_and_maps_repository_result(self) -> None:
        document = Document(
            id=1,
            document_key="GUIDE-ACCOUNT-TAKEOVER",
            title="계정탈취 대응 절차",
            source_type="INTERNAL_DEMO_GUIDE",
            fraud_types=["ACCOUNT_TAKEOVER"],
            audiences=["MONITORING"],
            content="원문",
            meta={
                "source_name": "FDShield 내부 지침",
                "source_url": None,
            },
        )
        chunk = DocumentChunk(
            id=1,
            document_id=1,
            chunk_index=0,
            content="고객 본인 거래 여부를 확인한다.",
            meta={"heading": "고객 확인"},
            embedding=[0.0] * EMBEDDING_DIM,
        )
        repository = FakeGuideSearchRepository(
            [GuideSearchRow(document=document, chunk=chunk, similarity_score=0.91)]
        )
        search_service = GuideSearchService(repository, self.embedder)  # type: ignore[arg-type]

        results = search_service.search(
            GuideSearchRequestDTO(
                query="계정탈취 고객 확인 절차",
                fraud_type="ACCOUNT_TAKEOVER",
                audience="MONITORING",
                risk_grade="VERY_HIGH",
                action_codes=("VERIFY_CUSTOMER_TRANSACTION",),
                top_k=5,
            )
        )

        self.assertEqual(repository.last_search["fraud_type"], "ACCOUNT_TAKEOVER")
        self.assertEqual(repository.last_search["audience"], "MONITORING")
        self.assertEqual(repository.last_search["risk_grade"], "VERY_HIGH")
        self.assertEqual(
            repository.last_search["action_codes"],
            ("VERIFY_CUSTOMER_TRANSACTION",),
        )
        self.assertTrue(repository.last_search["apply_metadata_filter"])
        self.assertEqual(results[0].document_id, "GUIDE-ACCOUNT-TAKEOVER")
        self.assertEqual(results[0].similarity_score, 0.91)
        self.assertEqual(results[0].retrieval_rank, 1)


class AgentGuideEmbeddingTest(unittest.TestCase):
    """DB 저장 전에 임베딩 개수와 1536차원 계약을 확인한다."""

    def test_embedding_dimension_is_normalized(self) -> None:
        vector = validate_embedding([1] * EMBEDDING_DIM)

        self.assertEqual(len(vector), EMBEDDING_DIM)
        self.assertIsInstance(vector[0], float)

    def test_wrong_embedding_dimension_is_rejected(self) -> None:
        with self.assertRaises(GuideEmbeddingError):
            validate_embedding([0.0] * 10)

    def test_document_and_embedding_counts_must_match(self) -> None:
        with self.assertRaises(GuideEmbeddingError):
            validate_document_embeddings(
                ["첫 번째", "두 번째"],
                [[0.0] * EMBEDDING_DIM],
            )


class AgentGuideSearchEvaluationTest(unittest.TestCase):
    """첫 정답 문서 순위를 기반으로 검색 품질 지표를 계산한다."""

    def test_retrieval_metrics_use_first_relevant_document_rank(self) -> None:
        results = {
            "Q1": ["WRONG", "EXPECTED-1", "EXPECTED-2"],
            "Q2": ["EXPECTED-3"],
            "Q3": ["WRONG"],
        }
        expected = {
            "Q1": {"EXPECTED-1"},
            "Q2": {"EXPECTED-3"},
            "Q3": {"EXPECTED-4"},
        }

        metrics = calculate_retrieval_metrics(results, expected)

        self.assertEqual(metrics.query_count, 3)
        self.assertEqual(metrics.precision_at_1, 0.3333)
        self.assertEqual(metrics.hit_rate_at_3, 0.6667)
        self.assertEqual(metrics.hit_rate_at_5, 0.6667)
        self.assertEqual(metrics.mrr, 0.5)


if __name__ == "__main__":
    unittest.main()
