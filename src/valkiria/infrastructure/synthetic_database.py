from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
from typing import Any

from valkiria.application.automation_execution import build_evidence_report, static_analyse_database_script

NISSAN_SCHEMA = """
CREATE TABLE IF NOT EXISTS vehicles (vehicle_id INTEGER PRIMARY KEY, model TEXT NOT NULL, year INTEGER NOT NULL, price REAL NOT NULL, stock INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS dealers (dealer_id INTEGER PRIMARY KEY, name TEXT NOT NULL, region TEXT NOT NULL, active INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS customers (customer_id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS inventory (inventory_id INTEGER PRIMARY KEY, vehicle_id INTEGER NOT NULL, dealer_id INTEGER NOT NULL, available INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sales_orders (order_id INTEGER PRIMARY KEY, dealer_id INTEGER NOT NULL, vehicle_id INTEGER NOT NULL, customer_id INTEGER NOT NULL, status TEXT NOT NULL, total REAL NOT NULL);
CREATE TABLE IF NOT EXISTS service_appointments (appointment_id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL, dealer_id INTEGER NOT NULL, status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS parts (part_id INTEGER PRIMARY KEY, name TEXT NOT NULL, stock INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS test_cases (case_id TEXT PRIMARY KEY, scenario TEXT NOT NULL, expected_result TEXT NOT NULL);
"""


def seed_synthetic_nissan(connection: sqlite3.Connection) -> None:
    connection.executescript(NISSAN_SCHEMA)
    connection.executemany("INSERT OR IGNORE INTO vehicles VALUES (?, ?, ?, ?, ?)", [(1, "Sentra", 2025, 289900.0, 10), (2, "Kicks", 2025, 355000.0, 0), (3, "Versa", 2024, 245000.0, 3)])
    connection.executemany("INSERT OR IGNORE INTO dealers VALUES (?, ?, ?, ?)", [(1, "Nissan Apodaca Sintético", "Nuevo León", 1), (2, "Nissan Centro Sintético", "Nuevo León", 0)])
    connection.executemany("INSERT OR IGNORE INTO customers VALUES (?, ?, ?)", [(1, "Cliente Sintético 001", "cliente001@example.test"), (2, "Cliente Sintético 002", "cliente002@example.test")])
    connection.executemany("INSERT OR IGNORE INTO inventory VALUES (?, ?, ?, ?)", [(1, 1, 1, 1), (2, 2, 1, 0), (3, 3, 2, 1)])
    connection.executemany("INSERT OR IGNORE INTO sales_orders VALUES (?, ?, ?, ?, ?, ?)", [(1, 1, 1, 1, "approved", 289900.0), (2, 1, 2, 2, "cancelled", 355000.0)])
    connection.executemany("INSERT OR IGNORE INTO service_appointments VALUES (?, ?, ?, ?)", [(1, 1, 1, "scheduled")])
    connection.executemany("INSERT OR IGNORE INTO parts VALUES (?, ?, ?)", [(1, "Filtro sintético", 25), (2, "Balata sintética", 0)])
    connection.executemany("INSERT OR IGNORE INTO test_cases VALUES (?, ?, ?)", [("HU011-TC-001", "Consultar vehículos disponibles", "Devuelve Sentra y Versa")])
    connection.commit()


