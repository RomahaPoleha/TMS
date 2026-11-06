from django.test import TestCase
from django.core.exceptions import ValidationError

from inventory.models import (
    Client, EquipmentType, Product, Unit, DefectType,
    Kit, KitComponent, Assembly, AssemblyUnit,
    Reservation, ReservationLine, Return,
)
from inventory.services import (
    AssemblyService, ReservationService, ServiceService, ReturnService,
)


class AssemblyServiceTests(TestCase):
    """Тесты AssemblyService"""

    def setUp(self):
        self.equip_type = EquipmentType.objects.create(name="Тип")
        self.product_a = Product.objects.create(
            name="Процессор", equipment_type=self.equip_type, requires_serial=True
        )
        self.product_b = Product.objects.create(
            name="Материнка", equipment_type=self.equip_type, requires_serial=True
        )
        self.kit = Kit.objects.create(
            name="Комплект X", equipment_type=self.equip_type
        )
        KitComponent.objects.create(kit=self.kit, product=self.product_a, quantity=2)
        KitComponent.objects.create(kit=self.kit, product=self.product_b, quantity=1)

        # Units для product_a (2 шт READY)
        self.units_a = [
            Unit.objects.create(product=self.product_a, status="READY", serial_number=f"SN-A{i}")
            for i in range(2)
        ]
        # Units для product_b (1 шт READY)
        self.units_b = [
            Unit.objects.create(product=self.product_b, status="READY", serial_number="SN-B1")
        ]

    def _build_units_by_component(self):
        comp_a = self.kit.components.get(product=self.product_a)
        comp_b = self.kit.components.get(product=self.product_b)
        return {
            comp_a: self.units_a,
            comp_b: self.units_b,
        }

    def test_create_assembly_success(self):
        """Сборка создаётся, Units переходят в ASSEMBLED"""
        assembly = AssemblyService.create_assembly(
            self.kit, self._build_units_by_component(), "KIT-SN-001"
        )
        self.assertEqual(assembly.status, "READY")
        self.assertEqual(assembly.serial_number, "KIT-SN-001")
        self.assertEqual(assembly.units.count(), 3)

        for unit in self.units_a + self.units_b:
            unit.refresh_from_db()
            self.assertEqual(unit.status, "ASSEMBLED")

    def test_create_assembly_duplicate_serial(self):
        """Дубликат серийника → ValidationError"""
        AssemblyService.create_assembly(self.kit, self._build_units_by_component(), "KIT-SN-001")
        # Сбрасываем Units обратно для повторной сборки
        Unit.objects.filter(id__in=[u.id for u in self.units_a + self.units_b]).update(status="READY")
        with self.assertRaises(ValidationError):
            AssemblyService.create_assembly(self.kit, self._build_units_by_component(), "KIT-SN-001")

    def test_create_assembly_wrong_quantity(self):
        """Неверное количество Units → ValidationError"""
        comp_a = self.kit.components.get(product=self.product_a)
        comp_b = self.kit.components.get(product=self.product_b)
        units_by_component = {
            comp_a: self.units_a[:1],  # нужно 2, даём 1
            comp_b: self.units_b,
        }
        with self.assertRaises(ValidationError):
            AssemblyService.create_assembly(self.kit, units_by_component, "KIT-SN-002")

    def test_create_assembly_wrong_product(self):
        """Unit не того продукта → ValidationError"""
        comp_a = self.kit.components.get(product=self.product_a)
        comp_b = self.kit.components.get(product=self.product_b)
        wrong_unit = Unit.objects.create(product=self.product_b, status="READY", serial_number="WRONG")
        units_by_component = {
            comp_a: [wrong_unit] + self.units_a[:1],  # первый Unit не того продукта
            comp_b: self.units_b,
        }
        with self.assertRaises(ValidationError):
            AssemblyService.create_assembly(self.kit, units_by_component, "KIT-SN-003")

    def test_disassembly_success(self):
        """Разборка возвращает Units в IN_STOCK"""
        assembly = AssemblyService.create_assembly(
            self.kit, self._build_units_by_component(), "KIT-SN-010"
        )
        units = AssemblyService.disassembly(assembly)
        self.assertEqual(len(units), 3)
        self.assertFalse(Assembly.objects.filter(id=assembly.id).exists())
        for unit in units:
            unit.refresh_from_db()
            self.assertEqual(unit.status, "IN_STOCK")

    def test_disassembly_sold_raises(self):
        """Нельзя разобрать проданную сборку"""
        assembly = AssemblyService.create_assembly(
            self.kit, self._build_units_by_component(), "KIT-SN-011"
        )
        assembly.status = "SOLD"
        assembly.save()
        with self.assertRaises(ValidationError):
            AssemblyService.disassembly(assembly)

    def test_calculate_potential_count(self):
        """Потенциал = min по компонентам"""
        # 2 Units product_a, 1 Unit product_b → потенциал = min(2//2, 1//1) = 1
        potential = AssemblyService.calculate_potential_count(self.kit)
        self.assertEqual(potential, 1)

    def test_calculate_potential_count_with_reservation(self):
        """Резерв компонента снижает потенциал"""
        client = Client.objects.create(name="Тест")
        reservation = Reservation.objects.create(client=client)
        ReservationLine.objects.create(
            reservation=reservation, type="product", product=self.product_a, quantity=2
        )
        # product_a: 2 - 2 = 0 → потенциал 0
        potential = AssemblyService.calculate_potential_count(self.kit)
        self.assertEqual(potential, 0)


