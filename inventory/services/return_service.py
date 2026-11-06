"""Сервисы для работы с возвратами (RMA)"""
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from inventory.models import Return, Unit, Assembly, AssemblyUnit, DefectType


class ReturnService:
    """Бизнес-логика возвратов (RMA)"""

    @staticmethod
    @transaction.atomic
    def process_unit_return(
        unit: Unit,
        client,
        reason: str,
        disposition: str,
        defect_type: DefectType = None,
        comment: str = "",
    ) -> Return:
        """
        Обрабатывает возврат устройства.

        Args:
            unit: Устройство (статус SOLD)
            client: Клиент, вернувший устройство
            reason: Причина возврата
            disposition: back_to_stock / to_service
            defect_type: Неисправность (если to_service)
            comment: Комментарий

        Returns:
            Созданный Return

        Raises:
            ValidationError: если устройство не SOLD или неверный disposition
        """
        if unit.status != "SOLD":
            raise ValidationError(f"Устройство #{unit.id} не продано (статус: {unit.status})")

        if disposition not in (Return.DISPOSITION_BACK_TO_STOCK, Return.DISPOSITION_TO_SERVICE):
            raise ValidationError(f"Неверное решение: {disposition}")

        if disposition == Return.DISPOSITION_TO_SERVICE and not defect_type:
            raise ValidationError("Для отправки в сервис укажите неисправность")

        # Меняем статус
        if disposition == Return.DISPOSITION_BACK_TO_STOCK:
            unit.status = "READY"
            unit.reservation = None
        else:
            unit.status = "IN_SERVICE"
            unit.defect_type = defect_type
            unit.service_comment = comment
            unit.service_received_at = timezone.now()

        unit.save()

        # Создаём запись возврата
        ret = Return.objects.create(
            client=client,
            unit=unit,
            reason=reason,
            disposition=disposition,
            defect_type=defect_type,
            comment=comment,
        )
        return ret

    @staticmethod
    @transaction.atomic
    def process_assembly_return(
        assembly: Assembly,
        client,
        reason: str,
        disposition: str,
        defect_type: DefectType = None,
        comment: str = "",
    ) -> Return:
        """
        Обрабатывает возврат сборки (и всех её Units).

        Args:
            assembly: Сборка (статус SOLD)
            client: Клиент, вернувший сборку
            reason: Причина возврата
            disposition: back_to_stock / to_service
            defect_type: Неисправность (если to_service)
            comment: Комментарий

        Returns:
            Созданный Return

        Raises:
            ValidationError: если сборка не SOLD или неверный disposition
        """
        if assembly.status != "SOLD":
            raise ValidationError(f"Сборка #{assembly.id} не продана (статус: {assembly.status})")

        if disposition not in (Return.DISPOSITION_BACK_TO_STOCK, Return.DISPOSITION_TO_SERVICE):
            raise ValidationError(f"Неверное решение: {disposition}")

        if disposition == Return.DISPOSITION_TO_SERVICE and not defect_type:
            raise ValidationError("Для отправки в сервис укажите неисправность")

        unit_ids = list(assembly.units.values_list("unit_id", flat=True))

        # Меняем статус сборки
        if disposition == Return.DISPOSITION_BACK_TO_STOCK:
            assembly.status = "READY"
            Unit.objects.filter(id__in=unit_ids).update(
                status="READY",
                reservation=None,
            )
        else:
            assembly.status = "IN_SERVICE"
            Unit.objects.filter(id__in=unit_ids).update(
                status="IN_SERVICE",
                defect_type=defect_type,
                service_comment=comment,
                service_received_at=timezone.now(),
            )

        assembly.save(update_fields=["status", "updated_at"])

        # Создаём запись возврата
        ret = Return.objects.create(
            client=client,
            assembly=assembly,
            reason=reason,
            disposition=disposition,
            defect_type=defect_type,
            comment=comment,
        )
        return ret

    @staticmethod
    def find_sold_unit(serial_number: str) -> Unit:
        """Находит проданное устройство по серийному номеру"""
        try:
            unit = Unit.objects.get(serial_number=serial_number, status="SOLD")
            return unit
        except Unit.DoesNotExist:
            raise ValidationError(f"Проданное устройство с серийным номером '{serial_number}' не найдено")

    @staticmethod
    def find_sold_assembly(serial_number: str) -> Assembly:
        """Находит проданную сборку по серийному номеру"""
        try:
            assembly = Assembly.objects.get(serial_number=serial_number, status="SOLD")
            return assembly
        except Assembly.DoesNotExist:
            raise ValidationError(f"Проданная сборка с серийным номером '{serial_number}' не найдена")