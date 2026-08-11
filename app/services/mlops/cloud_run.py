"""Cloud Run v2 Admin API를 통한 학습 실행과 무중단 모델 배포 제어."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from functools import lru_cache
from threading import Lock
from typing import Annotated, Any

import httpx
from fastapi import Depends
from google import auth as google_auth
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleAuthRequest

from app.core.config import (
    CLOUD_RUN_ADMIN_TIMEOUT_SECONDS,
    CLOUD_RUN_SERVING_CONTAINER,
    CLOUD_RUN_SERVING_SERVICE,
    CLOUD_RUN_TRAINING_CONTAINER,
    CLOUD_RUN_TRAINING_JOB,
    GCP_PROJECT_ID,
    GCP_REGION,
    MLOPS_MODEL_ALIAS,
    MLOPS_MODEL_NAME,
)
from app.services.ml_serving.client import (
    MLPredictionResponse,
    MLServingClient,
    _google_id_token_provider,
)

CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
TRAFFIC_REVISION = "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION"
TRAFFIC_LATEST = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"

# GET 응답에 포함되는 output-only 필드를 다시 PATCH하지 않도록 입력 가능 필드만
# 복사합니다. 컨테이너 내부 필드는 Cloud Run API가 반환하는 입력 스키마를 보존합니다.
REVISION_TEMPLATE_INPUT_FIELDS = (
    "labels",
    "annotations",
    "scaling",
    "vpcAccess",
    "timeout",
    "serviceAccount",
    "containers",
    "volumes",
    "executionEnvironment",
    "encryptionKey",
    "maxInstanceRequestConcurrency",
    "serviceMesh",
    "encryptionKeyRevocationAction",
    "encryptionKeyShutdownDuration",
    "sessionAffinity",
    "healthCheckDisabled",
    "nodeSelector",
    "client",
    "clientVersion",
    "gpuZonalRedundancyDisabled",
)


class CloudRunAdminError(RuntimeError):
    """Cloud Run Admin API 요청이나 배포 안전성 검증 실패."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        request_may_have_been_accepted: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_may_have_been_accepted = request_may_have_been_accepted


class GoogleAccessTokenProvider:
    """ADC access token을 안전하게 갱신하고 요청 사이에서 재사용한다."""

    def __init__(self) -> None:
        self._credentials, _ = google_auth.default(scopes=[CLOUD_PLATFORM_SCOPE])
        self._request = GoogleAuthRequest()
        self._refresh_lock = Lock()

    def __call__(self) -> str:
        with self._refresh_lock:
            try:
                if not self._credentials.valid:
                    self._credentials.refresh(self._request)
            except GoogleAuthError as exc:
                raise CloudRunAdminError(
                    "Cloud Run Admin API용 access token 발급에 실패했습니다."
                ) from exc

            token = self._credentials.token

        if not token:
            raise CloudRunAdminError("Cloud Run access token이 비어 있습니다.")
        return token


@lru_cache
def _google_access_token_provider() -> GoogleAccessTokenProvider:
    return GoogleAccessTokenProvider()


def _default_smoke_client(
    tagged_url: str,
    service_audience: str,
) -> MLServingClient:
    return MLServingClient(
        base_url=tagged_url,
        auth_mode="google-id-token",
        token_provider=_google_id_token_provider(service_audience),
    )


