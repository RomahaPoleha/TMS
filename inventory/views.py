from django.db.models import F, ExpressionWrapper, IntegerField, Value
from django.db.models.functions import Coalesce
from django.db.models import Count, Sum, Q, Subquery, OuterRef
from django.utils import timezone
from inventory.models import Product, Reservation, ReservationItem, Unit, EquipmentType, Client , DefectType, Kit, Assembly, AssemblyUnit
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib import messages

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
            # Если товар не требует серийника — просто списываем нужное количество
            if not item.product.requires_serial:
                units_to_ship = Unit.objects.filter(
                    product=item.product,
                    status="READY"
                )[:item.quantity]

                if len(units_to_ship) < item.quantity:
                    messages.error(
                        request,
                        f"Недостаточно товара {item.product.name} на складе (нужно {item.quantity}, есть {len(units_to_ship)})"
                    )
                    return redirect('ship_reservation', reservation_id=reservation_id)

                for unit in units_to_ship:
                    unit.reservation = reservation
                    unit.status = 'SOLD'
                    unit.save()
            else:
                # Для маркируемых товаров — обычный выбор конкретных Unit'ов
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
                    unit.reservation = reservation
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
    query = request.GET.get('q', '')
    units = Unit.objects.filter(status="IN_SERVICE").order_by('service_received_at')
    if query:
        units = units.filter(serial_number__icontains=query)
    units = units.order_by('service_received_at')
    return render(request, "inventory/service.html", {"units": units, "query": query})


def add_units(request):
    """Приёмка партии: создание N Unit'ов."""
    products = Product.objects.all()

    if request.method == "POST":
        product = Product.objects.get(id=request.POST.get('product'))
        quantity = int(request.POST.get('quantity'))

        # Если товар не требует серийника — сразу READY
        initial_status = "READY" if not product.requires_serial else "IN_STOCK"

        for _ in range(quantity):
            Unit.objects.create(product=product, status=initial_status)

        messages.success(request, f"Принято {quantity} шт. товара '{product.name}'")
        return redirect("dashboard")

    return render(request, "inventory/add_units.html", {"products": products})


def add_product(request):
    """Создание номенклатуры с возможностью создать новый тип оборудования"""
    equipment_types = EquipmentType.objects.all()

    if request.method == "POST":
        name = request.POST.get('name')
        equipment_type_id = request.POST.get('equipment_type')
        new_equipment_type_name = request.POST.get('new_equipment_type')

        # Валидация названия товара
        if not name:
            return render(request, "inventory/add_product.html", {
                "equipment_types": equipment_types,
                "error": "Введите название товара",
            })

        # Проверка дубликата товара
        if Product.objects.filter(name=name).exists():
            return render(request, "inventory/add_product.html", {
                "equipment_types": equipment_types,
                "error": "Товар с таким названием уже существует",
            })

        # Определяем тип оборудования
        equipment_type = None

        # Вариант 1: Выбран существующий тип из списка
        if equipment_type_id:
            try:
                equipment_type = EquipmentType.objects.get(id=equipment_type_id)
            except EquipmentType.DoesNotExist:
                return render(request, "inventory/add_product.html", {
                    "equipment_types": equipment_types,
                    "error": "Выбранный тип оборудования не найден",
                })

        # Вариант 2: Введено название нового типа
        elif new_equipment_type_name:
            # Проверяем, нет ли уже такого типа
            if EquipmentType.objects.filter(name=new_equipment_type_name).exists():
                return render(request, "inventory/add_product.html", {
                    "equipment_types": equipment_types,
                    "error": f"Тип оборудования '{new_equipment_type_name}' уже существует. Выберите его из списка.",
                })
            # Создаём новый тип
            equipment_type = EquipmentType.objects.create(name=new_equipment_type_name)

        # Если не выбрано ни то, ни другое
        else:
            return render(request, "inventory/add_product.html", {
                "equipment_types": equipment_types,
                "error": "Выберите тип оборудования из списка или введите название нового типа",
            })

        # Создаём товар
        requires_serial = request.POST.get('requires_serial') == 'on'
        Product.objects.create(
            name=name,
            equipment_type=equipment_type,
            requires_serial=requires_serial
        )
        messages.success(request, f"Товар '{name}' успешно создан")
        return redirect('add_units')

    # GET-запрос: показываем форму
    return render(request, "inventory/add_product.html", {"equipment_types": equipment_types})


def prepare_list(request):
    """Товары с Unit'ами без серийников (только маркируемые)."""
    products = Product.objects.annotate(
        units_to_prepare=Count('unit', filter=Q(unit__status='IN_STOCK'))
    ).filter(units_to_prepare__gt=0, requires_serial=True)  # ← добавили фильтр
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
    """Перенос оборудования из сервиса (ремонт закончен)"""
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
    """История отгруженных серийников с возможностью поиска."""
    query = request.GET.get('q', '')
    units = Unit.objects.filter(
        status="SOLD",
        reservation__isnull=False
    ).select_related('reservation', 'reservation__client', 'product').order_by('-reservation__created_at')

    if query:
        units = units.filter(serial_number__icontains=query)

    return render(request, "inventory/shipment_registry.html", {"units": units, "query": query})


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





