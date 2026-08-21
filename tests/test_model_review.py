import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from app.services.mlops.model_review import ModelReviewError, ModelReviewLLM


class ModelReviewLLMTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = Mock()
        self.reviewer = ModelReviewLLM(client=self.client, model="test-model")

    def test_review_uses_metrics_without_mlflow_recommendation(self) -> None:
        self.client.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(
                            {
                                "decision": "NOT_RECOMMENDED",
                                "summary": "Recall 하락폭이 커 승격을 비추천합니다.",
                            }
                        )
                    )
                )
            ]
        )

        result = self.reviewer.review(
            candidate_run_id=5,
            candidate_details={
                "model_version": "18",
                "metrics": {
                    "validation_pr_auc": 0.82,
                    "validation_recall": 0.65,
                    "validation_fpr": 0.002,
                },
                "tags": {"promotion_recommendation": "RECOMMENDED"},
            },
            production_run_id=2,
            production_details={
                "model_version": "17",
                "metrics": {
                    "validation_pr_auc": 0.92,
                    "validation_recall": 0.88,
                    "validation_fpr": 0.001,
                },
            },
        )

        self.assertEqual(result.decision, "NOT_RECOMMENDED")
        request = self.client.chat.completions.create.call_args.kwargs
        prompt = json.loads(request["messages"][1]["content"])
        self.assertNotIn("promotion_recommendation", request["messages"][1]["content"])
        self.assertEqual(prompt["candidate"]["training_run_id"], 5)
        self.assertEqual(prompt["metrics"][0]["candidate"], 0.82)
        self.assertAlmostEqual(prompt["metrics"][0]["delta"], -0.10)
        self.assertEqual(request["model"], "test-model")
        self.assertEqual(request["reasoning_effort"], "low")

    def test_review_rejects_invalid_ai_response(self) -> None:
        self.client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="not-json"))]
        )

        with self.assertRaises(ModelReviewError):
            self.reviewer.review(
                candidate_run_id=5,
                candidate_details={"metrics": {}},
                production_run_id=None,
                production_details=None,
            )


if __name__ == "__main__":
    unittest.main()
