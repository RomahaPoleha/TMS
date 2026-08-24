from symtable import Class

from django.db import models
from django.db.models import ForeignKey


# Тип оборудования
class EquipmentType(models.Model):
    name = models.CharField(max_length=100)
    def __str__(self):
        return self.name

# Наименование товара
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

# Еденица техники
class Unit(models.Model):
    STATUS_CHOICES = [
        ("IN_STOCK","На складе"),
        ("READY","Готово к продаже"),
        ("IN_SERVICE", "В сервисе"),
        ("SOLD","Продано")
    ]
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    serial_number = models.CharField(max_length=100, unique=True)
    status = models.CharField(max_length=20, choices = STATUS_CHOICES, default = "IN_STOCK")
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.product} - {self.serial_number}"