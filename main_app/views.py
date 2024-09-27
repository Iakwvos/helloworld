# hello/views.py
from django.http import HttpResponse
from django.shortcuts import render

def main_page(request):
    """Render the main landing page."""
    return render(request, 'main_page.html')