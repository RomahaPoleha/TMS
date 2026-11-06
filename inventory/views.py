from django.db.models import F, ExpressionWrapper, IntegerField, Value
from django.db.models.functions import Coalesce
from django.db.models import Count, Sum, Q, Subquery, OuterRef
from django.utils import timezone
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from inventory.models import Product, Reservation, ReservationLine, Unit, EquipmentType, Client, DefectType, Kit, Assembly, AssemblyUnit, Return
from inventory.services import ReservationService, AssemblyService, ServiceService, ShipmentService, ReturnService
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib import messages


def main_dashboard(request: HttpRequest) -> HttpResponse:
    """Сводная таблица по товарам и комплектам."""

    # 1. Подсчёт по товарам
    products = Product.objects.annotate(
        total_count=Count("unit", filter=Q(unit__status__in=["IN_STOCK", "READY", "IN_SERVICE"])),
        ready_count=Count("unit", filter=Q(unit__status="READY")),
        service_count=Count("unit", filter=Q(unit__status="IN_SERVICE")),
        reserved_count=Coalesce(
            Subquery(
                ReservationLine.objects.filter(
                    type="product",
                    product=OuterRef('pk'),
                    reservation__is_fulfilled=False,
                    reservation__is_canceled=False
                ).values("product").annotate(total=Sum('quantity')).values('total')[:1]
            ),
            Value(0)
        ),
        available_count=ExpressionWrapper(
            F('total_count') - Coalesce(F('reserved_count'), Value(0)) - Coalesce(F('service_count'), Value(0)),
            output_field=IntegerField()
        )
    ).order_by('equipment_type__name', 'name')

    # 2. Подсчёт по комплексам
    kits = Kit.objects.filter(is_active=True).annotate(
        total_assemblies=Count("assembly", filter=Q(assembly__status__in=["READY", "RESERVED", "IN_SERVICE"])),
        ready_assemblies=Count("assembly", filter=Q(assembly__status="READY")),
        reserved_assemblies=Count("assembly", filter=Q(assembly__status="RESERVED")),
        service_assemblies=Count("assembly", filter=Q(assembly__status="IN_SERVICE")),
        sold_assemblies=Count("assembly", filter=Q(assembly__status="SOLD")),
        available_assemblies=ExpressionWrapper(
            F('total_assemblies') - F('reserved_assemblies') - F('service_assemblies'),
            output_field=IntegerField()
        )
    ).order_by('equipment_type__name', 'name').distinct()

    # 3. Передаём обе переменные в шаблон
    return render(request, "inventory/dashboard.html", {
        "products": products,
        "kits": kits,
    })


@transaction.atomic
def ship_reservation(request: HttpRequest, reservation_id: int) -> HttpResponse:
    """Отгрузка резервов клиенту (товары + сборки)"""
    reservation = Reservation.objects.get(id=reservation_id)
    reservation_lines = reservation.lines.select_related("product", "assembly__kit").all()

    if request.method == "POST":
        # Собираем выбранные Units для маркируемых товаров
        selected_units = {}
        for line in reservation_lines.filter(type="product"):
            if line.product.requires_serial:
                selected_ids = request.POST.getlist(f'unit_{line.id}')
                if len(selected_ids) != line.quantity:
                    messages.error(
                        request,
                        f"Для товара {line.product} нужно выбрать {line.quantity} шт., выбрано {len(selected_ids)}"
                    )
                    return redirect('ship_reservation', reservation_id=reservation_id)
                units = list(Unit.objects.filter(id__in=selected_ids))
                selected_units[line] = units

        try:
            ReservationService.ship_reservation(reservation, selected_units)
        except ValidationError as e:
            messages.error(request, str(e))
            return redirect('ship_reservation', reservation_id=reservation_id)

        messages.success(request, "Резерв успешно отгружен")
        return redirect('reservations')

    # GET-запрос: собираем данные для шаблона
    items_with_units = []
    for line in reservation_lines.filter(type="product"):
        available_units = Unit.objects.filter(
            product=line.product,
            status="READY"
        )
        items_with_units.append({
            "item": line,
            "available_units": available_units,
            "quantity_range": range(line.quantity),
        })

    assemblies_with_info = []
    for line in reservation_lines.filter(type="assembly"):
        assemblies_with_info.append({
            "line": line,
            "assembly": line.assembly,
        })

    return render(request, "inventory/ship_reservation.html", {
        "reservation": reservation,
        "items_with_units": items_with_units,
        "assemblies_with_info": assemblies_with_info,
    })

