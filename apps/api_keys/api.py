from drf_spectacular.utils import extend_schema
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from common.permissions.views import TenantScopedMixin

from . import services
from .models import APIKey
from .serializers import APIKeySerializer


class APIKeyViewSet(TenantScopedMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = APIKeySerializer
    queryset = APIKey.objects.none()
    capabilities = {"retrieve": "apikey.view", "revoke": "apikey.manage"}

    def get_queryset(self):
        return APIKey.objects.for_user(self.request.user).select_related("project", "created_by")

    @extend_schema(request=None, responses={200: APIKeySerializer})
    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        api_key = services.revoke_api_key(actor=request.user, api_key=self.get_object(), request=request)
        return Response(APIKeySerializer(api_key).data)