def _statements(script: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    for line in script.splitlines():
        current.append(line)
        if sqlite3.complete_statement("\n".join(current)):
            statement = "\n".join(current).strip().rstrip(";").strip()
            if statement:
                statements.append(statement)
            current = []
    remainder = "\n".join(current).strip().rstrip(";").strip()
    if remainder:
        statements.append(remainder)
    return statements


class SyntheticSQLiteExecutor:
    engine = "sqlite"

    def __init__(self, database: str = ":memory:"):
        self.connection = sqlite3.connect(database, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.cache: dict[str, tuple[dict[str, Any], dict[str, Any] | None]] = {}
        seed_synthetic_nissan(self.connection)

    def execute(self, *, script: str, case_id: str, trace_id: str, output_format: str = "pdf") -> tuple[dict[str, Any], dict[str, Any] | None]:
        analysis = static_analyse_database_script(script)
        execution_key = hashlib.sha256(f"{case_id}:{script}".encode()).hexdigest()
        with self.lock:
            if execution_key in self.cache:
                cached, report = self.cache[execution_key]
                return {**cached, "idempotent_replay": True, "trace_id": trace_id}, report
            execution_id = hashlib.sha256(f"{execution_key}:{trace_id}".encode()).hexdigest()[:24]
            if not analysis["passed"]:
                result = {"id": execution_id, "status": "blocked", "blocked": True, "engine": self.engine, "case_id": case_id, "trace_id": trace_id, "static_analysis": analysis, "report_generated": False, "execution_mode": "synthetic_sqlite"}
                self.cache[execution_key] = (result, None)
                return result, None
            started = time.perf_counter()
            rows: list[dict[str, Any]] = []
            affected_rows = 0
            try:
                statements = _statements(script)
                is_query = len(statements) == 1 and statements[0].lower().startswith(("select", "with", "pragma"))
                if is_query:
                    cursor = self.connection.execute(statements[0])
                    rows = [dict(row) for row in cursor.fetchall()]
                    self.connection.rollback()
                else:
                    has_explicit_transaction = any(statement.lower().split(maxsplit=1)[0] in {"begin", "start"} for statement in statements)
                    if not has_explicit_transaction:
                        self.connection.execute("BEGIN")
                    for statement in statements:
                        keyword = statement.lower().split(maxsplit=1)[0]
                        if keyword in {"begin", "start", "rollback", "commit"}:
                            self.connection.execute(statement)
                            continue
                        cursor = self.connection.execute(statement)
                        if cursor.rowcount >= 0:
                            affected_rows += cursor.rowcount
                    self.connection.rollback()
                duration_ms = round((time.perf_counter() - started) * 1000, 2)
                logs = ["static_analysis=passed", "execution_mode=synthetic_sqlite", "transaction_policy=rollback_after_test", "result=pass"]
                result = {"id": execution_id, "status": "completed", "blocked": False, "engine": self.engine, "case_id": case_id, "trace_id": trace_id, "static_analysis": analysis, "pass": True, "rows": rows, "affected_rows": affected_rows, "duration_ms": duration_ms, "logs": logs, "execution_mode": "synthetic_sqlite", "report_generated": True, "idempotency_key": execution_key}
                report = build_evidence_report(execution_id=execution_id, title="Valkiria · Evidencia Nissan sintética", output_format=output_format, fields={"execution_id": execution_id, "case_id": case_id, "engine": self.engine, "status": "pass", "affected_rows": affected_rows, "trace_id": trace_id}, logs=logs)
                result["report_id"] = report["id"]
            except sqlite3.Error as exc:
                self.connection.rollback()
                result = {"id": execution_id, "status": "failed", "blocked": False, "engine": self.engine, "case_id": case_id, "trace_id": trace_id, "error": "synthetic_database_error", "error_type": type(exc).__name__, "report_generated": False}
                report = None
            self.cache[execution_key] = (result, report)
            return result, report


class SyntheticPostgresExecutor:
    engine = "postgresql"

    def __init__(self, url: str | None = None):
        self.url = url or os.getenv("VALKIRIA_SYNTHETIC_DATABASE_URL")
        self._engine = None

    def _get_engine(self):
        if not self.url:
            raise RuntimeError("synthetic_postgresql_url_not_configured")
        if self._engine is None:
            try:
                from sqlalchemy import create_engine
            except ImportError as exc:
                raise RuntimeError("sqlalchemy_and_psycopg_required_for_synthetic_postgresql") from exc
            self._engine = create_engine(self.url, pool_pre_ping=True, future=True)
        return self._engine

    def execute(self, *, script: str, case_id: str, trace_id: str, output_format: str = "pdf") -> tuple[dict[str, Any], dict[str, Any] | None]:
        analysis = static_analyse_database_script(script)
        execution_id = hashlib.sha256(f"{case_id}:{script}:{trace_id}".encode()).hexdigest()[:24]
        if not analysis["passed"]:
            return {"id": execution_id, "status": "blocked", "blocked": True, "engine": self.engine, "case_id": case_id, "trace_id": trace_id, "static_analysis": analysis, "report_generated": False}, None
        try:
            from sqlalchemy import text
            started = time.perf_counter()
            with self._get_engine().begin() as connection:
                result = connection.execute(text(script))
                rows = [dict(row._mapping) for row in result.fetchall()] if result.returns_rows else []
                affected_rows = result.rowcount if result.rowcount != -1 else 0
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logs = ["static_analysis=passed", "execution_mode=synthetic_postgresql", "transaction_policy=controlled_transaction", "result=pass"]
            report = build_evidence_report(execution_id=execution_id, title="Valkiria · Evidencia PostgreSQL sintética", output_format=output_format, fields={"execution_id": execution_id, "case_id": case_id, "engine": self.engine, "status": "pass", "affected_rows": affected_rows, "trace_id": trace_id}, logs=logs)
            return {"id": execution_id, "status": "completed", "blocked": False, "engine": self.engine, "case_id": case_id, "trace_id": trace_id, "rows": rows, "affected_rows": affected_rows, "duration_ms": duration_ms, "logs": logs, "static_analysis": analysis, "report_generated": True, "report_id": report["id"]}, report
        except Exception as exc:
            return {"id": execution_id, "status": "failed", "blocked": False, "engine": self.engine, "case_id": case_id, "trace_id": trace_id, "error": "synthetic_database_error", "error_type": type(exc).__name__, "report_generated": False}, None


def build_synthetic_executor(profile: str, database_url: str | None = None):
    if profile == "synthetic_postgresql" and database_url:
        return SyntheticPostgresExecutor(database_url)
    return SyntheticSQLiteExecutor()