def reservation_history(request: HttpRequest) -> HttpResponse:
    """История отгрузок"""
    reservations = Reservation.objects.filter(is_fulfilled=True).order_by('-created_at')
    return render(request, "inventory/reservations_history.html", {"reservations": reservations})


def active_reservations(request: HttpRequest) -> HttpResponse:
    """Список неисполненных резервов (товары + сборки в одном месте)."""
    reservations = Reservation.objects.filter(
        is_fulfilled=False,
        is_canceled=False
    ).order_by('-created_at')

    # Резервированные сборки через ReservationLine
    reserved_assembly_lines = ReservationLine.objects.filter(
        type="assembly",
        reservation__is_fulfilled=False,
        reservation__is_canceled=False
    ).select_related("assembly__kit", "reservation").order_by("reservation__id", "assembly__id")

    return render(request, "inventory/reservations.html", {
        "reservations": reservations,
        "reserved_assembly_lines": reserved_assembly_lines,
    })


def create_reservation(request: HttpRequest) -> HttpResponse:
    clients = Client.objects.all()
    products = Product.objects.filter(is_component=False)
    available_assemblies = Assembly.objects.filter(
        status="READY",
    ).select_related('kit')

    if request.method == "POST":
        client_id = request.POST.get('client')
        new_client_name = request.POST.get('new_client_name', '')

        # 1. Определяем клиента
        client = None
        if client_id:
            try:
                client = Client.objects.get(id=client_id)
            except Client.DoesNotExist:
                return render(request, "inventory/create_reservation.html", {
                    "clients": clients, "products": products,
                    "available_assemblies": available_assemblies,
                    "error": "Выбранный клиент не найден"
                })
        elif new_client_name:
            if Client.objects.filter(name=new_client_name).exists():
                return render(request, "inventory/create_reservation.html", {
                    "clients": clients, "products": products,
                    "available_assemblies": available_assemblies,
                    "error": f"Клиент '{new_client_name}' уже существует. Выберите его из списка."
                })
            client = Client.objects.create(name=new_client_name)
        else:
            return render(request, "inventory/create_reservation.html", {
                "clients": clients, "products": products,
                "available_assemblies": available_assemblies,
                "error": "Выберите клиента из списка или введите имя нового"
            })

        # 2. Собираем позиции из POST
        product_lines = []
        assembly_lines = []

        for key, value in request.POST.items():
            if (key.startswith('item_') or key.startswith('product_')) and value:
                idx = key.split('_')[1]
                quantity_str = request.POST.get(f'quantity_{idx}')

                if quantity_str:
                    try:
                        quantity = int(quantity_str)
                        if quantity > 0:
                            if value.startswith('p_'):
                                product_id = value[2:]
                                product = Product.objects.get(id=product_id)
                                product_lines.append((product, quantity))
                            elif value.startswith('a_'):
                                assembly_id = value[2:]
                                assembly = Assembly.objects.get(id=assembly_id)
                                assembly_lines.append((assembly, quantity))
                    except (ValueError, Product.DoesNotExist, Assembly.DoesNotExist):
                        pass

        if not product_lines and not assembly_lines:
            return render(request, "inventory/create_reservation.html", {
                "clients": clients, "products": products,
                "available_assemblies": available_assemblies,
                "error": "Добавьте хотя бы 1 позицию."
            })

        # 3. Создаём резерв через сервис
        try:
            ReservationService.create_reservation(
                client=client,
                product_lines=product_lines,
                assembly_lines=assembly_lines,
            )
        except ValidationError as e:
            messages.error(request, str(e))
            return redirect('create_reservation')

        messages.success(request, f"Резерв для '{client.name}' успешно создан!")
        return redirect('reservations')

    # GET-запрос
    return render(request, "inventory/create_reservation.html", {
        "clients": clients,
        "products": products,
        "available_assemblies": available_assemblies
    })

