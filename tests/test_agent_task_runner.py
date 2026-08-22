import unittest
from unittest.mock import MagicMock, patch

from app.domain.enums import RiskGrade
from app.dto.agent import AgentInputDTO
from app.services.agent.task_runner import run_agent_task, run_demo_agent_task


class AgentTaskRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.agent_input = AgentInputDTO(
            transaction_id=1,
            fraud_type_score_result_id=7,
            risk_score=91,
            risk_grade=RiskGrade.VERY_HIGH,
        )

    @patch("app.services.agent.task_runner.create_agent_workflow")
    @patch("app.services.agent.task_runner.Session")
    def test_runs_agent_workflow_in_its_own_session(
        self,
        session_class,
        workflow_factory,
    ) -> None:
        session = self._session_from(session_class)

        with patch(
            "app.services.agent.task_runner.dashboard_event_broker.publish"
        ) as publish:
            run_agent_task(self.agent_input)

        workflow_factory.assert_called_once_with(session, send_email=True)
        workflow_factory.return_value.run.assert_called_once_with(self.agent_input)
        publish.assert_called_once_with(
            event="dashboard_updated",
            data={"source": "agent"},
        )

    @patch("app.services.agent.task_runner.create_agent_workflow")
    @patch("app.services.agent.task_runner.Session")
    def test_demo_agent_runs_without_email(
        self,
        session_class,
        workflow_factory,
    ) -> None:
        session = self._session_from(session_class)

        run_demo_agent_task(self.agent_input)

        workflow_factory.assert_called_once_with(session, send_email=False)
        workflow_factory.return_value.run.assert_called_once_with(self.agent_input)

    @patch("app.services.agent.task_runner.create_agent_workflow")
    @patch("app.services.agent.task_runner.Session")
    def test_agent_failure_is_logged_and_does_not_escape(
        self,
        session_class,
        workflow_factory,
    ) -> None:
        """거래 API 응답 뒤 도는 백그라운드 작업이라 예외를 밖으로 올리지 않는다."""

        self._session_from(session_class)
        workflow_factory.return_value.run.side_effect = RuntimeError("agent")

        with (
            self.assertLogs("app.services.agent.task_runner", "ERROR"),
            patch(
                "app.services.agent.task_runner.dashboard_event_broker.publish"
            ) as publish,
        ):
            result = run_agent_task(self.agent_input)

        self.assertIsNone(result)
        publish.assert_called_once_with(
            event="dashboard_updated",
            data={"source": "agent"},
        )

    @staticmethod
    def _session_from(session_class) -> MagicMock:
        session = MagicMock()
        session_class.return_value.__enter__.return_value = session
        return session


if __name__ == "__main__":
    unittest.main()
