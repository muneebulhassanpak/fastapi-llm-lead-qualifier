from httpx import AsyncClient


async def test_openapi_has_metadata(client: AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()

    assert schema["info"]["title"] == "Lead Qualification API"
    assert schema["info"]["version"] == "9.9.9"
    assert schema["info"]["license"]["name"] == "MIT"
    assert "/v1/health" in schema["paths"]