class ReservationServiceTests(TestCase):
    """Тесты ReservationService"""

    def setUp(self):
        self.client_obj = Client.objects.create(name="Клиент")
        self.equip_type = EquipmentType.objects.create(name="Тип")
        self.product = Product.objects.create(
            name="Товар", equipment_type=self.equip_type, requires_serial=True
        )
        self.product_ns = Product.objects.create(
            name="Товар без серийника", equipment_type=self.equip_type, requires_serial=False
        )
        self.kit = Kit.objects.create(name="Комплект", equipment_type=self.equip_type)
        KitComponent.objects.create(kit=self.kit, product=self.product, quantity=1)
        self.assembly = Assembly.objects.create(
            kit=self.kit, serial_number="KIT-001", status="READY"
        )

    def test_create_reservation_product(self):
        """Резерв с товаром"""
        reservation = ReservationService.create_reservation(
            client=self.client_obj,
            product_lines=[(self.product, 3)],
        )
        self.assertEqual(reservation.client, self.client_obj)
        self.assertEqual(reservation.lines.count(), 1)
        line = reservation.lines.first()
        self.assertEqual(line.type, "product")
        self.assertEqual(line.quantity, 3)

    def test_create_reservation_assembly(self):
        """Резерв с готовой сборкой"""
        reservation = ReservationService.create_reservation(
            client=self.client_obj,
            assembly_lines=[(self.assembly, 1)],
        )
        self.assertEqual(reservation.lines.count(), 1)
        line = reservation.lines.first()
        self.assertEqual(line.type, "assembly")
        self.assembly.refresh_from_db()
        self.assertEqual(self.assembly.status, "RESERVED")

    def test_create_reservation_assembly_not_ready(self):
        """Нельзя зарезервировать не-READY сборку"""
        self.assembly.status = "SOLD"
        self.assembly.save()
        with self.assertRaises(ValidationError):
            ReservationService.create_reservation(
                client=self.client_obj,
                assembly_lines=[(self.assembly, 1)],
            )

    def test_ship_reservation_non_serial(self):
        """Отгрузка немаркируемого товара"""
        Unit.objects.create(product=self.product_ns, status="READY")
        Unit.objects.create(product=self.product_ns, status="READY")
        reservation = ReservationService.create_reservation(
            client=self.client_obj,
            product_lines=[(self.product_ns, 2)],
        )
        ReservationService.ship_reservation(reservation)
        reservation.refresh_from_db()
        self.assertTrue(reservation.is_fulfilled)
        self.assertEqual(Unit.objects.filter(product=self.product_ns, status="SOLD").count(), 2)

    def test_ship_reservation_serial(self):
        """Отгрузка маркируемого товара с выбором Units"""
        u1 = Unit.objects.create(product=self.product, status="READY", serial_number="SN-1")
        u2 = Unit.objects.create(product=self.product, status="READY", serial_number="SN-2")
        reservation = ReservationService.create_reservation(
            client=self.client_obj,
            product_lines=[(self.product, 2)],
        )
        line = reservation.lines.first()
        ReservationService.ship_reservation(reservation, selected_units={line: [u1, u2]})
        reservation.refresh_from_db()
        self.assertTrue(reservation.is_fulfilled)
        u1.refresh_from_db()
        u2.refresh_from_db()
        self.assertEqual(u1.status, "SOLD")
        self.assertEqual(u2.status, "SOLD")

    def test_ship_reservation_assembly(self):
        """Отгрузка сборки"""
        reservation = ReservationService.create_reservation(
            client=self.client_obj,
            assembly_lines=[(self.assembly, 1)],
        )
        ReservationService.ship_reservation(reservation)
        reservation.refresh_from_db()
        self.assertTrue(reservation.is_fulfilled)
        self.assembly.refresh_from_db()
        self.assertEqual(self.assembly.status, "SOLD")
        self.assertEqual(reservation.lines.filter(type="assembly").count(), 0)

    def test_cancel_reservation(self):
        """Отмена резерва освобождает сборки"""
        reservation = ReservationService.create_reservation(
            client=self.client_obj,
            assembly_lines=[(self.assembly, 1)],
        )
        ReservationService.cancel_reservation(reservation)
        reservation.refresh_from_db()
        self.assertTrue(reservation.is_canceled)
        self.assembly.refresh_from_db()
        self.assertEqual(self.assembly.status, "READY")

    def test_cancel_shipped_raises(self):
        """Нельзя отменить отгруженный резерв"""
        reservation = ReservationService.create_reservation(
            client=self.client_obj,
            product_lines=[(self.product_ns, 1)],
        )
        Unit.objects.create(product=self.product_ns, status="READY")
        ReservationService.ship_reservation(reservation)
        with self.assertRaises(ValidationError):
            ReservationService.cancel_reservation(reservation)

    def test_reserve_assembly_success(self):
        """Бронирование готовой сборки"""
        reservation = ReservationService.reserve_assembly(self.assembly, self.client_obj)
        self.assertEqual(reservation.client, self.client_obj)
        self.assembly.refresh_from_db()
        self.assertEqual(self.assembly.status, "RESERVED")

    def test_reserve_assembly_not_ready(self):
        """Нельзя забронировать не-READY сборку"""
        self.assembly.status = "SOLD"
        self.assembly.save()
        with self.assertRaises(ValidationError):
            ReservationService.reserve_assembly(self.assembly, self.client_obj)

    def test_cancel_assembly_reservation(self):
        """Отмена резерва конкретной сборки"""
        ReservationService.reserve_assembly(self.assembly, self.client_obj)
        ReservationService.cancel_assembly_reservation(self.assembly)
        self.assembly.refresh_from_db()
        self.assertEqual(self.assembly.status, "READY")


