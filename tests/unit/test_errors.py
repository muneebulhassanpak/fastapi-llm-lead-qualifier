from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import BaseModel

from app.errors import AppError


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class Body(BaseModel):
    message: str


def _add_test_routes(app: FastAPI) -> None:
    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret internal detail")

    @app.get("/conflict")
    async def conflict() -> None:
        raise ConflictError("Lead is not in review.", details={"status": "synced"})

    @app.post("/echo")
    async def echo(body: Body) -> Body:
        return body


async def test_not_found_uses_error_envelope(client: AsyncClient) -> None:
    response = await client.get("/nope", headers={"X-Request-ID": "req-1"})

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "Not Found", "request_id": "req-1"}
    }


async def test_app_error_uses_its_status_and_code(app: FastAPI, client: AsyncClient) -> None:
    _add_test_routes(app)

    response = await client.get("/conflict")

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "conflict"
    assert error["details"] == {"status": "synced"}


async def test_validation_error_lists_fields(app: FastAPI, client: AsyncClient) -> None:
    _add_test_routes(app)

    response = await client.post("/echo", json={})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"][0]["loc"] == ["body", "message"]


async def test_unhandled_error_hides_internals(app: FastAPI, client: AsyncClient) -> None:
    _add_test_routes(app)

    response = await client.get("/boom")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "secret internal detail" not in response.text
