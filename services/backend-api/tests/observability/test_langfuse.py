"""OB-03 degradation: with no Langfuse keys, tracing is a silent no-op that blocks nothing."""

from __future__ import annotations

from app.observability.langfuse_tracer import LangfuseTracer, build_langfuse_client
from app.observability.config import ObservabilitySettings


def test_absent_keys_yield_a_disabled_tracer() -> None:
    settings = ObservabilitySettings(_env_file=None)  # type: ignore[call-arg]
    settings.langfuse_public_key = ""
    settings.langfuse_secret_key = ""
    tracer = LangfuseTracer(build_langfuse_client(settings))
    assert tracer.enabled is False


def test_disabled_tracer_is_inert() -> None:
    tracer = LangfuseTracer(None)
    assert tracer.new_trace_id() is None
    assert tracer.callbacks_for(None) == []
    tracer.flush()  # no raise
    tracer.shutdown()  # no raise


def test_disabled_tracer_reconcile_is_a_noop() -> None:
    from app.agentic.prompts.registry import get_prompt

    tracer = LangfuseTracer(None)
    assert tracer.reconcile_prompt(get_prompt("daily_sentiment")) is True
