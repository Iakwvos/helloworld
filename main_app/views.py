import hashlib
import json
import os
import re
import time
import logging
from io import BytesIO

import requests
from bs4 import BeautifulSoup
from PIL import Image
from django.shortcuts import render, redirect
from django.http import JsonResponse, HttpResponse
from django.contrib import messages
from django.conf import settings
from django.urls import reverse
from django.core.validators import URLValidator
from django.core.exceptions import ValidationError
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from gotrue import errors as gotrue_errors
from gotrue.errors import AuthRetryableError, AuthApiError

from supabase import create_client, Client
import openai
import shopify

# ---------------------
# Logging Configuration
# ---------------------
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # Set to DEBUG for detailed logs

# Create handlers
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)

file_handler = logging.FileHandler(os.path.join(settings.BASE_DIR, 'app.log'))
file_handler.setLevel(logging.ERROR)  # Log errors to file

# Create formatters and add to handlers
formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
console_handler.setFormatter(formatter)
file_handler.setFormatter(formatter)

# Add handlers to the logger
logger.addHandler(console_handler)
logger.addHandler(file_handler)

PLAN_LIMITS = {
    'Free': {
        'max_products': 3,
        'max_stores': 1,
        'price': 0,
        'generations_per_month': 3,
        'features': [
            'Access to basic features',
            'Community support',
            'Limited to 3 products',
            '1 connected store',
            'Basic analytics',
            'Standard security',
            'Limited promotional offers',
            'Basic product listing capabilities',
            'Access to community forums',
            'User guide for getting started',
        ],
    },
    'Starter': {
        'max_products': 20,
        'max_stores': 3,
        'price': 29,
        'generations_per_month': 20,
        'features': [
            'Access to all basic features',
            'Email support',
            'Up to 20 products',
            '3 connected stores',
            'Standard analytics',
            'Advanced security',
            'Customizable templates',
            'Promotional discounts for upgrades',
            'Integration with social media channels',
            'Monthly performance reports',
        ],
    },
    'Professional': {
        'max_products': 100,
        'max_stores': 10,
        'price': 99,
        'generations_per_month': 100,
        'features': [
            'All features included',
            'Priority support',
            'Unlimited products',
            'Up to 10 connected stores',
            'Advanced analytics',
            'Premium security',
            'Early access to new features',
            'Personalized onboarding',
            'Dedicated account manager',
            'Access to exclusive webinars and training sessions',
        ],
    },
}




# ---------------------
# Initialize Clients
# ---------------------
supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
openai.api_key = settings.OPENAI_API_KEY

# ---------------------
# Helper Functions
# ---------------------


def sanitize_title(title):
    sanitized = re.sub(r'[^A-Za-z0-9]+', '_', title).strip('_')
    return sanitized


def get_user_id(request):
    """Retrieve the user_id from the session."""
    return request.session.get('user_id')


def handle_exception(request, e, user_id, context):
    """
    Log the exception and provide user feedback.

    Args:
        request (HttpRequest): The HTTP request object.
        e (Exception): The exception that was raised.
        user_id (str): The ID of the user involved in the error.
        context (str): Additional context about where the error occurred.

    Returns:
        None
    """
    logger.error("Error in %s for user_id %s: %s", context, user_id, e)
    messages.error(
        request,
        f"An unexpected error occurred during {context.replace('_', ' ')}. Please try again."
    )


def signup_user(email, password):
    """Sign up a new user with Supabase."""
    try:
        response = supabase.auth.sign_up({'email': email, 'password': password})
        logger.debug("Signup response for %s: %s", email, response)
        return response
    except AuthRetryableError as e:
        logger.warning("Retryable error during signup for %s: %s", email, e)
        raise
    except AuthApiError as e:
        logger.error("API error during signup for %s: %s", email, e)
        raise
    except Exception as e:
        logger.error("Unexpected error during signup for %s: %s", email, e)
        raise


def login_user(email, password):
    """Log in a user with Supabase."""
    try:
        response = supabase.auth.sign_in_with_password(
            {'email': email, 'password': password}
        )
        logger.debug("Login response for %s: %s", email, response)
        return response
    except AuthApiError as e:
        logger.error("API error during login for %s: %s", email, e)
        raise
    except Exception as e:
        logger.error("Unexpected error during login for %s: %s", email, e)
        raise


def reset_password(email):
    """Initiate password reset for a user."""
    try:
        response = supabase.auth.reset_password_for_email(email)
        logger.debug("Password reset response for %s: %s", email, response)
        return response
    except AuthApiError as e:
        logger.error("API error during password reset for %s: %s", email, e)
        raise
    except Exception as e:
        logger.error("Unexpected error during password reset for %s: %s", email, e)
        raise


def initialize_shopify_session(shop_url, api_version, private_app_password):
    """Initialize the Shopify session and show the store's name."""
    try:
        shopify.ShopifyResource.clear_session()
        session = shopify.Session(shop_url, api_version, private_app_password)
        shopify.ShopifyResource.activate_session(session)
    except shopify.errors.ShopifyError as e:
        logger.error("Shopify error initializing session for %s: %s", shop_url, e)
        raise
    except Exception as e:
        logger.error("Unexpected error initializing Shopify session for %s: %s", shop_url, e)
        raise


