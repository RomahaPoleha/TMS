from django.db import models

# Тип оборудования
class EquipmentType(models.Model):
    name = models.CharField(max_length=100, verbose_name="Наименование")

    class Meta:
        verbose_name = "Тип оборудования"
        verbose_name_plural = "Типы оборудования"

    def __str__(self):
        return self.name


# Модель/наименование товара (справочник)
class Product(models.Model):
    name = models.CharField(max_length=100, verbose_name="Наименование")
    equipment_type = models.ForeignKey(EquipmentType, on_delete=models.CASCADE, verbose_name = "Тип оборудования")

    class Meta:
        verbose_name = "Товар"
        verbose_name_plural = "Товары"

    def __str__(self):
        return self.name


# Клиент
class Client(models.Model):
    name = models.CharField(max_length=200, verbose_name = "Организация")

    class Meta:
        verbose_name = "Клиент"
        verbose_name_plural = "Клиенты"

    def __str__(self):
        return self.name



# Тип неисправности
class DefectType(models.Model):
    name = models.CharField(max_length=200, verbose_name="Наименование")

    class Meta:
        verbose_name = "Тип неисправности"
        verbose_name_plural = "Типы неисправностей"

    def __str__(self):
        return self.name


class Batch(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, verbose_name="Товар")
    quantity = models.PositiveIntegerField(verbose_name="Количество")
    received_at  = models.DateTimeField(auto_now_add=True, verbose_name= "Дата приёмки")

    class Meta:
        verbose_name = "Партия"
        verbose_name_plural = "Партии"

    def __str__(self):
        date_str = self.received_at.strftime("%d.%m.%Y") if self.received_at else "нет даты"
        return f"{self.product.name} - {self.quantity}шт. ({date_str})"

    def save(self, *args, **kwargs):
        is_new = self.pk is None

        super().save(*args, **kwargs)

        if is_new:
            units = [Unit(batch=self, product=self.product, status="IN_STOCK") for _ in range(self.quantity)]
            Unit.objects.bulk_create(units)



# Физическая единица товара с серийным номером
class Unit(models.Model):
    STATUS_CHOICES = [
        ("IN_STOCK","На складе"),
        ("READY","Готово к продаже"),
        ("IN_SERVICE", "В сервисе"),
        ("SOLD","Продано")
    ]
    product = models.ForeignKey(Product, on_delete=models.CASCADE, verbose_name= "Товар")
    serial_number = models.CharField(max_length=100, unique=True, null=True, blank=True, verbose_name="Серийный номер")
    status = models.CharField(max_length=20, choices = STATUS_CHOICES, default = "IN_STOCK", verbose_name="Статус")
    created_at  = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обнавления")
    batch = models.ForeignKey(Batch,on_delete=models.CASCADE, null=True, blank=True, verbose_name="Партия")

    class Meta:
        verbose_name = "Единица техники"
        verbose_name_plural = "Единицы техники"

    def __str__(self):
        if self.serial_number:
            return f"{self.product} - {self.serial_number}"
        else:
            return f"{self.product} - (без серийника)"

