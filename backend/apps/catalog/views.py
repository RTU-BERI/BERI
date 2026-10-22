
# Подключаем инструмент для просмотра данных через API
from rest_framework.viewsets import ReadOnlyModelViewSet

# Импортируем модель категорий
from .models import ProductCategory

# Импортируем сериализатор категорий
from .serializers import ProductCategorySerializer


# Создаём обработчик запросов к категориям товаров
class ProductCategoryViewSet(ReadOnlyModelViewSet):

    # Получаем все категории из базы данных
    queryset = ProductCategory.objects.all()

    # Преобразуем категории в данные для API
    serializer_class = ProductCategorySerializer
