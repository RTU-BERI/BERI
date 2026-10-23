"""Category API routes; preserve the names introduced by PR #1."""

from rest_framework.routers import DefaultRouter

from .views import ProductCategoryViewSet

router = DefaultRouter()
router.register("categories", ProductCategoryViewSet, basename="category")

urlpatterns = router.urls
