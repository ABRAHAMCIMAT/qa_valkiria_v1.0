"""Pantallas web mínimas de la app sintética de Nissan, para ejecutar localmente los scripts web (HU-009/HU-010).

HTML generado en el servidor, sin JavaScript y con etiquetas accesibles: los localizadores semánticos que generan
los scripts (getByLabel, getByRole('button' | 'combobox'), getByText) las encuentran igual que en una aplicación real.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from valkiria.application.web_steps import SYNTHETIC_DATA

# Vocabulario de las pantallas: orienta a la matriz para que sus pasos sean ejecutables en este entorno.
UI_GUIDE = (
    "Pantallas web de la app sintética (usa estos nombres en los pasos cuando la prueba sea de interfaz): "
    "«Consulta de inventario» (lista Concesionario, casilla Solo disponibles, botón Buscar; muestra modelo, año, precio y stock, "
    "y los mensajes «Concesionario inactivo» y «Sin vehículos disponibles»); «Registrar orden de venta» (listas Concesionario, Vehículo y Cliente, "
    "campo Total, botón Registrar orden; mensajes «Orden aprobada», «Vehículo sin stock» y «Concesionario inactivo»); "
    "«Citas de servicio» (tabla de citas con su estado). "
    f"Datos sintéticos que existen (úsalos en data y en los pasos para que el caso sea ejecutable): {SYNTHETIC_DATA} "
    "Escribe los pasos de interfaz como acciones sobre esos controles, en orden: «Abrir <pantalla>», «Seleccionar <lista> <opción>», "
    "«Ingresar <campo> <valor>», «Marcar Solo disponibles», «Hacer clic en <botón>»; primero actúa y al final envía con el botón. "
    "El resultado esperado es el mensaje o el dato que muestra la pantalla. No inventes filtros, pantallas ni mensajes que no están en esta lista."
)

_INVENTORY = ("SELECT v.model, v.year, v.price, v.stock, i.available FROM inventory i JOIN vehicles v ON v.vehicle_id = i.vehicle_id "
              "WHERE i.dealer_id = :dealer")
_AVAILABLE = _INVENTORY + " AND i.available = TRUE AND v.stock > 0"

_STYLE = ("<style>body{font-family:system-ui,sans-serif;margin:24px;max-width:860px}nav a{margin-right:14px}label{display:block;margin:10px 0 4px}"
          "table{border-collapse:collapse;margin-top:14px}td,th{border:1px solid #ccc;padding:6px 10px}.msg{margin:14px 0;padding:10px;border-radius:6px}"
          ".ok{background:#e6f6ee}.error{background:#fdecea}</style>")


def _page(title: str, body: str) -> HTMLResponse:
    nav = '<nav><a href="/ui">Consulta de inventario</a><a href="/ui/orders">Registrar orden de venta</a><a href="/ui/appointments">Citas de servicio</a></nav>'
    return HTMLResponse(f"<!doctype html><html lang='es'><head><meta charset='utf-8'><title>{html.escape(title)} · Nissan sintético</title>{_STYLE}</head>"
                        f"<body>{nav}<h1>{html.escape(title)}</h1>{body}</body></html>")


def _options(items: list[dict[str, Any]], key: str, label: Callable[[dict[str, Any]], str], selected: Any = None) -> str:
    return "".join(f"<option value='{item[key]}'{' selected' if str(item[key]) == str(selected) else ''}>{html.escape(label(item))}</option>" for item in items)


def _vehicle_label(vehicle: dict[str, Any]) -> str:
    return f"{vehicle['model']} {vehicle['year']}"


def mount_web(app: FastAPI, engine) -> None:
    def rows(query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with engine.connect() as connection:
            return [dict(row) for row in connection.execute(text(query), params or {}).mappings().all()]

    @app.get("/ui", response_class=HTMLResponse)
    async def inventory_page(dealer_id: int | None = None, available_only: str | None = None):
        dealers = rows("SELECT * FROM dealers ORDER BY dealer_id")
        form = ("<form method='get' action='/ui'><label for='dealer'>Concesionario</label>"
                f"<select id='dealer' name='dealer_id'>{_options(dealers, 'dealer_id', lambda d: d['name'], dealer_id)}</select>"
                f"<label><input type='checkbox' name='available_only' value='on'{' checked' if available_only else ''}> Solo disponibles</label>"
                "<button type='submit'>Buscar</button></form>")
        if dealer_id is None:
            return _page("Consulta de inventario", form)
        dealer = next((d for d in dealers if d["dealer_id"] == dealer_id), None)
        if not dealer or not dealer["active"]:
            return _page("Consulta de inventario", form + "<p class='msg error' role='alert'>Concesionario inactivo</p>")
        found = rows(_AVAILABLE if available_only else _INVENTORY, {"dealer": dealer_id})
        if not found:
            return _page("Consulta de inventario", form + "<p class='msg error' role='alert'>Sin vehículos disponibles</p>")
        table = ("<table><thead><tr><th>Modelo</th><th>Año</th><th>Precio</th><th>Stock</th><th>Disponible</th></tr></thead><tbody>"
                 + "".join(f"<tr><td>{html.escape(r['model'])}</td><td>{r['year']}</td><td>{float(r['price']):,.0f}</td><td>{r['stock']}</td>"
                           f"<td>{'Sí' if r['available'] and r['stock'] > 0 else 'No'}</td></tr>" for r in found) + "</tbody></table>")
        return _page("Consulta de inventario", form + f"<p class='msg ok'>{len(found)} vehículo(s) en {html.escape(dealer['name'])}</p>" + table)

    def order_form(message: str = "", kind: str = "ok") -> HTMLResponse:
        dealers, vehicles, customers = rows("SELECT * FROM dealers"), rows("SELECT * FROM vehicles"), rows("SELECT * FROM customers")
        form = ("<form method='post' action='/ui/orders'>"
                f"<label for='o-dealer'>Concesionario</label><select id='o-dealer' name='dealer_id'>{_options(dealers, 'dealer_id', lambda d: d['name'])}</select>"
                f"<label for='o-vehicle'>Vehículo</label><select id='o-vehicle' name='vehicle_id'>{_options(vehicles, 'vehicle_id', _vehicle_label)}</select>"
                f"<label for='o-customer'>Cliente</label><select id='o-customer' name='customer_id'>{_options(customers, 'customer_id', lambda c: c['name'])}</select>"
                "<label for='o-total'>Total</label><input id='o-total' name='total' type='number' step='0.01' value='289900'>"
                "<button type='submit'>Registrar orden</button></form>")
        notice = f"<p class='msg {kind}' role='alert'>{html.escape(message)}</p>" if message else ""
        return _page("Registrar orden de venta", form + notice)

    @app.get("/ui/orders", response_class=HTMLResponse)
    async def orders_page():
        return order_form()

    @app.post("/ui/orders", response_class=HTMLResponse)
    async def create_order(dealer_id: int = Form(...), vehicle_id: int = Form(...), customer_id: int = Form(...), total: float = Form(...)):
        with engine.begin() as connection:
            vehicle = connection.execute(text("SELECT * FROM vehicles WHERE vehicle_id=:id"), {"id": vehicle_id}).mappings().first()
            dealer = connection.execute(text("SELECT * FROM dealers WHERE dealer_id=:id"), {"id": dealer_id}).mappings().first()
            if not dealer or not dealer["active"]:
                return order_form("Concesionario inactivo", "error")
            if not vehicle or vehicle["stock"] <= 0:
                return order_form("Vehículo sin stock", "error")
            if total <= 0:
                return order_form("El total debe ser mayor a cero", "error")
            order_id = connection.execute(text("SELECT COALESCE(MAX(order_id), 0) + 1 FROM sales_orders")).scalar_one()
            connection.execute(text("INSERT INTO sales_orders VALUES (:id,:dealer,:vehicle,:customer,:status,:total)"),
                               {"id": order_id, "dealer": dealer_id, "vehicle": vehicle_id, "customer": customer_id, "status": "approved", "total": total})
        return order_form(f"Orden aprobada #{order_id}")

    @app.get("/ui/appointments", response_class=HTMLResponse)
    async def appointments_page():
        found = rows("SELECT a.appointment_id, c.name AS customer, d.name AS dealer, a.status FROM service_appointments a "
                     "JOIN customers c ON c.customer_id = a.customer_id JOIN dealers d ON d.dealer_id = a.dealer_id")
        table = ("<table><thead><tr><th>Cita</th><th>Cliente</th><th>Concesionario</th><th>Estado</th></tr></thead><tbody>"
                 + "".join(f"<tr><td>{r['appointment_id']}</td><td>{html.escape(r['customer'])}</td><td>{html.escape(r['dealer'])}</td><td>{html.escape(r['status'])}</td></tr>"
                           for r in found) + "</tbody></table>")
        return _page("Citas de servicio", table if found else "<p class='msg'>Sin citas registradas</p>")
