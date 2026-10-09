from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from common.permissions.views import parse_uuid
from common.utilities.time import parse_time_range

from . import services


class DashboardView(APIView):
    """Organization/project summary for the overview screen (FR-ANA-001, Section 21.2)."""

    @extend_schema(
        parameters=[
            OpenApiParameter("organization", str),
            OpenApiParameter("project", str),
            OpenApiParameter("range", str, description="1h, 24h, 7d, 30d, 90d or custom"),
        ],
        responses={200: dict},
    )
    def get(self, request):
        params = request.query_params
        organization = parse_uuid(params["organization"], "organization") if params.get("organization") else None
        project = parse_uuid(params["project"], "project") if params.get("project") else None
        tr = parse_time_range(params, default="24h")
        return Response(
            services.dashboard_summary(user=request.user, tr=tr, organization_id=organization, project_id=project)
        )


class StatusGridView(APIView):
    """Time-aligned monitor x time outcome grid for the overview screen."""

    @extend_schema(
        parameters=[
            OpenApiParameter("organization", str),
            OpenApiParameter("project", str),
            OpenApiParameter("range", str),
            OpenApiParameter("buckets", int, description="12-120, default 60"),
        ],
        responses={200: dict},
    )
    def get(self, request):
        params = request.query_params
        organization = parse_uuid(params["organization"], "organization") if params.get("organization") else None
        project = parse_uuid(params["project"], "project") if params.get("project") else None
        try:
            buckets = int(params.get("buckets", 60))
        except ValueError:
            buckets = 60
        tr = parse_time_range(params, default="24h")
        return Response(
            services.status_grid(
                user=request.user, tr=tr, organization_id=organization, project_id=project, buckets=buckets
            )
        )
