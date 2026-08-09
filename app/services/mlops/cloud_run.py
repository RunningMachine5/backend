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
    MLServingClient,
    MLPredictionResponse,
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
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise CloudRunAdminError(
                f"Cloud Run Admin API {method} 요청에 실패했습니다."
            ) from exc

        if not isinstance(body, dict):
            raise CloudRunAdminError("Cloud Run Admin API 응답이 JSON 객체가 아닙니다.")
        return body

    def run_training(
        self,
        *,
        auto_promote: bool,
        min_pr_auc: float,
        min_recall: float,
        dataset_uri: str | None = None,
        split_datetime: str | None = None,
        training_run_id: int | None = None,
        champion_model_version: str | None = None,
    ) -> dict[str, Any]:
        """기존 Cloud Run Job을 환경변수 override와 함께 한 번 실행한다."""

        if auto_promote:
            raise CloudRunAdminError(
                "자동 모델 승격은 비활성화되어 있으며 관리자 승인이 필요합니다."
            )
        env = [
            {"name": "TRAINING_MODE", "value": "train"},
            {
                "name": "MLFLOW_AUTO_PROMOTE",
                "value": str(auto_promote).lower(),
            },
            {"name": "MODEL_MIN_PR_AUC", "value": str(min_pr_auc)},
            {"name": "MODEL_MIN_RECALL", "value": str(min_recall)},
            {"name": "MLFLOW_REGISTERED_MODEL_NAME", "value": self.model_name},
            {"name": "MLFLOW_MODEL_ALIAS", "value": self.model_alias},
        ]
        if dataset_uri:
            env.append({"name": "TRAINING_DATA_URI", "value": dataset_uri})
        if split_datetime:
            env.append(
                {"name": "TRAINING_SPLIT_DATETIME", "value": split_datetime}
            )
        if training_run_id is not None:
            env.append(
                {"name": "BACKEND_TRAINING_RUN_ID", "value": str(training_run_id)}
            )
        if champion_model_version is not None:
            if not champion_model_version.isdigit():
                raise CloudRunAdminError("champion model version은 숫자여야 합니다.")
            env.append(
                {
                    "name": "CHAMPION_MODEL_VERSION",
                    "value": champion_model_version,
                }
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

    def get_training_status(self) -> dict[str, Any]:
        return self._request("GET", self._job_name)

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
        revision = target.get("revision")
        tagged_url = target.get("uri")
        service_uri = service.get("uri")
        if not revision or not tagged_url or not service_uri:
            raise CloudRunAdminError("승격 대상 리비전 URL 정보가 비어 있습니다.")
        if revision != service.get("latestCreatedRevision"):
            raise CloudRunAdminError("승격 대상이 가장 최근에 생성된 리비전이 아닙니다.")
        if revision != service.get("latestReadyRevision"):
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
