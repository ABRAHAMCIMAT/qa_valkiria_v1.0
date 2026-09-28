import os

import pytest
from httpx import ASGITransport, AsyncClient

from valkiria.synthetic_app.app import create_synthetic_app

pytestmark = pytest.mark.skipif(os.getenv("RUN_SYNTHETIC_E2E") != "1", reason="E2E sintética deshabilitada; usar RUN_SYNTHETIC_E2E=1")


async def test_nissan_synthetic_application_flow():
    app = create_synthetic_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://synthetic.test") as client:
        response = await client.get("/vehicles", params={"available_only": "true"})
        assert response.status_code == 200
        assert {item["model"] for item in response.json()["items"]} == {"Sentra", "Versa"}

        order = await client.post("/sales-orders", json={"dealer_id": 1, "vehicle_id": 1, "customer_id": 1, "total": 289900})
        assert order.status_code == 201

        out_of_stock = await client.post("/sales-orders", json={"dealer_id": 1, "vehicle_id": 2, "customer_id": 1, "total": 355000})
        assert out_of_stock.status_code == 409
