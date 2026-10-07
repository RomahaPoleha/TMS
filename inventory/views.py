from Tools.scripts.make_ctype import method
from django.contrib import messages
from django.db.models import F, ExpressionWrapper, IntegerField, Value
from django.db.models.functions import Coalesce
from django.shortcuts import render, redirect
from django.db.models import Count, Sum, Q, Subquery, OuterRef
from django.utils import timezone
from inventory.models import Product, Reservation, ReservationItem, Unit, EquipmentType, Client , DefectType


def main_dashboard(request):
    """Сводная таблица по товарам: наличие, резервы, сервис, доступность."""
    products = Product.objects.annotate(
        total_count=Count("unit", filter=Q(unit__status__in=["IN_STOCK", "READY", "IN_SERVICE"])),
        ready_count=Count("unit", filter=Q(unit__status="READY")),
        service_count=Count("unit", filter=Q(unit__status="IN_SERVICE")),

        # Сумма quantity из активных резервов через подзапрос,
        # т.к. резервы хранятся в ReservationItem, а не в Reservation
        reserved_count=Coalesce(
            Subquery(
                ReservationItem.objects.filter(
                    product=OuterRef('pk'),
                    reservation__is_fulfilled=False, reservation__is_canceled=False).values("product"
                ).annotate(total=Sum('quantity')).values('total')[:1]
            ),
            Value(0)
        ),

        # Свободно = Всего - Резерв - Сервис
        available_count=ExpressionWrapper(
            F('total_count') - Coalesce(F('reserved_count'), Value(0)) - Coalesce(F('service_count'), Value(0)),
            output_field=IntegerField()
        )
    ).order_by('equipment_type__name', 'name')

    return render(request, "inventory/dashboard.html", {"products": products})


def ship_reservation(request, reservation_id):
    """Отгрузка резервов клиенту"""
    reservation = Reservation.objects.get(id=reservation_id)
    reservation_items = ReservationItem.objects.filter(reservation=reservation)

    if request.method == "POST":
        # Проходим по всем позициям резерва
        for item in reservation_items:
            selected_ids = request.POST.getlist(f'unit_{item.id}')

            # Проверяем, что выбрано правильное количество устройств
            if len(selected_ids) != item.quantity:
                messages.error(
                    request,
                    f"Для товара {item.product} нужно выбрать {item.quantity} шт., выбрано {len(selected_ids)}"
                )
                return redirect('ship_reservation', reservation_id=reservation_id)

            # Меняем статус каждого выбранного Unit на SOLD
            for unit_id in selected_ids:
                unit = Unit.objects.get(id=unit_id)
                unit.reservation=reservation # привязка Unit к текущему резерву через новое поле
                unit.status = 'SOLD'
                unit.save()

        # Помечаем резерв как исполненный
        reservation.is_fulfilled = True
        reservation.date_of_shipment = timezone.now()
        reservation.save()
        messages.success(request, "Резерв успешно отгружен")
        return redirect('reservations')

    # GET-запрос: собираем данные для шаблона
    items_with_units = []
    for item in reservation_items:
        available_units = Unit.objects.filter(
            product=item.product,
            status="READY"
        )
        items_with_units.append({
            "item": item,
            "available_units": available_units,
            "quantity_range": range(item.quantity),
        })

    return render(request, "inventory/ship_reservation.html", {
        "reservation": reservation,
        "items_with_units": items_with_units,
    })

def reservation_history(request):
    """История отгрузок"""
    reservations = Reservation.objects.filter(is_fulfilled=True).order_by('-created_at')
    return render (request, "inventory/reservations_history.html" , {"reservations": reservations })


def active_reservations(request):
    """Список неисполненных резервов."""
    reservations = Reservation.objects.filter(is_fulfilled=False, is_canceled=False)
    return render(request, "inventory/reservations.html", {"reservations": reservations})

