import importlib
import unittest
from unittest.mock import Mock, patch

import app.services.rag.docs_embedding as docs_embedding


class TestDocsEmbedding(unittest.TestCase):
    def tearDown(self) -> None:
        docs_embedding.get_embedder.cache_clear()

    def test_import_does_not_create_openai_client(self) -> None:
        with patch("langchain_openai.OpenAIEmbeddings") as embedding_class:
            importlib.reload(docs_embedding)

        embedding_class.assert_not_called()
        importlib.reload(docs_embedding)

    @patch("app.services.rag.docs_embedding.OpenAIEmbeddings")
    def test_reuses_client_for_query_and_document_embedding(
        self,
        embedding_class: Mock,
    ) -> None:
        embedder = embedding_class.return_value
        embedder.embed_query.return_value = [0.1, 0.2]
        embedder.embed_documents.return_value = [[0.3, 0.4]]

        query_result = docs_embedding.query_embedding("질문")
        document_result = docs_embedding.docs_embedding(["문서"])

        self.assertEqual(query_result, [0.1, 0.2])
        self.assertEqual(document_result, [[0.3, 0.4]])
        embedding_class.assert_called_once_with(model="text-embedding-3-small")
        embedder.embed_query.assert_called_once_with("질문")
        embedder.embed_documents.assert_called_once_with(["문서"])


if __name__ == "__main__":
    unittest.main()
