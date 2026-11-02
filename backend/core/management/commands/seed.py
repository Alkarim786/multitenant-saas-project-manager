"""Demo data: two organisations with users in every role. Safe to run twice."""

import os
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core import services, tenancy
from core.models import Column, Membership, Project, Role, Tenant, User

ORGS = {
    "acme": (
        "Acme Corp",
        "WEB",
        "Website relaunch",
        ["Design the pricing page", "Fix checkout on Safari", "Write launch announcement", "Add cookie settings", "Migrate blog images", "Add uptime alerts"],
    ),
    "globex": ("Globex", "OPS", "Warehouse rollout", ["Order barcode scanners", "Train night shift", "Label aisle 12", "Audit stock counts"]),
}


class Command(BaseCommand):
    help = "Create demo organisations, users and issues. Passwords come from SEED_PASSWORD."

    def handle(self, *args, **options):
        password = os.environ.get("SEED_PASSWORD")
        if not password:
            raise CommandError("set SEED_PASSWORD")
        for slug, (org_name, key, project_name, titles) in ORGS.items():
            tenant, _ = Tenant.objects.get_or_create(slug=slug, defaults={"name": org_name})
            users = {}
            for role in Role:
                email = f"{role.value.lower()}@{slug}.test"
                user = User.objects.filter(email=email).first() or User.objects.create_user(email, password, f"{role.value.title()} ({org_name})")
                Membership.objects.get_or_create(user=user, tenant=tenant, defaults={"role": role})
                users[role] = user
            with tenancy.tenant_context(tenant.id):
                if Project.objects.filter(key=key).exists():
                    continue
                project = services.create_project(users[Role.OWNER], key, project_name)
                columns = list(Column.objects.filter(project=project))
                for n, title in enumerate(titles):
                    services.create_issue(
                        users[Role.OWNER], project, columns[n % 3], title=title,
                        assignee=users[Role.MEMBER] if n % 2 == 0 else None,
                        priority=["MEDIUM", "HIGH", "LOW", "URGENT"][n % 4],
                        story_points=[1, 2, 3, 5, 8][n % 5],
                        due_date=timezone.now().date() + timedelta(days=n * 3),
                    )  # fmt: skip
            self.stdout.write(f"seeded {org_name}: owner@{slug}.test, admin@, member@, viewer@")
