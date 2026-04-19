import os
import sys
import asyncio
import subprocess
import psutil
import time
import json
import zipfile
import shutil
import threading
import queue
import random
import string
import re
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.constants import ParseMode
import logging
import sqlite3
from io import BytesIO

# Try to import nest_asyncio (for Pydroid/Colab)
try:
    import nest_asyncio
    nest_asyncio.apply()
except:
    pass

# Try to import GPUtil for GPU monitoring
try:
    import GPUtil
    GPU_AVAILABLE = True
except:
    GPU_AVAILABLE = False

# Try to import nbformat for notebook execution
try:
    import nbformat
    from nbconvert.preprocessors import ExecutePreprocessor
    NOTEBOOK_SUPPORT = True
except:
    NOTEBOOK_SUPPORT = False

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# BOT CONFIGURATION - CHANGE THESE VALUES
# ═══════════════════════════════════════════════════════════════

BOT_TOKEN = "8328344954:AAHqUEyhHg9hKwz9plHyaHh0PZnU0V8txl4"
OWNER_ID = 8528813709
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB for normal users

print(f"🤖 Bot Token: {BOT_TOKEN[:20]}...")
print(f"👤 Owner ID: {OWNER_ID}")

# ═══════════════════════════════════════════════════════════════
# 💾 SIMPLE LOCAL SQLITE DATABASE (WORKS EVERYWHERE)
# ═══════════════════════════════════════════════════════════════

# Use local directory for database
DB_DIR = os.path.join(os.getcwd(), "bot_data")
os.makedirs(DB_DIR, exist_ok=True)
SQLITE_DB = os.path.join(DB_DIR, "bot_data.db")
USER_FILES_DIR = os.path.join(DB_DIR, "user_files")
os.makedirs(USER_FILES_DIR, exist_ok=True)

def migrate_database():
    """Add missing columns to existing database"""
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()
        
        # Check and add missing columns to users table
        cursor.execute("PRAGMA table_info(users)")
        columns = [col[1] for col in cursor.fetchall()]
        
        if 'credits' not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN credits INTEGER DEFAULT 3")
        if 'verified' not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN verified INTEGER DEFAULT 0")
        if 'referred_by' not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN referred_by INTEGER DEFAULT 0")
        if 'referral_code' not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN referral_code TEXT UNIQUE")
        if 'total_referrals' not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN total_referrals INTEGER DEFAULT 0")
        if 'is_premium' not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN is_premium INTEGER DEFAULT 0")
        if 'premium_expiry' not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN premium_expiry TEXT")
        
        conn.commit()
        conn.close()
        logger.info("✅ Database migration completed!")
    except Exception as e:
        logger.error(f"Migration error: {e}")

