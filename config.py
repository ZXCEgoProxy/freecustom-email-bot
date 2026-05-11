import os
from dotenv import load_dotenv
from urllib.parse import urlparse

# Load .env file if it exists (for local development)
# In production (Railway), environment variables are set directly
load_dotenv(override=False)  # Don't override existing env vars

class Config:
    # Get BOT_TOKEN with multiple fallback options (Railway might use different names)
    BOT_TOKEN = (
        os.environ.get('BOT_TOKEN') or
        os.environ.get('TELEGRAM_BOT_TOKEN') or
        os.environ.get('TG_BOT_TOKEN')
    )

    API_BASE_URL = os.environ.get('API_BASE_URL', 'https://api2.freecustom.email')

    # Database configuration
    DATABASE_URL = os.environ.get('DATABASE_URL')  # Railway PostgreSQL
    DATABASE_PATH = os.environ.get('DATABASE_PATH', 'database.db')  # Fallback for SQLite

    # Determine database type
    USE_POSTGRESQL = bool(DATABASE_URL and DATABASE_URL.startswith('postgresql'))

    # Convert to int with defaults
    EMAIL_CHECK_INTERVAL = int(os.environ.get('EMAIL_CHECK_INTERVAL', 30))
    DEADLINE_CHECK_INTERVAL = int(os.environ.get('DEADLINE_CHECK_INTERVAL', 60))
    DEADLINE_WARNING_MINUTES = int(os.environ.get('DEADLINE_WARNING_MINUTES', 5))

    @classmethod
    def validate(cls):
        if not cls.BOT_TOKEN:
            # Show helpful error with available environment variables for debugging
            available_vars = [k for k in os.environ.keys() if 'TOKEN' in k.upper() or 'BOT' in k.upper()]
            all_vars = list(os.environ.keys())[:10]
            error_msg = "BOT_TOKEN environment variable is required. Please set it in Railway Variables."
            if available_vars:
                error_msg += f" Found token-related vars: {available_vars}"
            else:
                error_msg += f" No token-related environment variables found. Available vars: {all_vars}"
            raise ValueError(error_msg)