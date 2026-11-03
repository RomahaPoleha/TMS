"""Сервисы для работы с сборками (Assembly)"""
from django.db import transaction
from django.core.exceptions import ValidationError

from inventory.models import Kit, Unit, Assembly, AssemblyUnit, KitComponent


class AssemblyService:
    """Бизнес-логика сборки и разборки комплектов"""

    @staticmethod
    @transaction.atomic
    def create_assembly(kit: Kit, units_by_component: dict, assembly_serial: str) -> Assembly:
        """
        Создаёт сборку из выбранных Units.

        Args:
            kit: Комплект (рецепт)
            units_by_component: {KitComponent: [Unit, ...]} — выбранные устройства по компонентам
            assembly_serial: Серийный номер сборки

        Returns:
            Созданная Assembly

        Raises:
            ValidationError: если не хватает Units или дубликат серийника
        """
        if not assembly_serial:
            raise ValidationError("Серийный номер комплекта обязателен")

        if Assembly.objects.filter(serial_number=assembly_serial).exists():
            raise ValidationError(f"Комплект с серийным номером '{assembly_serial}' уже существует")

        # Проверяем, что все Units подходят и свободны
        used_unit_ids = set()
        for component, units in units_by_component.items():
            if len(units) != component.quantity:
                raise ValidationError(
                    f"Для '{component.product.name}' нужно {component.quantity} шт., передано {len(units)}"
                )
            for unit in units:
                if unit.id in used_unit_ids:
                    raise ValidationError(f"Unit #{unit.id} указан дважды")
                if unit.status not in ("IN_STOCK", "READY"):
                    raise ValidationError(f"Unit #{unit.id} ({unit.serial_number or 'б/н'}) имеет статус {unit.status}, нужен IN_STOCK или READY")
                if unit.product_id != component.product_id:
                    raise ValidationError(f"Unit #{unit.id} — {unit.product.name}, а нужен {component.product.name}")
                used_unit_ids.add(unit.id)

        # Создаём сборку
        assembly = Assembly.objects.create(
            kit=kit,
            serial_number=assembly_serial,
            status="READY",
        )

        # Привязываем Units
        for units in units_by_component.values():
            for unit in units:
                unit.status = "ASSEMBLED"
                unit.save(update_fields=["status", "updated_at"])
                AssemblyUnit.objects.create(assembly=assembly, unit=unit)

        return assembly

    @staticmethod
    @transaction.atomic
    def disassembly(assembly: Assembly) -> list[Unit]:
        """
        Разбирает сборку, возвращает Units на склад (IN_STOCK).

        Args:
            assembly: Сборка для разборки (должна быть READY или RESERVED)

        Returns:
            Список освобождённых Units

        Raises:
            ValidationError: если сборка в SOLD или IN_SERVICE
        """
        if assembly.status in ("SOLD", "IN_SERVICE"):
            raise ValidationError(f"Нельзя разобрать сборку в статусе {assembly.status}")

        # Получаем Units перед удалением связей
        unit_ids = list(assembly.units.values_list("unit_id", flat=True))

        # Удаляем связи AssemblyUnit
        AssemblyUnit.objects.filter(assembly=assembly).delete()

        # Возвращаем Units в IN_STOCK
        Unit.objects.filter(id__in=unit_ids).update(status="IN_STOCK")

        # Удаляем саму сборку
        assembly.delete()

        return list(Unit.objects.filter(id__in=unit_ids))

    @staticmethod
    def get_available_units_for_kit(kit: Kit) -> dict:
        """
        Возвращает доступные Units для каждого компонента комплекта.

        Returns:
            {KitComponent: QuerySet[Unit]}
        """
        result = {}
        for component in kit.components.all():
            qs = Unit.objects.filter(
                product=component.product,
                status__in=["IN_STOCK", "READY"]
            ).order_by("created_at")
            result[component] = qs
        return result

    @staticmethod
    def calculate_potential_count(kit: Kit) -> int:
        """
        Сколько комплектов можно собрать прямо сейчас из свободных компонентов.
        Учитывает резервы через ReservationLine.
        """
        from django.db.models import Sum
        from inventory.models import ReservationLine

        min_count = float("inf")

        for component in kit.components.all():
            # Все доступные Units
            total_available = Unit.objects.filter(
                product=component.product,
                status__in=["IN_STOCK", "READY"]
            ).count()

            # Зарезервированные как компоненты (ReservationLine type=product)
            reserved_as_component = ReservationLine.objects.filter(
                type="product",
                product=component.product,
                reservation__is_fulfilled=False,
                reservation__is_canceled=False
            ).aggregate(total=Sum("quantity"))["total"] or 0

            # Резервы готовых сборок этого же комплекта (съедают компоненты)
            reserved_assemblies = Assembly.objects.filter(kit=kit, status="RESERVED").count()
            ready_assemblies = Assembly.objects.filter(kit=kit, status="READY").count()
            deficit = max(0, reserved_assemblies - ready_assemblies)
            components_needed_for_deficit = deficit * component.quantity

            free = total_available - reserved_as_component - components_needed_for_deficit
            possible = free // component.quantity

            if possible < min_count:
                min_count = possible

        return min_count if min_count != float("inf") else 0