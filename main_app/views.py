# hello/views.py
from django.http import HttpResponse

def pagepilot(request):
    return HttpResponse("Hello, World!")