def active_service(request: HttpRequest) -> HttpResponse:
    """Устройства в ремонте, отсортированные по дате приёма."""
    query = request.GET.get('q', '')
    units = Unit.objects.filter(status="IN_SERVICE").order_by('service_received_at')
    if query:
        units = units.filter(serial_number__icontains=query)
    units = units.order_by('service_received_at')
    return render(request, "inventory/service.html", {"units": units, "query": query})


def add_units(request: HttpRequest) -> HttpResponse:
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


def add_product(request: HttpRequest) -> HttpResponse:
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


def prepare_list(request: HttpRequest) -> HttpResponse:
    """Товары с Unit'ами без серийников (только маркируемые)."""
    products = Product.objects.annotate(
        units_to_prepare=Count('unit', filter=Q(unit__status='IN_STOCK'))
    ).filter(units_to_prepare__gt=0, requires_serial=True)  # ← добавили фильтр
    return render(request, "inventory/prepare_list.html", {"products": products})


def prepare_product(request: HttpRequest, product_id: int) -> HttpResponse:
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




# Завершение сервиса (смена статуса)
@transaction.atomic
def finish_service(request: HttpRequest, unit_id: int) -> HttpResponse:
    """Перенос оборудования из сервиса (ремонт закончен)"""
    if request.method != "POST":
        return redirect('service')

    try:
        unit = Unit.objects.get(id=unit_id)
    except Unit.DoesNotExist:
        messages.error(request, "Устройство не найдено")
        return redirect('service')

    try:
        ServiceService.finish_unit_service(unit)
        messages.success(request, f"Ремонт завершён: {unit.serial_number}")
    except ValidationError as e:
        messages.error(request, str(e))

    return redirect('service')


def shipment_registry(request: HttpRequest) -> HttpResponse:
    """История отгруженных серийников с возможностью поиска."""
    query = request.GET.get('q', '')
    units = Unit.objects.filter(
        status="SOLD",
        reservation__isnull=False
    ).select_related('reservation', 'reservation__client', 'product').prefetch_related('assemblyunit_set__assembly__kit').order_by('-reservation__created_at')

    if query:
        units = units.filter(
            Q(serial_number__icontains=query) |
            Q(reservation__client__name__icontains=query) |
            Q(product__name__icontains=query)
        )
        assembly_unit_ids = AssemblyUnit.objects.filter(
            assembly__serial_number__icontains=query
        ).values_list('unit_id', flat=True)
        if assembly_unit_ids:
            units = units | Unit.objects.filter(
                id__in=assembly_unit_ids,
                status="SOLD",
                reservation__isnull=False
            ).select_related('reservation', 'reservation__client', 'product').prefetch_related('assemblyunit_set__assembly__kit')

    return render(request, "inventory/shipment_registry.html", {"units": units, "query": query})


def return_list(request: HttpRequest) -> HttpResponse:
    """Список возвратов"""
    returns = Return.objects.select_related(
        "client", "unit__product", "assembly__kit", "defect_type"
    ).order_by("-returned_at")
    return render(request, "inventory/return_list.html", {"returns": returns})


