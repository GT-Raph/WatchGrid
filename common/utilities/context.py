"""Context variables shared by middleware, logging and Celery tasks."""

from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("watchgrid_request_id", default=None)
