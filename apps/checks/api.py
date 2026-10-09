from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from . import health


class PlatformStatusView(APIView):
    """Operator view of scheduler, queue and worker health (platform administrators only)."""

    permission_classes = [IsAdminUser]

    @extend_schema(responses={200: dict})
    def get(self, request):
        return Response(health.platform_status())
