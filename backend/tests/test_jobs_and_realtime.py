import csv
import io
from datetime import timedelta

import pytest
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.core import mail
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from config.asgi import application
from core import services, tasks, tenancy
from core.consumers import CLOSE_FORBIDDEN, CLOSE_UNAUTHORIZED
from core.models import ExportJob, Issue, Role

pytestmark = pytest.mark.django_db


def test_export_runs_after_commit_and_downloads_csv(acme, django_capture_on_commit_callbacks):
    acme.issue("=HYPERLINK(evil)")
    api = acme.client(Role.MEMBER)
    with django_capture_on_commit_callbacks(execute=True):
        resp = api.post(f"/api/projects/{acme.project.id}/export/")
    assert resp.status_code == 202
    job_id = resp.json()["id"]
    assert api.get(f"/api/exports/{job_id}/").json()["status"] == "DONE"
    rows = list(csv.reader(io.StringIO(api.get(f"/api/exports/{job_id}/download/").content.decode())))
    assert rows[1][0] == "APP-1"
    assert rows[1][1] == "'=HYPERLINK(evil)", "formula cells must be neutralised"


def test_export_is_idempotent_and_tenant_bound(acme, globex):
    acme.issue("mine")
    globex.issue("theirs")
    with tenancy.tenant_context(acme.tenant.id):
        job = ExportJob.objects.create(project=acme.project)
    assert tasks.export_project_csv(acme.tenant.id, job.id) == "done"
    assert tasks.export_project_csv(acme.tenant.id, job.id) == "already done"
    with tenancy.tenant_context(acme.tenant.id):
        output = ExportJob.objects.get(pk=job.id).csv
    assert "mine" in output and "theirs" not in output
    with pytest.raises(ExportJob.DoesNotExist):
        tasks.export_project_csv(globex.tenant.id, job.id)


def test_download_before_done_is_409(acme):
    with tenancy.tenant_context(acme.tenant.id):
        job = ExportJob.objects.create(project=acme.project)
    assert acme.client().get(f"/api/exports/{job.id}/download/").status_code == 409


def test_due_reminders_reach_every_tenant_once(acme, globex):
    tomorrow = timezone.now().date() + timedelta(days=1)
    acme.issue("due", assignee=acme.users[Role.MEMBER], due_date=tomorrow)
    acme.issue("later", assignee=acme.users[Role.MEMBER], due_date=tomorrow + timedelta(days=30))
    acme.issue("nobody", due_date=tomorrow)
    globex.issue("due too", assignee=globex.users[Role.ADMIN], due_date=tomorrow)
    assert tasks.send_due_reminders() == 2
    assert sorted(m.to[0] for m in mail.outbox) == ["admin@globex.test", "member@acme.test"]
    assert tasks.send_due_reminders() == 0, "a redelivered task must not email twice"


def _ws(org, role=Role.VIEWER, tenant=None, project=None):
    token = AccessToken.for_user(org.users[role])
    return WebsocketCommunicator(application, f"/ws/boards/{project or org.project.id}/?token={token}&tenant={tenant or org.tenant.id}")


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_board_viewer_receives_a_move_after_commit(acme):
    issue = await database_sync_to_async(acme.issue)()
    ws = _ws(acme)
    connected, _ = await ws.connect()
    assert connected

    def move():
        with tenancy.tenant_context(acme.tenant.id):
            services.move_issue(acme.users[Role.OWNER], issue.id, acme.columns[1].id, None, None, 1)

    await database_sync_to_async(move)()
    message = await ws.receive_json_from(timeout=5)
    assert message["event"] == "issue.moved"
    assert message["payload"]["id"] == issue.id and message["payload"]["column"] == acme.columns[1].id
    await ws.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_rolled_back_change_is_never_broadcast(acme):
    from django.db import transaction

    issue = await database_sync_to_async(acme.issue)()
    ws = _ws(acme)
    await ws.connect()

    def move_then_fail():
        with tenancy.tenant_context(acme.tenant.id):
            try:
                with transaction.atomic():
                    services.move_issue(acme.users[Role.OWNER], issue.id, acme.columns[1].id, None, None, 1)
                    raise RuntimeError("boom")
            except RuntimeError:
                pass
            return Issue.objects.get(pk=issue.id).column_id

    assert await database_sync_to_async(move_then_fail)() == acme.columns[0].id
    assert await ws.receive_nothing(timeout=0.3)
    await ws.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_socket_refuses_bad_token(acme):
    ws = WebsocketCommunicator(application, f"/ws/boards/{acme.project.id}/?token=garbage&tenant={acme.tenant.id}")
    connected, code = await ws.connect()
    assert not connected and code == CLOSE_UNAUTHORIZED


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_socket_refuses_a_tenant_you_are_not_in(acme, globex):
    connected, code = await _ws(acme, tenant=globex.tenant.id, project=globex.project.id).connect()
    assert not connected and code == CLOSE_FORBIDDEN


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_socket_refuses_another_tenants_project_via_own_tenant(acme, globex):
    connected, code = await _ws(acme, project=globex.project.id).connect()
    assert not connected and code == CLOSE_FORBIDDEN
