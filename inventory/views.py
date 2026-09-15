from itertools import product

from django.db.models import F, ExpressionWrapper, IntegerField, Value
from django.db.models.functions import Coalesce
from django.shortcuts import render, redirect
from django.db.models import Count, Sum, Q, Subquery, OuterRef

from inventory.models import Product, Reservation, ReservationItem, Unit, EquipmentType


# Вывод данных в главную таблицу
def main_dashboard(request):
    products=Product.objects.annotate(
        total_count = Count("unit", filter=Q(unit__status__in=["IN_STOCK", "READY", "IN_SERVICE"])), # Фильтрация по нескольким значяениям
        ready_count = Count("unit", filter=Q(unit__status="READY")), # считаем Unit'ы, у которых статус READY
        service_count = Count("unit", filter=Q(unit__status="IN_SERVICE")), #
        reserved_count=Coalesce(
            Subquery(
                ReservationItem.objects.filter(
                    product=OuterRef('pk'),
                    reservation__is_fulfilled=False
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

# Вывод устройств в резерве
def active_reservations(request):
    reservations = Reservation.objects.filter(is_fulfilled=False)
    return render(request, "inventory/reservations.html", {"reservations":reservations})

# Вывод устройст в сервисе
def active_service(request):
    units = Unit.objects.filter( status = "IN_SERVICE").order_by('service_received_at')
    return render(request, "inventory/service.html", {"units":units})

# Функция прихода
def add_units(request):
    if request.method == "POST":
        product_id = request.POST.get('product')
        quantity = int(request.POST.get('quantity'))

        product = Product.objects.get(id = product_id)

        for _ in range(quantity):
            Unit.objects.create(
                product=product,
                status = "IN_STOCK" # serial_number не указываем — он будет None
            )
        return redirect("dashboard")

    products = Product.objects.all()
    return render(request, "inventory/add_units.html", {"products": products})

# Создание новой номенклатуры
def add_product(request):
    equipment_types = EquipmentType.objects.all()

    if request.method == "POST":
        name = request.POST.get('name')
        equipment_type_id = request.POST.get('equipment_type')
        error_message = None

        if name and equipment_type_id:
            if Product.objects.filter(name=name).exists():
                error_message = "Товар с таким названием уже существует"
            else:
                equipment_type = EquipmentType.objects.get(id=equipment_type_id)
                Product.objects.create(name=name, equipment_type=equipment_type)
                return redirect('add_units')
        else:
            error_message = "Заполните все поля"

        if error_message:
            return render(request, "inventory/add_product.html", {
                "equipment_types": equipment_types,
                "error": error_message,
            })

    return render(request, "inventory/add_product.html", {"equipment_types": equipment_types})


def prepare_list(request):
    products = Product.objects.annotate(
        units_to_prepare=Count("unit", filter=Q(unit__status="IN_STOCK"))).filter(units_to_prepare__gt=0)
    return render(request, "inventory/prepare_list.html", {"products": products})




