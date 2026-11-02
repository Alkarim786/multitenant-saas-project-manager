from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

from .tenancy import TenantModel


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, name=""):
        user = self.model(email=self.normalize_email(email).lower(), name=name)
        user.set_password(password)
        user.save(using=self._db)
        return user


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    USERNAME_FIELD = "email"
    objects = UserManager()


class Tenant(models.Model):
    name = models.CharField(max_length=120)
    slug = models.SlugField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Role(models.TextChoices):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    MEMBER = "MEMBER"
    VIEWER = "VIEWER"


ROLE_RANK = {Role.VIEWER: 0, Role.MEMBER: 1, Role.ADMIN: 2, Role.OWNER: 3}


class Membership(models.Model):
    """Not row-level secured: it's what decides which tenants a user may enter."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MEMBER)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "tenant"], name="one_membership_per_tenant")]


class Project(TenantModel):
    key = models.CharField(max_length=10)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    issue_counter = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "key"], name="project_key_unique_per_tenant")]


class Column(TenantModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="columns")
    name = models.CharField(max_length=60)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]


class Priority(models.TextChoices):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    URGENT = "URGENT"


class Issue(TenantModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="issues")
    column = models.ForeignKey(Column, on_delete=models.PROTECT, related_name="issues")
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.MEDIUM)
    story_points = models.PositiveSmallIntegerField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    reminder_sent = models.BooleanField(default=False)
    # Fractional index: a move rewrites one row instead of renumbering the column.
    rank = models.CharField(max_length=64, db_collation="C")
    version = models.PositiveIntegerField(default=1)  # optimistic concurrency
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["rank", "id"]
        indexes = [models.Index(fields=["column", "rank"])]
        constraints = [models.UniqueConstraint(fields=["project", "number"], name="issue_number_unique_per_project")]


class Comment(TenantModel):
    issue = models.ForeignKey(Issue, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]


class AuditLog(TenantModel):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=40)
    entity_type = models.CharField(max_length=40)
    entity_id = models.BigIntegerField()
    before = models.JSONField(null=True)
    after = models.JSONField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]
        indexes = [models.Index(fields=["tenant", "-id"])]


class ExportJob(TenantModel):
    class Status(models.TextChoices):
        PENDING = "PENDING"
        DONE = "DONE"
        FAILED = "FAILED"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="+")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    csv = models.TextField(blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
