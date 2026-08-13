from typing import Any, Generic, TypeVar

from pydantic import BaseModel


T = TypeVar("T")


class ApiError(BaseModel):
    code: str
    message: str
    details: Any | None = None


class ApiResponse(BaseModel, Generic[T]):
    success: bool
    data: T | None = None
    error: ApiError | None = None


def success_response(data: T | None = None) -> ApiResponse[T]:
    return ApiResponse(
        success=True,
        data=data,
        error=None,
    )