def create_reservation(request):
    clients = Client.objects.all()
    products = Product.objects.all()
    if request.method == "POST":
        client_id = request.POST.get('client')  # получаем ID клиента из формы
        if client_id:
            items = []
            for i in range(1, 10):
                product = request.POST.get(f"product_{i}")
                quantity = request.POST.get(f"quantity_{i}")
                if not product or not quantity:
                    continue
                items.append((product,quantity))


            if not items:
                error = "Добавьте хотябы 1 позицию"
                return render(request, "inventory/create_reservation.html",
                              {"clients": clients, "products": products, "error": error})
        else:
            error = "Выберите клиента"
            return render(request, "inventory/create_reservation.html",
                          {"clients": clients, "products": products, "error": error})

        client = Client.objects.get(id=client_id)
        reservation = Reservation.objects.create(client=client)
        for product_id, quantity in items:
            product = Product.objects.get(id=product_id)
            quantity = int(quantity)
            ReservationItem.objects.create(reservation=reservation, product=product, quantity=quantity)

        return redirect('reservations')

    return render(request, "inventory/create_reservation.html", {
        "clients": clients,
        "products": products
    })

def active_service(request):
    """Устройства в ремонте, отсортированные по дате приёма."""
    units = Unit.objects.filter(status="IN_SERVICE").order_by('service_received_at')
    return render(request, "inventory/service.html", {"units": units})




def add_units(request):
    """Приёмка партии: создание N Unit'ов без серийников."""
    if request.method == "POST":
        product = Product.objects.get(id=request.POST.get('product'))
        quantity = int(request.POST.get('quantity'))

        for _ in range(quantity):
            Unit.objects.create(product=product, status="IN_STOCK")


        return redirect("dashboard")

    return render(request, "inventory/add_units.html", {"products": Product.objects.all()})


def add_product(request):
    """Создание номенклатуры с проверкой дублей."""
    equipment_types = EquipmentType.objects.all()

    if request.method == "POST":
        name = request.POST.get('name')
        equipment_type_id = request.POST.get('equipment_type')

        if not name or not equipment_type_id:
            return render(request, "inventory/add_product.html", {
                "equipment_types": equipment_types,
                "error": "Заполните все поля",
            })

        if Product.objects.filter(name=name).exists():
            return render(request, "inventory/add_product.html", {
                "equipment_types": equipment_types,
                "error": "Товар с таким названием уже существует",
            })

        Product.objects.create(
            name=name,
            equipment_type=EquipmentType.objects.get(id=equipment_type_id)
        )
        return redirect('add_units')

    return render(request, "inventory/add_product.html", {"equipment_types": equipment_types})


def prepare_list(request):
    """Товары с Unit'ами без серийников (статус IN_STOCK)."""
    products = Product.objects.annotate(
        units_to_prepare=Count('unit', filter=Q(unit__status='IN_STOCK'))
    ).filter(units_to_prepare__gt=0)

    return render(request, "inventory/prepare_list.html", {"products": products})


def prepare_product(request, product_id):
    """Ввод серийника и перевод Unit в READY. Одна кнопка на строку."""
    product = Product.objects.get(id=product_id)
    units = Unit.objects.filter(product=product, status='IN_STOCK')

    if request.method == "POST":
        unit_id = request.POST.get('prepare_unit')
        serial = request.POST.get(f'serial_{unit_id}')

        if serial:
            unit = Unit.objects.get(id=unit_id)
            unit.serial_number = serial
            unit.status = 'READY'
            unit.save()
            messages.success(request, f"Оборудование отпредпродажено: {unit.serial_number}")

        return redirect('prepare_product', product_id=product.id)

    return render(request, "inventory/prepare_product.html", {
        "product": product,
        "units": units,
    })




