"""
Configuration file for Discord Bot
Optimized for Linux server environments (Cyberpanel, VPS, Docker, etc.)
"""

import os
from dotenv import load_dotenv
from pathlib import Path

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
MAX_TOKENS = int(os.getenv('MAX_TOKENS', '150'))
RESTART_DELAY = int(os.getenv('RESTART_DELAY', '5'))

# ============== AI MODEL CONFIGURATION (LINUX) ==============

# For server/VPS hosting, use local models directory
# This works with Cyberpanel, Docker, and most Linux hosting
MODEL_DIR = Path(os.getenv('MODEL_DIR', './models'))
MODEL_DIR.mkdir(parents=True, exist_ok=True)

# Set MODEL_PATH to the models directory
MODEL_PATH = MODEL_DIR

# Model filename
MODEL_NAME = os.getenv(
    'MODEL_NAME',
    "Phi-3-mini-128k-instruct.Q4_0.gguf"
)

# Full path to model file
MODEL_FILE = MODEL_PATH / MODEL_NAME

# ============== OPTIONAL SETTINGS ==============

# Enable/disable debug mode
DEBUG_MODE = os.getenv('DEBUG_MODE', 'False').lower() == 'true'

# Custom status message
BOT_STATUS = os.getenv('BOT_STATUS', 'Use /help for commands')

# Command cooldown (seconds)
COMMAND_COOLDOWN = int(os.getenv('COMMAND_COOLDOWN', '3'))

# Max trivia time (seconds)
TRIVIA_TIMEOUT = int(os.getenv('TRIVIA_TIMEOUT', '60'))

# ============== LINUX SERVER OPTIMIZATIONS ==============

# For low-memory servers, you can reduce these
MAX_MEMORY_MB = int(os.getenv('MAX_MEMORY_MB', '2048'))

# Thread count (adjust based on CPU cores)
N_THREADS = int(os.getenv('N_THREADS', '4'))

# GPU support (usually disabled on servers)
USE_GPU = os.getenv('USE_GPU', 'false').lower() == 'true'

# ============== VALIDATION ==============

def validate_config():
    """Validate that all required config is present"""
    if not DISCORD_TOKEN:
        return False, "Discord token is missing"
    
    if not MODEL_PATH.exists():
        return False, f"Model directory does not exist: {MODEL_PATH}"
    
    # Check if model file exists
    if not MODEL_FILE.exists():
        return False, (
            f"Model file not found: {MODEL_FILE}\n"
            f"Please download the model:\n"
            f"1. cd {MODEL_PATH}\n"
            f"2. wget https://gpt4all.io/models/gguf/Phi-3-mini-128k-instruct.Q4_0.gguf\n"
            f"Or use a different model from https://gpt4all.io/"
        )
    
    return True, "Configuration valid"

def print_config_info():
    """Print configuration information for debugging"""
    print("=" * 50)
    print("🔧 CONFIGURATION")
    print("=" * 50)
    print(f"Environment: Linux Server")
    print(f"Working Directory: {Path.cwd()}")
    print(f"Model Directory: {MODEL_PATH}")
    print(f"Model Name: {MODEL_NAME}")
    print(f"Model File: {MODEL_FILE}")
    print(f"Model Exists: {MODEL_FILE.exists()}")
    print(f"Debug Mode: {DEBUG_MODE}")
    print(f"Max Tokens: {MAX_TOKENS}")
    print(f"Threads: {N_THREADS}")
    print(f"Use GPU: {USE_GPU}")
    print("=" * 50)
    
    is_valid, message = validate_config()
    if is_valid:
        print(f"✅ {message}")
    else:
        print(f"❌ {message}")
    print("=" * 50)

# Auto-print on import if debug mode or run directly
if DEBUG_MODE or __name__ == "__main__":
    print_config_info()