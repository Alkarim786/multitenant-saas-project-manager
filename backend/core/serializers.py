from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from .models import AuditLog, Column, Comment, ExportJob, Issue, Membership, Project, Role, Tenant, User
from .services import issue_data


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(min_length=8, write_only=True)
    name = serializers.CharField(max_length=120)
    organisation = serializers.CharField(max_length=120)

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("an account with this email already exists")
        return value.lower()

    def validate_password(self, value):
        validate_password(value)
        return value


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "email", "name"]


class TenantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tenant
        fields = ["id", "name", "slug"]


class MembershipSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)

    class Meta:
        model = Membership
        fields = ["id", "user", "role", "created_at"]
        read_only_fields = ["id", "user", "created_at"]


class AddMemberSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role = serializers.ChoiceField(choices=Role.choices, default=Role.MEMBER)


class ColumnSerializer(serializers.ModelSerializer):
    class Meta:
        model = Column
        fields = ["id", "name", "position"]


class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = ["id", "key", "name", "description", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate_key(self, value):
        value = value.upper()
        if not value.isalnum():
            raise serializers.ValidationError("key must be letters and digits")
        qs = Project.objects.filter(key=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("key already used in this organisation")
        return value


class IssueSerializer(serializers.ModelSerializer):
    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.none())
    column = serializers.PrimaryKeyRelatedField(queryset=Column.objects.none())
    assignee = serializers.PrimaryKeyRelatedField(queryset=User.objects.none(), allow_null=True, required=False)
    key = serializers.SerializerMethodField()
    expected_version = serializers.IntegerField(write_only=True, required=False)

    class Meta:
        model = Issue
        fields = ["id", "key", "project", "column", "number", "title", "description", "assignee", "priority",
                  "story_points", "due_date", "rank", "version", "created_at", "updated_at", "expected_version"]  # fmt: skip
        read_only_fields = ["id", "number", "rank", "version", "created_at", "updated_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Evaluated per request: related objects must belong to the active tenant.
        request = self.context.get("request")
        self.fields["project"].queryset = Project.objects.all()
        self.fields["column"].queryset = Column.objects.all()
        if request is not None and getattr(request, "membership", None):
            self.fields["assignee"].queryset = User.objects.filter(memberships__tenant_id=request.membership.tenant_id)

    def get_key(self, obj):
        return issue_data(obj)["key"]

    def validate(self, attrs):
        project = attrs.get("project") or (self.instance and self.instance.project)
        column = attrs.get("column")
        if column is not None and column.project_id != project.id:
            raise serializers.ValidationError({"column": "column belongs to another project"})
        if self.instance is not None:
            if "project" in attrs and attrs["project"].id != self.instance.project_id:
                raise serializers.ValidationError({"project": "an issue can't change project"})
            if "column" in attrs and attrs["column"].id != self.instance.column_id:
                raise serializers.ValidationError({"column": "use the move endpoint to change column"})
        return attrs


class MoveSerializer(serializers.Serializer):
    column = serializers.IntegerField()
    before = serializers.IntegerField(required=False, allow_null=True, help_text="issue directly above")
    after = serializers.IntegerField(required=False, allow_null=True, help_text="issue directly below")
    version = serializers.IntegerField(required=False)


class CommentSerializer(serializers.ModelSerializer):
    author = UserSerializer(read_only=True)

    class Meta:
        model = Comment
        fields = ["id", "author", "body", "created_at"]
        read_only_fields = ["id", "author", "created_at"]


class AuditLogSerializer(serializers.ModelSerializer):
    actor = UserSerializer(read_only=True)

    class Meta:
        model = AuditLog
        fields = ["id", "actor", "action", "entity_type", "entity_id", "before", "after", "created_at"]


class ExportJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExportJob
        fields = ["id", "project", "status", "error", "created_at"]
