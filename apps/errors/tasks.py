from celery import shared_task

from . import services


@shared_task(
    bind=True,
    name="apps.errors.tasks.process_error_event",
    ignore_result=True,
    acks_late=True,
    max_retries=5,
)
def process_error_event(self, payload: dict) -> str:
    try:
        return services.process_event(payload)
    except Exception as exc:
        if self.request.is_eager:
            raise
        raise self.retry(exc=exc, countdown=min(10 * (2**self.request.retries), 300)) from exc
