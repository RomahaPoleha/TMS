from django.contrib import admin

from inventory.models import EquipmentType, Product, Client, DefectType, Unit

class UnitAdmin(admin.ModelAdmin):
    list_display = ("product" , "serial_number", "status" , "created_at", "updated_at")

# Регистрация в админке моделей
admin.site.register(EquipmentType)
admin.site.register(Product)
admin.site.register(Client)
admin.site.register(DefectType)
admin.site.register(Unit, UnitAdmin)



