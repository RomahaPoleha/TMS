"""Сервисы для работы с сервисом/ремонтом"""
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from inventory.models import Unit, Assembly, DefectType, AssemblyUnit
from inventory.services.movement import log_movement


class ServiceService:
    """Бизнес-логика отправки в сервис и завершения ремонта"""

    @staticmethod
    @transaction.atomic
    def send_unit_to_service(
        unit: Unit,
        defect_type: DefectType,
        comment: str,
        task_url: str = "",
        serial_number: str = None,
    ) -> Unit:
        """
        Отправляет Unit в сервис.

        Args:
            unit: Устройство (статус READY или IN_STOCK)
            defect_type: Тип неисправности
            comment: Описание проблемы
            task_url: Ссылка на задачу (опционально)
            serial_number: Если у Unit нет серийника — можно задать здесь

        Returns:
            Обновлённый Unit

        Raises:
            ValidationError: если Unit в неправильном статусе или уже в сборке
        """
        if unit.status not in ("READY", "IN_STOCK"):
            raise ValidationError(f"Нельзя отправить в сервис: статус {unit.status}")

        # Проверка: не в составе сборки
        if AssemblyUnit.objects.filter(unit=unit).exists():
            au = AssemblyUnit.objects.filter(unit=unit).first()
            raise ValidationError(
                f"Устройство входит в сборку #{au.assembly.id} ({au.assembly.kit.name}). "
                "Сначала разберите сборку."
            )

        if serial_number:
            if Unit.objects.filter(serial_number=serial_number).exclude(id=unit.id).exists():
                raise ValidationError(f"Серийный номер '{serial_number}' уже занят")
            unit.serial_number = serial_number

        unit.status = "IN_SERVICE"
        unit.defect_type = defect_type
        unit.service_comment = comment
        unit.task_url = task_url
        unit.service_received_at = timezone.now()
        unit.save()
        log_movement(
            object_type="unit",
            object_id=unit.id,
            serial_number=unit.serial_number or "",
            action="to_service",
            old_status="READY",
            new_status="IN_SERVICE",
            comment=comment,
        )
        return unit

    @staticmethod
    @transaction.atomic
    def finish_unit_service(unit: Unit) -> Unit:
        """
        Завершает ремонт Unit: IN_SERVICE -> READY.

        Args:
            unit: Устройство в сервисе

        Returns:
            Обновлённый Unit

        Raises:
            ValidationError: если Unit не в сервисе
        """
        if unit.status != "IN_SERVICE":
            raise ValidationError("Устройство не в сервисе")

        unit.status = "READY"
        unit.service_resolved_at = timezone.now()
        unit.save()
        log_movement(
            object_type="unit",
            object_id=unit.id,
            serial_number=unit.serial_number or "",
            action="from_service",
            old_status="IN_SERVICE",
            new_status="READY",
            comment="Ремонт завершён",
        )
        return unit

    @staticmethod
    @transaction.atomic
    def send_assembly_to_service(
        assembly: Assembly,
        defect_type: DefectType,
        comment: str,
        task_url: str = "",
    ) -> Assembly:
        """
        Отправляет всю сборку в сервис.

        Args:
            assembly: Сборка (статус READY или RESERVED)
            defect_type: Тип неисправности
            comment: Описание проблемы
            task_url: Ссылка на задачу

        Returns:
            Обновлённая Assembly

        Raises:
            ValidationError: если сборка в неправильном статусе
        """
        if assembly.status not in ("READY", "RESERVED"):
            raise ValidationError(f"Нельзя отправить в сервис: статус {assembly.status}")

        assembly.status = "IN_SERVICE"
        # Сохраняем инфо о сервисе можно в comment или отдельном поле
        assembly.save(update_fields=["status"])

        # Все Units сборки тоже в IN_SERVICE
        unit_ids = list(assembly.units.values_list("unit_id", flat=True))
        Unit.objects.filter(id__in=unit_ids).update(
            status="IN_SERVICE",
            defect_type=defect_type,
            service_comment=comment,
            task_url=task_url,
            service_received_at=timezone.now(),
        )
        log_movement(
            object_type="assembly",
            object_id=assembly.id,
            serial_number=assembly.serial_number or "",
            action="to_service",
            old_status="READY",
            new_status="IN_SERVICE",
            comment=comment,
        )
        for uid in unit_ids:
            u = Unit.objects.get(id=uid)
            log_movement(
                object_type="unit",
                object_id=uid,
                serial_number=u.serial_number or "",
                action="to_service",
                old_status="ASSEMBLED",
                new_status="IN_SERVICE",
                comment=comment,
            )
        return assembly

    @staticmethod
    @transaction.atomic
    def finish_assembly_service(assembly: Assembly) -> Assembly:
        """
        Завершает ремонт сборки: IN_SERVICE -> READY.

        Args:
            assembly: Сборка в сервисе

        Returns:
            Обновлённая Assembly
        """
        if assembly.status != "IN_SERVICE":
            raise ValidationError("Сборка не в сервисе")

        assembly.status = "READY"
        assembly.save(update_fields=["status"])

        unit_ids = list(assembly.units.values_list("unit_id", flat=True))
        Unit.objects.filter(id__in=unit_ids).update(
            status="READY",
            service_resolved_at=timezone.now(),
        )
        log_movement(
            object_type="assembly",
            object_id=assembly.id,
            serial_number=assembly.serial_number or "",
            action="from_service",
            old_status="IN_SERVICE",
            new_status="READY",
            comment="Ремонт завершён",
        )
        for uid in unit_ids:
            u = Unit.objects.get(id=uid)
            log_movement(
                object_type="unit",
                object_id=uid,
                serial_number=u.serial_number or "",
                action="from_service",
                old_status="IN_SERVICE",
                new_status="READY",
                comment="Ремонт завершён",
            )
        return assembly