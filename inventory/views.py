from django.db.models import F, ExpressionWrapper, IntegerField, Value
from django.db.models.functions import Coalesce
from django.shortcuts import render
from django.db.models import Count, Sum, Q, Subquery, OuterRef

from inventory.models import Product, Reservation


# Вывод данных в главную таблицу
def main_dashboard(request):
    products=Product.objects.annotate(
        total_count = Count("unit", filter=Q(unit__status__in=["IN_STOCK", "READY", "IN_SERVICE"])), # Фильтрация по нескольким значяениям
        ready_count = Count("unit", filter=Q(unit__status="READY")), # считаем Unit'ы, у которых статус READY
        service_count = Count("unit", filter=Q(unit__status="IN_SERVICE")), #
        reserved_count=Coalesce(
            Subquery(
                Reservation.objects.filter(
                    product=OuterRef('pk'),
                    is_fulfilled=False
                ).annotate(
                    total=Sum('quantity')
                ).values('total')[:1]
            ),
            Value(0)
        ),
        available_count=ExpressionWrapper(
            F('total_count') -
            Coalesce(F('reserved_count'), Value(0)) - # Coalesce заменяет None на значение по умолчанию в данном случае на 0
            Coalesce(F('service_count'), Value(0)),
            output_field=IntegerField()
        )
        ).order_by('equipment_type__name', 'name')
    return render(request, "inventory/dashboard.html", {"products": products})