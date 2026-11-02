"""Celery tasks. Each one activates its tenant explicitly and is safe to run twice,
because acks_late means a crashed worker's task is delivered again."""

import csv
import io
from datetime import timedelta

from celery import shared_task
from django.core.mail import send_mail
from django.utils import timezone

from . import tenancy
from .models import ExportJob, Issue, Tenant


@shared_task
def export_project_csv(tenant_id: int, job_id: int) -> str:
    with tenancy.tenant_context(tenant_id):
        job = ExportJob.objects.get(pk=job_id)
        if job.status == ExportJob.Status.DONE:
            return "already done"
        try:
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["key", "title", "column", "priority", "story_points", "assignee", "due_date", "created_at"])
            issues = Issue.objects.filter(project_id=job.project_id).select_related("project", "column", "assignee").order_by("number")
            for i in issues:
                w.writerow(
                    [
                        f"{i.project.key}-{i.number}",
                        _safe_cell(i.title),
                        i.column.name,
                        i.priority,
                        i.story_points if i.story_points is not None else "",
                        i.assignee.email if i.assignee else "",
                        i.due_date.isoformat() if i.due_date else "",
                        i.created_at.isoformat(),
                    ]
                )
            job.csv, job.status = buf.getvalue(), ExportJob.Status.DONE
            job.save(update_fields=["csv", "status"])
            return "done"
        except Exception as e:
            job.status, job.error = ExportJob.Status.FAILED, str(e)[:500]
            job.save(update_fields=["status", "error"])
            raise


def _safe_cell(value: str) -> str:
    """Stop spreadsheet formula injection: a title like =HYPERLINK(...) must stay plain text."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


@shared_task
def send_due_reminders() -> int:
    """Email assignees of issues due within 24 hours. ``reminder_sent`` prevents duplicates."""
    sent = 0
    soon = (timezone.now() + timedelta(days=1)).date()
    for tenant_id in Tenant.objects.values_list("id", flat=True):
        with tenancy.tenant_context(tenant_id):
            due = Issue.objects.filter(due_date__lte=soon, reminder_sent=False, assignee__isnull=False).select_related("assignee", "project")
            for issue in due:
                send_mail(
                    f"[{issue.project.key}-{issue.number}] due {issue.due_date}",
                    f'"{issue.title}" is due on {issue.due_date}.',
                    None,
                    [issue.assignee.email],
                )
                Issue.objects.filter(pk=issue.pk).update(reminder_sent=True)
                sent += 1
    return sent
