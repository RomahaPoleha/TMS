from django.contrib import admin
from inventory.models import EquipmentType, Product, Client, DefectType, Unit, Batch, Reservation, Shipment, \
    ShipmentItem


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

class ReservationAdmin(admin.ModelAdmin):
    list_display = ("client", "product", "quantity", "created_at", "is_fulfilled")
    list_filter = ("client","product", "is_fulfilled")
    search_fields = ("client__name", "product__name")
    list_editable = ("is_fulfilled",) # поля которые можно редактировать прям в резерве


class ShipmentItemInline(admin.TabularInline):
    model = ShipmentItem
    fields = ('unit',)


class ShipmentAdmin(admin.ModelAdmin):
    inlines = [ShipmentItemInline]
    list_display = ("client", "created_at", "reservation")

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)

        # сохранённый объект Shipment
        shipment = form.instance
        shipment_all =shipment.shipmentitem_set.all()
        for item in shipment_all :
            if item.unit.status != "SOLD":
                item.unit.status = "SOLD"
                item.unit.save()
        if shipment.reservation:
            shipment.reservation.is_fulfilled = True
            shipment.reservation.save()

# Регистрация в админке моделей
admin.site.register(EquipmentType)
admin.site.register(Product, ProductAdmin)
admin.site.register(Client, ClientAdmin)
admin.site.register(DefectType)
admin.site.register(Unit, UnitAdmin)
admin.site.register(Batch, BatchAdmin)
admin.site.register(Reservation, ReservationAdmin)
admin.site.register(Shipment, ShipmentAdmin)