@transaction.atomic
def create_return(request: HttpRequest) -> HttpResponse:
    """Оформление возврата (поиск по серийному номеру)"""
    if request.method == "POST":
        serial_number = request.POST.get("serial_number", "").strip()
        disposition = request.POST.get("disposition")
        reason = request.POST.get("reason", "").strip()
        comment = request.POST.get("comment", "").strip()
        defect_type_id = request.POST.get("defect_type")

        if not serial_number:
            messages.error(request, "Введите серийный номер")
            return redirect("create_return")

        if not reason:
            messages.error(request, "Укажите причину возврата")
            return redirect("create_return")

        if not disposition:
            messages.error(request, "Выберите решение")
            return redirect("create_return")

        defect_type = None
        if defect_type_id:
            try:
                defect_type = DefectType.objects.get(id=defect_type_id)
            except DefectType.DoesNotExist:
                messages.error(request, "Неисправность не найдена")
                return redirect("create_return")

        # Определяем клиента: ищем по проданному устройству/сборке
        client = None

        # Сначала пробуем найти устройство
        try:
            unit = ReturnService.find_sold_unit(serial_number)
            # Клиент из резерва
            if unit.reservation and unit.reservation.client:
                client = unit.reservation.client
            elif not client:
                # Ищем клиента по имени из POST
                client_name = request.POST.get("client_name", "").strip()
                if client_name:
                    client, _ = Client.objects.get_or_create(name=client_name)
                else:
                    messages.error(request, "Укажите клиента")
                    return redirect("create_return")

            ret = ReturnService.process_unit_return(
                unit=unit,
                client=client,
                reason=reason,
                disposition=disposition,
                defect_type=defect_type,
                comment=comment,
            )
            messages.success(request, f"Возврат оформлен: {unit.serial_number}")
            return redirect("return_list")

        except ValidationError:
            pass

        # Пробуем найти сборку
        try:
            assembly = ReturnService.find_sold_assembly(serial_number)
            if assembly.units.exists():
                first_unit = assembly.units.first().unit
                if first_unit.reservation and first_unit.reservation.client:
                    client = first_unit.reservation.client

            if not client:
                client_name = request.POST.get("client_name", "").strip()
                if client_name:
                    client, _ = Client.objects.get_or_create(name=client_name)
                else:
                    messages.error(request, "Укажите клиента")
                    return redirect("create_return")

            ret = ReturnService.process_assembly_return(
                assembly=assembly,
                client=client,
                reason=reason,
                disposition=disposition,
                defect_type=defect_type,
                comment=comment,
            )
            messages.success(request, f"Возврат оформлен: сборка {assembly.serial_number or assembly.id}")
            return redirect("return_list")

        except ValidationError as e:
            messages.error(request, str(e))
            return redirect("create_return")

    # GET
    defect_types = DefectType.objects.all()
    clients = Client.objects.all()
    return render(request, "inventory/create_return.html", {
        "defect_types": defect_types,
        "clients": clients,
    })
    query = request.GET.get('q', '')
    units = Unit.objects.filter(
        status="SOLD",
        reservation__isnull=False
    ).select_related('reservation', 'reservation__client', 'product').prefetch_related('assemblyunit_set__assembly__kit').order_by('-reservation__created_at')

    if query:
        units = units.filter(
            Q(serial_number__icontains=query) |
            Q(reservation__client__name__icontains=query) |
            Q(product__name__icontains=query)
        )
        # Также ищем по серийникам сборок
        assembly_unit_ids = AssemblyUnit.objects.filter(
            assembly__serial_number__icontains=query
        ).values_list('unit_id', flat=True)
        if assembly_unit_ids:
            units = units | Unit.objects.filter(
                id__in=assembly_unit_ids,
                status="SOLD",
                reservation__isnull=False
            ).select_related('reservation', 'reservation__client', 'product').prefetch_related('assemblyunit_set__assembly__kit')

    return render(request, "inventory/shipment_registry.html", {"units": units, "query": query})


