from django.contrib import admin
from inventory.models import EquipmentType, Product, Client, DefectType, Unit, Batch, Reservation, Shipment,  ShipmentItem, ReservationItem


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


class ReservationItemInline(admin.TabularInline):
    model = ReservationItem
    fields = ('product', 'quantity')

class ReservationAdmin(admin.ModelAdmin):
    inlines = [ReservationItemInline]
    list_display = ("client",  "created_at", "is_fulfilled")
    list_filter = ("client", "is_fulfilled")
    list_editable = ("is_fulfilled",) # поля которые можно редактировать прям в резерве



class ShipmentItemInline(admin.TabularInline):
    model = ShipmentItem
    fields = ('unit',)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "unit": # Проверяем, какое поле сейчас рисует
            kwargs["queryset"] = Unit.objects.filter(status = "READY") # Подменяем список вариантов
        return super().formfield_for_foreignkey(db_field,request,**kwargs) # отдаём управление дальше


class ShipmentAdmin(admin.ModelAdmin):
    inlines = [ShipmentItemInline]
    list_display = ("client", "created_at", "reservation")

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change) # сохранить все данные

        # сохранённый объект Shipment
        shipment = form.instance
        shipment_all = shipment.shipmentitem_set.all() # получение всех позиций через обратную связь
        for item in shipment_all :
            if item.unit.status != "SOLD":
                item.unit.status = "SOLD"
                item.unit.save()
        if shipment.reservation:
            shipment.reservation.is_fulfilled = True
            shipment.reservation.save()



# Регистрация в админке моделей
admin.site.register(Product, ProductAdmin)
admin.site.register(Client, ClientAdmin)
admin.site.register(Unit, UnitAdmin)
admin.site.register(Batch, BatchAdmin)
admin.site.register(Reservation, ReservationAdmin)
admin.site.register(Shipment, ShipmentAdmin)


