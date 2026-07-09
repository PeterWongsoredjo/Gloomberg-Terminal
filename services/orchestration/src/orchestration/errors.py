from __future__ import annotations


class DbtTransientError(RuntimeError):
    """A dbt phase flap worth one retry: a transient lock or MinIO briefly unreachable."""


class DbtTestFailure(RuntimeError):
    """An error-severity dbt test failed; never retried into a pass, blocks promotion."""


class DbtCompilationError(RuntimeError):
    """A dbt model failed to compile; deterministic, never retried."""


class TriggerTransientError(RuntimeError):
    """The agentic trigger hit a connect timeout or 503; worth a bounded retry."""


class TriggerPermanentError(RuntimeError):
    """The agentic trigger failed unrecoverably; the flow degrades the step, keeps Gold."""
