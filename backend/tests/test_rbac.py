import pytest

from core import tenancy
from core.models import Membership, Role, User

pytestmark = pytest.mark.django_db

ALL = [Role.OWNER, Role.ADMIN, Role.MEMBER, Role.VIEWER]


def allowed(role, minimum):
    order = [Role.VIEWER, Role.MEMBER, Role.ADMIN, Role.OWNER]
    return order.index(role) >= order.index(minimum)


@pytest.mark.parametrize("role", ALL)
def test_everyone_can_read_the_board(acme, role):
    assert acme.client(role).get(f"/api/projects/{acme.project.id}/board/").status_code == 200


@pytest.mark.parametrize("role", ALL)
def test_creating_issues_needs_member(acme, role):
    resp = acme.client(role).post("/api/issues/", {"project": acme.project.id, "column": acme.columns[0].id, "title": "x"}, format="json")
    assert resp.status_code == (201 if allowed(role, Role.MEMBER) else 403)


@pytest.mark.parametrize("role", ALL)
def test_moving_issues_needs_member(acme, role):
    issue = acme.issue()
    resp = acme.client(role).post(f"/api/issues/{issue.id}/move/", {"column": acme.columns[1].id}, format="json")
    assert resp.status_code == (200 if allowed(role, Role.MEMBER) else 403)


@pytest.mark.parametrize("role", ALL)
def test_commenting_needs_member(acme, role):
    issue = acme.issue()
    resp = acme.client(role).post(f"/api/issues/{issue.id}/comments/", {"body": "hi"}, format="json")
    assert resp.status_code == (201 if allowed(role, Role.MEMBER) else 403)


@pytest.mark.parametrize("role", ALL)
def test_creating_projects_needs_admin(acme, role):
    resp = acme.client(role).post("/api/projects/", {"key": "NEW", "name": "New"}, format="json")
    assert resp.status_code == (201 if allowed(role, Role.ADMIN) else 403)


@pytest.mark.parametrize("role", ALL)
def test_audit_log_needs_admin(acme, role):
    assert acme.client(role).get("/api/audit-log/").status_code == (200 if allowed(role, Role.ADMIN) else 403)


@pytest.mark.parametrize("role", ALL)
def test_managing_members_needs_admin(acme, role):
    User.objects.create_user("new@acme.test", "pw-for-tests-1", "New")
    resp = acme.client(role).post("/api/members/", {"email": "new@acme.test", "role": "MEMBER"}, format="json")
    assert resp.status_code == (201 if allowed(role, Role.ADMIN) else 403)


def _membership(org, role):
    return Membership.objects.get(tenant=org.tenant, user=org.users[role])


def test_admin_cannot_grant_ownership(acme):
    m = _membership(acme, Role.MEMBER)
    assert acme.client(Role.ADMIN).patch(f"/api/members/{m.id}/", {"role": "OWNER"}, format="json").status_code == 403


def test_admin_cannot_remove_or_demote_an_owner(acme):
    m = _membership(acme, Role.OWNER)
    admin = acme.client(Role.ADMIN)
    assert admin.delete(f"/api/members/{m.id}/").status_code == 403
    assert admin.patch(f"/api/members/{m.id}/", {"role": "VIEWER"}, format="json").status_code == 403


def test_the_last_owner_cannot_leave(acme):
    m = _membership(acme, Role.OWNER)
    owner = acme.client(Role.OWNER)
    assert owner.delete(f"/api/members/{m.id}/").status_code == 400
    assert owner.patch(f"/api/members/{m.id}/", {"role": "ADMIN"}, format="json").status_code == 400


def test_role_change_is_audited(acme):
    m = _membership(acme, Role.VIEWER)
    assert acme.client(Role.ADMIN).patch(f"/api/members/{m.id}/", {"role": "MEMBER"}, format="json").status_code == 200
    entry = acme.client(Role.ADMIN).get("/api/audit-log/").json()["results"][0]
    assert entry["action"] == "member.role_changed"
    assert entry["before"] == {"role": "VIEWER"} and entry["after"] == {"role": "MEMBER"}


def test_unauthenticated_requests_get_401(client, acme):
    assert client.get("/api/projects/").status_code == 401


def test_register_creates_an_owner_of_a_new_organisation(client):
    resp = client.post(
        "/api/auth/register/",
        {"email": "New@Corp.test", "password": "long-enough-pw", "name": "N", "organisation": "New Corp"},
        content_type="application/json",
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["tenant"]["slug"] == "new-corp"
    me = client.get("/api/me/", HTTP_AUTHORIZATION=f"Bearer {body['access']}").json()
    assert me["organisations"][0]["role"] == "OWNER" and me["user"]["email"] == "new@corp.test"
    assert tenancy.current_tenant_id() is None
