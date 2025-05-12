# swiftsaleapp/config.py

import os
import sys
import configparser
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

def get_resource_path(relative_path: str) -> str:
    """
    Return the absolute path to a resource, handling both
    development and PyInstaller-extracted execution.
    """
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(__file__))
    return os.path.join(base_path, relative_path)

def load_config() -> (configparser.ConfigParser, str):
    """
    Load (or create) config.ini in the application directory.
    Returns a tuple of (ConfigParser instance, config file path).
    """
    config = configparser.ConfigParser()
    path = get_resource_path("config.ini")

    # Default configuration settings
    defaults = {
        "Telegram": {"bot_token": "", "chat_id": ""},
        "Subscription": {"tier": "Trial", "license_key": ""},
        "GUI": {
            "top_buyer_text": "Great job, {username}! You've snagged {count} items!",
            "giveaway_announcement_text": (
                "Giveaway #{number} Alert! Must be following us & share the stream to enter! "
                "Winner announced in a few minutes!"
            ),
            "flash_sale_announcement_text": (
                "Flash Sale Alert! Grab these deals before the timer runs out!"
            )
        }
    }

    # Load or create config file
    if not os.path.exists(path):
        config.read_dict(defaults)
        save_config(config, path)
    else:
        config.read(path)
        # Ensure all defaults exist
        updated = False
        for section, keys in defaults.items():
            if section not in config:
                config[section] = {}
                updated = True
            for key, value in keys.items():
                if key not in config[section]:
                    config[section][key] = value
                    updated = True
        if updated:
            save_config(config, path)

    return config, path

def save_config(config: configparser.ConfigParser, path: str):
    """
    Save the configuration to the specified path.
    """
    with open(path, "w", encoding="utf-8") as f:
        config.write(f)

# ─── Constants ────────────────────────────────────────────────────────────────

# Maximum bin assignments allowed per subscription tier
TIER_LIMITS = {
    "Trial": 150,
    "Bronze": 50,
    "Silver": 150,
    "Gold": 300
}

# Mapping of Stripe Price IDs to subscription tiers
PRICE_MAP = {
    'price_1RMISXJ7WrcpTNl6dFGmS7v2': 'Bronze',
    'price_1RMIT8J7WrcpTNl6JFldsRLo': 'Silver',
    'price_1RMIXyJ7WrcpTNl6qjTjkG3o': 'Gold',
    'price_1RMIj1J7WrcpTNl6q2jxLwMI': 'Trial'
}

# Stripe API keys and webhook secret from environment (.env)
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

# Validate critical Stripe keys
if not STRIPE_SECRET_KEY:
    raise ValueError("STRIPE_SECRET_KEY is missing in the environment (.env).")
if not STRIPE_PUBLISHABLE_KEY:
    raise ValueError("STRIPE_PUBLISHABLE_KEY is missing in the environment (.env).")
if not STRIPE_WEBHOOK_SECRET:
    raise ValueError("STRIPE_WEBHOOK_SECRET is missing in the environment (.env).")

# Port for the built-in Flask server
PORT = int(os.getenv("PORT", "5000"))
