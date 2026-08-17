import unittest
from unittest.mock import MagicMock, patch

from app.data.model.chatbot import ChatSession, ChatSessionStatus
from app.domain.enums import RiskGrade
from app.dto.agent import AgentInputDTO
from app.services.agent.task_runner import run_agent_task


class AgentTaskRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.agent_input = AgentInputDTO(
            transaction_id=1,
            fraud_type_score_result_id=7,
            risk_score=91,
            risk_grade=RiskGrade.VERY_HIGH,
        )
        self.chat_session = ChatSession(
            chat_session_id="CHAT-001",
            transaction_id=1,
            status=ChatSessionStatus.URL_SENT.value,
        )

    @patch("app.services.agent.task_runner.chat_session_event_broker")
    @patch("app.services.agent.task_runner.create_agent_workflow")
    @patch("app.services.agent.task_runner.ChatSessionRepository")
    @patch("app.services.agent.task_runner.Session")
    def test_publishes_new_session_status_after_agent_run(
        self,
        session_class,
        repository_class,
        workflow_factory,
        event_broker,
    ) -> None:
        session = self._session_from(session_class)
        repository = repository_class.return_value
        repository.find_by_transaction.return_value = self.chat_session

        run_agent_task(self.agent_input)

        workflow_factory.assert_called_once_with(session)
        workflow_factory.return_value.run.assert_called_once_with(self.agent_input)
        event_broker.publish_status_changed.assert_called_once_with(
            self.chat_session
        )

    @patch("app.services.agent.task_runner.chat_session_event_broker")
    @patch("app.services.agent.task_runner.create_agent_workflow")
    @patch("app.services.agent.task_runner.ChatSessionRepository")
    @patch("app.services.agent.task_runner.Session")
    def test_agent_failure_does_not_publish_uncommitted_session(
        self,
        session_class,
        repository_class,
        workflow_factory,
        event_broker,
    ) -> None:
        self._session_from(session_class)
        repository_class.return_value.find_by_transaction.return_value = None
        workflow_factory.return_value.run.side_effect = RuntimeError("agent")

        with self.assertLogs("app.services.agent.task_runner", "ERROR"):
            run_agent_task(self.agent_input)

        event_broker.publish_status_changed.assert_not_called()
        repository_class.return_value.find_by_transaction.assert_not_called()

    @patch("app.services.agent.task_runner.chat_session_event_broker")
    @patch("app.services.agent.task_runner.create_agent_workflow")
    @patch("app.services.agent.task_runner.ChatSessionRepository")
    @patch("app.services.agent.task_runner.Session")
    def test_sse_failure_does_not_escape_completed_agent_task(
        self,
        session_class,
        repository_class,
        workflow_factory,
        event_broker,
    ) -> None:
        self._session_from(session_class)
        repository_class.return_value.find_by_transaction.return_value = (
            self.chat_session
        )
        event_broker.publish_status_changed.side_effect = RuntimeError("sse")

        with self.assertLogs("app.services.agent.task_runner", "ERROR"):
            result = run_agent_task(self.agent_input)

        self.assertIsNone(result)
        workflow_factory.return_value.run.assert_called_once_with(self.agent_input)

    @staticmethod
    def _session_from(session_class) -> MagicMock:
        session = MagicMock()
        session_class.return_value.__enter__.return_value = session
        return session


if __name__ == "__main__":
    unittest.main()
