"""Load test (Section 27.3): dashboard readers plus applications reporting errors.

Not collected by pytest. Run against a staging deployment:

    pip install locust
    WATCHGRID_EMAIL=... WATCHGRID_PASSWORD=... WATCHGRID_PROJECT_KEY=wgk_... \\
        locust -f tests/performance/locustfile.py --host https://staging.watchgrid.example.com

Targets (NFR-PERF-001): typical authenticated reads under 500 ms at normal load.
"""

import os
import random
import uuid

from locust import HttpUser, between, task


class DashboardReader(HttpUser):
    """An operator with the dashboard open: overview, monitor list, incidents, API reads."""

    wait_time = between(2, 8)

    def on_start(self):
        self.client.get("/login/")
        self.client.post(
            "/login/",
            {
                "email": os.environ["WATCHGRID_EMAIL"],
                "password": os.environ["WATCHGRID_PASSWORD"],
                "csrfmiddlewaretoken": self.client.cookies.get("watchgrid_csrftoken", ""),
            },
            headers={"Referer": f"{self.host}/login/"},
        )

    @task(4)
    def overview(self):
        self.client.get("/", name="/ (overview)")

    @task(2)
    def monitors(self):
        self.client.get("/monitors/")

    @task(1)
    def incidents(self):
        self.client.get("/incidents/")

    @task(2)
    def api_grid(self):
        self.client.get("/api/v1/dashboard/grid/?range=24h", name="/api/v1/dashboard/grid/")


class ErrorReporter(HttpUser):
    """An instrumented application sending exceptions with a project key."""

    wait_time = between(0.05, 0.5)

    @task
    def report_error(self):
        self.client.post(
            "/api/v1/events/errors/",
            json={
                "event_id": str(uuid.uuid4()),
                "environment": "production",
                "error_type": random.choice(["TimeoutError", "DatabaseError", "KeyError"]),
                "message": f"Order {random.randint(1, 10_000)} failed",
                "endpoint": "/api/checkout/",
                "user_reference": f"user-{random.randint(1, 500)}",
            },
            headers={"X-Project-Key": os.environ["WATCHGRID_PROJECT_KEY"]},
            name="/api/v1/events/errors/",
        )
