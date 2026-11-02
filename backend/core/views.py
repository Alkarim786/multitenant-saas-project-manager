from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from . import serializers as s
from . import services, tasks
from .models import AuditLog, Comment, ExportJob, Issue, Membership, Project, Role, User
from .permissions import TenantViewMixin


def tokens_for(user) -> dict:
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


class ThrottledTokenView(TokenObtainPairView):
    throttle_scope = "auth"


class RegisterView(APIView):
    """Create an account and its first organisation (the caller becomes OWNER)."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "auth"

    def post(self, request):
        ser = s.RegisterSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        with transaction.atomic():
            user = User.objects.create_user(d["email"], d["password"], d["name"])
            tenant = services.create_organisation(user, d["organisation"])
        return Response({**tokens_for(user), "user": s.UserSerializer(user).data, "tenant": s.TenantSerializer(tenant).data}, status=201)


class MeView(APIView):
    def get(self, request):
        memberships = Membership.objects.filter(user=request.user).select_related("tenant")
        return Response(
            {
                "user": s.UserSerializer(request.user).data,
                "organisations": [{**s.TenantSerializer(m.tenant).data, "role": m.role} for m in memberships],
            }
        )


class ProjectViewSet(TenantViewMixin, viewsets.ModelViewSet):
    serializer_class = s.ProjectSerializer
    action_roles = {"create": Role.ADMIN, "update": Role.ADMIN, "partial_update": Role.ADMIN, "destroy": Role.ADMIN, "export": Role.MEMBER}

    def get_queryset(self):
        return Project.objects.all().order_by("key")

    def perform_create(self, ser):
        d = ser.validated_data
        ser.instance = services.create_project(self.request.user, d["key"], d["name"], d.get("description", ""))

    def perform_update(self, ser):
        before = {"key": ser.instance.key, "name": ser.instance.name}
        project = ser.save()
        services.audit(self.request.user, "project.updated", "project", project.id, before, {"key": project.key, "name": project.name})

    def perform_destroy(self, instance):
        with transaction.atomic():
            services.audit(self.request.user, "project.deleted", "project", instance.id, before={"key": instance.key, "name": instance.name})
            Issue.objects.filter(project=instance).delete()
            instance.delete()

    @action(detail=True, methods=["get"])
    def board(self, request, pk=None):
        project = self.get_object()
        issues = Issue.objects.filter(project=project).select_related("project")
        by_column: dict[int, list] = {}
        for issue in issues:
            by_column.setdefault(issue.column_id, []).append(services.issue_data(issue))
        return Response(
            {
                "project": s.ProjectSerializer(project).data,
                "columns": [{**s.ColumnSerializer(c).data, "issues": by_column.get(c.id, [])} for c in project.columns.all()],
                "members": [
                    s.UserSerializer(m.user).data
                    for m in Membership.objects.filter(tenant_id=project.tenant_id).select_related("user")
                ],
            }
        )

    @action(detail=True, methods=["post"])
    def export(self, request, pk=None):
        project = self.get_object()
        job = ExportJob.objects.create(project=project, requested_by=request.user)
        tenant_id, job_id = project.tenant_id, job.id
        # Queue after commit, so the worker can never look for a job that isn't visible yet.
        transaction.on_commit(lambda: tasks.export_project_csv.delay(tenant_id, job_id))
        services.audit(request.user, "project.exported", "project", project.id)
        return Response(s.ExportJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


class IssueViewSet(TenantViewMixin, viewsets.ModelViewSet):
    serializer_class = s.IssueSerializer

    def get_queryset(self):
        qs = Issue.objects.select_related("project").all()
        project = self.request.query_params.get("project")
        if project is None:
            return qs
        if not project.isdigit():
            raise ValidationError({"project": "must be a project id"})
        return qs.filter(project_id=project)

    def perform_create(self, ser):
        d = {**ser.validated_data}
        d.pop("expected_version", None)
        project, column = d.pop("project"), d.pop("column")
        ser.instance = services.create_issue(self.request.user, project, column, **d)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        with transaction.atomic():
            issue = get_object_or_404(self.get_queryset().select_for_update(of=("self",)), pk=kwargs["pk"])
            ser = self.get_serializer(issue, data=request.data, partial=partial)
            ser.is_valid(raise_exception=True)
            expected = ser.validated_data.pop("expected_version", None)
            if expected is not None and expected != issue.version:
                body = {"detail": "this issue was changed by someone else", "code": "stale_version", "current": services.issue_data(issue)}
                return Response(body, status=409)
            before = services.issue_data(issue)
            issue = ser.save(version=issue.version + 1)
            after = services.issue_data(issue)
            services.audit(request.user, "issue.updated", "issue", issue.id, before, after)
            services.broadcast(issue.project_id, "issue.updated", after)
        return Response(self.get_serializer(issue).data)

    def perform_destroy(self, instance):
        with transaction.atomic():
            data = services.issue_data(instance)
            pk, project_id = instance.id, instance.project_id
            services.audit(self.request.user, "issue.deleted", "issue", pk, before=data)
            instance.delete()
            services.broadcast(project_id, "issue.deleted", {"id": pk, "column": data["column"]})

    @action(detail=True, methods=["post"])
    def move(self, request, pk=None):
        ser = s.MoveSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        issue = self.get_object()
        try:
            moved = services.move_issue(request.user, issue.id, d["column"], d.get("before"), d.get("after"), d.get("version"))
        except services.StaleVersion as e:
            return Response({"detail": "this issue was moved by someone else", "code": "stale_version", "current": e.current}, status=409)
        except (Issue.DoesNotExist, services.Column.DoesNotExist) as e:
            raise ValidationError({"detail": "column or neighbouring issue not found in this project"}) from e
        return Response(services.issue_data(moved))

    @action(detail=True, methods=["get", "post"])
    def comments(self, request, pk=None):
        issue = self.get_object()
        if request.method == "GET":
            return Response(s.CommentSerializer(issue.comments.select_related("author"), many=True).data)
        ser = s.CommentSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        with transaction.atomic():
            comment = Comment.objects.create(issue=issue, author=request.user, body=ser.validated_data["body"])
            services.audit(request.user, "comment.created", "comment", comment.id, after={"issue": issue.id})
            data = s.CommentSerializer(comment).data
            services.broadcast(issue.project_id, "comment.created", {"issue": issue.id, "comment": dict(data)})
        return Response(data, status=201)


class MemberViewSet(TenantViewMixin, mixins.ListModelMixin, mixins.UpdateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet):
    serializer_class = s.MembershipSerializer
    write_role = Role.ADMIN
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        return Membership.objects.filter(tenant_id=self.request.membership.tenant_id).select_related("user").order_by("id")

    def _guard_owner_change(self, target: Membership, new_role: str | None = None) -> None:
        """Only an owner may touch owners or grant ownership, and an organisation keeps at least one."""
        touches_owner = target.role == Role.OWNER or new_role == Role.OWNER
        if touches_owner:
            self.require_role(Role.OWNER)
        if target.role == Role.OWNER and new_role != Role.OWNER:
            owners = Membership.objects.filter(tenant_id=target.tenant_id, role=Role.OWNER).count()
            if owners <= 1:
                raise ValidationError({"detail": "an organisation must keep at least one owner"})

    def create(self, request):
        ser = s.AddMemberSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        role = ser.validated_data["role"]
        if role == Role.OWNER:
            self.require_role(Role.OWNER)
        user = User.objects.filter(email__iexact=ser.validated_data["email"]).first()
        if user is None:
            raise ValidationError({"email": "no account with this email; ask them to register first"})
        try:
            with transaction.atomic():
                m = Membership.objects.create(user=user, tenant_id=request.membership.tenant_id, role=role)
                services.audit(request.user, "member.added", "membership", m.id, after={"user": user.id, "role": role})
        except IntegrityError as e:
            raise ValidationError({"email": "already a member"}) from e
        return Response(self.get_serializer(m).data, status=201)

    def perform_update(self, ser):
        target, new = ser.instance, ser.validated_data.get("role", ser.instance.role)
        self._guard_owner_change(target, new)
        before = target.role
        m = ser.save()
        services.audit(self.request.user, "member.role_changed", "membership", m.id, {"role": before}, {"role": new})

    def perform_destroy(self, instance):
        self._guard_owner_change(instance)
        services.audit(self.request.user, "member.removed", "membership", instance.id, before={"user": instance.user_id, "role": instance.role})
        instance.delete()


class AuditLogViewSet(TenantViewMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = s.AuditLogSerializer
    read_role = Role.ADMIN

    def get_queryset(self):
        return AuditLog.objects.select_related("actor").all()


class ExportJobViewSet(TenantViewMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = s.ExportJobSerializer
    read_role = Role.MEMBER

    def get_queryset(self):
        return ExportJob.objects.all().order_by("-id")

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        job = self.get_object()
        if job.status != ExportJob.Status.DONE:
            return Response({"detail": f"export is {job.status}"}, status=409)
        resp = HttpResponse(job.csv, content_type="text/csv")
        resp["Content-Disposition"] = f'attachment; filename="export-{job.id}.csv"'
        return resp


