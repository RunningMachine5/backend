"""Cloud Run v2 Admin API를 통한 학습 실행과 무중단 모델 배포 제어."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from copy import deepcopy
from functools import lru_cache
from threading import Lock
from typing import Annotated, Any

import httpx
from fastapi import Depends, Request
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
        http_client=httpx.Client(),
    )


class CloudRunAdminClient:
    """학습 Job 실행과 검증된 Serving 리비전 승격을 담당한다.

    이 client는 모델의 성능을 판단하지 않는다. API 계층이 승인한 정확한 모델
    버전을 0% 리비전으로 확인하고, tagged URL의 실제 예측이 성공한 뒤에만
    운영 트래픽 변경을 요청한다.
    """

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
        http_client: httpx.Client | None = None,
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
        self._http_client = http_client

    @property
    def _parent(self) -> str:
        return f"projects/{self.project_id}/locations/{self.region}"

    @property
    def _job_name(self) -> str:
        return f"{self._parent}/jobs/{self.training_job}"

    @property
    def _service_name(self) -> str:
        return f"{self._parent}/services/{self.serving_service}"

    def _revision_resource(self, revision: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", revision):
            raise CloudRunAdminError("올바르지 않은 Cloud Run revision 이름입니다.")
        return f"{self._service_name}/revisions/{revision}"

    def _request(
        self,
        method: str,
        resource: str,
        *,
        params: Mapping[str, str] | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            request = (
                self._http_client.request if self._http_client else httpx.request
            )
            response = request(
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

    def close(self) -> None:
        if self._http_client is not None:
            self._http_client.close()

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
        operation을 신뢰하지 않습니다. Service template이나 latestCreated는 이후
        준비된 0% 후보를 가리킬 수 있으므로, 실제 100% 트래픽 리비전을 찾아 그
        구체 Revision 리소스의 Ready 상태와 모델 환경변수를 검증합니다.
        """

        self._deployment_tag(model_version)
        service = self.get_serving_status()
        total_traffic = 0
        traffic_by_revision: dict[str, int] = {}
        latest_created = self._revision_name(service.get("latestCreatedRevision"))
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
                if revision and percent > 0:
                    traffic_by_revision[revision] = (
                        traffic_by_revision.get(revision, 0) + percent
                    )

        live_revisions = [
            revision
            for revision, percent in traffic_by_revision.items()
            if percent == 100
        ]
        live_revision = live_revisions[0] if len(live_revisions) == 1 else None
        revision: dict[str, Any] | None = None
        revision_model_version: Any = None
        revision_model_name: Any = None
        revision_predictor_mode: Any = None
        revision_ready = False
        revision_contract_error: str | None = None
        if live_revision is not None and total_traffic == 100:
            revision = self._request(
                "GET",
                self._revision_resource(live_revision),
            )
            contract = self._inspect_revision_model_contract(
                revision,
                model_version=model_version,
            )
            env_by_name = contract["env"]
            revision_model_version = env_by_name.get("ML_MODEL_VERSION")
            revision_model_name = env_by_name.get("ML_MODEL_NAME")
            revision_predictor_mode = env_by_name.get("ML_PREDICTOR_MODE")
            if not contract["image_digest_valid"]:
                revision_contract_error = "트래픽 100% 리비전 이미지가 digest로 고정되지 않았습니다."
            elif "ML_FRAUD_THRESHOLD" in env_by_name:
                revision_contract_error = "트래픽 100% 리비전에 레거시 임계값 설정이 남아 있습니다."
            elif revision_predictor_mode != "mlflow":
                revision_contract_error = "트래픽 100% 리비전이 mlflow 추론 모드가 아닙니다."
            elif revision_model_name != self.model_name:
                revision_contract_error = "트래픽 100% 리비전의 모델 이름이 승인 대상과 다릅니다."
            elif revision_model_version != model_version:
                revision_contract_error = "트래픽 100% 리비전의 모델 버전이 학습 실행과 다릅니다."
            conditions = revision.get("conditions", [])
            revision_ready = any(
                isinstance(condition, dict)
                and condition.get("type") == "Ready"
                and condition.get("state") == "CONDITION_SUCCEEDED"
                for condition in conditions
            ) and not bool(revision.get("reconciling", False))

        reconciling = bool(service.get("reconciling", False))
        ready = (
            not reconciling
            and live_revision is not None
            and revision_ready
            and revision_contract_error is None
            and total_traffic == 100
        )
        reason: str | None = None
        if reconciling:
            reason = "Serving Service가 아직 조정 중입니다."
        elif live_revision is None or total_traffic != 100:
            reason = "하나의 Serving 리비전에 트래픽 100%가 반영되지 않았습니다."
        elif not revision_ready:
            reason = "트래픽 100% Serving 리비전이 Ready 상태가 아닙니다."
        elif revision_contract_error is not None:
            reason = revision_contract_error

        return {
            "ready": ready,
            "reason": reason,
            "modelVersion": model_version,
            "revisionModelVersion": revision_model_version,
            "revisionModelName": revision_model_name,
            "revisionPredictorMode": revision_predictor_mode,
            "revision": live_revision,
            "trafficPercent": traffic_by_revision.get(live_revision or "", 0),
            "totalTrafficPercent": total_traffic,
            "reconciling": reconciling,
            "service": service.get("name"),
        }

    @staticmethod
    def _deployment_tag(model_version: str) -> str:
        if not re.fullmatch(r"[1-9][0-9]*", model_version):
            raise CloudRunAdminError("MLflow model_version은 1 이상의 ASCII 숫자여야 합니다.")
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

    def _inspect_revision_model_contract(
        self,
        resource: Mapping[str, Any],
        *,
        model_version: str,
    ) -> dict[str, Any]:
        """리비전의 이미지와 MLflow 환경변수를 공통 형식으로 읽는다."""

        container = self._target_container(dict(resource))
        env = container.get("env", [])
        if not isinstance(env, list):
            raise CloudRunAdminError("Serving 컨테이너 env 형식이 올바르지 않습니다.")
        env_by_name = {
            item.get("name"): item.get("value")
            for item in env
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        expected = {
            "ML_PREDICTOR_MODE": "mlflow",
            "ML_MODEL_NAME": self.model_name,
            "ML_MODEL_VERSION": model_version,
        }
        mismatched = [
            name for name, value in expected.items() if env_by_name.get(name) != value
        ]
        if "ML_FRAUD_THRESHOLD" in env_by_name:
            mismatched.append("ML_FRAUD_THRESHOLD")
        image = container.get("image")
        return {
            "container": container,
            "env": env_by_name,
            "image": image,
            "image_digest_valid": isinstance(image, str)
            and re.search(r"@sha256:[0-9a-f]{64}$", image) is not None,
            "mismatched": mismatched,
        }

    def _validate_revision_model_contract(
        self,
        resource: Mapping[str, Any],
        *,
        model_version: str,
        context: str,
    ) -> dict[str, Any]:
        """공통 모델 계약을 검증하고 기존 오류 문구를 유지한다."""

        contract = self._inspect_revision_model_contract(
            resource,
            model_version=model_version,
        )
        if not contract["image_digest_valid"]:
            raise CloudRunAdminError(f"{context} 리비전 이미지가 digest로 고정되지 않았습니다.")
        if contract["mismatched"]:
            raise CloudRunAdminError(
                f"{context} 리비전의 모델 설정이 승인 대상과 다릅니다: "
                + ", ".join(contract["mismatched"])
            )
        return {
            "container": contract["container"],
            "env": contract["env"],
            "image": contract["image"],
        }

    @staticmethod
    def _pinned_current_traffic(service: Mapping[str, Any]) -> list[dict[str, Any]]:
        by_revision: dict[str, int] = {}
        latest_created = CloudRunAdminClient._revision_name(
            service.get("latestCreatedRevision")
        )
        statuses = service.get("trafficStatuses", [])
        if isinstance(statuses, list):
            for target in statuses:
                if not isinstance(target, dict):
                    continue
                revision = CloudRunAdminClient._revision_name(target.get("revision"))
                if revision is None and target.get("type") == TRAFFIC_LATEST:
                    revision = latest_created
                percent = target.get("percent", 0)
                if revision and isinstance(percent, int) and percent > 0:
                    by_revision[revision] = by_revision.get(revision, 0) + percent

        if not by_revision:
            latest_ready = CloudRunAdminClient._revision_name(
                service.get("latestReadyRevision")
            )
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

    @staticmethod
    def _find_tagged_target(
        service: Mapping[str, Any],
        tag: str,
    ) -> dict[str, Any] | None:
        statuses = service.get("trafficStatuses", [])
        if not isinstance(statuses, list):
            return None
        matches = [
            target
            for target in statuses
            if isinstance(target, dict) and target.get("tag") == tag
        ]
        if len(matches) > 1:
            raise CloudRunAdminError(
                f"Serving Service에 {tag} 트래픽 태그가 중복되어 있습니다."
            )
        return matches[0] if matches else None

    def _validate_prepared_model_revision(
        self,
        *,
        service: Mapping[str, Any],
        target: Mapping[str, Any],
        model_version: str,
        tag: str,
    ) -> dict[str, Any]:
        """CD가 미리 만든 0% 리비전이 관리자 승격 계약을 만족하는지 검증한다."""

        if service.get("reconciling"):
            raise CloudRunAdminError("Serving Service가 아직 리비전을 준비 중입니다.")

        latest_created = self._revision_name(service.get("latestCreatedRevision"))
        latest_ready = self._revision_name(service.get("latestReadyRevision"))
        revision = self._revision_name(target.get("revision"))
        if revision is None and target.get("type") == TRAFFIC_LATEST:
            revision = latest_created
        if not revision or revision != latest_created:
            raise CloudRunAdminError(
                "CD가 준비한 Serving 리비전이 가장 최근에 생성된 리비전이 아닙니다."
            )
        if revision != latest_ready:
            raise CloudRunAdminError(
                "CD가 준비한 최신 Serving 리비전이 아직 Ready 상태가 아닙니다."
            )

        # Cloud Run v2 JSON은 기본값인 0을 응답에서 생략할 수 있다.
        percent = target.get("percent", 0)
        if not isinstance(percent, int) or isinstance(percent, bool) or percent != 0:
            raise CloudRunAdminError(
                "CD가 준비한 Serving 리비전의 트래픽이 0%가 아닙니다."
            )
        tagged_url = target.get("uri")
        if not isinstance(tagged_url, str) or not tagged_url.strip():
            raise CloudRunAdminError("CD가 준비한 Serving 리비전의 태그 URL이 없습니다.")

        template = self._copy_template(service)
        contract = self._validate_revision_model_contract(
            template,
            model_version=model_version,
            context="CD가 준비한 Serving",
        )

        current_traffic = self._pinned_current_traffic(service)
        if any(item["revision"] == revision for item in current_traffic):
            raise CloudRunAdminError(
                "CD가 준비한 Serving 리비전에 운영 트래픽이 연결되어 있습니다."
            )

        return {
            "operation": None,
            "tag": tag,
            "revision": revision,
            "image": contract["image"],
            "taggedUrl": tagged_url,
            "previousTraffic": current_traffic,
            "reused": True,
        }

    def verify_staged_model_revision(self, model_version: str) -> dict[str, Any]:
        """ML Serving CD가 준비한 0% 리비전의 계약을 검증한다."""

        tag = self._deployment_tag(model_version)
        service = self.get_serving_status()
        target = self._find_tagged_target(service, tag)
        if target is None:
            raise CloudRunAdminError(
                f"{tag} 태그의 0% Serving 리비전이 없습니다. "
                "ML Serving CD를 먼저 실행해 후보 리비전을 준비하세요."
            )
        return self._validate_prepared_model_revision(
            service=service,
            target=target,
            model_version=model_version,
            tag=tag,
        )

    @staticmethod
    def _tagged_target(
        service: Mapping[str, Any],
        tag: str,
    ) -> dict[str, Any]:
        target = CloudRunAdminClient._find_tagged_target(service, tag)
        if target is not None:
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

    def _validate_concrete_model_revision(
        self,
        revision: Mapping[str, Any],
        *,
        model_version: str,
        context: str,
    ) -> dict[str, Any]:
        if revision.get("reconciling"):
            raise CloudRunAdminError(f"{context} 리비전이 아직 준비 중입니다.")
        conditions = revision.get("conditions", [])
        ready = any(
            isinstance(condition, dict)
            and condition.get("type") == "Ready"
            and condition.get("state") == "CONDITION_SUCCEEDED"
            for condition in conditions
        )
        if not ready:
            raise CloudRunAdminError(f"{context} 리비전이 Ready 상태가 아닙니다.")

        return self._validate_revision_model_contract(
            revision,
            model_version=model_version,
            context=context,
        )

    def promote_model_revision(
        self,
        *,
        model_version: str,
        transaction_id: int,
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
        percent = target.get("percent", 0)
        if not isinstance(percent, int) or isinstance(percent, bool) or percent != 0:
            raise CloudRunAdminError("승격 대상 후보 리비전의 트래픽이 0%가 아닙니다.")

        concrete_revision = self._request(
            "GET",
            self._revision_resource(revision),
        )
        self._validate_concrete_model_revision(
            concrete_revision,
            model_version=model_version,
            context="승격 대상",
        )

        smoke_client = self._smoke_client_factory(tagged_url, service_uri)
        try:
            prediction: MLPredictionResponse = smoke_client.predict(
                transaction_id=transaction_id,
                features=features,
            )
        finally:
            smoke_client.close()
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
                    "tag": tag,
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


def get_cloud_run_admin_client(request: Request) -> CloudRunAdminClient:
    return request.app.state.service_clients.cloud_run()


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