class ServiceServiceTests(TestCase):
    """Тесты ServiceService"""

    def setUp(self):
        self.equip_type = EquipmentType.objects.create(name="Тип")
        self.product = Product.objects.create(
            name="Товар", equipment_type=self.equip_type, requires_serial=True
        )
        self.defect_type = DefectType.objects.create(name="Дефект")
        self.unit = Unit.objects.create(
            product=self.product, status="READY", serial_number="SN-001"
        )
        self.unit_free = Unit.objects.create(
            product=self.product, status="READY", serial_number="SN-FREE"
        )
        self.kit = Kit.objects.create(name="Комплект", equipment_type=self.equip_type)
        KitComponent.objects.create(kit=self.kit, product=self.product, quantity=1)
        self.assembly = Assembly.objects.create(
            kit=self.kit, serial_number="KIT-001", status="READY"
        )
        AssemblyUnit.objects.create(assembly=self.assembly, unit=self.unit)

    def test_send_unit_to_service(self):
        """Отправка Unit в сервис"""
        unit = ServiceService.send_unit_to_service(
            unit=self.unit_free,
            defect_type=self.defect_type,
            comment="Сломался",
            task_url="https://example.com/task/1",
        )
        self.assertEqual(unit.status, "IN_SERVICE")
        self.assertEqual(unit.defect_type, self.defect_type)
        self.assertEqual(unit.service_comment, "Сломался")

    def test_finish_unit_service(self):
        """Завершение ремонта Unit"""
        ServiceService.send_unit_to_service(
            unit=self.unit_free,
            defect_type=self.defect_type,
            comment="Тест",
        )
        finished = ServiceService.finish_unit_service(self.unit_free)
        self.assertEqual(finished.status, "READY")

    def test_send_assembly_to_service(self):
        """Отправка сборки в сервис"""
        # Сначала разбираем сборку (unit не должен быть в другой сборке)
        assembly = ServiceService.send_assembly_to_service(
            assembly=self.assembly,
            defect_type=self.defect_type,
            comment="Весь комплект сломан",
            task_url="https://example.com/task/2",
        )
        self.assertEqual(assembly.status, "IN_SERVICE")
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.status, "IN_SERVICE")

    def test_finish_assembly_service(self):
        """Завершение ремонта сборки"""
        ServiceService.send_assembly_to_service(
            assembly=self.assembly,
            defect_type=self.defect_type,
            comment="Тест",
        )
        finished = ServiceService.finish_assembly_service(self.assembly)
        self.assertEqual(finished.status, "READY")
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.status, "READY")


