import pytest
from django.db import connection
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from core import services, tenancy
from core.models import Column, Membership, Role, Tenant, User


@pytest.fixture(autouse=True)
def app_role(request):
    """Run as the app's role, not the superuser, so RLS is really enforced during tests."""
    if not request.node.get_closest_marker("django_db"):
        yield
        return
    request.getfixturevalue("db")
    with connection.cursor() as c:
        c.execute("SET ROLE pm_app")
    yield
    tenancy.deactivate()
    with connection.cursor() as c:
        c.execute("RESET ROLE")


class Org:
    """A tenant with one user per role and a project."""

    def __init__(self, slug: str):
        self.tenant = Tenant.objects.create(name=slug.title(), slug=slug)
        self.users = {}
        for role in Role:
            # No password: these users authenticate with tokens, and hashing is deliberately slow.
            user = User.objects.create_user(f"{role.value.lower()}@{slug}.test", None, role.value.title())
            Membership.objects.create(user=user, tenant=self.tenant, role=role)
            self.users[role] = user
        with tenancy.tenant_context(self.tenant.id):
            self.project = services.create_project(self.users[Role.OWNER], "APP", f"{slug} app")
            self.columns = list(Column.objects.filter(project=self.project))

    def client(self, role=Role.OWNER) -> APIClient:
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(self.users[role])}", HTTP_X_TENANT_ID=str(self.tenant.id))
        return c

    def issue(self, title="An issue", column=None, **fields):
        with tenancy.tenant_context(self.tenant.id):
            return services.create_issue(self.users[Role.OWNER], self.project, column or self.columns[0], title=title, **fields)


@pytest.fixture
def acme(db):
    return Org("acme")


@pytest.fixture
def globex(db):
    return Org("globex")