def init_sqlite_db():
    """Initialize SQLite database with tables"""
    conn = sqlite3.connect(SQLITE_DB)
    cursor = conn.cursor()

    # Users table with all columns
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        banned INTEGER DEFAULT 0,
        first_seen TEXT,
        last_seen TEXT,
        auto_restart INTEGER DEFAULT 0,
        credits INTEGER DEFAULT 3,
        verified INTEGER DEFAULT 0,
        referred_by INTEGER DEFAULT 0,
        referral_code TEXT UNIQUE,
        total_referrals INTEGER DEFAULT 0,
        is_premium INTEGER DEFAULT 0,
        premium_expiry TEXT
    )''')

    # Files table
    cursor.execute('''CREATE TABLE IF NOT EXISTS files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        filename TEXT,
        filepath TEXT,
        file_type TEXT,
        upload_time TEXT,
        main_file TEXT,
        FOREIGN KEY (user_id) REFERENCES users(user_id)
    )''')

    # Packages table
    cursor.execute('''CREATE TABLE IF NOT EXISTS packages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        package_name TEXT,
        install_time TEXT,
        UNIQUE(user_id, package_name)
    )''')

    # Settings table
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')
    
    # Verification sessions table
    cursor.execute('''CREATE TABLE IF NOT EXISTS verification_sessions (
        user_id INTEGER PRIMARY KEY,
        correct_color TEXT,
        options TEXT,
        expires_at TEXT
    )''')

    # Add bot_start_time if not exists
    cursor.execute("SELECT value FROM settings WHERE key = 'bot_start_time'")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO settings (key, value) VALUES ('bot_start_time', ?)", (str(time.time()),))

    conn.commit()
    conn.close()
    logger.info(f"✅ SQLite database initialized at: {SQLITE_DB}")

# Initialize SQLite DB with migration
try:
    init_sqlite_db()
    migrate_database()
    logger.info("✅ SQLite database ready!")
except Exception as e:
    logger.error(f"Database init error: {e}")

# ═══════════════════════════════════════════════════════════════
# PREMIUM SYSTEM FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def is_premium(user_id):
    """Check if user is premium and not expired"""
    if user_id == OWNER_ID:
        return True  # Owner is always premium
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT is_premium, premium_expiry FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        conn.close()
        
        if result and result[0] == 1:
            if result[1]:
                expiry = datetime.fromisoformat(result[1])
                if expiry > datetime.now():
                    return True
                else:
                    remove_expired_premium(user_id)
                    return False
            return True
        return False
    except:
        return False

def remove_expired_premium(user_id):
    """Remove expired premium status"""
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_premium = 0, premium_expiry = NULL WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
    except:
        pass

def add_premium(user_id, duration_str):
    """Add premium to user (e.g., 29d, 20m, 2y)"""
    try:
        match = re.match(r'^(\d+)([dmy])$', duration_str.lower())
        if not match:
            return False, "Invalid format! Use like: 29d, 20m, 2y"
        
        amount = int(match.group(1))
        unit = match.group(2)
        
        if unit == 'd':
            delta = timedelta(days=amount)
        elif unit == 'm':
            delta = timedelta(days=amount * 30)
        elif unit == 'y':
            delta = timedelta(days=amount * 365)
        else:
            return False, "Invalid unit"
        
        expiry = datetime.now() + delta
        
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_premium = 1, premium_expiry = ? WHERE user_id = ?", 
                      (expiry.isoformat(), user_id))
        conn.commit()
        conn.close()
        
        return True, expiry.strftime("%Y-%m-%d")
    except Exception as e:
        return False, str(e)

def remove_premium(user_id):
    """Remove premium from user"""
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_premium = 0, premium_expiry = NULL WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        return True
    except:
        return False

def get_all_premium_users():
    """Get all premium users"""
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, first_name, premium_expiry FROM users WHERE is_premium = 1")
        users = cursor.fetchall()
        conn.close()
        return users
    except:
        return []

def get_all_expired_premium_users():
    """Get all expired premium users"""
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, first_name, premium_expiry FROM users WHERE is_premium = 0 AND premium_expiry IS NOT NULL")
        users = cursor.fetchall()
        conn.close()
        return users
    except:
        return []

def can_upload_large_file(user_id):
    """Check if user can upload files > 50MB"""
    return user_id == OWNER_ID or is_premium(user_id)

# ═══════════════════════════════════════════════════════════════
# CREDIT SYSTEM FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def generate_referral_code(user_id):
    """Generate unique referral code for user"""
    random_str = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"REF{user_id}{random_str}"

def get_user_credits(user_id):
    """Get user credits"""
    if user_id == OWNER_ID:
        return 999999  # Unlimited credits for owner
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT credits FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else 0
    except:
        return 0

def add_credits(user_id, amount):
    """Add credits to user"""
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET credits = credits + ? WHERE user_id = ?", (amount, user_id))
        conn.commit()
        conn.close()
        return True
    except:
        return False

def remove_credits(user_id, amount):
    """Remove credits from user"""
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET credits = MAX(0, credits - ?) WHERE user_id = ?", (amount, user_id))
        conn.commit()
        conn.close()
        return True
    except:
        return False

def deduct_credit_for_runtime(user_id):
    """Deduct 1 credit for 20 minutes runtime"""
    if user_id == OWNER_ID or is_premium(user_id):
        return True
    credits = get_user_credits(user_id)
    if credits >= 1:
        remove_credits(user_id, 1)
        return True
    return False

def process_referral(new_user_id, referral_code):
    """Process referral and give credits"""
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()
        
        cursor.execute("SELECT user_id FROM users WHERE referral_code = ?", (referral_code,))
        result = cursor.fetchone()
        
        if result:
            referrer_id = result[0]
            if referrer_id != new_user_id:
                cursor.execute("UPDATE users SET referred_by = ? WHERE user_id = ?", (referrer_id, new_user_id))
                cursor.execute("UPDATE users SET credits = credits + 5, total_referrals = total_referrals + 1 WHERE user_id = ?", (referrer_id,))
                conn.commit()
                conn.close()
                return referrer_id
        
        conn.close()
        return None
    except:
        return None

def is_user_verified(user_id):
    """Check if user is verified"""
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT verified FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        conn.close()
        return bool(result[0]) if result else False
    except:
        return False

def set_user_verified(user_id):
    """Mark user as verified"""
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET verified = 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        return True
    except:
        return False

# ═══════════════════════════════════════════════════════════════
# COLOR VERIFICATION SYSTEM
# ═══════════════════════════════════════════════════════════════

COLORS = {
    "🔴 Red": "red",
    "🔵 Blue": "blue", 
    "🟢 Green": "green",
    "🟡 Yellow": "yellow",
    "🟠 Orange": "orange",
    "🟣 Purple": "purple",
    "⚫ Black": "black",
    "⚪ White": "white",
    "🟤 Brown": "brown",
    "🌸 Pink": "pink"
}

def generate_verification():
    """Generate color verification challenge"""
    color_names = list(COLORS.keys())
    correct = random.choice(color_names)
    options = random.sample(color_names, 4)
    if correct not in options:
        options[random.randint(0, 3)] = correct
    random.shuffle(options)
    
    return COLORS[correct], options

def create_verification_keyboard(options):
    """Create verification keyboard with color options"""
    keyboard = []
    row = []
    for i, color in enumerate(options):
        row.append(InlineKeyboardButton(color, callback_data=f"verify_{COLORS[color]}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)

# ═══════════════════════════════════════════════════════════════
# MONOSPACE FONT FOR COPYABLE TEXT
# ═══════════════════════════════════════════════════════════════

def mono(text):
    """Format text as monospace for easy copying"""
    return f"`{text}`"

# ═══════════════════════════════════════════════════════════════
# NON-BLOCKING TERMINAL
# ═══════════════════════════════════════════════════════════════

class NonBlockingTerminal:
    """Non-blocking terminal that handles long-running commands and interactive input"""
    
    def __init__(self, user_id, chat_id, bot, cwd=None):
        self.user_id = user_id
        self.chat_id = chat_id
        self.bot = bot
        self.cwd = cwd or os.getcwd()
        self.process = None
        self.output_lines = []
        self.is_running = False
        self.msg_id = None
        self.refresh_task = None
        self.reader_task = None
        self.waiting_for_input = False
        self.input_prompt = ""
        self.showing_termux_keyboard = False
        
    def get_display(self):
        """Get current terminal display"""
        lines = self.output_lines[-20:] if self.output_lines else ["⚡ Terminal Ready"]
        text = f"╭──〔 ⚡ TERMINAL ⚡ 〕──╮\n"
        for l in lines:
            clean_l = l.strip().replace('`', "'")[:55]
            text += f"│ {clean_l}\n"
        
        if self.is_running:
            if self.waiting_for_input:
                text += f"│\n│ 📝 {self.input_prompt}\n╰──────────────────────────╯"
            else:
                text += "│\n│ ⏳ Process running...\n╰──────────────────────────╯"
        elif self.waiting_for_input:
            text += f"│\n│ {self.input_prompt}\n╰──────────────────────────╯"
        else:
            text += "│\n│ $ _\n╰──────────────────────────╯"
        return text
    
    def get_keyboard(self):
        """Get terminal keyboard"""
        if self.is_running:
            return InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Refresh", callback_data="term_refresh"),
                 InlineKeyboardButton("📤 Input", callback_data="term_input_mode")],
                [InlineKeyboardButton("⏹️ Stop", callback_data="term_stop"),
                 InlineKeyboardButton("🔴 Force Kill", callback_data="term_kill")]
            ])
        elif self.waiting_for_input:
            return InlineKeyboardMarkup([
                [InlineKeyboardButton("📤 Send Input", callback_data="term_send_input")],
                [InlineKeyboardButton("❌ Cancel", callback_data="term_cancel_input"),
                 InlineKeyboardButton("🔴 Stop", callback_data="term_stop")]
            ])
        elif self.showing_termux_keyboard:
            return self.get_termux_keyboard()
        else:
            return InlineKeyboardMarkup([
                [InlineKeyboardButton("🐍 Python", callback_data="term_python"),
                 InlineKeyboardButton("🟢 Node", callback_data="term_node"),
                 InlineKeyboardButton("📜 Bash", callback_data="term_bash")],
                [InlineKeyboardButton("📦 Pip", callback_data="term_pip_mode"),
                 InlineKeyboardButton("📝 Input Cmd", callback_data="term_input_mode"),
                 InlineKeyboardButton("⚡ Custom", callback_data="term_custom_mode")],
                [InlineKeyboardButton("📂 LS", callback_data="term_ls"),
                 InlineKeyboardButton("📁 PWD", callback_data="term_pwd"),
                 InlineKeyboardButton("⌨️ Termux KB", callback_data="term_show_termux")],
                [InlineKeyboardButton("🧹 Clear", callback_data="term_clear"),
                 InlineKeyboardButton("🔴 Close", callback_data="term_close")]
            ])
    
    def get_termux_keyboard(self):
        """Termux-style keyboard"""
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("ESC", callback_data="term_key_esc"),
             InlineKeyboardButton("TAB", callback_data="term_key_tab"),
             InlineKeyboardButton("↑", callback_data="term_key_up"),
             InlineKeyboardButton("↓", callback_data="term_key_down")],
            [InlineKeyboardButton("CTRL", callback_data="term_key_ctrl"),
             InlineKeyboardButton("ALT", callback_data="term_key_alt"),
             InlineKeyboardButton("←", callback_data="term_key_left"),
             InlineKeyboardButton("→", callback_data="term_key_right")],
            [InlineKeyboardButton("CTRL+C", callback_data="term_key_ctrl_c"),
             InlineKeyboardButton("CTRL+Z", callback_data="term_key_ctrl_z"),
             InlineKeyboardButton("CTRL+D", callback_data="term_key_ctrl_d"),
             InlineKeyboardButton("CTRL+L", callback_data="term_key_ctrl_l")],
            [InlineKeyboardButton("HOME", callback_data="term_key_home"),
             InlineKeyboardButton("END", callback_data="term_key_end"),
             InlineKeyboardButton("PGUP", callback_data="term_key_pgup"),
             InlineKeyboardButton("PGDN", callback_data="term_key_pgdn")],
            [InlineKeyboardButton("/", callback_data="term_key_slash"),
             InlineKeyboardButton("-", callback_data="term_key_dash"),
             InlineKeyboardButton("⌫", callback_data="term_key_backspace")],
            [InlineKeyboardButton("🔙 Back", callback_data="term_back_main")]
        ])
    
    async def execute_command(self, command):
        """Execute command in background without blocking"""
        if self.is_running:
            self.output_lines.append(f"⚠️ Already running a command")
            return False
        
        self.output_lines.append(f"$ {command[:50]}..." if len(command) > 50 else f"$ {command}")
        self.is_running = True
        self.waiting_for_input = False
        
        try:
            self.process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.PIPE,
                cwd=self.cwd,
                executable='/bin/bash' if os.path.exists('/bin/bash') else None
            )
            
            self.reader_task = asyncio.create_task(self._read_output())
            self.refresh_task = asyncio.create_task(self._auto_refresh())
            return True
        except Exception as e:
            self.output_lines.append(f"❌ Error: {str(e)}")
            self.is_running = False
            return False
    
    async def _read_output(self):
        """Read process output continuously"""
        try:
            while self.is_running and self.process:
                try:
                    line = await asyncio.wait_for(self.process.stdout.readline(), timeout=0.3)
                    if not line:
                        break
                    decoded = line.decode('utf-8', errors='ignore').rstrip()
                    if decoded:
                        self.output_lines.append(decoded)
                        if len(self.output_lines) > 200:
                            self.output_lines = self.output_lines[-200:]
                        
                        # Auto-detect input prompts
                        prompt_keywords = ['enter', 'input', 'phone', 'number', ':', '?', '>', 'login', 'password', 'code', 'otp']
                        decoded_lower = decoded.lower()
                        if any(kw in decoded_lower for kw in prompt_keywords):
                            if not self.waiting_for_input:
                                self.waiting_for_input = True
                                self.input_prompt = "📝 Process is waiting for input..."
                except asyncio.TimeoutError:
                    continue
                except:
                    break
            
            if self.process:
                await self.process.wait()
                self.is_running = False
                self.waiting_for_input = False
                self.output_lines.append(f"✅ Completed (exit: {self.process.returncode})")
                self.process = None
            
            if self.refresh_task:
                self.refresh_task.cancel()
                self.refresh_task = None
            
            await self._update_message()
            
        except Exception as e:
            self.output_lines.append(f"❌ Reader error: {str(e)}")
            self.is_running = False
            self.waiting_for_input = False
    
    async def _auto_refresh(self):
        """Auto-refresh display every 2.5 seconds"""
        try:
            while self.is_running:
                await asyncio.sleep(2.5)
                await self._update_message()
        except asyncio.CancelledError:
            pass
    
    async def _update_message(self):
        """Update the terminal message"""
        try:
            await self.bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=self.msg_id,
                text=self.get_display(),
                reply_markup=self.get_keyboard()
            )
        except:
            pass
    
    async def send_input(self, input_text):
        """Send input to running process"""
        if self.process and self.is_running:
            try:
                self.process.stdin.write((input_text + '\n').encode())
                await self.process.stdin.drain()
                self.output_lines.append(f"📤 Sent: {input_text}")
                self.waiting_for_input = False
                await self._update_message()
                return True
            except:
                return False
        return False
    
    async def stop(self):
        """Stop the running process"""
        if self.process:
            try:
                self.process.terminate()
                await asyncio.sleep(1)
                if self.process.returncode is None:
                    self.process.kill()
                self.is_running = False
                self.waiting_for_input = False
                self.output_lines.append("⏹️ Process stopped")
            except:
                pass
        self.is_running = False
        if self.refresh_task:
            self.refresh_task.cancel()
        if self.reader_task:
            self.reader_task.cancel()
        await self._update_message()
    
    async def force_kill(self):
        """Force kill the process"""
        if self.process:
            try:
                self.process.kill()
                self.is_running = False
                self.waiting_for_input = False
                self.output_lines.append("🔴 Process killed")
            except:
                pass
        self.is_running = False
        if self.refresh_task:
            self.refresh_task.cancel()
        if self.reader_task:
            self.reader_task.cancel()
        await self._update_message()

# ═══════════════════════════════════════════════════════════════
# ASYNC PACKAGE INSTALLER
# ═══════════════════════════════════════════════════════════════

async def install_package_async(package_name, msg, user_id):
    """Non-blocking async package installer"""
    log_lines = []
    
    async def update_logs():
        try:
            show_lines = log_lines[-12:] if log_lines else ["Installing..."]
            text = f"╭────〔 📦 INSTALLING {package_name} 📦 〕────╮\n"
            text += "\n".join(f"│ {l[:50]}" for l in show_lines)
            text += "\n╰──────────────────────────────────╯"
            await msg.edit_text(text)
        except:
            pass
    
    async def run_install():
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, '-m', 'pip', 'install', package_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )
            
            log_lines.append(f"Starting installation of {package_name}...")
            await update_logs()
            
            while True:
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=30)
                    if not line:
                        break
                    decoded = line.decode().strip()
                    if decoded:
                        log_lines.append(decoded)
                        await update_logs()
                except asyncio.TimeoutError:
                    log_lines.append("Still installing...")
                    await update_logs()
            
            await proc.wait()
            
            if proc.returncode == 0:
                try:
                    conn = sqlite3.connect(SQLITE_DB)
                    c = conn.cursor()
                    c.execute("INSERT OR REPLACE INTO packages VALUES (?, ?, ?)",
                             (user_id, package_name, datetime.now().isoformat()))
                    conn.commit()
                    conn.close()
                except:
                    pass
                
                log_lines.append(f"✅ {package_name} installed successfully!")
            else:
                log_lines.append(f"❌ Installation failed")
            
            await update_logs()
            
            final_text = f"╭────〔 ✅ INSTALLED ✅ 〕────╮\n│ 📦 {package_name}\n│ ✅ Successfully installed!\n╰──────────────────────────────────╯"
            await msg.edit_text(final_text)
            
        except Exception as e:
            log_lines.append(f"❌ Error: {str(e)}")
            await update_logs()
    
    asyncio.create_task(run_install())
    return True

# ═══════════════════════════════════════════════════════════════
# PROCESS MONITOR
# ═══════════════════════════════════════════════════════════════

class ProcessMonitor:
    def __init__(self, process, file_name, user_id, cmd):
        self.process = process
        self.file_name = file_name
        self.user_id = user_id
        self.cmd = cmd
        self.start_time = time.time()
        self.logs = []
        self.running = True
        self.credit_check_time = time.time()
        self.reader_thread = threading.Thread(target=self._read_output, daemon=True)
        self.reader_thread.start()

    def _read_output(self):
        try:
            for line in iter(self.process.stdout.readline, ''):
                if not self.running:
                    break
                if line:
                    self.logs.append(line.strip())
                    if len(self.logs) > 100:
                        self.logs = self.logs[-100:]
        except:
            pass

    def get_logs(self, lines=20):
        return self.logs[-lines:] if self.logs else ["No output yet..."]

    def stop(self):
        self.running = False
        try:
            self.process.terminate()
            self.process.wait(timeout=5)
        except:
            try:
                self.process.kill()
            except:
                pass

# ═══════════════════════════════════════════════════════════════
# DATABASE FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def save_user_to_db(user_id, username, first_name, referred_by=None):
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()
        
        current_time = datetime.now().isoformat()
        
        cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        exists = cursor.fetchone()
        
        if exists:
            cursor.execute('''UPDATE users SET 
                username = ?, first_name = ?, last_seen = ? 
                WHERE user_id = ?''',
                (username, first_name, current_time, user_id))
        else:
            referral_code = generate_referral_code(user_id)
            cursor.execute('''INSERT INTO users 
                (user_id, username, first_name, last_seen, first_seen, referral_code, credits, verified) 
                VALUES (?, ?, ?, ?, ?, ?, 3, 0)''',
                (user_id, username, first_name, current_time, current_time, referral_code))
        
        if referred_by:
            cursor.execute("UPDATE users SET referred_by = ? WHERE user_id = ?", (referred_by, user_id))
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Save user error: {e}")
        return False

def get_user_auto_restart(user_id):
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT auto_restart FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        conn.close()
        return bool(result[0]) if result else False
    except:
        return False

def set_user_auto_restart(user_id, enabled):
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET auto_restart = ? WHERE user_id = ?", (1 if enabled else 0, user_id))
        conn.commit()
        conn.close()
        return True
    except:
        return False

def save_file_to_db(user_id, filename, filepath, file_type, main_file=None):
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()
        cursor.execute('''INSERT INTO files 
            (user_id, filename, filepath, file_type, upload_time, main_file) 
            VALUES (?, ?, ?, ?, ?, ?)''',
            (user_id, filename, filepath, file_type, datetime.now().isoformat(), main_file))
        file_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return file_id
    except:
        return None

def get_user_files(user_id):
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT id, filename, filepath, file_type, upload_time, main_file FROM files WHERE user_id = ? ORDER BY id DESC", (user_id,))
        files = []
        for row in cursor.fetchall():
            files.append({
                'id': row[0],
                'name': row[1],
                'path': row[2],
                'type': row[3],
                'upload_time': row[4],
                'main_file': row[5]
            })
        conn.close()
        return files
    except:
        return []

def get_file_by_id(user_id, file_id):
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT id, filename, filepath, file_type, upload_time, main_file FROM files WHERE user_id = ? AND id = ?", (user_id, file_id))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {
                'id': row[0],
                'name': row[1],
                'path': row[2],
                'type': row[3],
                'upload_time': row[4],
                'main_file': row[5]
            }
        return None
    except:
        return None

def delete_file_from_db(user_id, file_id):
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM files WHERE user_id = ? AND id = ?", (user_id, file_id))
        conn.commit()
        conn.close()
        return True
    except:
        return False

def update_file_main_file(user_id, file_id, main_file):
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()
        cursor.execute("UPDATE files SET main_file = ? WHERE user_id = ? AND id = ?", (main_file, user_id, file_id))
        conn.commit()
        conn.close()
        return True
    except:
        return False

def get_user_packages(user_id):
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT package_name FROM packages WHERE user_id = ?", (user_id,))
        packages = [row[0] for row in cursor.fetchall()]
        conn.close()
        return packages
    except:
        return []

def is_user_banned(user_id):
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT banned FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        conn.close()
        return bool(result[0]) if result else False
    except:
        return False

def ban_user(user_id):
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET banned = 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
    except:
        pass

def unban_user(user_id):
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET banned = 0 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
    except:
        pass

def get_bot_start_time():
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'bot_start_time'")
        result = cursor.fetchone()
        conn.close()
        return float(result[0]) if result else time.time()
    except:
        return time.time()

def get_user_info(user_id):
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, username, first_name, credits, is_premium, premium_expiry, total_referrals, first_seen FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {
                'user_id': row[0],
                'username': row[1],
                'first_name': row[2],
                'credits': row[3],
                'is_premium': row[4],
                'premium_expiry': row[5],
                'total_referrals': row[6],
                'first_seen': row[7]
            }
        return None
    except:
        return None

def get_all_users_count():
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        count = cursor.fetchone()[0]
        conn.close()
        return count
    except:
        return 0

def get_banned_users_count():
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users WHERE banned = 1")
        count = cursor.fetchone()[0]
        conn.close()
        return count
    except:
        return 0

def get_total_credits():
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT SUM(credits) FROM users")
        total = cursor.fetchone()[0] or 0
        conn.close()
        return total
    except:
        return 0

# Global variables
running_processes = {}
terminal_sessions = {}
user_file_browsers = {}

# TNY Bold Font Mapping
def to_tny_bold(text):
    if not text:
        return text
    normal = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
    bold = '𝐀𝐁𝐂𝐃𝐄𝐅𝐆𝐇𝐈𝐉𝐊𝐋𝐌𝐍𝐎𝐏𝐐𝐑𝐒𝐓𝐔𝐕𝐖𝐗𝐘𝐙𝐚𝐛𝐜𝐝𝐞𝐟𝐠𝐡𝐢𝐣𝐤𝐥𝐦𝐧𝐨𝐩𝐪𝐫𝐬𝐭𝐮𝐯𝐰𝐱𝐲𝐳𝟎𝟏𝟐𝟑𝟒𝟓𝟔𝟕𝟖𝟗'
    trans = str.maketrans(normal, bold)
    return text.translate(trans)

# ═══════════════════════════════════════════════════════════════
# UNIVERSAL EXECUTOR
# ═══════════════════════════════════════════════════════════════

def ensure_node_installed():
    try:
        subprocess.run(['node', '--version'], capture_output=True, check=True)
        return True
    except:
        return False

def prepare_universal_python(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        universal_lines = [
            "# -*- coding: utf-8 -*-",
            "import sys, os, subprocess",
            "def ensure_pip_packages():",
            "    try:",
            "        import importlib",
            "        for line in open(__file__).readlines():",
            "            if 'import ' in line or 'from ' in line:",
            "                parts = line.strip().split()",
            "                if len(parts) > 1:",
            "                    pkg = parts[1].split('.')[0]",
            "                    if pkg not in ['sys', 'os', 'subprocess', 'importlib']:",
            "                        try: importlib.import_module(pkg)",
            "                        except: subprocess.run([sys.executable, '-m', 'pip', 'install', pkg, '-q'])",
            "    except: pass",
            "ensure_pip_packages()",
            ""
        ]

        for line in content.split('\n'):
            stripped = line.lstrip()
            indent = line[:len(line) - len(stripped)]
            if stripped.startswith('!pip'):
                cmd = stripped[1:].strip()
                universal_lines.append(f'{indent}subprocess.run([sys.executable, "-m", "pip", "install"] + "{cmd}".split()[2:], capture_output=True)')
            elif stripped.startswith('!apt'):
                universal_lines.append(f'{indent}# Skipped: {line}')
            elif stripped.startswith('!'):
                cmd = stripped[1:].strip()
                universal_lines.append(f'{indent}subprocess.run("{cmd}", shell=True)')
            else:
                universal_lines.append(line)

        universal_path = file_path.replace('.py', '_universal.py')
        if universal_path == file_path:
            universal_path = file_path + '.universal.py'
        with open(universal_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(universal_lines))
        return universal_path
    except:
        return file_path

def prepare_universal_js(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        universal_lines = [
            "const { execSync } = require('child_process');",
            "function installIfMissing(pkg) {",
            "    try { require.resolve(pkg); } catch(e) {",
            "        execSync('npm install ' + pkg, {stdio: 'inherit'});",
            "    }",
            "}",
            "['node-telegram-bot-api', 'telegraf', 'express', 'axios'].forEach(installIfMissing);",
            content
        ]
        universal_path = file_path.replace('.js', '_universal.js')
        if universal_path == file_path:
            universal_path = file_path + '.universal.js'
        with open(universal_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(universal_lines))
        return universal_path
    except:
        return file_path

def get_user_dir(user_id):
    user_dir = os.path.join(USER_FILES_DIR, str(user_id))
    os.makedirs(user_dir, exist_ok=True)
    return user_dir

# ═══════════════════════════════════════════════════════════════
# MAIN KEYBOARD
# ═══════════════════════════════════════════════════════════════

def get_main_keyboard():
    keyboard = [
        [KeyboardButton("🌺" + to_tny_bold("UPLOAD FILES") + "🌺"), KeyboardButton("🍃" + to_tny_bold("CHOOSE FILES") + "🍃")],
        [KeyboardButton("⚡" + to_tny_bold("TERMINAL") + "⚡"), KeyboardButton("🔋" + to_tny_bold("AUTO RESTART") + "🔋")],
        [KeyboardButton("🪴" + to_tny_bold("MANUAL INSTALL") + "🪴"), KeyboardButton("📦" + to_tny_bold("INSTALLED PACKAGE") + "📦")],
        [KeyboardButton("🏓" + to_tny_bold("PING") + "🏓"), KeyboardButton("📊" + to_tny_bold("STATUS") + "📊"), KeyboardButton("🎛️" + to_tny_bold("CONSOLE") + "🎛️")],
        [KeyboardButton("👤" + to_tny_bold("MY ACCOUNT") + "👤"), KeyboardButton("👀" + to_tny_bold("MEET OWNER") + "👀")],
        [KeyboardButton("💰" + to_tny_bold("BUY CREDITS") + "💰"), KeyboardButton("🔗" + to_tny_bold("REFERRAL") + "🔗")],
        [KeyboardButton("🧊" + to_tny_bold("SETTINGS") + "🧊"), KeyboardButton("🌿" + to_tny_bold("GUIDE") + "🌿")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_settings_keyboard():
    keyboard = [
        [KeyboardButton("⚡" + to_tny_bold("BAN USERS") + "⚡"), KeyboardButton("🌺" + to_tny_bold("UNBAN USERS") + "🌺")],
        [KeyboardButton("🥀" + to_tny_bold("BANNED USERS") + "🥀"), KeyboardButton("🍻" + to_tny_bold("ALL USERS") + "🍻")],
        [KeyboardButton("🔮" + to_tny_bold("ANNOUNCEMENT") + "🔮")],
        [KeyboardButton("💎" + to_tny_bold("ADD COINS") + "💎"), KeyboardButton("💸" + to_tny_bold("REMOVE COINS") + "💸")],
        [KeyboardButton("⭐" + to_tny_bold("ADD PREMIUM") + "⭐"), KeyboardButton("❌" + to_tny_bold("REMOVE PREMIUM") + "❌")],
        [KeyboardButton("👑" + to_tny_bold("ALL PREMIUM") + "👑"), KeyboardButton("⌛" + to_tny_bold("EXPIRED PREMIUM") + "⌛")],
        [KeyboardButton("🍁" + to_tny_bold("BACK") + "🍁")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ═══════════════════════════════════════════════════════════════
# UTILITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def is_banned(user_id):
    return is_user_banned(user_id)

def is_owner(user_id):
    return user_id == OWNER_ID

def format_bytes(bytes_val):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_val < 1024.0:
            return f"{bytes_val:.2f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.2f} PB"

def format_time(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours}H {minutes}M {secs}S"

def get_progress_bar(value, max_value, length=10):
    if max_value == 0:
        filled = 0
    else:
        filled = int((value / max_value) * length)
    bar = "█" * filled + "░" * (length - filled)
    return f"[{bar}]"

def get_system_stats():
    cpu_percent = psutil.cpu_percent(interval=1)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    stats = {
        'cpu_percent': cpu_percent,
        'memory_percent': memory.percent,
        'memory_used': memory.used,
        'memory_total': memory.total,
        'disk_percent': disk.percent,
        'disk_used': disk.used,
        'disk_total': disk.total
    }
    if GPU_AVAILABLE:
        try:
            gpus = GPUtil.getGPUs()
            if gpus:
                gpu = gpus[0]
                stats['gpu_percent'] = gpu.load * 100
                stats['gpu_memory_used'] = gpu.memoryUsed
                stats['gpu_memory_total'] = gpu.memoryTotal
        except:
            pass
    return stats

# ═══════════════════════════════════════════════════════════════
# FILE BROWSER FUNCTIONS
# ═══════════════════════════════════════════════════════════════

class FileBrowser:
    def __init__(self, user_id, base_path, file_id):
        self.user_id = user_id
        self.base_path = base_path
        self.file_id = file_id
        self.current_path = base_path
        self.msg_id = None
        self.chat_id = None
    
    def list_items(self):
        items = []
        try:
            for item in os.listdir(self.current_path):
                full_path = os.path.join(self.current_path, item)
                rel_path = os.path.relpath(full_path, self.base_path)
                if os.path.isdir(full_path):
                    items.append(('folder', item, rel_path))
                else:
                    items.append(('file', item, rel_path))
        except:
            pass
        return sorted(items, key=lambda x: (x[0] != 'folder', x[1].lower()))
    
    def get_display(self):
        rel = os.path.relpath(self.current_path, self.base_path)
        if rel == '.':
            rel = 'Root'
        items = self.list_items()
        text = f"╭──〔 📂 {rel} 📂 〕──╮\n"
        if not items:
            text += "│ (Empty folder)\n"
        else:
            for item_type, name, _ in items[:15]:
                icon = "📁" if item_type == 'folder' else "📄"
                text += f"│ {icon} {name[:40]}\n"
            if len(items) > 15:
                text += f"│ ... and {len(items) - 15} more\n"
        text += "╰────────────────────╯"
        return text
    
    def get_keyboard(self):
        items = self.list_items()
        keyboard = []
        
        for item_type, name, rel_path in items[:12]:
            if item_type == 'folder':
                keyboard.append([InlineKeyboardButton(f"📁 {name[:30]}", callback_data=f"browse_folder_{rel_path}")])
            else:
                keyboard.append([InlineKeyboardButton(f"📄 {name[:30]}", callback_data=f"browse_select_{rel_path}")])
        
        nav_row = []
        if os.path.normpath(self.current_path) != os.path.normpath(self.base_path):
            nav_row.append(InlineKeyboardButton("⬆️ Up", callback_data="browse_up"))
        nav_row.append(InlineKeyboardButton("🏠 Root", callback_data="browse_root"))
        nav_row.append(InlineKeyboardButton("🔙 Back", callback_data=f"file_{self.file_id}"))
        keyboard.append(nav_row)
        
        return InlineKeyboardMarkup(keyboard)

# ═══════════════════════════════════════════════════════════════
# COMMAND HANDLERS
# ═══════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if is_banned(user_id):
        keyboard = [[InlineKeyboardButton(to_tny_bold("CONTACT OWNER"), url=f"tg://openmessage?user_id={OWNER_ID}")]]
        await update.message.reply_text(to_tny_bold("YOU'RE BANNED... CONTACT OWNER"), reply_markup=InlineKeyboardMarkup(keyboard))
        return

    args = context.args
    referred_by = None
    if args and args[0].startswith('REF'):
        referred_by = process_referral(user_id, args[0])
    
    save_user_to_db(user_id, update.effective_user.username, update.effective_user.first_name, referred_by)
    
    if referred_by:
        try:
            await context.bot.send_message(
                chat_id=referred_by,
                text=f"🎉 {to_tny_bold('NEW REFERRAL!')}\n\n💰 {to_tny_bold('+5 Credits added to your account!')}"
            )
        except:
            pass

    if not is_user_verified(user_id):
        await show_verification(update, context, user_id)
        return

    await show_welcome_message(update, user_id)

async def show_verification(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id):
    correct_color, options = generate_verification()
    
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO verification_sessions (user_id, correct_color, options, expires_at) VALUES (?, ?, ?, ?)",
                      (user_id, correct_color, json.dumps(options), (datetime.now() + timedelta(minutes=5)).isoformat()))
        conn.commit()
        conn.close()
    except:
        pass
    
    color_name = correct_color
    for name, value in COLORS.items():
        if value == correct_color:
            color_name = name
            break
    
    await update.message.reply_text(
        f"🔐 {to_tny_bold('VERIFICATION REQUIRED')}\n\n"
        f"🎨 {to_tny_bold('Select the color:')} {color_name}\n\n"
        f"⏰ {to_tny_bold('You have 5 minutes to verify')}",
        reply_markup=create_verification_keyboard(options)
    )

async def show_welcome_message(update: Update, user_id):
    if user_id == OWNER_ID:
        credits_display = "♾️ Unlimited"
        premium_status = "👑 Owner"
    else:
        credits = get_user_credits(user_id)
        credits_display = str(credits)
        premium_status = "⭐ Premium" if is_premium(user_id) else "🆓 Free"
    
    welcome_text = f"""╭────────〔 🌺 {to_tny_bold("WELCOME")} 🌺 〕────────╮
│
│ 👋 {to_tny_bold("HELLO")} {update.effective_user.first_name}!
│
│ 🍃 {to_tny_bold("HOSTING BOT")}
│ ✨ {to_tny_bold("UNIVERSAL EDITION")}
│
│ 💰 {to_tny_bold("CREDITS:")} {credits_display}
│ 👑 {to_tny_bold("STATUS:")} {premium_status}
│ ⏱️ {to_tny_bold("1 Credit = 20 Minutes")}
│
│ 🔗 {to_tny_bold("Refer & Earn 5 Credits!")}
│
╰──────────────────────────────────╯"""
    await update.message.reply_text(welcome_text, reply_markup=get_main_keyboard())

async def handle_verification_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    
    if not data.startswith("verify_"):
        return
    
    selected_color = data.replace("verify_", "")
    
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT correct_color FROM verification_sessions WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        conn.close()
    except:
        result = None
    
    if not result:
        await query.answer("Session expired!")
        await query.edit_message_text("❌ Verification session expired. Please /start again.")
        return
    
    correct_color = result[0]
    
    if selected_color == correct_color:
        set_user_verified(user_id)
        await query.answer("✅ Verified!")
        
        if user_id == OWNER_ID:
            credits_display = "♾️ Unlimited"
            premium_status = "👑 Owner"
        else:
            credits = get_user_credits(user_id)
            credits_display = str(credits)
            premium_status = "⭐ Premium" if is_premium(user_id) else "🆓 Free"
        
        welcome_text = f"""╭────────〔 🌺 {to_tny_bold("WELCOME")} 🌺 〕────────╮
│
│ 👋 {to_tny_bold("HELLO")} {query.from_user.first_name}!
│
│ 💰 {to_tny_bold("CREDITS:")} {credits_display}
│ 👑 {to_tny_bold("STATUS:")} {premium_status}
│ ⏱️ {to_tny_bold("1 Credit = 20 Minutes")}
│
│ 🔗 {to_tny_bold("Refer & Earn 5 Credits!")}
│
╰──────────────────────────────────╯"""
        await query.edit_message_text(welcome_text)
        await query.message.reply_text("✅ Use buttons below to get started!", reply_markup=get_main_keyboard())
    else:
        await query.answer("❌ Wrong color!")
        correct_color_name = correct_color
        for name, value in COLORS.items():
            if value == correct_color:
                correct_color_name = name
                break
        await query.edit_message_text(f"❌ Wrong! The correct color was {correct_color_name}\nPlease /start again.")

async def handle_my_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_banned(user_id):
        return
    
    if user_id == OWNER_ID:
        text = f"""╭────────〔 👤 {to_tny_bold("MY ACCOUNT")} 👤 〕────────╮
│
│ 🆔 {to_tny_bold("User ID:")} {mono(str(user_id))}
│ 👤 {to_tny_bold("Name:")} {update.effective_user.first_name}
│ 📛 {to_tny_bold("Username:")} @{update.effective_user.username or 'N/A'}
│
│ 💰 {to_tny_bold("Credits:")} ♾️ Unlimited
│ 👑 {to_tny_bold("Status:")} 👑 Owner
│ 📅 {to_tny_bold("Premium Expiry:")} Never
│
│ 👥 {to_tny_bold("Total Referrals:")} -
│ 📆 {to_tny_bold("Joined:")} -
│
╰──────────────────────────────────╯"""
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        return
    
    user_info = get_user_info(user_id)
    if not user_info:
        await update.message.reply_text("❌ User not found! Please /start")
        return
    
    premium_status = "⭐ Premium" if user_info['is_premium'] else "🆓 Free"
    if user_info['is_premium']:
        expiry_text = user_info['premium_expiry'][:10] if user_info['premium_expiry'] else "N/A"
    else:
        expiry_text = "N/A"
    
    text = f"""╭────────〔 👤 {to_tny_bold("MY ACCOUNT")} 👤 〕────────╮
│
│ 🆔 {to_tny_bold("User ID:")} {mono(str(user_info['user_id']))}
│ 👤 {to_tny_bold("Name:")} {user_info['first_name']}
│ 📛 {to_tny_bold("Username:")} @{user_info['username'] or 'N/A'}
│
│ 💰 {to_tny_bold("Credits:")} {user_info['credits']}
│ 👑 {to_tny_bold("Status:")} {premium_status}
│ 📅 {to_tny_bold("Premium Expiry:")} {expiry_text}
│
│ 👥 {to_tny_bold("Total Referrals:")} {user_info['total_referrals']}
│ 📆 {to_tny_bold("Joined:")} {user_info['first_seen'][:10] if user_info['first_seen'] else 'N/A'}
│
╰──────────────────────────────────╯"""
    
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def handle_upload_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_banned(user_id):
        return
    
    if not is_user_verified(user_id):
        await update.message.reply_text(f"❌ {to_tny_bold('Please verify first! Use /start')}")
        return

    max_size_text = "Unlimited" if can_upload_large_file(user_id) else "50MB"
    await update.message.reply_text(
        f"🍃 {to_tny_bold('SEND YOUR FILE')}\n\n"
        f"✨ {to_tny_bold('SUPPORTED FORMATS:')}\n"
        f"📁 {to_tny_bold('PY, JS, ZIP, TXT, JSON, SH, HTML, CSS, IPYNB')}\n\n"
        f"📏 {to_tny_bold('Max file size:')} {max_size_text}"
    )
    context.user_data['waiting_for_file'] = True

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_banned(user_id):
        return

    document = update.message.document
    file_name = document.file_name
    file_size = document.file_size
    
    if not can_upload_large_file(user_id) and file_size > MAX_FILE_SIZE:
        await update.message.reply_text(
            f"❌ {to_tny_bold('FILE TOO LARGE!')}\n\n"
            f"📏 {to_tny_bold('Max size: 50MB')}\n"
            f"⭐ {to_tny_bold('Premium users can upload larger files!')}"
        )
        return

    allowed_extensions = ['.py', '.js', '.zip', '.txt', '.json', '.sh', '.html', '.css', '.ipynb']
    if not any(file_name.endswith(ext) for ext in allowed_extensions):
        await update.message.reply_text(f"❌ {to_tny_bold('UNSUPPORTED FILE TYPE')}")
        return

    msg = await update.message.reply_text(f"⏳ {to_tny_bold('DOWNLOADING...')}")

    user_dir = get_user_dir(user_id)
    file_path = os.path.join(user_dir, file_name)

    file = await document.get_file()
    await file.download_to_drive(file_path)

    if file_name.endswith('.zip'):
        try:
            extract_path = file_path.replace('.zip', '')
            os.makedirs(extract_path, exist_ok=True)

            with zipfile.ZipFile(file_path, 'r') as zip_ref:
                zip_ref.extractall(extract_path)

            file_id = save_file_to_db(user_id, file_name, file_path, 'zip', None)
            await show_file_browser(msg, user_id, file_id, extract_path)
        except Exception as e:
            await msg.edit_text(f"❌ {to_tny_bold('ZIP ERROR:')}\n{str(e)}")
    else:
        file_type = 'python' if file_name.endswith('.py') else 'javascript' if file_name.endswith('.js') else 'notebook' if file_name.endswith('.ipynb') else 'shell' if file_name.endswith('.sh') else 'other'
        file_id = save_file_to_db(user_id, file_name, file_path, file_type, None)
        await show_file_details_in_message(msg, user_id, file_id)

async def show_file_browser(msg, user_id, file_id, base_path):
    browser = FileBrowser(user_id, base_path, file_id)
    user_file_browsers[f"{user_id}_{file_id}"] = browser
    browser.msg_id = msg.message_id
    browser.chat_id = msg.chat_id
    
    await msg.edit_text(
        browser.get_display(),
        reply_markup=browser.get_keyboard()
    )

async def show_file_details_in_message(msg, user_id, file_id):
    file_info = get_file_by_id(user_id, file_id)
    if not file_info:
        await msg.edit_text(f"❌ {to_tny_bold('FILE NOT FOUND')}")
        return
    
    file_path = file_info.get('main_file') or file_info['path']
    file_name = file_info['name']
    process_key = f"{user_id}_{os.path.basename(file_path)}"
    
    is_running = process_key in running_processes and running_processes[process_key].process.poll() is None
    status = "🟢 RUNNING" if is_running else "🔴 STOPPED"
    
    text = f"""╭────────〔 📄 {to_tny_bold("FILE DETAILS")} 📄 〕────────╮
│
│ 📁 {to_tny_bold("NAME:")} {file_name}
│ 📊 {to_tny_bold("STATUS:")} {status}
│ 📅 {to_tny_bold("UPLOADED:")} {file_info.get('upload_time', 'Unknown')[:10]}
│
╰──────────────────────────────────╯"""
    
    keyboard = [
        [InlineKeyboardButton(to_tny_bold("START"), callback_data=f"start_{file_id}")],
        [InlineKeyboardButton(to_tny_bold("STOP"), callback_data=f"stop_{file_id}")],
        [InlineKeyboardButton(to_tny_bold("LOGS"), callback_data=f"logs_{file_id}")],
        [InlineKeyboardButton(to_tny_bold("DELETE"), callback_data=f"delete_{file_id}")]
    ]
    
    await msg.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_browse_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    
    await query.answer()
    
    if data == "browse_up":
        for key, browser in user_file_browsers.items():
            if browser.msg_id == query.message.message_id:
                parent = os.path.dirname(browser.current_path)
                if parent.startswith(browser.base_path):
                    browser.current_path = parent
                await query.edit_message_text(
                    browser.get_display(),
                    reply_markup=browser.get_keyboard()
                )
                return
                
    elif data == "browse_root":
        for key, browser in user_file_browsers.items():
            if browser.msg_id == query.message.message_id:
                browser.current_path = browser.base_path
                await query.edit_message_text(
                    browser.get_display(),
                    reply_markup=browser.get_keyboard()
                )
                return
                
    elif data.startswith("browse_folder_"):
        folder_rel = data.replace("browse_folder_", "")
        for key, browser in user_file_browsers.items():
            if browser.msg_id == query.message.message_id:
                new_path = os.path.join(browser.base_path, folder_rel)
                if os.path.isdir(new_path):
                    browser.current_path = new_path
                await query.edit_message_text(
                    browser.get_display(),
                    reply_markup=browser.get_keyboard()
                )
                return
                
    elif data.startswith("browse_select_"):
        file_rel = data.replace("browse_select_", "")
        for key, browser in user_file_browsers.items():
            if browser.msg_id == query.message.message_id:
                main_file = os.path.join(browser.base_path, file_rel)
                update_file_main_file(user_id, browser.file_id, main_file)
                await query.edit_message_text(
                    f"✅ {to_tny_bold('MAIN FILE SET!')}\n📄 {os.path.basename(main_file)}"
                )
                del user_file_browsers[key]
                return

async def handle_choose_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_banned(user_id):
        return

    user_files = get_user_files(user_id)

    if not user_files:
        await update.message.reply_text(f"❌ {to_tny_bold('NO FILES UPLOADED')}")
        return

    keyboard = []
    for file_info in user_files:
        icon = "📦" if file_info['type'] == 'zip' else "📄"
        keyboard.append([InlineKeyboardButton(f"{icon} {file_info['name']}", callback_data=f"file_{file_info['id']}")])

    await update.message.reply_text(f"🍃 {to_tny_bold('YOUR FILES')}", reply_markup=InlineKeyboardMarkup(keyboard))

async def file_details_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    file_id = int(query.data.split('_')[1])

    file_info = get_file_by_id(user_id, file_id)

    if not file_info:
        await query.edit_message_text(f"❌ {to_tny_bold('FILE NOT FOUND')}")
        return
    
    if file_info['type'] == 'zip':
        extract_path = file_info['path'].replace('.zip', '')
        if os.path.exists(extract_path):
            await show_file_browser(query.message, user_id, file_id, extract_path)
            return

    file_path = file_info.get('main_file') or file_info['path']
    file_name = file_info['name']
    process_key = f"{user_id}_{os.path.basename(file_path)}"

    is_running = process_key in running_processes and running_processes[process_key].process.poll() is None
    status = "🟢 RUNNING" if is_running else "🔴 STOPPED"

    text = f"""╭────────〔 📄 {to_tny_bold("FILE DETAILS")} 📄 〕────────╮
│
│ 📁 {to_tny_bold("NAME:")} {file_name}
│ 📊 {to_tny_bold("STATUS:")} {status}
│ 📅 {to_tny_bold("UPLOADED:")} {file_info.get('upload_time', 'Unknown')[:10]}
│
╰──────────────────────────────────╯"""

    keyboard = [
        [InlineKeyboardButton(to_tny_bold("START"), callback_data=f"start_{file_id}")],
        [InlineKeyboardButton(to_tny_bold("STOP"), callback_data=f"stop_{file_id}")],
        [InlineKeyboardButton(to_tny_bold("LOGS"), callback_data=f"logs_{file_id}")],
        [InlineKeyboardButton(to_tny_bold("DELETE"), callback_data=f"delete_{file_id}")]
    ]
    
    if file_info['type'] == 'zip':
        keyboard.append([InlineKeyboardButton(to_tny_bold("📂 BROWSE FILES"), callback_data=f"browse_{file_id}")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def start_file_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    file_id = int(query.data.split('_')[1])

    credits = get_user_credits(user_id)
    if user_id != OWNER_ID and not is_premium(user_id) and credits < 1:
        await query.edit_message_text(
            f"❌ {to_tny_bold('INSUFFICIENT CREDITS!')}\n\n"
            f"💰 {to_tny_bold('Your Credits:')} {credits}\n"
            f"💎 {to_tny_bold('Use BUY CREDITS button to purchase')}"
        )
        return

    file_info = get_file_by_id(user_id, file_id)

    if not file_info:
        await query.edit_message_text(f"❌ {to_tny_bold('FILE NOT FOUND')}")
        return

    file_path = file_info.get('main_file') or file_info['path']
    file_name = os.path.basename(file_path)
    process_key = f"{user_id}_{file_name}"

    cmd = None
    cwd = os.path.dirname(file_path)

    if file_path.endswith('.py'):
        await query.edit_message_text(f"⚙️ {to_tny_bold('PREPARING PYTHON...')}")
        actual_file = prepare_universal_python(file_path)
        cmd = [sys.executable, actual_file]
    elif file_path.endswith('.js'):
        await query.edit_message_text(f"⚙️ {to_tny_bold('PREPARING JS...')}")
        if not ensure_node_installed():
            await query.edit_message_text(f"❌ {to_tny_bold('Node.js not installed!')}")
            return
        actual_file = prepare_universal_js(file_path)
        cmd = ['node', actual_file]
    elif file_path.endswith('.sh'):
        cmd = ['bash', file_path]
    elif file_path.endswith('.ipynb') and NOTEBOOK_SUPPORT:
        await query.edit_message_text(f"📓 {to_tny_bold('EXECUTING NOTEBOOK...')}")
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                nb = nbformat.read(f, as_version=4)
            ep = ExecutePreprocessor(timeout=600, kernel_name='python3')
            ep.preprocess(nb, {'metadata': {'path': cwd}})
            await query.edit_message_text(f"✅ {to_tny_bold('NOTEBOOK COMPLETED')}")
            return
        except Exception as e:
            await query.edit_message_text(f"❌ {to_tny_bold('NOTEBOOK ERROR:')}\n{str(e)[:500]}")
            return
    else:
        await query.edit_message_text(f"❌ {to_tny_bold('UNSUPPORTED FILE TYPE')}")
        return

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=cwd,
            env={**os.environ, 'PYTHONUNBUFFERED': '1'}
        )

        await asyncio.sleep(2)

        if process.poll() is not None:
            stdout, _ = process.communicate()
            await query.edit_message_text(f"❌ {to_tny_bold('PROCESS CRASHED')}\n\n```\n{stdout[-500:]}\n```", parse_mode=ParseMode.MARKDOWN)
            return

        running_processes[process_key] = ProcessMonitor(process, file_name, user_id, cmd)
        
        if user_id != OWNER_ID and not is_premium(user_id):
            deduct_credit_for_runtime(user_id)

        await query.edit_message_text(f"🚀 {to_tny_bold('STARTED!')}\n📄 {file_name}")

        if get_user_auto_restart(user_id):
            asyncio.create_task(monitor_process_auto_restart(user_id, file_name, file_path, cmd, process_key))

    except Exception as e:
        await query.edit_message_text(f"❌ {to_tny_bold('ERROR:')}\n{str(e)}")

async def monitor_process_auto_restart(user_id, file_name, file_path, cmd, process_key):
    while get_user_auto_restart(user_id):
        await asyncio.sleep(5)

        if process_key not in running_processes:
            break

        monitor = running_processes[process_key]

        if monitor.process.poll() is not None:
            credits = get_user_credits(user_id)
            if user_id != OWNER_ID and not is_premium(user_id) and credits < 1:
                break
                
            try:
                new_process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    cwd=os.path.dirname(file_path)
                )
                running_processes[process_key] = ProcessMonitor(new_process, file_name, user_id, cmd)
            except:
                break

async def stop_file_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    file_id = int(query.data.split('_')[1])

    file_info = get_file_by_id(user_id, file_id)

    if not file_info:
        await query.edit_message_text(f"❌ {to_tny_bold('FILE NOT FOUND')}")
        return

    file_path = file_info.get('main_file') or file_info['path']
    file_name = os.path.basename(file_path)
    process_key = f"{user_id}_{file_name}"

    if process_key in running_processes:
        running_processes[process_key].stop()
        del running_processes[process_key]
        await query.edit_message_text(f"⏹️ {to_tny_bold('STOPPED')}\n📄 {file_name}")
    else:
        await query.edit_message_text(f"⚠️ {to_tny_bold('NOT RUNNING')}")

async def delete_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    file_id = int(query.data.split('_')[1])

    file_info = get_file_by_id(user_id, file_id)

    if not file_info:
        await query.edit_message_text(f"❌ {to_tny_bold('FILE NOT FOUND')}")
        return

    file_path = file_info['path']
    file_name = file_info['name']
    process_key = f"{user_id}_{os.path.basename(file_info.get('main_file') or file_path)}"

    if process_key in running_processes:
        running_processes[process_key].stop()
        del running_processes[process_key]

    try:
        if os.path.exists(file_path):
            if os.path.isfile(file_path):
                os.remove(file_path)
            else:
                shutil.rmtree(file_path)

        if file_path.endswith('.zip'):
            extract_path = file_path.replace('.zip', '')
            if os.path.exists(extract_path):
                shutil.rmtree(extract_path)
    except:
        pass

    delete_file_from_db(user_id, file_id)
    await query.edit_message_text(f"🗑️ {to_tny_bold('FILE DELETED')}\n📄 {file_name}")

async def show_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    file_id = int(query.data.split('_')[1])

    file_info = get_file_by_id(user_id, file_id)

    if not file_info:
        await query.edit_message_text(f"❌ {to_tny_bold('FILE NOT FOUND')}")
        return

    file_path = file_info.get('main_file') or file_info['path']
    file_name = os.path.basename(file_path)
    process_key = f"{user_id}_{file_name}"

    if process_key not in running_processes:
        await query.edit_message_text(f"❌ {to_tny_bold('PROCESS NOT RUNNING')}")
        return

    monitor = running_processes[process_key]
    logs = monitor.get_logs(15)

    log_text = "\n│ ".join(logs) if logs else to_tny_bold("NO OUTPUT YET...")

    text = f"""╭────────〔 📋 {to_tny_bold("LOGS")} 📋 〕────────╮
│
│ {log_text}
│
╰──────────────────────────────────╯"""

    keyboard = [
        [InlineKeyboardButton(f"🔄 {to_tny_bold('REFRESH')}", callback_data=f"logs_{file_id}"),
         InlineKeyboardButton(f"🔙 {to_tny_bold('BACK')}", callback_data=f"file_{file_id}")]
    ]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_auto_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_banned(user_id):
        return

    current = get_user_auto_restart(user_id)
    set_user_auto_restart(user_id, not current)

    status = "🟢 ENABLED" if not current else "🔴 DISABLED"
    await update.message.reply_text(f"🔄 {to_tny_bold('AUTO RESTART')}\n\n📊 {to_tny_bold('STATUS:')} {status}")

async def handle_manual_install(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_banned(user_id):
        return

    await update.message.reply_text(f"📦 {to_tny_bold('MANUAL INSTALL')}\n\n💡 {to_tny_bold('SEND PACKAGE NAME')}")
    context.user_data['waiting_for_package'] = True

async def handle_package_install(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    package_name = update.message.text.strip()
    context.user_data['waiting_for_package'] = False
    msg = await update.message.reply_text(f"📦 {to_tny_bold('PIP INSTALLING')} {package_name}...")
    await install_package_async(package_name, msg, user_id)

async def handle_installed_packages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_banned(user_id):
        return

    packages = get_user_packages(user_id)

    if not packages:
        await update.message.reply_text(f"❌ {to_tny_bold('NO PACKAGES INSTALLED')}")
        return

    packages_text = "\n│ • ".join(packages[:20])
    text = f"""╭────────〔 📦 {to_tny_bold("PACKAGES")} 📦 〕────────╮
│
│ • {packages_text}
│
╰──────────────────────────────────╯"""
    await update.message.reply_text(text)

async def handle_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start_time = time.time()
    msg = await update.message.reply_text(f"🏓 {to_tny_bold('PINGING...')}")
    ping_ms = round((time.time() - start_time) * 1000, 2)
    await msg.edit_text(f"🏓 {to_tny_bold('PONG!')}\n⏱️ {ping_ms}ms")

async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_banned(user_id):
        return

    stats = get_system_stats()
    user_processes = sum(1 for key in running_processes.keys() if key.startswith(f"{user_id}_"))
    session_time = format_time(time.time() - get_bot_start_time())
    
    if user_id == OWNER_ID:
        credits_display = "♾️ Unlimited"
        premium_status = "👑 Owner"
    else:
        credits = get_user_credits(user_id)
        credits_display = str(credits)
        premium_status = "⭐ Premium" if is_premium(user_id) else "🆓 Free"

    text = f"""╭────────〔 📊 {to_tny_bold("STATUS")} 📊 〕────────╮
│
│ 💻 {to_tny_bold("CPU:")} {stats['cpu_percent']:.1f}%
│ {get_progress_bar(stats['cpu_percent'], 100)}
│
│ 🧠 {to_tny_bold("RAM:")} {stats['memory_percent']:.1f}%
│ {get_progress_bar(stats['memory_percent'], 100)}
│ 📊 {format_bytes(stats['memory_used'])} / {format_bytes(stats['memory_total'])}
│
│ 💾 {to_tny_bold("DISK:")} {stats['disk_percent']:.1f}%
│ {get_progress_bar(stats['disk_percent'], 100)}
│
│ 🔄 {to_tny_bold("YOUR PROCESSES:")} {user_processes}
│ ⏱️ {to_tny_bold("SESSION:")} {session_time}
│ 🟢 {to_tny_bold("AUTO RESTART:")} {'ON' if get_user_auto_restart(user_id) else 'OFF'}
│ 💰 {to_tny_bold("CREDITS:")} {credits_display}
│ 👑 {to_tny_bold("STATUS:")} {premium_status}
│
╰──────────────────────────────────╯"""

    await update.message.reply_text(text)

async def handle_console(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await update.message.reply_text(f"❌ {to_tny_bold('OWNER ONLY!')}")
        return

    total_users = get_all_users_count()
    banned_users = get_banned_users_count()
    total_credits = get_total_credits()

    text = f"""╭────────〔 🎛️ {to_tny_bold("CONSOLE")} 🎛️ 〕────────╮
│
│ 👥 {to_tny_bold("TOTAL USERS:")} {total_users}
│ 🚫 {to_tny_bold("BANNED:")} {banned_users}
│ 🔄 {to_tny_bold("RUNNING PROCESSES:")} {len(running_processes)}
│ 💰 {to_tny_bold("TOTAL CREDITS:")} {total_credits}
│
╰──────────────────────────────────╯"""
    await update.message.reply_text(text)

async def handle_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await update.message.reply_text(f"❌ {to_tny_bold('OWNER ONLY!')}")
        return
    await update.message.reply_text(f"⚙️ {to_tny_bold('SETTINGS')}", reply_markup=get_settings_keyboard())

async def handle_add_coins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text(f"❌ {to_tny_bold('OWNER ONLY!')}")
        return
    await update.message.reply_text(
        f"💎 {to_tny_bold('ADD COINS')}\n\n"
        f"💡 {to_tny_bold('Format:')} {mono('user_id amount')}\n"
        f"📌 {to_tny_bold('Example:')} {mono('123456789 100')}",
        parse_mode=ParseMode.MARKDOWN
    )
    context.user_data['waiting_for_add_coins'] = True

async def process_add_coins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        parts = update.message.text.strip().split()
        target_user_id = int(parts[0])
        amount = int(parts[1])
        add_credits(target_user_id, amount)
        await update.message.reply_text(f"✅ {to_tny_bold('Added')} {amount} {to_tny_bold('coins to user')} {mono(str(target_user_id))}", parse_mode=ParseMode.MARKDOWN)
        try:
            await context.bot.send_message(chat_id=target_user_id, text=f"💰 {to_tny_bold('You received')} {amount} {to_tny_bold('credits!')}")
        except:
            pass
    except:
        await update.message.reply_text(f"❌ {to_tny_bold('INVALID FORMAT')}")
    context.user_data['waiting_for_add_coins'] = False

async def handle_remove_coins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text(f"❌ {to_tny_bold('OWNER ONLY!')}")
        return
    await update.message.reply_text(
        f"💸 {to_tny_bold('REMOVE COINS')}\n\n"
        f"💡 {to_tny_bold('Format:')} {mono('user_id amount')}\n"
        f"📌 {to_tny_bold('Example:')} {mono('123456789 100')}",
        parse_mode=ParseMode.MARKDOWN
    )
    context.user_data['waiting_for_remove_coins'] = True

async def process_remove_coins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        parts = update.message.text.strip().split()
        target_user_id = int(parts[0])
        amount = int(parts[1])
        remove_credits(target_user_id, amount)
        await update.message.reply_text(f"✅ {to_tny_bold('Removed')} {amount} {to_tny_bold('coins from user')} {mono(str(target_user_id))}", parse_mode=ParseMode.MARKDOWN)
    except:
        await update.message.reply_text(f"❌ {to_tny_bold('INVALID FORMAT')}")
    context.user_data['waiting_for_remove_coins'] = False

async def handle_add_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text(f"❌ {to_tny_bold('OWNER ONLY!')}")
        return
    await update.message.reply_text(
        f"⭐ {to_tny_bold('ADD PREMIUM')}\n\n"
        f"💡 {to_tny_bold('Format:')} {mono('user_id duration')}\n"
        f"📌 {to_tny_bold('Example:')} {mono('123456789 29d')}\n"
        f"📌 {to_tny_bold('d=days, m=months, y=years')}",
        parse_mode=ParseMode.MARKDOWN
    )
    context.user_data['waiting_for_add_premium'] = True

async def process_add_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        parts = update.message.text.strip().split()
        target_user_id = int(parts[0])
        duration = parts[1]
        success, result = add_premium(target_user_id, duration)
        if success:
            await update.message.reply_text(f"✅ {to_tny_bold('Premium added!')}\n👤 {mono(str(target_user_id))}\n📅 Expires: {result}", parse_mode=ParseMode.MARKDOWN)
            try:
                await context.bot.send_message(chat_id=target_user_id, text=f"⭐ {to_tny_bold('You are now PREMIUM!')}\n📅 Expires: {result}")
            except:
                pass
        else:
            await update.message.reply_text(f"❌ {to_tny_bold('Error:')} {result}")
    except:
        await update.message.reply_text(f"❌ {to_tny_bold('INVALID FORMAT')}")
    context.user_data['waiting_for_add_premium'] = False

async def handle_remove_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text(f"❌ {to_tny_bold('OWNER ONLY!')}")
        return
    await update.message.reply_text(
        f"❌ {to_tny_bold('REMOVE PREMIUM')}\n\n"
        f"💡 {to_tny_bold('Send user ID:')} {mono('user_id')}",
        parse_mode=ParseMode.MARKDOWN
    )
    context.user_data['waiting_for_remove_premium'] = True

async def process_remove_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        target_user_id = int(update.message.text.strip())
        remove_premium(target_user_id)
        await update.message.reply_text(f"✅ {to_tny_bold('Premium removed from user')} {mono(str(target_user_id))}", parse_mode=ParseMode.MARKDOWN)
    except:
        await update.message.reply_text(f"❌ {to_tny_bold('INVALID USER ID')}")
    context.user_data['waiting_for_remove_premium'] = False

async def handle_all_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text(f"❌ {to_tny_bold('OWNER ONLY!')}")
        return
    
    users = get_all_premium_users()
    if not users:
        await update.message.reply_text(f"❌ {to_tny_bold('NO PREMIUM USERS')}")
        return
    
    users_text = "\n│ ".join([f"• {name} - {mono(str(uid))} - Expires: {expiry[:10] if expiry else 'N/A'}" for uid, name, expiry in users[:30]])
    await update.message.reply_text(f"╭────────〔 👑 {to_tny_bold('PREMIUM USERS')} 👑 〕────────╮\n│\n│ {users_text}\n│\n╰──────────────────────────────────╯", parse_mode=ParseMode.MARKDOWN)

async def handle_expired_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text(f"❌ {to_tny_bold('OWNER ONLY!')}")
        return
    
    users = get_all_expired_premium_users()
    if not users:
        await update.message.reply_text(f"✅ {to_tny_bold('NO EXPIRED PREMIUM USERS')}")
        return
    
    users_text = "\n│ ".join([f"• {name} - {mono(str(uid))} - Expired: {expiry[:10] if expiry else 'N/A'}" for uid, name, expiry in users[:30]])
    await update.message.reply_text(f"╭────────〔 ⌛ {to_tny_bold('EXPIRED PREMIUM')} ⌛ 〕────────╮\n│\n│ {users_text}\n│\n╰──────────────────────────────────╯", parse_mode=ParseMode.MARKDOWN)

async def handle_buy_credits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_banned(user_id):
        return

    keyboard = [[InlineKeyboardButton(f"👤 {to_tny_bold('CONTACT OWNER TO BUY')}", url=f"tg://openmessage?user_id={OWNER_ID}")]]
    if user_id == OWNER_ID:
        credits_display = "♾️ Unlimited"
    else:
        credits = get_user_credits(user_id)
        credits_display = str(credits)
    await update.message.reply_text(
        f"💰 {to_tny_bold('BUY CREDITS')}\n\n"
        f"📊 {to_tny_bold('Your Credits:')} {credits_display}\n"
        f"⏱️ {to_tny_bold('1 Credit = 20 Minutes')}\n\n"
        f"💎 {to_tny_bold('Contact owner to purchase credits!')}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_banned(user_id):
        return

    user_info = get_user_info(user_id)
    if not user_info:
        await update.message.reply_text(f"❌ {to_tny_bold('Please /start first!')}")
        return

    conn = sqlite3.connect(SQLITE_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT referral_code FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if result:
        ref_code = result[0]
        bot_username = (await context.bot.get_me()).username
        ref_link = f"https://t.me/{bot_username}?start={ref_code}"
        
        await update.message.reply_text(
            f"🔗 {to_tny_bold('YOUR REFERRAL LINK')}\n\n"
            f"{mono(ref_link)}\n\n"
            f"👥 {to_tny_bold('Total Referrals:')} {user_info['total_referrals']}\n"
            f"💰 {to_tny_bold('Earn 5 credits per referral!')}\n\n"
            f"📤 {to_tny_bold('Share this link with friends!')}",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(f"❌ {to_tny_bold('Please /start first!')}")

async def handle_ban_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text(f"❌ {to_tny_bold('OWNER ONLY!')}")
        return
    await update.message.reply_text(
        f"🚫 {to_tny_bold('BAN USERS')}\n\n"
        f"💡 {to_tny_bold('SEND USER ID:')} {mono('user_id')}",
        parse_mode=ParseMode.MARKDOWN
    )
    context.user_data['waiting_for_ban'] = True

async def process_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        ban_user_id = int(update.message.text.strip())
        ban_user(ban_user_id)
        await update.message.reply_text(f"✅ {to_tny_bold('USER BANNED')}\n👤 ID: {mono(str(ban_user_id))}", parse_mode=ParseMode.MARKDOWN)
    except:
        await update.message.reply_text(f"❌ {to_tny_bold('INVALID USER ID')}")
    context.user_data['waiting_for_ban'] = False

async def handle_unban_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text(f"❌ {to_tny_bold('OWNER ONLY!')}")
        return
    await update.message.reply_text(
        f"✅ {to_tny_bold('UNBAN USERS')}\n\n"
        f"💡 {to_tny_bold('SEND USER ID:')} {mono('user_id')}",
        parse_mode=ParseMode.MARKDOWN
    )
    context.user_data['waiting_for_unban'] = True

async def process_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        unban_user_id = int(update.message.text.strip())
        unban_user(unban_user_id)
        await update.message.reply_text(f"✅ {to_tny_bold('USER UNBANNED')}\n👤 ID: {mono(str(unban_user_id))}", parse_mode=ParseMode.MARKDOWN)
    except:
        await update.message.reply_text(f"❌ {to_tny_bold('INVALID USER ID')}")
    context.user_data['waiting_for_unban'] = False

async def handle_banned_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text(f"❌ {to_tny_bold('OWNER ONLY!')}")
        return

    conn = sqlite3.connect(SQLITE_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, first_name FROM users WHERE banned = 1")
    banned = cursor.fetchall()
    conn.close()

    if not banned:
        await update.message.reply_text(f"✅ {to_tny_bold('NO BANNED USERS')}")
        return

    users_text = "\n│ ".join([f"• {name} - {mono(str(uid))}" for uid, name in banned])
    await update.message.reply_text(f"╭────────〔 🚫 {to_tny_bold('BANNED')} 🚫 〕────────╮\n│\n│ {users_text}\n│\n╰──────────────────────────────────╯", parse_mode=ParseMode.MARKDOWN)

async def handle_all_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text(f"❌ {to_tny_bold('OWNER ONLY!')}")
        return

    conn = sqlite3.connect(SQLITE_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, first_name, credits, is_premium FROM users")
    users = cursor.fetchall()
    conn.close()

    if not users:
        await update.message.reply_text(f"❌ {to_tny_bold('NO USERS')}")
        return

    users_text = "\n│ ".join([f"• {name} - {mono(str(uid))} - 💰{cred} {'⭐' if prem else ''}" for uid, name, cred, prem in users[:30]])
    await update.message.reply_text(f"╭────────〔 👥 {to_tny_bold('ALL USERS')} 👥 〕────────╮\n│\n│ {users_text}\n│\n╰──────────────────────────────────╯", parse_mode=ParseMode.MARKDOWN)

async def handle_announcement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text(f"❌ {to_tny_bold('OWNER ONLY!')}")
        return
    await update.message.reply_text(f"📢 {to_tny_bold('ANNOUNCEMENT')}\n\n🔮 {to_tny_bold('SEND YOUR MESSAGE')}")
    context.user_data['waiting_for_announcement'] = True

async def process_announcement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    announcement_text = update.message.text
    context.user_data['waiting_for_announcement'] = False

    conn = sqlite3.connect(SQLITE_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    users = [row[0] for row in cursor.fetchall()]
    conn.close()

    msg = await update.message.reply_text(f"📤 {to_tny_bold('SENDING...')}")
    success, failed = 0, 0

    broadcast_message = f"╭────────〔 📢 {to_tny_bold('ANNOUNCEMENT')} 📢 〕────────╮\n│\n{announcement_text}\n│\n╰──────────────────────────────────╯"

    for uid in users:
        try:
            await context.bot.send_message(chat_id=uid, text=broadcast_message)
            success += 1
            await asyncio.sleep(0.05)
        except:
            failed += 1

    await msg.edit_text(f"✅ {to_tny_bold('ANNOUNCEMENT SENT!')}\n\n✔️ {to_tny_bold('SUCCESS:')} {success}\n❌ {to_tny_bold('FAILED:')} {failed}")

async def handle_meet_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(f"👤 {to_tny_bold('CONTACT OWNER')}", url=f"tg://openmessage?user_id={OWNER_ID}")]]
    await update.message.reply_text(f"👤 {to_tny_bold('CLICK BELOW TO CONTACT OWNER')}", reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_guide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    guide_text = f"""╭────────〔 📖 {to_tny_bold("GUIDE")} 📖 〕────────╮
│
│ 📁 {to_tny_bold("UPLOAD FILES")} - Upload PY, JS, ZIP
│ 🗂️ {to_tny_bold("CHOOSE FILES")} - Manage files
│ ⚡ {to_tny_bold("TERMINAL")} - Run commands
│ 🔄 {to_tny_bold("AUTO RESTART")} - Auto-restart
│ 📦 {to_tny_bold("MANUAL INSTALL")} - pip install
│
│ 💰 {to_tny_bold("CREDIT SYSTEM:")}
│ • 3 Free Credits on Start
│ • 1 Credit = 20 Minutes
│ • Refer = +5 Credits
│
│ ⭐ {to_tny_bold("PREMIUM:")}
│ • Unlimited file size
│ • No credit deduction
│
╰──────────────────────────────────╯"""
    await update.message.reply_text(guide_text)

# ═══════════════════════════════════════════════════════════════
# TERMINAL HANDLERS
# ═══════════════════════════════════════════════════════════════

async def handle_terminal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_banned(user_id):
        return

    user_dir = get_user_dir(user_id)
    
    terminal = NonBlockingTerminal(user_id, update.effective_chat.id, context.bot, cwd=user_dir)
    terminal_sessions[user_id] = terminal
    
    context.user_data['in_terminal'] = True
    context.user_data['terminal_mode'] = True
    
    msg = await update.message.reply_text(
        terminal.get_display(),
        reply_markup=terminal.get_keyboard()
    )
    terminal.msg_id = msg.message_id

async def terminal_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    
    terminal = terminal_sessions.get(user_id)
    if not terminal:
        await query.answer("Session expired")
        return
    
    await query.answer()
    
    if data == "term_close":
        context.user_data['in_terminal'] = False
        context.user_data['terminal_mode'] = False
        if terminal.is_running:
            await terminal.stop()
        if user_id in terminal_sessions:
            del terminal_sessions[user_id]
        await query.message.edit_text(f"🔴 Terminal closed")
        return
    
    elif data == "term_clear":
        terminal.output_lines = []
        await query.message.edit_text(terminal.get_display(), reply_markup=terminal.get_keyboard())
        return
    
    elif data == "term_stop":
        await terminal.stop()
        return
    
    elif data == "term_kill":
        await terminal.force_kill()
        return
    
    elif data == "term_refresh":
        await query.message.edit_text(terminal.get_display(), reply_markup=terminal.get_keyboard())
        return
    
    elif data == "term_cancel_input":
        terminal.waiting_for_input = False
        terminal.output_lines.append("Input cancelled")
        await query.message.edit_text(terminal.get_display(), reply_markup=terminal.get_keyboard())
        return
    
    elif data == "term_ls":
        result = subprocess.run("ls -la", shell=True, cwd=terminal.cwd, capture_output=True, text=True)
        terminal.output_lines.extend(result.stdout.strip().split('\n')[-10:])
        await query.message.edit_text(terminal.get_display(), reply_markup=terminal.get_keyboard())
        return
    
    elif data == "term_pwd":
        terminal.output_lines.append(f"📁 {terminal.cwd}")
        await query.message.edit_text(terminal.get_display(), reply_markup=terminal.get_keyboard())
        return
    
    elif data == "term_python":
        terminal.waiting_for_input = True
        terminal.input_prompt = "🐍 Send Python code:"
        context.user_data['in_terminal'] = True
        context.user_data['terminal_session_type'] = 'python_script'
        await query.message.edit_text(terminal.get_display(), reply_markup=terminal.get_keyboard())
        return
    
    elif data == "term_node":
        terminal.waiting_for_input = True
        terminal.input_prompt = "🟢 Send Node.js code:"
        context.user_data['in_terminal'] = True
        context.user_data['terminal_session_type'] = 'node_script'
        await query.message.edit_text(terminal.get_display(), reply_markup=terminal.get_keyboard())
        return
    
    elif data == "term_bash":
        terminal.waiting_for_input = True
        terminal.input_prompt = "📜 Send bash command:"
        context.user_data['in_terminal'] = True
        context.user_data['terminal_session_type'] = 'bash_command'
        await query.message.edit_text(terminal.get_display(), reply_markup=terminal.get_keyboard())
        return
    
    elif data == "term_pip_mode":
        context.user_data['in_terminal'] = False
        context.user_data['terminal_mode'] = False
        await query.message.edit_text(f"📦 Send package name to install")
        context.user_data['waiting_for_package'] = True
        return
    
    elif data == "term_input_mode":
        terminal.waiting_for_input = True
        terminal.input_prompt = "📝 Send input to process:"
        context.user_data['in_terminal'] = True
        context.user_data['terminal_session_type'] = 'terminal_input'
        await query.message.edit_text(terminal.get_display(), reply_markup=terminal.get_keyboard())
        return
    
    elif data == "term_custom_mode":
        terminal.waiting_for_input = True
        terminal.input_prompt = "⚡ Send custom command:"
        context.user_data['in_terminal'] = True
        context.user_data['terminal_session_type'] = 'terminal_custom'
        await query.message.edit_text(terminal.get_display(), reply_markup=terminal.get_keyboard())
        return
    
    elif data == "term_send_input":
        terminal.waiting_for_input = True
        terminal.input_prompt = "📝 Type your input and send:"
        context.user_data['in_terminal'] = True
        context.user_data['terminal_session_type'] = 'terminal_input'
        await query.message.edit_text(terminal.get_display(), reply_markup=terminal.get_keyboard())
        return
    
    elif data == "term_show_termux":
        terminal.showing_termux_keyboard = True
        await query.message.edit_text(terminal.get_display(), reply_markup=terminal.get_keyboard())
        return
    
    elif data == "term_back_main":
        terminal.showing_termux_keyboard = False
        await query.message.edit_text(terminal.get_display(), reply_markup=terminal.get_keyboard())
        return
    
    elif data.startswith("term_key_"):
        key = data.replace("term_key_", "")
        key_map = {
            "esc": "ESC", "tab": "TAB", "up": "↑", "down": "↓",
            "left": "←", "right": "→", "ctrl": "CTRL", "alt": "ALT",
            "home": "HOME", "end": "END", "pgup": "PGUP", "pgdn": "PGDN",
            "slash": "/", "dash": "-", "backspace": "⌫"
        }
        if key == "ctrl_c":
            if terminal.process:
                terminal.process.send_signal(2)
            terminal.output_lines.append("^C")
        elif key == "ctrl_z":
            if terminal.process:
                terminal.process.send_signal(20)
            terminal.output_lines.append("^Z")
        elif key == "ctrl_d":
            if terminal.process:
                terminal.process.stdin.write(b'\x04')
                await terminal.process.stdin.drain()
            terminal.output_lines.append("^D")
        elif key == "ctrl_l":
            terminal.output_lines = []
        else:
            char = key_map.get(key, f"[{key}]")
            if terminal.is_running and terminal.process:
                terminal.output_lines.append(f"Key: {char}")
        await query.message.edit_text(terminal.get_display(), reply_markup=terminal.get_keyboard())
        return

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    if is_banned(user_id):
        return

    if context.user_data.get('waiting_for_package'):
        await handle_package_install(update, context)
        return
    if context.user_data.get('waiting_for_ban'):
        await process_ban(update, context)
        return
    if context.user_data.get('waiting_for_unban'):
        await process_unban(update, context)
        return
    if context.user_data.get('waiting_for_announcement'):
        await process_announcement(update, context)
        return
    if context.user_data.get('waiting_for_add_coins'):
        await process_add_coins(update, context)
        return
    if context.user_data.get('waiting_for_remove_coins'):
        await process_remove_coins(update, context)
        return
    if context.user_data.get('waiting_for_add_premium'):
        await process_add_premium(update, context)
        return
    if context.user_data.get('waiting_for_remove_premium'):
        await process_remove_premium(update, context)
        return

    if context.user_data.get('in_terminal') or context.user_data.get('terminal_mode'):
        terminal = terminal_sessions.get(user_id)
        session_type = context.user_data.get('terminal_session_type')
        
        if session_type == 'python_script':
            await update.message.delete()
            terminal.output_lines.append(f"🐍 Running Python script...")
            escaped = text.replace("'", "'\\''")
            await terminal.execute_command(f"{sys.executable} -c '{escaped}'")
            context.user_data['in_terminal'] = False
            context.user_data['terminal_session_type'] = None
            return
            
        elif session_type == 'node_script':
            await update.message.delete()
            terminal.output_lines.append(f"🟢 Running Node.js script...")
            escaped = text.replace("'", "'\\''")
            await terminal.execute_command(f"node -e '{escaped}'")
            context.user_data['in_terminal'] = False
            context.user_data['terminal_session_type'] = None
            return
            
        elif session_type == 'bash_command':
            await update.message.delete()
            await terminal.execute_command(text)
            context.user_data['in_terminal'] = False
            context.user_data['terminal_session_type'] = None
            return
            
        elif session_type == 'terminal_input':
            await update.message.delete()
            if terminal and terminal.is_running:
                await terminal.send_input(text)
            else:
                terminal.output_lines.append(f"$ {text}")
                await terminal.execute_command(text)
            context.user_data['in_terminal'] = False
            context.user_data['terminal_session_type'] = None
            return
            
        elif session_type == 'terminal_custom':
            await update.message.delete()
            terminal.output_lines.append(f"⚡ Custom command:")
            await terminal.execute_command(text)
            context.user_data['in_terminal'] = False
            context.user_data['terminal_session_type'] = None
            return

    handlers = {
        "🌺" + to_tny_bold("UPLOAD FILES") + "🌺": handle_upload_files,
        "🍃" + to_tny_bold("CHOOSE FILES") + "🍃": handle_choose_files,
        "⚡" + to_tny_bold("TERMINAL") + "⚡": handle_terminal,
        "🔋" + to_tny_bold("AUTO RESTART") + "🔋": handle_auto_restart,
        "🪴" + to_tny_bold("MANUAL INSTALL") + "🪴": handle_manual_install,
        "📦" + to_tny_bold("INSTALLED PACKAGE") + "📦": handle_installed_packages,
        "🏓" + to_tny_bold("PING") + "🏓": handle_ping,
        "📊" + to_tny_bold("STATUS") + "📊": handle_status,
        "🎛️" + to_tny_bold("CONSOLE") + "🎛️": handle_console,
        "👤" + to_tny_bold("MY ACCOUNT") + "👤": handle_my_account,
        "👀" + to_tny_bold("MEET OWNER") + "👀": handle_meet_owner,
        "💰" + to_tny_bold("BUY CREDITS") + "💰": handle_buy_credits,
        "🔗" + to_tny_bold("REFERRAL") + "🔗": handle_referral,
        "🧊" + to_tny_bold("SETTINGS") + "🧊": handle_settings,
        "🌿" + to_tny_bold("GUIDE") + "🌿": handle_guide,
        "⚡" + to_tny_bold("BAN USERS") + "⚡": handle_ban_users,
        "🌺" + to_tny_bold("UNBAN USERS") + "🌺": handle_unban_users,
        "🥀" + to_tny_bold("BANNED USERS") + "🥀": handle_banned_users,
        "🍻" + to_tny_bold("ALL USERS") + "🍻": handle_all_users,
        "🔮" + to_tny_bold("ANNOUNCEMENT") + "🔮": handle_announcement,
        "💎" + to_tny_bold("ADD COINS") + "💎": handle_add_coins,
        "💸" + to_tny_bold("REMOVE COINS") + "💸": handle_remove_coins,
        "⭐" + to_tny_bold("ADD PREMIUM") + "⭐": handle_add_premium,
        "❌" + to_tny_bold("REMOVE PREMIUM") + "❌": handle_remove_premium,
        "👑" + to_tny_bold("ALL PREMIUM") + "👑": handle_all_premium,
        "⌛" + to_tny_bold("EXPIRED PREMIUM") + "⌛": handle_expired_premium,
        "🍁" + to_tny_bold("BACK") + "🍁": lambda u, c: u.message.reply_text(f"🔙 {to_tny_bold('MAIN MENU')}", reply_markup=get_main_keyboard())
    }

    handler = handlers.get(text)
    if handler:
        if context.user_data.get('in_terminal'):
            context.user_data['in_terminal'] = False
            context.user_data['terminal_mode'] = False
        await handler(update, context)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data.startswith("verify_"):
        await handle_verification_callback(update, context)
    elif data.startswith("browse_"):
        await handle_browse_callback(update, context)
    elif data.startswith("file_"):
        await file_details_callback(update, context)
    elif data.startswith("start_"):
        await start_file_process(update, context)
    elif data.startswith("stop_"):
        await stop_file_process(update, context)
    elif data.startswith("logs_"):
        await show_logs(update, context)
    elif data.startswith("delete_"):
        await delete_file(update, context)
    elif data.startswith("term_"):
        await terminal_callback(update, context)

# ═══════════════════════════════════════════════════════════════
# MAIN FUNCTION
# ═══════════════════════════════════════════════════════════════

async def main():
    print("\n" + "="*50)
    print("🌺 UNIVERSAL HOSTING BOT 🌺")
    print("="*50)
    print(f"💾 Database: {SQLITE_DB}")
    print(f"📁 User files: {USER_FILES_DIR}")
    print("="*50 + "\n")

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .get_updates_connect_timeout(30)
        .get_updates_read_timeout(30)
        .get_updates_write_timeout(30)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(handle_callback))

    try:
        await application.run_polling(close_loop=False)
    except Exception as e:
        logger.error(f"Bot stopped: {e}")
    finally:
        try:
            await application.shutdown()
        except:
            pass

if __name__ == "__main__":
    try:
        import nest_asyncio
        nest_asyncio.apply()
    except:
        pass
    asyncio.run(main())
