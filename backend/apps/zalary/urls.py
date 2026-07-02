from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .api import (
    CompanyViewSet,
    EnrollmentViewSet,
    LedgerCommandViewSet,
    LedgerHealthView,
    PayrollVaultViewSet,
    SalaryAllocationViewSet,
    SalaryClaimViewSet,
    ZalaryConfigListView,
    ZalaryConfigSyncView,
    ZalaryConfigViewSet,
    ZUSDBalanceView,
    ZUSDFaucetHistoryView,
    ZUSDFaucetRequestView,
)

router = DefaultRouter()
router.register("platform-configs", ZalaryConfigViewSet, basename="zalary-platform-config")
router.register("companies", CompanyViewSet, basename="zalary-company")
router.register("enrollments", EnrollmentViewSet, basename="zalary-enrollment")
router.register("payroll-vaults", PayrollVaultViewSet, basename="zalary-payroll-vault")
router.register("salary-allocations", SalaryAllocationViewSet, basename="zalary-salary-allocation")
router.register("salary-claims", SalaryClaimViewSet, basename="zalary-salary-claim")
router.register("ledger-commands", LedgerCommandViewSet, basename="zalary-ledger-command")

urlpatterns = [
    path("ledger/health/", LedgerHealthView.as_view(), name="zalary-ledger-health"),
    path("config/", ZalaryConfigListView.as_view(), name="zalary-config-list"),
    path("config/sync/", ZalaryConfigSyncView.as_view(), name="zalary-config-sync"),
    path("sandbox/zusd/balance/", ZUSDBalanceView.as_view(), name="zalary-zusd-balance"),
    path("sandbox/zusd/faucet/request/", ZUSDFaucetRequestView.as_view(), name="zalary-zusd-faucet-request"),
    path("sandbox/zusd/faucet/history/", ZUSDFaucetHistoryView.as_view(), name="zalary-zusd-faucet-history"),
    path("", include(router.urls)),
]
