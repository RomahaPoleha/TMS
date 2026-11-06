"""Сервис логирования движения (аудит-лог)"""
from inventory.models import MovementLog


def log_movement(
    object_type: str,
    object_id: int,
    serial_number: str = "",
    action: str = "",
    old_status: str = "",
    new_status: str = "",
    client=None,
    reservation=None,
    comment: str = "",
):
    """Создаёт запись в MovementLog"""
    MovementLog.objects.create(
        object_type=object_type,
        object_id=object_id,
        serial_number=serial_number,
        action=action,
        old_status=old_status,
        new_status=new_status,
        client=client,
        reservation=reservation,
        comment=comment,
    )