@transaction.atomic
def add_to_service(request: HttpRequest) -> HttpResponse:
    """Добавление в сервис"""
    if request.method == "POST":
        unit_id = request.POST.get("unit")
        defect_type_id = request.POST.get("defect_type")
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

        try:
            defect_type = DefectType.objects.get(id=defect_type_id)
        except DefectType.DoesNotExist:
            messages.error(request, "Неисправность не найдена")
            return redirect('add_to_service')

        try:
            ServiceService.send_unit_to_service(
                unit=unit,
                defect_type=defect_type,
                comment=service_comment,
                serial_number=serial_number,
            )
            messages.success(request, f"Оборудование отправлено в сервис: {unit.serial_number or 'без серийника'}")
            return redirect('service')
        except ValidationError as e:
            messages.error(request, str(e))
            return redirect('add_to_service')

    defect_types = DefectType.objects.all()
    units = Unit.objects.filter(status__in=['READY', 'IN_STOCK']).select_related('product')
    return render(request, "inventory/add_to_service.html", {"units": units, "defect_types": defect_types})


def create_defect_type(request: HttpRequest) -> HttpResponse:
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


@transaction.atomic
def cancel_reservation(request: HttpRequest, reservation_id: int) -> HttpResponse:
    """Отмена резерва (товары + сборки)"""
    if request.method == "POST":
        reservation = Reservation.objects.get(id=reservation_id)

        try:
            ReservationService.cancel_reservation(reservation)
        except ValidationError as e:
            messages.error(request, str(e))
            return redirect('reservations')

        messages.success(request, "Резерв успешно отменён")
        return redirect('reservations')

    return redirect('reservations')

def edit_unit(request: HttpRequest, unit_id: int) -> HttpResponse:
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


@transaction.atomic
def delete_unit(request: HttpRequest, unit_id: int) -> HttpResponse:
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


def unit_list(request: HttpRequest) -> HttpResponse:
    """Список всех устройств с возможностью поиска."""
    query = request.GET.get('q', '')
    units = Unit.objects.all().order_by('-created_at').select_related('product')

    if query:
        units = units.filter(serial_number__icontains=query)

    return render(request, "inventory/unit_list.html", {"units": units, "query": query})

def search_unit(request: HttpRequest) -> HttpResponse:
    """Поиск устройства по серийному номеру"""
    query = request.GET.get('q', '')
    if not query:
        return redirect('dashboard')
    units = Unit.objects.filter(serial_number__icontains=query).select_related('product')
    return render(request, 'inventory/search_unit.html', {
        'query': query,
        'units': units
    })
def kit_potential(request: HttpRequest, kit_id: int) -> HttpResponse:
    """Детальный расчёт потенциала комплекта"""
    kit = get_object_or_404(Kit, id=kit_id)
    available_units = AssemblyService.get_available_units_for_kit(kit)
    potential = AssemblyService.calculate_potential_count(kit)

    from django.db.models import Sum
    from inventory.models import ReservationLine

    components_info = []
    bottleneck = None
    min_possible = float('inf')

    for component in kit.components.all():
        units_qs = available_units[component]
        total_available = units_qs.count()

        # Зарезервировано как компоненты (ReservationLine type=product)
        reserved_as_component = ReservationLine.objects.filter(
            type="product",
            product=component.product,
            reservation__is_fulfilled=False,
            reservation__is_canceled=False
        ).aggregate(total=Sum('quantity'))['total'] or 0

        # Резервы готовых сборок этого комплекта (съедают компоненты)
        reserved_assemblies = Assembly.objects.filter(kit=kit, status="RESERVED").count()
        ready_assemblies = Assembly.objects.filter(kit=kit, status="READY").count()
        deficit = max(0, reserved_assemblies - ready_assemblies)
        components_needed_for_deficit = deficit * component.quantity

        free_components = total_available - reserved_as_component - components_needed_for_deficit
        possible = free_components // component.quantity if component.quantity > 0 else 0

        if possible < min_possible:
            min_possible = possible
            bottleneck = component

        components_info.append({
            "component": component,
            "total_available": total_available,
            "reserved_as_component": reserved_as_component,
            "components_needed_for_deficit": components_needed_for_deficit,
            "free_components": free_components,
            "possible": possible,
        })

    return render(request, "inventory/kit_potential.html", {
        "kit": kit,
        "potential": potential,
        "components_info": components_info,
        "bottleneck": bottleneck,
    })
    query = request.GET.get('q', '')
    if not query:
        return redirect('dashboard')
    units = Unit.objects.filter(serial_number__icontains=query).select_related('product')

    return render(request, 'inventory/search_unit.html', {
        'query': query,
        'units': units
    })





