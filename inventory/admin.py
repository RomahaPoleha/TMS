from django.contrib import admin
from inventory.models import EquipmentType, Product, Client, DefectType, Unit, Batch

class UnitInline(admin.TabularInline):
    model = Unit
    extra = 1

class UnitAdmin(admin.ModelAdmin):
    list_display = ("product" , "serial_number", "status" , "created_at", "updated_at")

class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "equipment_type",)
    list_filter = ("equipment_type",)
    search_fields = ("name", "equipment_type__name",) # lookup-запросы, нужны для работы с полями через связи
    inlines = [UnitInline]

class ClientAdmin(admin.ModelAdmin):
    search_fields = ("name",)

class BatchAdmin(admin.ModelAdmin):
    list_display = ("product", "quantity", "received_at")




# Регистрация в админке моделей
admin.site.register(EquipmentType)
admin.site.register(Product, ProductAdmin)
admin.site.register(Client, ClientAdmin)
admin.site.register(DefectType)
admin.site.register(Unit, UnitAdmin)
admin.site.register(Batch, BatchAdmin)



