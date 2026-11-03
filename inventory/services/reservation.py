"""Сервисы для работы с резервами"""
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from inventory.models import (
    Reservation, ReservationLine, Client, Product, Assembly, Kit, Unit
)


class ReservationService:
    """Бизнес-логика резервов: создание, отгрузка, отмена"""

    @staticmethod
    @transaction.atomic
    def create_reservation(
        client: Client,
        product_lines: list[tuple[Product, int]] = None,
        assembly_lines: list[tuple[Assembly, int]] = None,
        kit_lines: list[tuple[Kit, int]] = None,
    ) -> Reservation:
        """
        Создаёт резерв с позициями трёх типов.

        Args:
            client: Клиент
            product_lines: [(Product, quantity), ...] — товары
            assembly_lines: [(Assembly, quantity), ...] — готовые сборки (статус READY)
            kit_lines: [(Kit, quantity), ...] — комплекты на сборку (дефицитные)

        Returns:
            Созданный Reservation

        Raises:
            ValidationError: если не хватает сборок или товаров
        """
        reservation = Reservation.objects.create(client=client)

        # 1. Товары — просто создаём ReservationLine
        if product_lines:
            for product, qty in product_lines:
                if qty <= 0:
                    raise ValidationError(f"Количество товара '{product.name}' должно быть > 0")
                ReservationLine.objects.create(
                    reservation=reservation,
                    type="product",
                    product=product,
                    quantity=qty,
                )

        # 2. Готовые сборки — проверяем доступность, меняем статус, создаём ReservationLine
        if assembly_lines:
            for assembly, qty in assembly_lines:
                if assembly.status != "READY":
                    raise ValidationError(f"Сборка #{assembly.id} не доступна (статус: {assembly.status})")
                if qty != 1:
                    raise ValidationError("Сборки резервируются поштучно (qty=1)")

                assembly.status = "RESERVED"
                assembly.save(update_fields=["status"])
                ReservationLine.objects.create(
                    reservation=reservation,
                    type="assembly",
                    assembly=assembly,
                    quantity=1,
                )

        # 3. Комплекты на сборку — создаём ReservationLine type=kit
        # (логика сборки под заказ будет отдельно)
        if kit_lines:
            for kit, qty in kit_lines:
                if qty <= 0:
                    raise ValidationError(f"Количество комплектов '{kit.name}' должно быть > 0")
                ReservationLine.objects.create(
                    reservation=reservation,
                    type="kit",
                    kit=kit,
                    quantity=qty,
                )

        return reservation

    @staticmethod
    @transaction.atomic
    def ship_reservation(reservation: Reservation, selected_units: dict = None) -> None:
        """
        Отгружает резерв: товары -> SOLD, сборки -> SOLD.

        Args:
            reservation: Резерв для отгрузки
            selected_units: {ReservationLine: [Unit, ...]} — выбранные Units для маркируемых товаров

        Raises:
            ValidationError: если резерв уже отгружен/отменен или не хватает Units
        """
        if reservation.is_fulfilled:
            raise ValidationError("Резерв уже отгружен")
        if reservation.is_canceled:
            raise ValidationError("Резерв отменён")

        lines = reservation.lines.select_related("product", "assembly__kit").all()

        # Обрабатываем товары
        for line in lines.filter(type="product"):
            product = line.product
            qty = line.quantity

            if product.requires_serial:
                # Маркируемые: нужны конкретные Units из selected_units
                units = selected_units.get(line, [])
                if len(units) != qty:
                    raise ValidationError(f"Для '{product.name}' нужно выбрать {qty} шт., выбрано {len(units)}")
                for unit in units:
                    if unit.status != "READY":
                        raise ValidationError(f"Unit #{unit.id} не READY")
                    unit.status = "SOLD"
                    unit.save(update_fields=["status", "updated_at"])
            else:
                # Немаркируемые: списываем любые READY
                units = Unit.objects.filter(product=product, status="READY")[:qty]
                if len(units) < qty:
                    raise ValidationError(f"Недостаточно '{product.name}' на складе (нужно {qty}, есть {len(units)})")
                for unit in units:
                    unit.status = "SOLD"
                    unit.save(update_fields=["status", "updated_at"])

        # Обрабатываем сборки
        for line in lines.filter(type="assembly"):
            assembly = line.assembly
            if assembly.status != "RESERVED":
                raise ValidationError(f"Сборка #{assembly.id} не в резерве")
            assembly.status = "SOLD"
            assembly.save(update_fields=["status"])
            # Удаляем ReservationLine (история в reservation.lines остаётся через related_name)
            line.delete()

        # Kit lines — пока не отгружаем (нужна логика сборки под заказ)

        # Помечаем резерв исполненным
        reservation.is_fulfilled = True
        reservation.date_of_shipment = timezone.now().date()
        reservation.save(update_fields=["is_fulfilled", "date_of_shipment"])

    @staticmethod
    @transaction.atomic
    def cancel_reservation(reservation: Reservation) -> None:
        """Отменяет резерв: сборки -> READY, ReservationLine(type=assembly) удаляются"""
        if reservation.is_fulfilled:
            raise ValidationError("Нельзя отменить отгруженный резерв")
        if reservation.is_canceled:
            raise ValidationError("Резерв уже отменён")

        # Освобождаем сборки
        for line in reservation.lines.filter(type="assembly").select_related("assembly"):
            assembly = line.assembly
            if assembly.status == "RESERVED":
                assembly.status = "READY"
                assembly.save(update_fields=["status"])
            line.delete()

        reservation.is_canceled = True
        reservation.save(update_fields=["is_canceled"])

    @staticmethod
    @transaction.atomic
    def reserve_assembly(assembly: Assembly, client: Client) -> Reservation:
        """Бронирует готовую сборку под клиента (создаёт резерв с одной позицией assembly)"""
        if assembly.status != "READY":
            raise ValidationError(f"Сборка #{assembly.id} не доступна для резерва (статус: {assembly.status})")

        reservation = Reservation.objects.create(client=client)
        assembly.status = "RESERVED"
        assembly.save(update_fields=["status"])
        ReservationLine.objects.create(
            reservation=reservation,
            type="assembly",
            assembly=assembly,
            quantity=1,
        )
        return reservation

    @staticmethod
    @transaction.atomic
    def cancel_assembly_reservation(assembly: Assembly) -> None:
        """Отменяет резерв конкретной сборки"""
        if assembly.status != "RESERVED":
            raise ValidationError("Сборка не в резерве")

        # Находим и удаляем ReservationLine
        line = assembly.reservation_lines.first()
        if line:
            line.delete()

        assembly.status = "READY"
        assembly.save(update_fields=["status"])