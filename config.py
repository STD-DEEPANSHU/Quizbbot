import os
from dotenv import load_dotenv

# Local development ke liye .env load karega (Heroku me ignore ho jayega)
load_dotenv()

# Environment variables
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")
DB_NAME = os.environ.get("DB_NAME", "quiz_bot")

# Debugging ke liye (sirf logs me check karne ke liye)
if not TELEGRAM_TOKEN:
    print("❌ ERROR: TELEGRAM_TOKEN not set")
if not MONGO_URI:
    print("❌ ERROR: MONGO_URI not set")
else:
    print("✅ Config loaded successfully")