@transaction.atomic
def create_assembly(request: HttpRequest) -> HttpResponse:
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

        # Собираем Units по компонентам
        units_by_component = {}
        used_unit_ids = set()
        error_found = False

        for component in components:
            if component.product.requires_serial:
                selected_unit_ids = request.POST.getlist(f'component_{component.id}')
                selected_unit_ids = [uid for uid in selected_unit_ids if uid]

                if len(selected_unit_ids) != component.quantity:
                    messages.error(
                        request,
                        f"Для '{component.product.name}' нужно выбрать ровно {component.quantity} шт. (выбрано {len(selected_unit_ids)})"
                    )
                    error_found = True
                    break

                units = []
                for idx, unit_id in enumerate(selected_unit_ids):
                    if unit_id in used_unit_ids:
                        messages.error(request, f"Устройство ID {unit_id} выбрано несколько раз")
                        error_found = True
                        break
                    used_unit_ids.add(unit_id)

                    try:
                        unit = Unit.objects.get(id=unit_id)
                        if unit.status not in ("IN_STOCK", "READY"):
                            messages.error(request, f"Unit #{unit.id} статус {unit.status}, нужен IN_STOCK или READY")
                            error_found = True
                            break
                        if unit.product_id != component.product_id:
                            messages.error(request, f"Unit #{unit.id} — {unit.product.name}, а нужен {component.product.name}")
                            error_found = True
                            break
                        if unit.status == "IN_STOCK":
                            serial = request.POST.get(f'serial_{component.id}_{idx}')
                            if not serial:
                                messages.error(request, f"Для устройства {unit.id} нужен серийный номер")
                                error_found = True
                                break
                            if Unit.objects.filter(serial_number=serial).exclude(id=unit.id).exists():
                                messages.error(request, f"Серийный номер '{serial}' уже занят")
                                error_found = True
                                break
                            unit.serial_number = serial
                            unit.status = "READY"
                            unit.save(update_fields=["serial_number", "status", "updated_at"])
                        units.append(unit)
                    except Unit.DoesNotExist:
                        messages.error(request, f"Устройство ID {unit_id} не найдено")
                        error_found = True
                        break

                if error_found:
                    break
                units_by_component[component] = units
            else:
                # Немаркируемые: берём автоматически
                available_units = Unit.objects.filter(
                    product=component.product,
                    status__in=["IN_STOCK", "READY"]
                ).exclude(id__in=used_unit_ids)[:component.quantity]

                if len(available_units) < component.quantity:
                    messages.error(request, f"Недостаточно {component.product.name} на складе")
                    error_found = True
                    break

                for unit in available_units:
                    used_unit_ids.add(unit.id)
                units_by_component[component] = list(available_units)

        if not error_found:
            assembly_serial = request.POST.get('assembly_serial', '').strip()
            if not assembly_serial:
                messages.error(request, "Введите серийный номер комплекта")
                return redirect('create_assembly')

            try:
                AssemblyService.create_assembly(selected_kit, units_by_component, assembly_serial)
            except ValidationError as e:
                messages.error(request, str(e))
                return redirect('create_assembly')

            messages.success(request, "Сборка успешно создана!")
            return redirect('dashboard')

        return redirect('create_assembly')

    # GET-запрос
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


def assembly_detail(request: HttpRequest, assembly_id: int) -> HttpResponse:
    """Детальная информация о конкретной сборке (состав)"""
    assembly = get_object_or_404(
        Assembly.objects.select_related('kit'),
        id=assembly_id
    )
    # Получаем все устройства, которые входят в эту сборку
    assembly_units = AssemblyUnit.objects.select_related('unit__product').filter(assembly=assembly)

    return render(request, "inventory/assembly_detail.html", {
        "assembly": assembly,
        "assembly_units": assembly_units
    })

