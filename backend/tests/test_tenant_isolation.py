import pytest
from django.db import ProgrammingError, connection, transaction

from core import tenancy
from core.models import AuditLog, Issue, Project, Role

pytestmark = pytest.mark.django_db


def test_cannot_enter_a_tenant_without_membership(acme, globex):
    c = acme.client()
    c.credentials(HTTP_AUTHORIZATION=c._credentials["HTTP_AUTHORIZATION"], HTTP_X_TENANT_ID=str(globex.tenant.id))
    assert c.get("/api/projects/").status_code == 404


def test_tenant_header_is_required(acme):
    c = acme.client()
    c.credentials(HTTP_AUTHORIZATION=c._credentials["HTTP_AUTHORIZATION"])
    assert c.get("/api/projects/").status_code == 400


def test_list_only_shows_own_tenant(acme, globex):
    acme.issue("acme secret")
    globex.issue("globex secret")
    titles = [i["title"] for i in acme.client().get("/api/issues/").json()["results"]]
    assert titles == ["acme secret"]


@pytest.mark.parametrize(
    "method,url",
    [
        ("get", "/api/issues/{issue}/"),
        ("patch", "/api/issues/{issue}/"),
        ("delete", "/api/issues/{issue}/"),
        ("post", "/api/issues/{issue}/move/"),
        ("get", "/api/issues/{issue}/comments/"),
        ("get", "/api/projects/{project}/board/"),
        ("delete", "/api/projects/{project}/"),
        ("post", "/api/projects/{project}/export/"),
    ],
)
def test_other_tenants_objects_look_like_they_dont_exist(acme, globex, method, url):
    issue = globex.issue()
    target = url.format(issue=issue.id, project=globex.project.id)
    resp = getattr(acme.client(), method)(target, {"title": "hacked", "column": acme.columns[0].id}, format="json")
    assert resp.status_code == 404
    with tenancy.tenant_context(globex.tenant.id):
        assert Issue.objects.get(pk=issue.id).title == "An issue"


def test_cannot_reference_another_tenants_column_when_creating(acme, globex):
    resp = acme.client().post("/api/issues/", {"project": acme.project.id, "column": globex.columns[0].id, "title": "x"}, format="json")
    assert resp.status_code == 400


def test_cannot_move_into_another_tenants_column(acme, globex):
    issue = acme.issue()
    resp = acme.client().post(f"/api/issues/{issue.id}/move/", {"column": globex.columns[0].id}, format="json")
    assert resp.status_code == 400


def test_cannot_assign_someone_outside_the_tenant(acme, globex):
    resp = acme.client().post(
        "/api/issues/",
        {"project": acme.project.id, "column": acme.columns[0].id, "title": "x", "assignee": globex.users[Role.MEMBER].id},
        format="json",
    )
    assert resp.status_code == 400


def _raw_titles():
    with connection.cursor() as c:
        c.execute("SELECT title FROM core_issue ORDER BY title")
        return [r[0] for r in c.fetchall()]


def test_rls_filters_raw_sql_that_forgets_the_tenant(acme, globex):
    """The database itself refuses, even with no ORM filter involved."""
    acme.issue("a")
    globex.issue("g")
    with tenancy.tenant_context(acme.tenant.id):
        assert _raw_titles() == ["a"]
    with tenancy.tenant_context(globex.tenant.id):
        assert _raw_titles() == ["g"]


def test_rls_fails_closed_without_a_tenant(acme):
    acme.issue("a")
    tenancy.deactivate()
    assert _raw_titles() == []


def test_rls_rejects_writing_a_row_for_another_tenant(acme, globex):
    with tenancy.tenant_context(acme.tenant.id), pytest.raises(ProgrammingError, match="row-level security"), transaction.atomic():
        Project.objects.create(tenant_id=globex.tenant.id, key="EVIL", name="planted")


def test_rls_blocks_raw_update_of_another_tenant(acme, globex):
    issue = globex.issue("g")
    with tenancy.tenant_context(acme.tenant.id), connection.cursor() as c:
        c.execute("UPDATE core_issue SET title = 'hacked' WHERE id = %s", [issue.id])
        assert c.rowcount == 0


def test_orm_returns_nothing_without_a_tenant(acme):
    acme.issue()
    tenancy.deactivate()
    assert not Issue.objects.exists()


def test_saving_without_a_tenant_is_refused(db):
    with pytest.raises(RuntimeError, match="active tenant"):
        Project(key="X", name="x").save()


def test_audit_log_is_append_only_for_the_app(acme):
    acme.issue()
    with tenancy.tenant_context(acme.tenant.id):
        entry = AuditLog.objects.first()
        for statement in ("UPDATE core_auditlog SET action = 'forged' WHERE id = %s", "DELETE FROM core_auditlog WHERE id = %s"):
            with pytest.raises(ProgrammingError, match="permission denied"), transaction.atomic(), connection.cursor() as c:
                c.execute(statement, [entry.id])
