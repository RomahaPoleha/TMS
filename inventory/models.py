from django.db import models


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