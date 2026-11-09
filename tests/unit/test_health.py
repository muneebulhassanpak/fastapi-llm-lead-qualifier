from httpx import AsyncClient


async def test_health_reports_unreachable_database(client: AsyncClient) -> None:
    response = await client.get("/v1/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "db": "error", "version": "9.9.9"}
