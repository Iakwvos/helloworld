from django.contrib import admin
from django.urls import path, include
from main_app import views as main_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', main_views.main_page, name='main_page'),
    path('auth/', include('main_app.urls')),
    
    # Add any other routes as needed
]

