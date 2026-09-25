from django.urls import path
from . import views


urlpatterns = [
    path('', views.main_dashboard,name = 'dashboard'), # Путь на главную страницу
    path('reservations/', views.active_reservations, name = 'reservations'),
    path('service/', views.active_service, name = 'service'),
    path('add-units/',views.add_units, name = "add_units"),
    path('add-product/', views.add_product, name = "add_product"),
    path('prepare-list/', views.prepare_list, name = "prepare_list"),
    path('prepare/<int:product_id>/', views.prepare_product, name = "prepare_product"),
    path('finish-service/<int:unit_id>/', views.finish_service, name='finish_service'),
    path('create-reservation/', views.create_reservation, name = "create_reservation"),
    path('ship/<int:reservation_id>/', views.ship_reservation, name='ship_reservation'),

]