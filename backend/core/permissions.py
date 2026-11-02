"""Role-based access control and tenant activation for API views."""

from rest_framework.exceptions import NotFound, ParseError, PermissionDenied
from rest_framework.permissions import SAFE_METHODS, BasePermission

from . import tenancy
from .models import ROLE_RANK, Membership, Role


class HasTenantRole(BasePermission):
    """Compares the caller's role with the view's requirement.

    ``read_role`` applies to safe methods and ``write_role`` to the rest.
    ``action_roles`` overrides both for specific viewset actions.
    """

    def has_permission(self, request, view):
        membership = getattr(request, "membership", None)
        if membership is None:
            return False
        action = getattr(view, "action", None)
        roles = getattr(view, "action_roles", {})
        if action in roles:
            needed = roles[action]
        elif request.method in SAFE_METHODS:
            needed = getattr(view, "read_role", Role.VIEWER)
        else:
            needed = getattr(view, "write_role", Role.MEMBER)
        return ROLE_RANK[Role(membership.role)] >= ROLE_RANK[needed]


class TenantViewMixin:
    """Resolves X-Tenant-ID after authentication, verifies membership, activates the tenant."""

    permission_classes = [HasTenantRole]
    lookup_value_regex = r"\d+"

    def initial(self, request, *args, **kwargs):
        # Authenticate first, because the membership check needs the user.
        self.perform_authentication(request)
        request.membership = None
        if request.user and request.user.is_authenticated:
            raw = request.headers.get("X-Tenant-ID")
            if not raw:
                raise ParseError("X-Tenant-ID header is required")
            try:
                tenant_id = int(raw)
            except ValueError as e:
                raise ParseError("X-Tenant-ID must be an integer") from e
            membership = Membership.objects.filter(user=request.user, tenant_id=tenant_id).first()
            if membership is None:
                # 404, not 403: don't confirm that another organisation exists.
                raise NotFound("organisation not found")
            request.membership = membership
            tenancy.activate(tenant_id)
        super().initial(request, *args, **kwargs)

    def require_role(self, role):
        if ROLE_RANK[Role(self.request.membership.role)] < ROLE_RANK[role]:
            raise PermissionDenied()
