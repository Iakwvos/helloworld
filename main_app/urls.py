from django.urls import path
from . import views

urlpatterns = [

    # Main and Authentication
    path('', views.main_page, name='main_page'),
    



]