def parse_ai_response(request, response_text, selected_images):
    """Parse AI response and integrate selected images."""
    try:
        parsed_data = {
            'title': '',
            'descriptions': [],
            'key_benefits': [],
            'reviews': [],
            'hooks': [],
            'full_names': [],
            'images': selected_images[:5],  # Limit to 5 images
            'json_template': ''
        }

        lines = response_text.split('\n')
        current_section = None

        for line in lines:
            line = line.strip()
            if line.startswith('_Title:'):
                current_section = 'title'
                parsed_data['title'] = line[len('_Title:'):].strip()
            elif line.startswith('_Descriptions:'):
                current_section = 'descriptions'
                descriptions = line[len('_Descriptions:'):].strip().rstrip('|').split(' | ')
                parsed_data['descriptions'] = descriptions
            elif line.startswith('_Key Benefits:'):
                current_section = 'key_benefits'
                benefits = line[len('_Key Benefits:'):].strip().rstrip('|').split(' | ')
                parsed_data['key_benefits'] = benefits
            elif line.startswith('_Reviews:'):
                current_section = 'reviews'
                reviews = line[len('_Reviews:'):].strip().rstrip('|').split(' | ')
                parsed_data['reviews'] = reviews
            elif line.startswith('_Hooks:'):
                current_section = 'hooks'
                hooks = line[len('_Hooks:'):].strip().rstrip('|').split(' | ')
                parsed_data['hooks'] = hooks
            elif line.startswith('_Full Names:'):
                current_section = 'full_names'
                names = line[len('_Full Names:'):].strip().rstrip('|').split(' | ')
                parsed_data['full_names'] = names
            elif line.startswith('_Image Links:'):
                continue
            else:
                if current_section and current_section in parsed_data:
                    parsed_data[current_section].append(line.rstrip('|').strip())

        logger.debug("AI response parsed successfully.")
        return parsed_data
    except Exception as e:
        logger.error("Error parsing AI response: %s", e)
        messages.error(
            request,
            "Failed to parse the AI-generated description. Please try again."
        )
        raise


def create_replacements(parsed_data):
    """Create placeholder replacements for the Shopify template."""
    replacements = {}

    for key, value in parsed_data.items():
        if isinstance(value, list):
            for idx, item in enumerate(value):
                placeholder = f"{{data['{key}'][{idx}]}}"
                replacements[placeholder] = item
        else:
            placeholder = f"{{data['{key}']}}"
            replacements[placeholder] = value

    return replacements


def upload_images_and_get_handles(request, image_urls):
    """
    Upload multiple images to Shopify via a temporary product and return their handles.

    Args:
        request (HttpRequest): The HTTP request object.
        image_urls (list): List of image URLs to upload.

    Returns:
        list: List of Shopify image handles.
    """
    


def extract_image_handle(image_src):
    """
    Extract the image handle from the image source URL.

    Args:
        image_src (str): The source URL of the image.

    Returns:
        str: The handle of the image.
    """
    try:
        # Shopify image handles are typically the filename without the extension
        handle = os.path.splitext(os.path.basename(image_src))[0]
        return handle
    except Exception as e:
        logger.error("Error extracting handle from image src %s: %s", image_src, e)
        return ""


def create_replacements(product_data):
    """
    Creates a dictionary mapping placeholders in the Shopify template to actual product data.

    Args:
        product_data (dict): The product data from Supabase.

    Returns:
        dict: A dictionary where keys are placeholders and values are actual data.
    """
    replacements = {}

    # Title
    replacements["{data['title']}"] = product_data.get('title', '')

    # Descriptions
    for idx, description in enumerate(product_data.get('descriptions', [])):
        placeholder = f"{{data['descriptions'][{idx}]}}"
        replacements[placeholder] = description

    # Key Benefits
    for idx, benefit in enumerate(product_data.get('key_benefits', [])):
        placeholder = f"{{data['key benefits'][{idx}]}}"
        replacements[placeholder] = benefit

    # Reviews
    for idx, review in enumerate(product_data.get('reviews', [])):
        placeholder = f"{{data['reviews'][{idx}]}}"
        replacements[placeholder] = review

    # Hooks
    for idx, hook in enumerate(product_data.get('hooks', [])):
        placeholder = f"{{data['hooks'][{idx}]}}"
        replacements[placeholder] = hook

    # Full Names
    for idx, name in enumerate(product_data.get('full_names', [])):
        placeholder = f"{{data['full names'][{idx}]}}"
        replacements[placeholder] = name

    return replacements


# ---------------------
# Helper Decorators
# ---------------------


def login_required(view_func):
    """Decorator to ensure the user is logged in."""
    def wrapper(request, *args, **kwargs):
        user_id = get_user_id(request)
        if not user_id:
            logger.warning("Unauthorized access attempt to %s", request.path)
            messages.error(request, "You must be logged in to access this page.")
            return redirect('main_page')
        return view_func(request, *args, **kwargs)
    return wrapper


def log_view(func):
    """Decorator to log entry and exit of view functions."""
    def wrapper(request, *args, **kwargs):
        logger.debug("Entering %s", func.__name__)
        response = func(request, *args, **kwargs)
        logger.debug("Exiting %s", func.__name__)
        return response
    return wrapper


# ---------------------
# View Functions
# ---------------------


@log_view
def main_page(request):
    """Render the main landing page."""
    return render(request, 'main_page.html')


