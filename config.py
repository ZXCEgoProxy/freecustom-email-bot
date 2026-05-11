import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    API_BASE_URL = os.getenv('API_BASE_URL', 'https://api2.freecustom.email')
    DATABASE_PATH = os.getenv('DATABASE_PATH', 'database.db')
    EMAIL_CHECK_INTERVAL = int(os.getenv('EMAIL_CHECK_INTERVAL', 30))
    DEADLINE_CHECK_INTERVAL = int(os.getenv('DEADLINE_CHECK_INTERVAL', 60))
    DEADLINE_WARNING_MINUTES = int(os.getenv('DEADLINE_WARNING_MINUTES', 5))

    @classmethod
    def validate(cls):
        if not cls.BOT_TOKEN:
            raise ValueError("BOT_TOKEN is required in .env file")