def edit_unit(request, unit_id):
    """Редактирование позиции на складе"""
    # 1. Получаем конкретный объект.
    # get_object_or_404 сам выдаст красивую ошибку 404, если такого ID нет в базе.
    unit = get_object_or_404(Unit, id=unit_id)
    products = Product.objects.all()
    # 2. Проверка статуса (одинаковая для GET и POST, поэтому выносим наверх)
    if unit.status in ["SOLD", "IN_SERVICE"]:
        messages.error(request, "Редактировать проданное или находящееся в ремонте устройство нельзя")
        return redirect('unit_list')

    if request.method == "POST":
        serial_number = request.POST.get('serial_number')
        product_id = request.POST.get('product')

        # Валидация
        if not serial_number:
            messages.error(request, "Серийный номер не может быть пустым")
            return redirect('edit_unit', unit_id=unit_id)

        if not product_id:
            messages.error(request, "Выберите товар")
            return redirect('edit_unit', unit_id=unit_id)

        # Получаем объект Product
        try:
            product = Product.objects.get(id=product_id)
        except Product.DoesNotExist:
            messages.error(request, "Товар не найден")
            return redirect('edit_unit', unit_id=unit_id)

        # Обновляем поля
        unit.serial_number = serial_number
        unit.product = product
        unit.save()

        messages.success(request, "Данные успешно изменены")
        return redirect('unit_list')

    # 4. GET-запрос: просто показываем форму
    return render(request, "inventory/edit_unit.html", {
        "unit": unit,
        "products": products
    })


def delete_unit(request, unit_id):
    """Удаление позиции со склада"""
    unit = get_object_or_404(Unit, id=unit_id)
    # Проверка безопасности
    if unit.status in ["SOLD", "IN_SERVICE"]:
        messages.error(request, "Удалить проданное или находящееся в ремонте устройство нельзя")
        return redirect('unit_list')
    # Обработка POST-запроса (удаление)
    if request.method == "POST":
        unit.delete()
        messages.success(request, "Устройство успешно удалено")
        return redirect('unit_list')
    # Защита от GET-запроса (просто возвращаем на список)
    return redirect('unit_list')


def unit_list(request):
    """Список всех устройств с возможностью поиска."""
    query = request.GET.get('q', '')
    units = Unit.objects.all().order_by('-created_at').select_related('product')

    if query:
        units = units.filter(serial_number__icontains=query)

    return render(request, "inventory/unit_list.html", {"units": units, "query": query})

def search_unit(request):
    query = request.GET.get('q', '')
    if not query:
        return redirect('dashboard')
    units = Unit.objects.filter(serial_number__icontains=query).select_related('product')

    return render(request, 'inventory/search_unit.html', {
        'query': query,
        'units': units
    })





def create_assembly(request):
    """Сборка комплекта из устройств (готовых и неготовых)"""
    kits = Kit.objects.filter(is_active=True)
    selected_kit = None
    components_data = []

    if request.method == "POST":
        kit_id = request.POST.get('kit')
        if not kit_id:
            messages.error(request, "Выберите комплект")
            return redirect('create_assembly')

        selected_kit = get_object_or_404(Kit, id=kit_id)
        components = selected_kit.components.all()

        assembly_units_to_create = []
        used_unit_ids = set()  # ← для проверки дубликатов
        error_found = False

        for component in components:
            selected_unit_ids = request.POST.getlist(f'component_{component.id}')

            # Фильтруем пустые значения
            selected_unit_ids = [uid for uid in selected_unit_ids if uid]

            # Проверка: выбрано ли правильное количество РЕАЛЬНЫХ устройств
            if len(selected_unit_ids) != component.quantity:
                messages.error(
                    request,
                    f"Для '{component.product.name}' нужно выбрать ровно {component.quantity} шт. (выбрано {len(selected_unit_ids)})"
                )
                error_found = True
                break

            for unit_id in selected_unit_ids:
                # Проверка на дубликаты
                if unit_id in used_unit_ids:
                    messages.error(
                        request,
                        f"Устройство ID {unit_id} выбрано несколько раз. Выберите разные устройства."
                    )
                    error_found = True
                    break
                used_unit_ids.add(unit_id)

                try:
                    unit = Unit.objects.get(id=unit_id)

                    if unit.status not in ["IN_STOCK", "READY"]:
                        messages.error(
                            request,
                            f"Устройство {unit.serial_number or unit.id} имеет статус {unit.status}, а нужен IN_STOCK или READY"
                        )
                        error_found = True
                        break

                    if unit.status == "IN_STOCK":
                        if unit.product.requires_serial:
                            serial = request.POST.get(f'serial_{unit_id}')
                            if not serial:
                                messages.error(request, f"Для устройства {unit.id} нужно ввести серийный номер")
                                error_found = True
                                break
                            unit.serial_number = serial
                        unit.status = "READY"
                        unit.save()

                    assembly_units_to_create.append(unit)
                except Unit.DoesNotExist:
                    messages.error(request, f"Устройство ID {unit_id} не найдено")
                    error_found = True
                    break

            if error_found:
                break

        if not error_found and assembly_units_to_create:
            assembly = Assembly.objects.create(kit=selected_kit, status="READY")

            for unit in assembly_units_to_create:
                unit.status = "ASSEMBLED"
                unit.save()
                AssemblyUnit.objects.create(assembly=assembly, unit=unit)

            messages.success(request, f"Сборка #{assembly.id} успешно создана!")
            return redirect('dashboard')

        # Если ошибка не была показана, но сборка не создана — редирект с ошибкой
        if error_found:
            return redirect('create_assembly')

    # GET-запрос: подготовка данных для формы
    kit_id = request.GET.get('kit')
    if kit_id:
        selected_kit = get_object_or_404(Kit, id=kit_id)
        for component in selected_kit.components.all():
            available_units = Unit.objects.filter(
                product=component.product,
                status__in=["IN_STOCK", "READY"]
            )
            components_data.append({
                "component": component,
                "available_units": available_units,
                "quantity_range": range(component.quantity)
            })

    return render(request, "inventory/create_assembly.html", {
        "kits": kits,
        "selected_kit": selected_kit,
        "components_data": components_data
    })