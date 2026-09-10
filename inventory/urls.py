from django.urls import path
from . import views

urlpatterns = [
    path('', views.main_dashboard,name = 'dashboard'), # Путь на главную страницу
    path('reservations', views.active_reservations, name = 'reservations')
]