from django.urls import path
from . import views
from .views import add_product

urlpatterns = [
    path('', views.main_dashboard,name = 'dashboard'), # Путь на главную страницу
    path('reservations/', views.active_reservations, name = 'reservations'),
    path('service/', views.active_service, name = 'service'),
    path('add-units/',views.add_units, name = "add_units"),
    path('add-product/', views.add_product, name = "add_product")
]