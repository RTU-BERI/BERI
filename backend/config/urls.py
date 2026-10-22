
# Подключаем административную панель Django
from django.contrib import admin

# Инструменты для настройки адресов
from django.urls import include, path


# Основные адреса проекта «Бери»
urlpatterns = [
    # Административная панель Django
    path("admin/", admin.site.urls),

    # Подключаем API категорий товаров
    path("api/", include("apps.catalog.urls")),
]

