"""MLflow Tracking·Registry를 조회하고 관리자 결정만 기록하는 얇은 client.

학습 지표나 모델 버전을 Backend DB에 중복 저장하지 않는다. TrainingRun에 남은
``mlflow_run_id``를 기준으로 MLflow에서 정확한 모델 버전과 지표를 다시 찾는다.
"""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import urlsplit

import httpx
from fastapi import Depends, HTTPException, status

from app.core import config

MODEL_COMPARISON_ARTIFACT_PATH = "metadata/model-comparison.json"


class MLflowRegistryError(RuntimeError):
    """MLflow 연결 또는 Registry 계약 검증 실패."""


class MLflowRegistryClient:
    """MLflow REST API와 Backend MLOps 흐름 사이의 최소 어댑터."""

    def __init__(
        self,
        *,
        tracking_uri: str = config.MLFLOW_TRACKING_URI,
        username: str = config.MLFLOW_TRACKING_USERNAME,
        password: str = config.MLFLOW_TRACKING_PASSWORD,
        timeout_seconds: float = config.MLFLOW_TRACKING_TIMEOUT_SECONDS,
    ) -> None:
        tracking_uri = tracking_uri.strip().rstrip("/")
        parsed = urlsplit(tracking_uri)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise MLflowRegistryError(
                "MLFLOW_TRACKING_URI가 올바른 HTTP(S) 주소로 설정되지 않았습니다."
            )
        if bool(username) != bool(password):
            raise MLflowRegistryError(
                "MLflow basic auth username과 password를 함께 설정해야 합니다."
            )
        if timeout_seconds <= 0:
            raise MLflowRegistryError("MLflow timeout은 0보다 커야 합니다.")
        self.tracking_uri = tracking_uri
        self.timeout_seconds = timeout_seconds
        self._auth = (username, password) if username else None

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """인증·timeout·응답 형식 검사를 한곳에서 처리한다."""

        try:
            response = httpx.request(
                method,
                f"{self.tracking_uri}{path}",
                params=params,
                json=payload,
                auth=self._auth,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise MLflowRegistryError(
                f"MLflow {method} 요청에 실패했습니다."
            ) from exc
        if not isinstance(body, dict):
            raise MLflowRegistryError("MLflow 응답이 JSON 객체가 아닙니다.")
        return body

    def _model_versions(self, model_name: str) -> list[dict[str, Any]]:
        """페이지가 여러 개인 Registry 검색 결과를 빠짐없이 모은다."""

        escaped_name = model_name.replace("\\", "\\\\").replace("'", "\\'")
        params: dict[str, Any] = {
            "filter": f"name='{escaped_name}'",
            "max_results": 1000,
        }
        versions: list[dict[str, Any]] = []
        seen_tokens: set[str] = set()
        while True:
            body = self._request(
                "GET",
                "/api/2.0/mlflow/model-versions/search",
                params=params,
            )
            page = body.get("model_versions", [])
            if not isinstance(page, list) or any(
                not isinstance(item, dict) for item in page
            ):
                raise MLflowRegistryError(
                    "MLflow model version 목록 형식이 올바르지 않습니다."
                )
            versions.extend(page)
            token = body.get("next_page_token")
            if not isinstance(token, str) or not token:
                return versions
            if token in seen_tokens:
                raise MLflowRegistryError("MLflow pagination token이 반복되었습니다.")
            seen_tokens.add(token)
            params["page_token"] = token

    def resolve_model_version(self, model_name: str, run_id: str) -> str:
        """등록 모델 중 정확히 같은 MLflow run이 만든 단일 버전을 찾습니다."""

        # 최신 버전을 단순 선택하면 다른 학습 Run의 모델을 승인할 수 있다.
        # 따라서 Backend TrainingRun과 연결된 run_id가 정확히 같은 버전만 쓴다.
        matches = [
            item
            for item in self._model_versions(model_name)
            if item.get("name") == model_name and item.get("run_id") == run_id
        ]
        if not matches:
            raise MLflowRegistryError(
                "해당 학습 실행이 등록한 모델 버전을 MLflow에서 찾지 못했습니다."
            )
        versions = {str(item.get("version", "")) for item in matches}
        if len(versions) != 1:
            raise MLflowRegistryError(
                "학습 실행에 연결된 등록 모델 버전이 하나로 결정되지 않습니다."
            )
        version = versions.pop()
        if not version.isdigit() or int(version) <= 0:
            raise MLflowRegistryError("MLflow model version 형식이 올바르지 않습니다.")
        return version

    @staticmethod
    def _key_value_map(
        value: Any,
        *,
        numeric: bool,
    ) -> dict[str, float] | dict[str, str]:
        if not isinstance(value, list):
            return {}
        result: dict[str, float] | dict[str, str] = {}
        for item in value:
            if not isinstance(item, dict) or not isinstance(item.get("key"), str):
                continue
            raw_value = item.get("value")
            if numeric:
                try:
                    result[item["key"]] = float(raw_value)
                except (TypeError, ValueError):
                    continue
            elif raw_value is not None:
                result[item["key"]] = str(raw_value)
        return result

    def get_model_details(self, model_name: str, run_id: str) -> dict[str, Any]:
        """Backend 컬럼으로 복제하지 않고 MLflow run 상세를 그대로 요약합니다."""

        body = self._request(
            "GET",
            "/api/2.0/mlflow/runs/get",
            params={"run_id": run_id},
        )
        run = body.get("run")
        if not isinstance(run, dict):
            raise MLflowRegistryError("MLflow run 상세가 비어 있습니다.")
        info = run.get("info")
        data = run.get("data")
        if not isinstance(info, dict) or info.get("run_id") != run_id:
            raise MLflowRegistryError("MLflow run ID가 요청과 일치하지 않습니다.")
        if not isinstance(data, dict):
            data = {}
        tags = self._key_value_map(data.get("tags"), numeric=False)
        artifact_uri_value = info.get("artifact_uri")
        artifact_uri = (
            artifact_uri_value
            if isinstance(artifact_uri_value, str) and artifact_uri_value
            else None
        )
        return {
            "source": "MLFLOW",
            "run_id": run_id,
            "model_name": model_name,
            "model_version": self.resolve_model_version(model_name, run_id),
            "artifact_uri": artifact_uri,
            "model_comparison_artifact_path": MODEL_COMPARISON_ARTIFACT_PATH,
            "metrics": self._key_value_map(data.get("metrics"), numeric=True),
            "params": self._key_value_map(data.get("params"), numeric=False),
            "tags": tags,
        }

    def set_model_alias(self, model_name: str, alias: str, version: str) -> None:
        """실제 100% 배포가 확인된 버전에 champion alias를 붙인다."""

        if not alias:
            raise MLflowRegistryError("MLflow model alias가 비어 있습니다.")
        self._request(
            "POST",
            "/api/2.0/mlflow/registered-models/alias",
            payload={"name": model_name, "alias": alias, "version": version},
        )

    def set_model_version_tags(
        self,
        model_name: str,
        version: str,
        tags: dict[str, str],
    ) -> None:
        """확정 ERD에 없는 관리자 결정 감사 정보를 MLflow에 기록합니다."""

        for key, value in tags.items():
            self._request(
                "POST",
                "/api/2.0/mlflow/model-versions/set-tag",
                payload={
                    "name": model_name,
                    "version": version,
                    "key": key,
                    "value": value,
                },
            )


def get_mlflow_registry_client() -> MLflowRegistryClient:
    try:
        return MLflowRegistryClient()
    except MLflowRegistryError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


MLflowRegistryClientDep = Annotated[
    MLflowRegistryClient,
    Depends(get_mlflow_registry_client),
]


__all__ = [
    "MODEL_COMPARISON_ARTIFACT_PATH",
    "MLflowRegistryClient",
    "MLflowRegistryClientDep",
    "MLflowRegistryError",
    "get_mlflow_registry_client",
]