# Завершение серивиса(смена статуса)
def finish_service(request, unit_id):
    # Защита: принимаем только POST
    if request.method != "POST":
        return redirect('service')

    try:
        unit = Unit.objects.get(id=unit_id)
    except Unit.DoesNotExist:
        messages.error(request, "Устройство не найдено")
        return redirect('service')

    if unit.status == "IN_SERVICE":
        unit.status = "READY"
        unit.save()  # service_resolved_at проставится автоматически
        messages.success(request, f"Ремонт завершён: {unit.serial_number}")
    else:
        messages.error(request, "Данный товар отсутствует в сервисе")

    return redirect('service')


def shipment_registry(request):
    """История отгруженных серийников"""
    units = Unit.objects.filter(
        status = "SOLD",
        reservation__isnull=False).select_related('reservation', 'reservation__client', 'product').order_by('-reservation__created_at')
    return render(request, "inventory/shipment_registry.html", {"units": units})


def add_to_service(request):
    "Добавление в сервис"
    if request.method == "POST":
        unit_id = request.POST.get("unit")
        defect_type_id = request.POST.get("defect_type")  # ← Переименовал для ясности
        service_comment = request.POST.get("service_comment")
        serial_number = request.POST.get('serial_number')

        if not unit_id:
            messages.error(request, "Выберите устройство")
            return redirect('add_to_service')

        try:
            unit = Unit.objects.get(id=unit_id)
        except Unit.DoesNotExist:
            messages.error(request, "Устройство не найдено")
            return redirect('add_to_service')

        # Получаем объект DefectType по ID
        try:
            defect_type = DefectType.objects.get(id=defect_type_id)
        except DefectType.DoesNotExist:
            messages.error(request, "Неисправность не найдена")
            return redirect('add_to_service')

        if serial_number:
            unit.serial_number = serial_number

        if unit.status in ["READY", "IN_STOCK"]:
            unit.status = 'IN_SERVICE'
            unit.defect_type = defect_type  # ← Теперь присваиваем объект
            unit.service_comment = service_comment
            unit.save()
            messages.success(request, f"Оборудование отправлено в сервис: {unit.serial_number or 'без серийника'}")
            return redirect('service')
        else:
            messages.error(request, f"Устройство нельзя отправить в сервис (статус: {unit.status})")
            return redirect('add_to_service')

    defect_types = DefectType.objects.all()
    units = Unit.objects.filter(status__in=['READY', 'IN_STOCK']).select_related('product')
    return render(request, "inventory/add_to_service.html", {"units": units, "defect_types": defect_types})


def create_defect_type(request):
    """Создание нового типа неисправности"""
    if request.method == "POST":
        defect_name = request.POST.get('defect_type')

        # Проверка на пустое поле
        if not defect_name:
            messages.error(request, "Поле не может быть пустым")
            return redirect('create_defect_type')

        # Проверка на дубликат
        if DefectType.objects.filter(name=defect_name).exists():
            messages.error(request, f"Тип неисправности '{defect_name}' уже существует")
            return redirect('create_defect_type')

        # Создаём новую неисправность в справочнике
        DefectType.objects.create(name=defect_name)
        messages.success(request, f"Тип неисправности '{defect_name}' успешно добавлен")
        return redirect('add_to_service')

    # GET-запрос: просто показываем форму
    return render(request, "inventory/create_defect_type.html")


def cancel_reservation(request, reservation_id):
    """Отмена резерва"""
    if request.method == "POST":
        reservation = Reservation.objects.get(id=reservation_id)

        # Проверяем, не отгружен ли уже резерв
        if reservation.is_fulfilled:
            messages.error(request, "Этот резерв уже отгружен")
            return redirect('reservations')

        # Проверяем, не отменен ли уже
        if reservation.is_canceled:
            messages.error(request, "Этот резерв уже отменен")
            return redirect('reservations')

        # Отменяем резерв
        reservation.is_canceled = True
        reservation.save()
        messages.success(request, "Резерв успешно отменён")
        return redirect('reservations')

    # Если пришел GET-запрос, просто перенаправляем на список резервов
    return redirect('reservations')