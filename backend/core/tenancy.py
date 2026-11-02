"""Tenant context, shared by the ORM scoping layer and PostgreSQL row-level security.

Two independent layers keep tenants apart:

1. ``TenantManager`` adds ``tenant_id = <current>`` to every query in Python.
2. RLS policies in PostgreSQL compare each row with the ``app.tenant_id`` session
   setting, so a forgotten filter or raw SQL still can't cross tenants.

No tenant set means the ORM returns nothing and RLS matches no rows: failing closed.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager

from django.db import connection, models

_current: contextvars.ContextVar[int | None] = contextvars.ContextVar("tenant_id", default=None)


def current_tenant_id() -> int | None:
    return _current.get()


def _set_db(tenant_id: int | None) -> None:
    with connection.cursor() as c:
        c.execute("SELECT set_config('app.tenant_id', %s, false)", ["" if tenant_id is None else str(tenant_id)])


def activate(tenant_id: int | None) -> None:
    """Set the tenant for this request/task, in Python and on the DB session."""
    _current.set(tenant_id)
    _set_db(tenant_id)


def deactivate(**_kwargs) -> None:
    """Clear the tenant so a reused DB connection can't leak it into the next request.

    Unconditional: under ASGI this signal can run in a different context from the view,
    so the contextvar being empty here says nothing about the DB session.
    """
    _current.set(None)
    if connection.connection is not None:
        _set_db(None)


@contextmanager
def tenant_context(tenant_id: int):
    previous = _current.get()
    activate(tenant_id)
    try:
        yield
    finally:
        activate(previous)


class TenantManager(models.Manager):
    """Scopes every query to the active tenant; returns nothing when none is active."""

    def get_queryset(self):
        qs = super().get_queryset()
        tenant_id = current_tenant_id()
        return qs.none() if tenant_id is None else qs.filter(tenant_id=tenant_id)


class TenantModel(models.Model):
    tenant = models.ForeignKey("core.Tenant", on_delete=models.CASCADE, related_name="+")

    objects = TenantManager()
    unscoped = models.Manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.tenant_id is None:
            self.tenant_id = current_tenant_id()
        if self.tenant_id is None:
            raise RuntimeError(f"cannot save {type(self).__name__} without an active tenant")
        super().save(*args, **kwargs)
