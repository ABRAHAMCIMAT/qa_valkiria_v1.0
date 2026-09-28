import asyncio
import logging

from valkiria.infrastructure.logging import JsonFormatter, _safe
from valkiria.infrastructure.memory import InMemoryAudit, InMemoryMetrics
from valkiria.llmops.lifecycle import Gate, LLMOpsLifecycle, Phase


def test_redacts_sensitive_context():
    result = _safe({"api_key": "secret", "nested": {"password": "hidden"}, "safe": "ok"})
    assert result == {"api_key": "[REDACTADO]", "nested": {"password": "[REDACTADO]"}, "safe": "ok"}


def test_json_formatter_is_structured():
    record = logging.LogRecord("test", logging.INFO, "", 0, "mensaje", (), None)
    record.context = {"trace_id": "abc"}
    formatted = JsonFormatter().format(record)
    assert '"mensaje": "mensaje"' in formatted
    assert '"trace_id": "abc"' in formatted


def test_all_llmops_gates_are_recorded():
    async def run():
        audit, metrics = InMemoryAudit(), InMemoryMetrics()
        lifecycle = LLMOpsLifecycle(audit, metrics)
        ctx = lifecycle.new_context("qa", "modelo-test")
        await lifecycle.start(ctx, "test")
        for phase in Phase:
            await lifecycle.record(ctx, phase, "step", "completed")
        await lifecycle.finish(ctx)
        return ctx, audit, metrics

    ctx, audit, metrics = asyncio.run(run())
    assert all(gate.value in ctx.gates for gate in Gate)
    assert len(audit.events) >= 8
    assert any(item.name == "run.duration_ms" for item in metrics.items)