class CloudRunAdminClient:
    """학습 Job 실행과 검증된 Serving 리비전 승격을 담당한다."""

    def __init__(
        self,
        *,
        project_id: str = GCP_PROJECT_ID,
        region: str = GCP_REGION,
        training_job: str = CLOUD_RUN_TRAINING_JOB,
        training_container: str = CLOUD_RUN_TRAINING_CONTAINER,
        serving_service: str = CLOUD_RUN_SERVING_SERVICE,
        serving_container: str = CLOUD_RUN_SERVING_CONTAINER,
        model_name: str = MLOPS_MODEL_NAME,
        model_alias: str = MLOPS_MODEL_ALIAS,
        timeout_seconds: float = CLOUD_RUN_ADMIN_TIMEOUT_SECONDS,
        token_provider: Callable[[], str] | None = None,
        smoke_client_factory: Callable[[str, str], MLServingClient]
        | None = None,
        api_base_url: str = "https://run.googleapis.com/v2",
    ) -> None:
        required = {
            "GCP_PROJECT_ID": project_id,
            "GCP_REGION": region,
            "CLOUD_RUN_TRAINING_JOB": training_job,
            "CLOUD_RUN_SERVING_SERVICE": serving_service,
            "MLOPS_MODEL_NAME": model_name,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(f"필수 MLOps 설정이 비어 있습니다: {', '.join(missing)}")

        self.project_id = project_id
        self.region = region
        self.training_job = training_job
        self.training_container = training_container
        self.serving_service = serving_service
        self.serving_container = serving_container
        self.model_name = model_name
        self.model_alias = model_alias
        self.timeout_seconds = timeout_seconds
        self._token_provider = token_provider or _google_access_token_provider()
        self._smoke_client_factory = smoke_client_factory or _default_smoke_client
        self.api_base_url = api_base_url.rstrip("/")

    @property
    def _parent(self) -> str:
        return f"projects/{self.project_id}/locations/{self.region}"

    @property
    def _job_name(self) -> str:
        return f"{self._parent}/jobs/{self.training_job}"

    @property
    def _service_name(self) -> str:
        return f"{self._parent}/services/{self.serving_service}"

    def _request(
        self,
        method: str,
        resource: str,
        *,
        params: Mapping[str, str] | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = httpx.request(
                method,
                f"{self.api_base_url}/{resource}",
                params=params,
                json=payload,
                headers={"Authorization": f"Bearer {self._token_provider()}"},
                timeout=self.timeout_seconds,
            )
        except httpx.RequestError as exc:
            # timeout/연결 단절은 서버가 요청을 처리한 뒤 응답만 유실됐을 수도
            # 있으므로 jobs.run 호출자를 위한 수락 가능성을 보존한다.
            raise CloudRunAdminError(
                f"Cloud Run Admin API {method} 요청에 실패했습니다.",
                request_may_have_been_accepted=True,
            ) from exc

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            # 명시적인 4xx는 Cloud Run이 요청을 거절한 결과다. HTTP 408은
            # 처리 경계를 확정할 수 없는 timeout이므로 보수적으로 남긴다.
            request_may_have_been_accepted = (
                status_code >= 500 or status_code == 408
            )
            raise CloudRunAdminError(
                f"Cloud Run Admin API {method} 요청에 실패했습니다.",
                status_code=status_code,
                request_may_have_been_accepted=request_may_have_been_accepted,
            ) from exc

        try:
            body = response.json()
        except ValueError as exc:
            # 성공 HTTP 응답까지 왔으므로 요청 자체는 이미 수락됐을 수 있다.
            raise CloudRunAdminError(
                f"Cloud Run Admin API {method} 응답을 해석하지 못했습니다.",
                status_code=response.status_code,
                request_may_have_been_accepted=True,
            ) from exc

        if not isinstance(body, dict):
            raise CloudRunAdminError(
                "Cloud Run Admin API 응답이 JSON 객체가 아닙니다.",
                status_code=response.status_code,
                request_may_have_been_accepted=True,
            )
        return body

    def run_training(
        self,
        *,
        min_pr_auc: float,
        min_recall: float,
        dataset_uri: str | None = None,
        training_run_id: int | None = None,
    ) -> dict[str, Any]:
        """기존 Cloud Run Job을 환경변수 override와 함께 한 번 실행한다."""

        env = [
            {"name": "TRAINING_MODE", "value": "train"},
            {"name": "MODEL_MIN_PR_AUC", "value": str(min_pr_auc)},
            {"name": "MODEL_MIN_RECALL", "value": str(min_recall)},
            {"name": "MLFLOW_REGISTERED_MODEL_NAME", "value": self.model_name},
            {"name": "MLFLOW_MODEL_ALIAS", "value": self.model_alias},
        ]
        if dataset_uri:
            env.append({"name": "TRAINING_DATA_URI", "value": dataset_uri})
        if training_run_id is not None:
            env.append(
                {"name": "BACKEND_TRAINING_RUN_ID", "value": str(training_run_id)}
            )

        container_override: dict[str, Any] = {"env": env}
        if self.training_container:
            container_override["name"] = self.training_container

        return self._request(
            "POST",
            f"{self._job_name}:run",
            payload={
                "overrides": {"containerOverrides": [container_override]},
            },
        )

    @staticmethod
    def training_execution_name(operation: Mapping[str, Any]) -> str | None:
        """jobs.run LRO에서 실제 Execution resource name만 추출합니다.

        LRO 자체 이름을 ``cloud_run_execution_name`` 컬럼에 넣지 않습니다.
        Cloud Run 응답 시점에 target이 없으면 완료 전까지 ``None``을 유지합니다.
        """

        response = operation.get("response")
        metadata = operation.get("metadata")
        candidates = [
            response.get("name") if isinstance(response, dict) else None,
            metadata.get("target") if isinstance(metadata, dict) else None,
        ]
        for candidate in candidates:
            if isinstance(candidate, str) and "/executions/" in candidate:
                execution_name = candidate.rstrip("/").rsplit("/", 1)[-1]
                if execution_name and all(
                    character.isalnum() or character == "-"
                    for character in execution_name
                ):
                    return execution_name
        return None

    def get_training_status(self) -> dict[str, Any]:
        return self._request("GET", self._job_name)

    def get_training_execution(self, execution_name: str) -> dict[str, Any]:
        """이 Training Job에 속한 단일 Execution의 live 상태를 조회한다."""

        if not execution_name or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"
            for character in execution_name
        ):
            raise CloudRunAdminError("올바르지 않은 Cloud Run execution 이름입니다.")
        return self._request(
            "GET",
            f"{self._job_name}/executions/{execution_name}",
        )

    @staticmethod
    def training_execution_outcome(execution: Mapping[str, Any]) -> str:
        """Cloud Run v2 terminalCondition을 내부 최소 상태로 정규화한다."""

        terminal = execution.get("terminalCondition")
        if isinstance(terminal, dict):
            state = terminal.get("state")
            if state == "CONDITION_SUCCEEDED":
                return "SUCCEEDED"
            if state == "CONDITION_FAILED":
                return "FAILED"

        failed_count = execution.get("failedCount", 0)
        cancelled_count = execution.get("cancelledCount", 0)
        if any(
            isinstance(count, int) and count > 0
            for count in (failed_count, cancelled_count)
        ):
            return "FAILED"
        succeeded_count = execution.get("succeededCount", 0)
        if (
            isinstance(succeeded_count, int)
            and succeeded_count > 0
            and execution.get("completionTime")
        ):
            return "SUCCEEDED"
        return "RUNNING"

    def get_operation(self, operation_id: str) -> dict[str, Any]:
        if not operation_id or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for character in operation_id
        ):
            raise CloudRunAdminError("올바르지 않은 Cloud Run operation ID입니다.")
        return self._request(
            "GET",
            f"{self._parent}/operations/{operation_id}",
        )

    def get_serving_status(self) -> dict[str, Any]:
        return self._request("GET", self._service_name)

    def get_model_deployment_status(self, model_version: str) -> dict[str, Any]:
        """Cloud Run live 상태에서 특정 모델의 100% 전환 완료 여부를 판정한다.

        확정 ERD에는 serving revision/operation 컬럼이 없으므로 DB의 과거
        operation을 신뢰하지 않습니다. 최신 Ready 리비전, 그 리비전의 모델
        환경변수, 현재 traffic을 한 번의 Service 조회 결과로 함께 검증합니다.
        """

        self._deployment_tag(model_version)
        service = self.get_serving_status()
        latest_created = self._revision_name(service.get("latestCreatedRevision"))
        latest_ready = self._revision_name(service.get("latestReadyRevision"))

        template = self._copy_template(service)
        container = self._target_container(template)
        env = container.get("env", [])
        if not isinstance(env, list):
            raise CloudRunAdminError("Serving 컨테이너 env 형식이 올바르지 않습니다.")
        env_by_name = {
            item.get("name"): item.get("value")
            for item in env
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        revision_model_version = env_by_name.get("ML_MODEL_VERSION")

        traffic_to_ready = 0
        total_traffic = 0
        statuses = service.get("trafficStatuses", [])
        if isinstance(statuses, list):
            for target in statuses:
                if not isinstance(target, dict):
                    continue
                percent = target.get("percent", 0)
                if not isinstance(percent, int) or percent < 0:
                    continue
                total_traffic += percent
                revision = self._revision_name(target.get("revision"))
                if revision is None and target.get("type") == TRAFFIC_LATEST:
                    revision = latest_created
                if revision and revision == latest_ready:
                    traffic_to_ready += percent

        reconciling = bool(service.get("reconciling", False))
        ready = (
            not reconciling
            and latest_created is not None
            and latest_created == latest_ready
            and revision_model_version == model_version
            and traffic_to_ready == 100
            and total_traffic == 100
        )
        reason: str | None = None
        if reconciling:
            reason = "Serving Service가 아직 조정 중입니다."
        elif latest_created is None or latest_created != latest_ready:
            reason = "최신 Serving 리비전이 아직 Ready 상태가 아닙니다."
        elif revision_model_version != model_version:
            reason = "최신 Ready 리비전의 모델 버전이 학습 실행과 다릅니다."
        elif traffic_to_ready != 100 or total_traffic != 100:
            reason = "승격 대상 리비전에 트래픽 100%가 반영되지 않았습니다."

        return {
            "ready": ready,
            "reason": reason,
            "modelVersion": model_version,
            "revisionModelVersion": revision_model_version,
            "revision": latest_ready,
            "trafficPercent": traffic_to_ready,
            "totalTrafficPercent": total_traffic,
            "reconciling": reconciling,
            "service": service.get("name"),
        }

    @staticmethod
    def _deployment_tag(model_version: str) -> str:
        if not model_version.isdigit():
            raise CloudRunAdminError("MLflow model_version은 숫자여야 합니다.")
        return f"model-v{model_version}"

    @staticmethod
    def _copy_template(service: Mapping[str, Any]) -> dict[str, Any]:
        source = service.get("template")
        if not isinstance(source, dict):
            raise CloudRunAdminError("Serving Service에 revision template이 없습니다.")
        template = {
            field: deepcopy(source[field])
            for field in REVISION_TEMPLATE_INPUT_FIELDS
            if field in source
        }
        containers = template.get("containers", [])
        if isinstance(containers, list):
            for container in containers:
                if isinstance(container, dict):
                    container.pop("buildInfo", None)
        return template

    def _target_container(self, template: dict[str, Any]) -> dict[str, Any]:
        containers = template.get("containers")
        if not isinstance(containers, list) or not containers:
            raise CloudRunAdminError("Serving revision template에 컨테이너가 없습니다.")
        if not self.serving_container:
            return containers[0]
        for container in containers:
            if container.get("name") == self.serving_container:
                return container
        raise CloudRunAdminError(
            f"Serving 컨테이너 {self.serving_container!r}를 찾지 못했습니다."
        )

    @staticmethod
    def _set_plain_env(container: dict[str, Any], values: Mapping[str, str]) -> None:
        current = container.get("env", [])
        if not isinstance(current, list):
            raise CloudRunAdminError("Serving 컨테이너 env 형식이 올바르지 않습니다.")
        preserved = [item for item in current if item.get("name") not in values]
        preserved.extend(
            {"name": name, "value": value} for name, value in values.items()
        )
        container["env"] = preserved

    @staticmethod
    def _remove_env(container: dict[str, Any], names: set[str]) -> None:
        current = container.get("env", [])
        if not isinstance(current, list):
            raise CloudRunAdminError("Serving 컨테이너 env 형식이 올바르지 않습니다.")
        container["env"] = [item for item in current if item.get("name") not in names]

    @staticmethod
    def _pinned_current_traffic(service: Mapping[str, Any]) -> list[dict[str, Any]]:
        by_revision: dict[str, int] = {}
        statuses = service.get("trafficStatuses", [])
        if isinstance(statuses, list):
            for target in statuses:
                revision = target.get("revision")
                percent = target.get("percent", 0)
                if revision and isinstance(percent, int) and percent > 0:
                    by_revision[revision] = by_revision.get(revision, 0) + percent

        if not by_revision:
            latest_ready = service.get("latestReadyRevision")
            if not latest_ready:
                raise CloudRunAdminError("현재 서비스 중인 Serving 리비전이 없습니다.")
            by_revision[latest_ready] = 100

        if sum(by_revision.values()) != 100:
            raise CloudRunAdminError("현재 Serving 트래픽 합계가 100%가 아닙니다.")

        return [
            {
                "type": TRAFFIC_REVISION,
                "revision": revision,
                "percent": percent,
            }
            for revision, percent in by_revision.items()
        ]

    def create_model_revision(self, model_version: str) -> dict[str, Any]:
        """기존 트래픽을 고정한 채 새 모델 리비전과 태그 URL만 만든다."""

        tag = self._deployment_tag(model_version)
        service = self.get_serving_status()
        template = self._copy_template(service)
        container = self._target_container(template)
        # 판정 임계값은 모델 artifact와 Registry 버전 태그에 저장된다. 이전
        # 리비전의 수동 환경변수가 새 모델 판정을 덮어쓰지 않도록 제거한다.
        self._remove_env(container, {"ML_FRAUD_THRESHOLD"})
        self._set_plain_env(
            container,
            {
                "ML_PREDICTOR_MODE": "mlflow",
                "ML_MODEL_NAME": self.model_name,
                "ML_MODEL_VERSION": model_version,
            },
        )
        traffic = self._pinned_current_traffic(service)
        traffic.append(
            {
                "type": TRAFFIC_LATEST,
                "percent": 0,
                "tag": tag,
            }
        )

        payload: dict[str, Any] = {
            "name": self._service_name,
            "template": template,
            "traffic": traffic,
        }
        if service.get("etag"):
            payload["etag"] = service["etag"]

        operation = self._request(
            "PATCH",
            self._service_name,
            params={
                "updateMask": "template,traffic",
                "forceNewRevision": "true",
            },
            payload=payload,
        )
        return {
            "operation": operation,
            "tag": tag,
            "previousTraffic": traffic[:-1],
        }

    @staticmethod
    def _tagged_target(
        service: Mapping[str, Any],
        tag: str,
    ) -> dict[str, Any]:
        statuses = service.get("trafficStatuses", [])
        if isinstance(statuses, list):
            for target in statuses:
                if target.get("tag") == tag:
                    return target
        raise CloudRunAdminError(
            "새 모델의 태그 URL이 아직 준비되지 않았습니다. operation을 먼저 확인하세요."
        )

    @staticmethod
    def _revision_name(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip().rstrip("/")
        if not normalized:
            return None
        return normalized.rsplit("/", maxsplit=1)[-1]

    def promote_model_revision(
        self,
        *,
        model_version: str,
        transaction_id: str,
        features: dict[str, Any],
    ) -> dict[str, Any]:
        """태그 리비전을 실제 예측으로 검증한 뒤 트래픽 100%를 승격한다."""

        tag = self._deployment_tag(model_version)
        service = self.get_serving_status()
        if service.get("reconciling"):
            raise CloudRunAdminError("Serving Service가 아직 리비전을 준비 중입니다.")

        target = self._tagged_target(service, tag)
        latest_created_revision = self._revision_name(
            service.get("latestCreatedRevision")
        )
        latest_ready_revision = self._revision_name(
            service.get("latestReadyRevision")
        )
        revision = self._revision_name(target.get("revision"))
        if revision is None and target.get("type") == TRAFFIC_LATEST:
            revision = latest_created_revision
        tagged_url = target.get("uri")
        service_uri = service.get("uri")
        if not revision or not tagged_url or not service_uri:
            raise CloudRunAdminError("승격 대상 리비전 URL 정보가 비어 있습니다.")
        if revision != latest_created_revision:
            raise CloudRunAdminError("승격 대상이 가장 최근에 생성된 리비전이 아닙니다.")
        if revision != latest_ready_revision:
            raise CloudRunAdminError("가장 최근 리비전이 아직 Ready 상태가 아닙니다.")

        smoke_client = self._smoke_client_factory(tagged_url, service_uri)
        prediction: MLPredictionResponse = smoke_client.predict(
            transaction_id=transaction_id,
            features=features,
        )
        if prediction.model_name != self.model_name:
            raise CloudRunAdminError("스모크 응답의 model_name이 요청과 다릅니다.")
        if prediction.model_version != model_version:
            raise CloudRunAdminError("스모크 응답의 model_version이 요청과 다릅니다.")

        promotion_payload: dict[str, Any] = {
            "name": self._service_name,
            "traffic": [
                {
                    "type": TRAFFIC_REVISION,
                    "revision": revision,
                    "percent": 100,
                }
            ],
        }
        if service.get("etag"):
            promotion_payload["etag"] = service["etag"]

        operation = self._request(
            "PATCH",
            self._service_name,
            params={"updateMask": "traffic"},
            payload=promotion_payload,
        )
        return {
            "operation": operation,
            "revision": revision,
            "smokePrediction": prediction.model_dump(),
        }


def get_cloud_run_admin_client() -> CloudRunAdminClient:
    return CloudRunAdminClient()


CloudRunAdminClientDep = Annotated[
    CloudRunAdminClient,
    Depends(get_cloud_run_admin_client),
]


__all__ = [
    "CloudRunAdminClient",
    "CloudRunAdminClientDep",
    "CloudRunAdminError",
    "get_cloud_run_admin_client",
]
