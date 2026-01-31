"""
Configuration file for Discord Bot
This file loads settings from environment variables or .env file
"""

import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# ============== DISCORD CONFIGURATION ==============

DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')

if not DISCORD_TOKEN:
    raise ValueError(
        "❌ DISCORD_TOKEN not found!\n"
        "Please create a .env file and add: DISCORD_TOKEN=your_token_here\n"
        "Or set it as an environment variable."
    )

# ============== BOT SETTINGS ==============

PREFIX = os.getenv('BOT_PREFIX', '.')
MAX_TOKENS = int(os.getenv('MAX_TOKENS', '80'))
RESTART_DELAY = int(os.getenv('RESTART_DELAY', '5'))

# ============== AI MODEL CONFIGURATION ==============

MODEL_PATH = os.getenv(
    'MODEL_PATH',
    r"C:\Users\foxyn\AppData\Local\nomic.ai\GPT4All"
)

MODEL_NAME = os.getenv(
    'MODEL_NAME',
    "Llama-3.2-3B-Instruct-Q4_0.gguf"
)

# ============== OPTIONAL SETTINGS ==============

# Enable/disable debug mode
DEBUG_MODE = os.getenv('DEBUG_MODE', 'False').lower() == 'true'

# Custom status message
BOT_STATUS = os.getenv('BOT_STATUS', 'Use /help for commands')

# Command cooldown (seconds)
COMMAND_COOLDOWN = int(os.getenv('COMMAND_COOLDOWN', '3'))

# Max trivia time (seconds)
TRIVIA_TIMEOUT = int(os.getenv('TRIVIA_TIMEOUT', '60'))

# ============== VALIDATION ==============

def validate_config():
    """Validate that all required config is present"""
    if not DISCORD_TOKEN:
        return False, "Discord token is missing"
    
    if not os.path.exists(MODEL_PATH):
        return False, f"Model path does not exist: {MODEL_PATH}"
    
    return True, "Configuration valid"

# Print config validation on import (optional)
if DEBUG_MODE:
    is_valid, message = validate_config()
    print(f"Config Status: {message}")