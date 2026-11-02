from django.db import connection
from django.http import JsonResponse
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from core import views


def health(_request):
    with connection.cursor() as c:
        c.execute("SELECT 1")
    return JsonResponse({"status": "ok"})


router = DefaultRouter()
router.register("projects", views.ProjectViewSet, basename="project")
router.register("issues", views.IssueViewSet, basename="issue")
router.register("members", views.MemberViewSet, basename="member")
router.register("audit-log", views.AuditLogViewSet, basename="audit")
router.register("exports", views.ExportJobViewSet, basename="export")

urlpatterns = [
    path("health/", health),
    path("api/auth/register/", views.RegisterView.as_view()),
    path("api/auth/token/", views.ThrottledTokenView.as_view()),
    path("api/auth/token/refresh/", TokenRefreshView.as_view()),
    path("api/me/", views.MeView.as_view()),
    path("api/", include(router.urls)),
]
