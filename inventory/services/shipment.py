"""Сервисы для работы с отгрузками (Shipment)"""
from django.db import transaction
from django.core.exceptions import ValidationError

from inventory.models import Shipment, ShipmentItem, Unit, Reservation, ReservationLine


class ShipmentService:
    """Бизнес-логика создания отгрузок из резервов"""

    @staticmethod
    @transaction.atomic
    def create_from_reservation(reservation: Reservation, units_by_line: dict) -> Shipment:
        """
        Создаёт Shipment на основе исполненного резерва.

        Args:
            reservation: Резерв (должен быть is_fulfilled=True)
            units_by_line: {ReservationLine: [Unit, ...]} — какие Units отгружены по каждой позиции

        Returns:
            Созданный Shipment
        """
        if not reservation.is_fulfilled:
            raise ValidationError("Резерв не отгружен")

        shipment = Shipment.objects.create(
            client=reservation.client,
            reservation=reservation,
        )

        for line, units in units_by_line.items():
            for unit in units:
                if unit.status != "SOLD":
                    raise ValidationError(f"Unit #{unit.id} не в статусе SOLD")
                ShipmentItem.objects.create(shipment=shipment, unit=unit)

        return shipment

    @staticmethod
    @transaction.atomic
    def create_manual(client, units: list[Unit]) -> Shipment:
        """Ручная отгрузка без резерва"""
        shipment = Shipment.objects.create(client=client)
        for unit in units:
            if unit.status != "READY":
                raise ValidationError(f"Unit #{unit.id} не READY")
            unit.status = "SOLD"
            unit.save(update_fields=["status", "updated_at"])
            ShipmentItem.objects.create(shipment=shipment, unit=unit)
        return shipment