from rest_framework.routers import SimpleRouter

from .views import PickupPointViewSet


router = SimpleRouter()
router.register("pickup-points", PickupPointViewSet, basename="pickup-point")

urlpatterns = router.urls
