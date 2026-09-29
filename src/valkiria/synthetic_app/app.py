from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from valkiria.infrastructure.settings import Settings

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS vehicles (vehicle_id INTEGER PRIMARY KEY, model VARCHAR(100) NOT NULL, year INTEGER NOT NULL, price NUMERIC(12,2) NOT NULL, stock INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS dealers (dealer_id INTEGER PRIMARY KEY, name VARCHAR(150) NOT NULL, region VARCHAR(80) NOT NULL, active BOOLEAN NOT NULL)",
    "CREATE TABLE IF NOT EXISTS customers (customer_id INTEGER PRIMARY KEY, name VARCHAR(150) NOT NULL, email VARCHAR(200) NOT NULL)",
    "CREATE TABLE IF NOT EXISTS inventory (inventory_id INTEGER PRIMARY KEY, vehicle_id INTEGER NOT NULL, dealer_id INTEGER NOT NULL, available BOOLEAN NOT NULL)",
    "CREATE TABLE IF NOT EXISTS sales_orders (order_id INTEGER PRIMARY KEY, dealer_id INTEGER NOT NULL, vehicle_id INTEGER NOT NULL, customer_id INTEGER NOT NULL, status VARCHAR(30) NOT NULL, total NUMERIC(12,2) NOT NULL)",
    "CREATE TABLE IF NOT EXISTS service_appointments (appointment_id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL, dealer_id INTEGER NOT NULL, status VARCHAR(30) NOT NULL)",
)


class SalesOrderRequest(BaseModel):
    dealer_id: int
    vehicle_id: int
    customer_id: int
    total: float = Field(gt=0)


def create_synthetic_app(database_url: str | None = None) -> FastAPI:
    """Aplicación bajo prueba; usa PostgreSQL sintético si se configura y SQLite solo como fallback."""
    app = FastAPI(title="Nissan Synthetic App", version="1.1.0")
    url = database_url or Settings().secret("synthetic_database_url") or "sqlite+pysqlite:///:memory:"
    # SQLite en memoria: una sola conexión compartida; si no, cada hilo vería una base vacía distinta.
    options = {"connect_args": {"check_same_thread": False}, "poolclass": StaticPool} if url.startswith("sqlite") and ":memory:" in url else {"pool_pre_ping": True}
    engine = create_engine(url, future=True, **options)
    with engine.begin() as connection:
        for statement in SCHEMA:
            connection.execute(text(statement))
        if connection.execute(text("SELECT COUNT(*) FROM vehicles")).scalar_one() == 0:
            connection.execute(text("INSERT INTO vehicles VALUES (:id,:model,:year,:price,:stock)"), [{"id": 1, "model": "Sentra", "year": 2025, "price": 289900.0, "stock": 10}, {"id": 2, "model": "Kicks", "year": 2025, "price": 355000.0, "stock": 0}, {"id": 3, "model": "Versa", "year": 2024, "price": 245000.0, "stock": 3}])
            connection.execute(text("INSERT INTO dealers VALUES (:id,:name,:region,:active)"), [{"id": 1, "name": "Nissan Apodaca Sintético", "region": "Nuevo León", "active": True}, {"id": 2, "name": "Nissan Centro Sintético", "region": "Nuevo León", "active": False}])
            connection.execute(text("INSERT INTO customers VALUES (:id,:name,:email)"), [{"id": 1, "name": "Cliente Sintético 001", "email": "cliente001@example.test"}, {"id": 2, "name": "Cliente Sintético 002", "email": "cliente002@example.test"}])
            connection.execute(text("INSERT INTO inventory VALUES (:id,:vehicle,:dealer,:available)"), [{"id": 1, "vehicle": 1, "dealer": 1, "available": True}, {"id": 2, "vehicle": 2, "dealer": 1, "available": False}, {"id": 3, "vehicle": 3, "dealer": 2, "available": True}])
            connection.execute(text("INSERT INTO sales_orders VALUES (:id,:dealer,:vehicle,:customer,:status,:total)"), [{"id": 1, "dealer": 1, "vehicle": 1, "customer": 1, "status": "approved", "total": 289900.0}, {"id": 2, "dealer": 1, "vehicle": 2, "customer": 2, "status": "cancelled", "total": 355000.0}])
            connection.execute(text("INSERT INTO service_appointments VALUES (:id,:customer,:dealer,:status)"), {"id": 1, "customer": 1, "dealer": 1, "status": "scheduled"})

    def rows(query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with engine.connect() as connection:
            return [dict(row) for row in connection.execute(text(query), params or {}).mappings().all()]

    @app.get("/health")
    async def health():
        return {"status": "ok", "mode": "synthetic", "database": "configured_profile", "database_url_not_exposed": True}

    @app.get("/vehicles")
    async def vehicles(available_only: bool = False):
        query = "SELECT * FROM vehicles WHERE stock > 0" if available_only else "SELECT * FROM vehicles"
        return {"items": rows(query)}

    @app.get("/inventory")
    async def inventory():
        return {"items": rows("SELECT i.inventory_id, i.vehicle_id, v.model, i.dealer_id, i.available FROM inventory i JOIN vehicles v ON v.vehicle_id=i.vehicle_id")}

    @app.get("/dealers")
    async def dealers(active_only: bool = False):
        query = "SELECT * FROM dealers WHERE active = TRUE" if active_only else "SELECT * FROM dealers"
        return {"items": rows(query)}

    @app.post("/sales-orders", status_code=201)
    async def sales_orders(request: SalesOrderRequest):
        with engine.begin() as connection:
            vehicle = connection.execute(text("SELECT * FROM vehicles WHERE vehicle_id=:id"), {"id": request.vehicle_id}).mappings().first()
            dealer = connection.execute(text("SELECT * FROM dealers WHERE dealer_id=:id"), {"id": request.dealer_id}).mappings().first()
            customer = connection.execute(text("SELECT * FROM customers WHERE customer_id=:id"), {"id": request.customer_id}).mappings().first()
            if not vehicle or not customer:
                raise HTTPException(404, "vehicle_or_customer_not_found")
            if not dealer or not dealer["active"]:
                raise HTTPException(409, "dealer_inactive")
            if vehicle["stock"] <= 0:
                raise HTTPException(409, "vehicle_out_of_stock")
            order_id = connection.execute(text("SELECT COALESCE(MAX(order_id), 0) + 1 FROM sales_orders")).scalar_one()
            connection.execute(text("INSERT INTO sales_orders VALUES (:id,:dealer,:vehicle,:customer,:status,:total)"), {"id": order_id, "dealer": request.dealer_id, "vehicle": request.vehicle_id, "customer": request.customer_id, "status": "approved", "total": request.total})
        return {"order_id": order_id, "status": "approved", "vehicle_id": request.vehicle_id}

    @app.put("/sales-orders/{order_id}/cancel")
    async def cancel_order(order_id: int):
        with engine.begin() as connection:
            result = connection.execute(text("UPDATE sales_orders SET status='cancelled' WHERE order_id=:id"), {"id": order_id})
            if result.rowcount == 0:
                raise HTTPException(404, "sales_order_not_found")
        return {"order_id": order_id, "status": "cancelled"}

    @app.get("/service-appointments")
    async def service_appointments():
        return {"items": rows("SELECT * FROM service_appointments")}

    return app
