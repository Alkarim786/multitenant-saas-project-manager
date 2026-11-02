"""Business operations shared by the API and background jobs."""

from __future__ import annotations

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.utils.text import slugify

from . import ranking
from .models import AuditLog, Column, Issue, Membership, Project, Role, Tenant, User

DEFAULT_COLUMNS = ["To Do", "In Progress", "In Review", "Done"]


def board_group(project_id: int) -> str:
    return f"board_{project_id}"


def issue_data(issue: Issue) -> dict:
    return {
        "id": issue.id,
        "key": f"{issue.project.key}-{issue.number}",
        "project": issue.project_id,
        "column": issue.column_id,
        "number": issue.number,
        "title": issue.title,
        "description": issue.description,
        "assignee": issue.assignee_id,
        "priority": issue.priority,
        "story_points": issue.story_points,
        "due_date": issue.due_date.isoformat() if issue.due_date else None,
        "rank": issue.rank,
        "version": issue.version,
    }


def audit(actor, action: str, entity_type: str, entity_id: int, before=None, after=None) -> None:
    AuditLog.objects.create(actor=actor, action=action, entity_type=entity_type, entity_id=entity_id, before=before, after=after)


def broadcast(project_id: int, event: str, payload: dict) -> None:
    """Tell everyone on this board, but only once the change is really committed."""

    def send():
        layer = get_channel_layer()
        async_to_sync(layer.group_send)(board_group(project_id), {"type": "board.event", "event": event, "payload": payload})

    transaction.on_commit(send)


@transaction.atomic
def create_organisation(user: User, name: str) -> Tenant:
    base = slugify(name)[:40] or "org"
    slug, n = base, 1
    while Tenant.objects.filter(slug=slug).exists():
        n += 1
        slug = f"{base}-{n}"
    tenant = Tenant.objects.create(name=name, slug=slug)
    Membership.objects.create(user=user, tenant=tenant, role=Role.OWNER)
    return tenant


@transaction.atomic
def create_project(actor, key: str, name: str, description: str = "") -> Project:
    project = Project.objects.create(key=key.upper(), name=name, description=description)
    Column.objects.bulk_create(
        [Column(tenant_id=project.tenant_id, project=project, name=c, position=i) for i, c in enumerate(DEFAULT_COLUMNS)]
    )
    audit(actor, "project.created", "project", project.id, after={"key": project.key, "name": name})
    return project


@transaction.atomic
def create_issue(actor, project: Project, column: Column, **fields) -> Issue:
    # Row lock: concurrent creates in one project get distinct numbers.
    locked = Project.objects.select_for_update().get(pk=project.pk)
    locked.issue_counter += 1
    locked.save(update_fields=["issue_counter"])
    last = Issue.objects.filter(column=column).order_by("-rank").values_list("rank", flat=True).first()
    issue = Issue.objects.create(project=locked, column=column, number=locked.issue_counter, reporter=actor, rank=ranking.between(last, None), **fields)
    data = issue_data(issue)
    audit(actor, "issue.created", "issue", issue.id, after=data)
    broadcast(project.id, "issue.created", data)
    return issue


class StaleVersion(Exception):
    def __init__(self, current: dict):
        self.current = current


def _rebalance(column: Column) -> None:
    issues = list(Issue.objects.select_for_update().filter(column=column).order_by("rank", "id"))
    for issue, key in zip(issues, ranking.evenly_spaced(len(issues)), strict=True):
        issue.rank = key
    Issue.objects.bulk_update(issues, ["rank"])


@transaction.atomic
def move_issue(actor, issue_id: int, column_id: int, before_id: int | None, after_id: int | None, version: int | None) -> Issue:
    """Place an issue between two neighbours: ``before_id`` above it, ``after_id`` below it."""
    # of=("self",): lock the issue only, so moves of other cards in the project don't queue.
    issue = Issue.objects.select_for_update(of=("self",)).select_related("project").get(pk=issue_id)
    if version is not None and version != issue.version:
        raise StaleVersion(issue_data(issue))
    column = Column.objects.get(pk=column_id, project_id=issue.project_id)
    old = issue_data(issue)

    def neighbour(pk):
        if pk is None:
            return None
        return Issue.objects.get(pk=pk, column=column).rank

    low, high = neighbour(before_id), neighbour(after_id)
    if before_id is None and after_id is None:
        low = Issue.objects.filter(column=column).exclude(pk=issue.pk).order_by("-rank").values_list("rank", flat=True).first()
    elif before_id is None:
        low = Issue.objects.filter(column=column, rank__lt=high).exclude(pk=issue.pk).order_by("-rank").values_list("rank", flat=True).first()
    elif after_id is None:
        high = Issue.objects.filter(column=column, rank__gt=low).exclude(pk=issue.pk).order_by("rank").values_list("rank", flat=True).first()
    try:
        # Equal neighbours can only come from two concurrent moves into one gap.
        rank = ranking.between(low, high)
    except ValueError:
        _rebalance(column)
        low, high = neighbour(before_id), neighbour(after_id)
        rank = ranking.between(low, high)
    issue.column = column
    issue.rank = rank
    issue.version += 1
    issue.save(update_fields=["column", "rank", "version", "updated_at"])
    data = issue_data(issue)
    audit(actor, "issue.moved", "issue", issue.id, before={"column": old["column"], "rank": old["rank"]}, after={"column": column.id, "rank": rank})
    broadcast(issue.project_id, "issue.moved", data)
    return issue
