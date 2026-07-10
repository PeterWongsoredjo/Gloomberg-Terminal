"""
runtime tracing integrations with langfuse callback handlers
mints trace IDs, manages span submissions, and reconciles prompt templates
"""

from __future__ import annotations

import logging
from typing import Any

from app.agentic.prompts.registry import PromptTemplate
from app.observability.config import ObservabilitySettings

logger = logging.getLogger("gloomberg.observability.langfuse")


def build_langfuse_client(settings: ObservabilitySettings) -> Any | None:
    if not settings.has_langfuse():
        logger.info("langfuse keys absent, tracing runs as a no-op")
        return None
    try:
        from langfuse import Langfuse

        return Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
    except Exception as exc:  # a tracing backend must never block boot (§1.3)
        logger.warning("langfuse client unavailable, tracing disabled: %s", exc)
        return None


class LangfuseTracer:
    """wrapper over the langfuse client to manage callback handlers and prompt reconciliation"""

    def __init__(self, client: Any | None) -> None:
        self._client = client

    @property
    def enabled(self) -> bool:
        """returns true if the client is configured and active"""
        return self._client is not None

    def new_trace_id(self) -> str | None:
        """returns a new trace id or none if disabled"""
        if self._client is None:
            return None
        try:
            return str(self._client.create_trace_id())
        except Exception as exc:
            logger.warning("could not mint langfuse trace id: %s", exc)
            return None

    def callbacks_for(self, trace_id: str | None) -> list[Any]:
        """returns callback handler list bound to the provided trace id"""
        if self._client is None:
            return []
        try:
            from langfuse.langchain import CallbackHandler

            context: Any = {"trace_id": trace_id} if trace_id else None
            return [CallbackHandler(trace_context=context)]
        except Exception as exc:
            logger.warning("could not build langfuse handler: %s", exc)
            return []

    def flush(self) -> None:
        """submits any cached trace events to the cloud"""
        if self._client is None:
            return
        try:
            self._client.flush()
        except Exception as exc:
            logger.warning("langfuse flush failed, spans buffered locally: %s", exc)

    def shutdown(self) -> None:
        """drains queues and shutdowns the client connection"""
        if self._client is None:
            return
        try:
            self._client.shutdown()
        except Exception as exc:
            logger.warning("langfuse shutdown failed: %s", exc)

    def reconcile_prompt(self, template: PromptTemplate) -> bool:
        """checks if the local prompt version match the one stored on the langfuse dashboard"""
        if self._client is None:
            return True
        name = f"{template.objective}:{template.version}"
        try:
            existing = self._client.get_prompt(name, version=None, cache_ttl_seconds=0)
        except Exception:
            existing = None
        if existing is None:
            return self._register_prompt(name, template)
        registered_hash = (getattr(existing, "config", {}) or {}).get("content_sha256")
        if registered_hash and registered_hash != template.content_sha256:
            logger.warning("prompt %s drifted: local %s != langfuse %s", name, template.content_sha256, registered_hash)
            return False
        return True

    def _register_prompt(self, name: str, template: PromptTemplate) -> bool:
        """registers a new prompt version configuration in langfuse"""
        assert self._client is not None  # only reached from reconcile_prompt with a live client
        try:
            self._client.create_prompt(
                name=name,
                prompt=template.system_contract,
                labels=["production"],
                config={"content_sha256": template.content_sha256, "version": template.version},
            )
            return True
        except Exception as exc:
            logger.warning("could not register prompt %s in langfuse: %s", name, exc)
            return True  # registration is best-effort; do not fail the run over it