@transaction.atomic
def disassemble_assembly(request: HttpRequest, assembly_id: int) -> HttpResponse:
    """Разборка сборки (READY → Units на склад)"""
    if request.method == "POST":
        assembly = get_object_or_404(Assembly, id=assembly_id)
        try:
            units = AssemblyService.disassembly(assembly)
            messages.success(request, f"Сборка разобрана: {len(units)} устройств возвращено на склад")
        except ValidationError as e:
            messages.error(request, str(e))
    return redirect('dashboard')


@transaction.atomic
def finish_assembly_service(request: HttpRequest, assembly_id: int) -> HttpResponse:
    """Завершение ремонта сборки (IN_SERVICE → READY)"""
    if request.method == "POST":
        assembly = get_object_or_404(Assembly, id=assembly_id, status="IN_SERVICE")
        try:
            ServiceService.finish_assembly_service(assembly)
            messages.success(request, f"Ремонт сборки #{assembly.id} завершён")
        except ValidationError as e:
            messages.error(request, str(e))
    return redirect('dashboard')


@transaction.atomic
def assembly_to_service(request: HttpRequest, assembly_id: int) -> HttpResponse:
    """Отправка сборки в сервис (READY/RESERVED → IN_SERVICE)"""
    assembly = get_object_or_404(Assembly, id=assembly_id)

    if request.method == "POST":
        defect_type_id = request.POST.get("defect_type")
        comment = request.POST.get("service_comment", "")
        task_url = request.POST.get("task_url", "")

        try:
            defect_type = DefectType.objects.get(id=defect_type_id)
        except DefectType.DoesNotExist:
            messages.error(request, "Неисправность не найдена")
            return redirect('assembly_detail', assembly_id=assembly_id)

        try:
            ServiceService.send_assembly_to_service(assembly, defect_type, comment, task_url)
            messages.success(request, f"Сборка #{assembly.id} отправлена в сервис")
        except ValidationError as e:
            messages.error(request, str(e))

        return redirect('dashboard')

    defect_types = DefectType.objects.all()
    return render(request, "inventory/assembly_to_service.html", {
        "assembly": assembly,
        "defect_types": defect_types,
    })


@transaction.atomic
def ship_assembly(request: HttpRequest, assembly_id: int) -> HttpResponse:
    """Отгрузка сборки клиенту (статус → SOLD)"""
    if request.method == "POST":
        try:
            assembly = Assembly.objects.get(id=assembly_id, status="RESERVED")
            line = assembly.reservation_lines.first()
            if line and line.reservation:
                reservation = line.reservation
                ReservationService.ship_reservation(reservation, selected_units={})
            else:
                assembly.status = "SOLD"
                assembly.save(update_fields=["status", "updated_at"])
                ReservationLine.objects.filter(type="assembly", assembly=assembly).delete()
            messages.success(request, f"Комплект '{assembly.serial_number or f'#{assembly.id}'}' отгружен клиенту")
        except (Assembly.DoesNotExist, ValidationError) as e:
            messages.error(request, str(e) if isinstance(e, ValidationError) else "Сборка не найдена или уже не в резерве")
    return redirect('reservations')


@transaction.atomic
def cancel_assembly_reservation(request: HttpRequest, assembly_id: int) -> HttpResponse:
    """Отмена резерва сборки (статус → READY)"""
    if request.method == "POST":
        try:
            assembly = Assembly.objects.get(id=assembly_id, status="RESERVED")
            ReservationService.cancel_assembly_reservation(assembly)
            messages.success(request, f"Резерв комплекта '{assembly.serial_number or f'#{assembly.id}'}' отменён")
        except (Assembly.DoesNotExist, ValidationError) as e:
            messages.error(request, str(e) if isinstance(e, ValidationError) else "Сборка не найдена или уже не в резерве")
    return redirect('reservations')