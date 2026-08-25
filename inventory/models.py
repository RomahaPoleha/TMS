from django.db import models



# Тип оборудования
class EquipmentType(models.Model):
    name = models.CharField(max_length=100)
    def __str__(self):
        return self.name

# Модель/наименование товара (справочник)
class Product(models.Model):
    name = models.CharField(max_length=100)
    equipment_type = models.ForeignKey(EquipmentType, on_delete=models.CASCADE)
    def __str__(self):
        return self.name

# Клиент
class Client(models.Model):
    name = models.CharField(max_length=200)

    def __str__(self):
        return self.name

# Тип неисправности
class DefectType(models.Model):
    name = models.CharField(max_length=200)

    def __str__(self):
        return self.name

class Batch(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    received_at  = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        date_str = self.received_at.strftime("%d.%m.%Y") if self.received_at else "нет даты"
        return f"{self.product.name} - {self.quantity}шт. ({date_str})"

# Физическая единица товара с серийным номером
class Unit(models.Model):
    STATUS_CHOICES = [
        ("IN_STOCK","На складе"),
        ("READY","Готово к продаже"),
        ("IN_SERVICE", "В сервисе"),
        ("SOLD","Продано")
    ]
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    serial_number = models.CharField(max_length=100, unique=True, null=True, blank=True)
    status = models.CharField(max_length=20, choices = STATUS_CHOICES, default = "IN_STOCK")
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    batch = models.ForeignKey(Batch,on_delete=models.CASCADE, null=True, blank=True)

    def __str__(self):
        if self.serial_number:
            return f"{self.product} - {self.serial_number}"
        else:
            return f"{self.product} - (без серийника)"

