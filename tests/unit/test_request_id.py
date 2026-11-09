import pytest
from httpx import AsyncClient


async def test_generates_request_id_when_missing(client: AsyncClient) -> None:
    response = await client.get("/v1/health")

    assert len(response.headers["X-Request-ID"]) == 32


async def test_echoes_valid_request_id(client: AsyncClient) -> None:
    response = await client.get("/v1/health", headers={"X-Request-ID": "abc-123"})

    assert response.headers["X-Request-ID"] == "abc-123"


@pytest.mark.parametrize("bad_id", ["x" * 65, "has spaces", "new\\nline", "<script>"])
async def test_replaces_unsafe_request_id(client: AsyncClient, bad_id: str) -> None:
    response = await client.get("/v1/health", headers={"X-Request-ID": bad_id})

    assert response.headers["X-Request-ID"] != bad_id
