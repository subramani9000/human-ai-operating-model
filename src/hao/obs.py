"""Observability: one JSON log line per meaningful step, tied together by the ticket and delivery id.

Logs never contain ticket text or personal data, only ids, categories, levels, timings and outcomes,
so they can go to Kaveri's log platform without a second review.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys

from hao import clock

_ctx: contextvars.ContextVar[dict | None] = contextvars.ContextVar("hao_log_ctx", default=None)
logger = logging.getLogger("hao")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = {"ts": clock.now().isoformat(), "level": record.levelname.lower(), "event": record.getMessage()}
        base.update(_ctx.get() or {})
        base.update(getattr(record, "fields", {}))
        return json.dumps(base, default=str, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    if any(isinstance(h.formatter, JsonFormatter) for h in logger.handlers):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False


def bind(**fields) -> contextvars.Token:
    """Attach correlation fields (sys_id, delivery_id, ...) to every log line in this context."""
    return _ctx.set({**(_ctx.get() or {}), **{k: v for k, v in fields.items() if v is not None}})


def unbind(token: contextvars.Token) -> None:
    _ctx.reset(token)


def log(event: str, **fields) -> None:
    logger.info(event, extra={"fields": fields})
