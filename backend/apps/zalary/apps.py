from django.apps import AppConfig


class ZalaryBackendConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.zalary"
    label = "zalary"
    verbose_name = "Zalary Ledger Integration"
