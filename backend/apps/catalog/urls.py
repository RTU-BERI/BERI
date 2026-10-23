
# Подключаем инструменты для настройки адресов
from django.urls import include, path
from rest_framework.routers import DefaultRouter

# Импортируем обработчик запросов к категориям
from .views import ProductCategoryViewSet


# Создаём маршрутизатор для API
router = DefaultRouter()

# Регистрируем адрес для категорий товаров
router.register(
    "categories",
    ProductCategoryViewSet,
    basename="category"
)

# Подключаем созданные маршруты
urlpatterns = [
    path("", include(router.urls)),
]
