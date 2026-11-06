from django.contrib import admin
from inventory.models import EquipmentType, Product, Client, DefectType, Unit, Batch, Reservation, Shipment, ShipmentItem, ReservationLine, Kit, KitComponent, Assembly, Return, MovementLog


class UnitInline(admin.TabularInline):
    model = Unit
    extra = 1


class UnitAdmin(admin.ModelAdmin):
    list_display = ("product", "serial_number", "status", "created_at", "updated_at")


class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "equipment_type",)
    list_filter = ("equipment_type",)
    search_fields = ("name", "equipment_type__name",)
    inlines = [UnitInline]


class ClientAdmin(admin.ModelAdmin):
    search_fields = ("name",)


class BatchAdmin(admin.ModelAdmin):
    list_display = ("product", "quantity", "received_at")


class ReservationLineInline(admin.TabularInline):
    model = ReservationLine
    fields = ('type', 'product', 'assembly', 'kit', 'quantity')
    extra = 1

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "product":
            kwargs["queryset"] = Product.objects.filter(is_component=False)
        if db_field.name == "assembly":
            kwargs["queryset"] = Assembly.objects.filter(status="READY")
        if db_field.name == "kit":
            kwargs["queryset"] = Kit.objects.filter(is_active=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class ReservationAdmin(admin.ModelAdmin):
    inlines = [ReservationLineInline]
    list_display = ("client", "created_at", "is_fulfilled")
    list_filter = ("client", "is_fulfilled")
    list_editable = ("is_fulfilled",)


class ShipmentItemInline(admin.TabularInline):
    model = ShipmentItem
    fields = ('unit',)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "unit":
            kwargs["queryset"] = Unit.objects.filter(status="READY")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class ShipmentAdmin(admin.ModelAdmin):
    inlines = [ShipmentItemInline]
    list_display = ("client", "created_at", "reservation")

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)

        shipment = form.instance
        shipment_all = shipment.shipmentitem_set.all()
        for item in shipment_all:
            if item.unit.status != "SOLD":
                item.unit.status = "SOLD"
                item.unit.save()
        if shipment.reservation:
            shipment.reservation.is_fulfilled = True
            shipment.reservation.save()


class KitComponentInline(admin.TabularInline):
    model = KitComponent
    extra = 1


@admin.register(Kit)
class KitAdmin(admin.ModelAdmin):
    inlines = [KitComponentInline]
    list_display = ["name", "code", "is_active", "created_at"]
    list_filter = ["is_active"]


@admin.register(KitComponent)
class KitComponentAdmin(admin.ModelAdmin):
    list_display = ["kit", "product", "quantity"]


@admin.register(ReservationLine)
class ReservationLineAdmin(admin.ModelAdmin):
    list_display = ["reservation", "type", "product", "assembly", "kit", "quantity"]
    list_filter = ["type", "reservation__is_fulfilled"]


@admin.register(Return)
class ReturnAdmin(admin.ModelAdmin):
    list_display = ["returned_at", "client", "unit", "assembly", "disposition", "reason"]
    list_filter = ["disposition", "returned_at"]
    search_fields = ["client__name", "unit__serial_number", "assembly__serial_number", "reason"]

@admin.register(MovementLog)
class MovementLogAdmin(admin.ModelAdmin):
    list_display = ["created_at", "object_type", "object_id", "serial_number", "action", "old_status", "new_status", "client"]
    list_filter = ["object_type", "action", "created_at"]
    search_fields = ["serial_number", "comment"]
    readonly_fields = ["created_at"]

# Регистрация в админке моделей
admin.site.register(Product, ProductAdmin)
admin.site.register(Client, ClientAdmin)
admin.site.register(Unit, UnitAdmin)
admin.site.register(Batch, BatchAdmin)
admin.site.register(Reservation, ReservationAdmin)
admin.site.register(Shipment, ShipmentAdmin)