import unittest

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.exception_handlers import register_exception_handlers


class ValidationPayload(BaseModel):
    amount: int


def _test_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/http-error")
    def http_error() -> None:
        raise HTTPException(
            status_code=409,
            detail="이미 처리된 요청입니다.",
        )

    @app.get("/structured-http-error")
    def structured_http_error() -> None:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "금액이 유효하지 않습니다.",
                "field": "amount",
                "reason": "out_of_range",
            },
        )

    @app.post("/validation-error")
    def validation_error(payload: ValidationPayload) -> ValidationPayload:
        return payload

    @app.get("/value-error")
    def value_error() -> None:
        raise ValueError("금액은 0보다 커야 합니다.")

    @app.get("/unexpected-error")
    def unexpected_error() -> None:
        raise RuntimeError("외부에 노출하면 안 되는 내부 오류")

    return app


class ExceptionHandlersTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(
            _test_app(),
            raise_server_exceptions=False,
        )

    def test_http_exception_uses_common_error_response(self) -> None:
        response = self.client.get("/http-error")

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json(),
            {
                "success": False,
                "data": None,
                "error": {
                    "code": "HTTP_409",
                    "message": "이미 처리된 요청입니다.",
                    "details": None,
                },
            },
        )

    def test_structured_http_exception_preserves_details(self) -> None:
        response = self.client.get("/structured-http-error")

        self.assertEqual(response.status_code, 422, response.text)
        body = response.json()
        self.assertEqual(body["error"]["code"], "HTTP_422")
        self.assertEqual(
            body["error"]["message"],
            "금액이 유효하지 않습니다.",
        )
        self.assertEqual(
            body["error"]["details"],
            {"field": "amount", "reason": "out_of_range"},
        )

    def test_request_validation_error_uses_common_error_response(self) -> None:
        response = self.client.post(
            "/validation-error",
            json={"amount": "not-an-integer"},
        )

        self.assertEqual(response.status_code, 422, response.text)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertIsNone(body["data"])
        self.assertEqual(body["error"]["code"], "VALIDATION_ERROR")
        self.assertEqual(
            body["error"]["message"],
            "요청 값이 올바르지 않습니다.",
        )
        self.assertIsInstance(body["error"]["details"], list)
        self.assertEqual(body["error"]["details"][0]["loc"], ["body", "amount"])

    def test_value_error_uses_common_error_response(self) -> None:
        response = self.client.get("/value-error")

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(
            response.json()["error"],
            {
                "code": "VALUE_ERROR",
                "message": "금액은 0보다 커야 합니다.",
                "details": None,
            },
        )

    def test_unhandled_exception_hides_internal_message(self) -> None:
        response = self.client.get("/unexpected-error")

        self.assertEqual(response.status_code, 500, response.text)
        self.assertEqual(
            response.json()["error"],
            {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "서버 내부 오류가 발생했습니다.",
                "details": None,
            },
        )
        self.assertNotIn("외부에 노출하면 안 되는 내부 오류", response.text)


if __name__ == "__main__":
    unittest.main()
