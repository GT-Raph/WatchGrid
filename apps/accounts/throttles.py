from rest_framework.throttling import SimpleRateThrottle


class LoginEmailThrottle(SimpleRateThrottle):
    """Limit attempts per target account, complementing the per-IP limit."""

    scope = "login"

    def get_cache_key(self, request, view):
        email = str(request.data.get("email", "") if hasattr(request, "data") else "").strip().lower()
        if not email:
            return None
        return self.cache_format % {"scope": f"{self.scope}-email", "ident": email}