class ReturnServiceTests(TestCase):
    """Тесты ReturnService"""

    def setUp(self):
        self.client_obj = Client.objects.create(name="Клиент")
        self.equip_type = EquipmentType.objects.create(name="Тип")
        self.product = Product.objects.create(
            name="Товар", equipment_type=self.equip_type, requires_serial=True
        )
        self.defect_type = DefectType.objects.create(name="Дефект")
        self.unit = Unit.objects.create(
            product=self.product, status="SOLD", serial_number="SN-001"
        )

    def test_process_unit_return_back_to_stock(self):
        """Возврат устройства на склад"""
        ret = ReturnService.process_unit_return(
            unit=self.unit,
            client=self.client_obj,
            reason="Не подошёл",
            disposition=Return.DISPOSITION_BACK_TO_STOCK,
        )
        self.assertEqual(ret.disposition, Return.DISPOSITION_BACK_TO_STOCK)
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.status, "READY")

    def test_process_unit_return_to_service(self):
        """Возврат устройства в сервис"""
        ret = ReturnService.process_unit_return(
            unit=self.unit,
            client=self.client_obj,
            reason="Сломался",
            disposition=Return.DISPOSITION_TO_SERVICE,
            defect_type=self.defect_type,
            comment="Трещина",
        )
        self.assertEqual(ret.disposition, Return.DISPOSITION_TO_SERVICE)
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.status, "IN_SERVICE")

    def test_process_unit_return_not_sold_raises(self):
        """Нельзя вернуть не-проданное устройство"""
        self.unit.status = "READY"
        self.unit.save()
        with self.assertRaises(ValidationError):
            ReturnService.process_unit_return(
                unit=self.unit,
                client=self.client_obj,
                reason="Тест",
                disposition=Return.DISPOSITION_BACK_TO_STOCK,
            )

    def test_process_unit_return_to_service_no_defect_raises(self):
        """Отправка в сервис без неисправности → ошибка"""
        with self.assertRaises(ValidationError):
            ReturnService.process_unit_return(
                unit=self.unit,
                client=self.client_obj,
                reason="Тест",
                disposition=Return.DISPOSITION_TO_SERVICE,
            )

    def test_find_sold_unit(self):
        """Поиск проданного устройства по серийнику"""
        unit = ReturnService.find_sold_unit("SN-001")
        self.assertEqual(unit.id, self.unit.id)

    def test_find_sold_unit_not_found(self):
        """Не найдено → ValidationError"""
        with self.assertRaises(ValidationError):
            ReturnService.find_sold_unit("NONEXISTENT")
