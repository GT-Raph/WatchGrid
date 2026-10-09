"""Overview dashboard and uptime report pages."""

import csv

from django.http import HttpResponse

from common.utilities.time import parse_time_range
from common.utilities.web import page, render_page

from . import services

CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _scope_args(request) -> dict:
    return {
        "user": request.user,
        "tr": parse_time_range(request.GET),
        "organization_id": request.web_scope["organization"].pk,
        "project_id": getattr(request.web_scope["project"], "pk", None),
    }


def _csv_safe(value):
    """Neutralise spreadsheet formula injection in user-controlled names."""
    return "'" + value if str(value).startswith(CSV_FORMULA_PREFIXES) else value


@page(methods=("GET",))
def overview(request):
    args = _scope_args(request)
    return render_page(
        request,
        "analytics/overview.html",
        "Overview",
        summary=services.dashboard_summary(**args),
        grid=services.status_grid(**args, buckets=48),
        show_scope=True,
        show_range=True,
    )


@page(methods=("GET",))
def reports(request):
    args = _scope_args(request)
    grid = services.status_grid(**args, buckets=24)
    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="watchgrid-uptime.csv"'
        writer = csv.writer(response)
        writer.writerow(["Monitor", "Project", "Status", "Uptime %", "Average latency (ms)", "Start", "End"])
        for row in grid["monitors"]:
            writer.writerow(
                [
                    _csv_safe(row["name"]),
                    _csv_safe(row["project"]["name"]),
                    row["status"],
                    row["uptime_percent"],
                    row["avg_response_time_ms"],
                    args["tr"].start,
                    args["tr"].end,
                ]
            )
        return response
    return render_page(
        request,
        "analytics/reports.html",
        "Reports",
        grid=grid,
        summary=services.dashboard_summary(**args),
        show_scope=True,
        show_range=True,
    )
