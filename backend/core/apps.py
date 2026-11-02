from django.apps import AppConfig
from django.core.signals import request_finished


class CoreConfig(AppConfig):
    name = "core"

    def ready(self):
        from . import tenancy

        # Never let a reused connection carry one request's tenant into the next.
        request_finished.connect(tenancy.deactivate, dispatch_uid="tenant-reset")
