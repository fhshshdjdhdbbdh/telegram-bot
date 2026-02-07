import sys
import subprocess
import asyncio
import os
import zipfile
import nest_asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

nest_asyncio.apply()

BOT_TOKEN = "8328344954:AAHlbJ29vw1K5swt6jAyHuXHEJKEN8KQ-R4"
UPLOAD_DIR = "uploads"
ADMIN_ID = 8528813709

os.makedirs(UPLOAD_DIR, exist_ok=True)

# User-specific data storage - Each user has their own isolated data
user_files = {}  # {user_id: {file_id: {...}}}
user_log_tasks = {}  # {user_id: {file_id: task}}

def get_user_files(user_id):
    """Get files for specific user"""
    if user_id not in user_files:
        user_files[user_id] = {}
    return user_files[user_id]

def get_user_log_tasks(user_id):
    """Get log tasks for specific user"""
    if user_id not in user_log_tasks:
        user_log_tasks[user_id] = {}
    return user_log_tasks[user_id]

def get_status(user_id, file_id):
    """Get status of a specific file for a user"""
    files = get_user_files(user_id)
    if file_id not in files:
        return "𝐎𝐅𝐅𝐋𝐈𝐍𝐄 🔴"
    p = files[file_id]["process"]
    if p and p.poll() is None:
        return "𝐎𝐍𝐋𝐈𝐍𝐄 🟢"
    files[file_id]["process"] = None
    return "𝐎𝐅𝐅𝐋𝐈𝐍𝐄 🔴"

def is_online(user_id, file_id):
    """Check if file is online"""
    return "𝐎𝐍𝐋𝐈𝐍𝐄" in get_status(user_id, file_id)

async def start_process(user_id, file_id):
    """Start a process for specific user"""
    files = get_user_files(user_id)
    if file_id not in files:
        return False
    data = files[file_id]
    if data["process"] is None or data["process"].poll() is not None:
        try:
            data["process"] = subprocess.Popen(
                ["python3", data["path"]],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            return True
        except Exception:
            return False
    return False

async def stop_process(user_id, file_id):
    """Stop a process for specific user"""
    files = get_user_files(user_id)
    if file_id not in files:
        return False
    data = files[file_id]
    if data["process"] and data["process"].poll() is None:
        data["process"].terminate()
        try:
            data["process"].wait(timeout=5)
        except subprocess.TimeoutExpired:
            data["process"].kill()
        data["process"] = None
        return True
    return False

async def delete_file(user_id, file_id):
    """Delete file for specific user"""
    files = get_user_files(user_id)
    log_tasks = get_user_log_tasks(user_id)
    
    if file_id not in files:
        return False
    
    if file_id in log_tasks:
        log_tasks[file_id].cancel()
        log_tasks.pop(file_id, None)
    
    await stop_process(user_id, file_id)
    
    data = files[file_id]
    if os.path.exists(data["path"]):
        try:
            os.remove(data["path"])
        except Exception:
            pass
    
    files.pop(file_id, None)
    return True

async def pip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /pip command"""
    user_id = update.effective_user.id
    
    if not context.args or context.args[0] not in ("install", "uninstall"):
        await update.message.reply_text(
            "𝐔𝐒𝐄 ⚙️:\n/pip install\n/pip uninstall"
        )
        return

    mode = context.args[0]
    context.user_data["pip_mode"] = mode

    await update.message.reply_text(
        f"𝐏𝐀𝐂𝐊𝐀𝐆𝐄 𝐍𝐀𝐌𝐄 𝐁𝐇𝐄𝐉𝐎 📦 ({mode})"
    )

async def pip_package_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle package installation/uninstallation"""
    if "pip_mode" not in context.user_data:
        return

    mode = context.user_data.pop("pip_mode")
    package = update.message.text.strip()

    msg = await update.message.reply_text(
        "```text\n𝐒𝐓𝐀𝐑𝐓𝐈𝐍𝐆...\n```\n\n⏳ 𝐈𝐍𝐒𝐓𝐀𝐋𝐋𝐈𝐍𝐆..." if mode == "install"
        else "```text\n𝐒𝐓𝐀𝐑𝐓𝐈𝐍𝐆...\n```\n\n⏳ 𝐔𝐍𝐈𝐍𝐒𝐓𝐀𝐋𝐋𝐈𝐍𝐆...",
        parse_mode="Markdown"
    )

    cmd = (
        [sys.executable, "-m", "pip", "uninstall", package, "-y"]
        if mode == "uninstall"
        else [sys.executable, "-m", "pip", "install", package]
    )

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

    logs = ""
    last_update = asyncio.get_event_loop().time()

    while True:
        line = process.stdout.readline()
        if not line and process.poll() is not None:
            break

        if line:
            logs += line
            logs = logs[-3500:]

            # Update message only every 0.5 seconds to avoid rate limits
            current_time = asyncio.get_event_loop().time()
            if current_time - last_update >= 0.5:
                try:
                    await msg.edit_text(
                        f"```text\n{logs}\n```\n\n⏳ {'𝐈𝐍𝐒𝐓𝐀𝐋𝐋𝐈𝐍𝐆' if mode=='install' else '𝐔𝐍𝐈𝐍𝐒𝐓𝐀𝐋𝐋𝐈𝐍𝐆'}...",
                        parse_mode="Markdown"
                    )
                    last_update = current_time
                except Exception:
                    pass

        await asyncio.sleep(0.1)

    if process.poll() == 0:
        await msg.edit_text(
            f"```text\n{logs}\n```\n\n✅ {mode.upper()}𝐄𝐃 `{package}`",
            parse_mode="Markdown"
        )
    else:
        await msg.edit_text(
            f"```text\n{logs}\n```\n\n❌ {mode.upper()} 𝐅𝐀𝐈𝐋𝐄𝐃",
            parse_mode="Markdown"
        )

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user = update.effective_user
    full_name = user.full_name or user.first_name or "User"
    username = f"@{user.username}" if user.username else "None"
    
    welcome_text = (
        f"𝐖𝐄𝐋𝐂𝐎𝐌𝐄 𝐓𝐎 𝐎𝐔𝐑 𝐄𝐑𝐄𝐍𝐒 𝐁𝐎𝐓 👑\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"𝐘𝐎𝐔𝐑 𝐈𝐍𝐅𝐎𝐑𝐌𝐀𝐓𝐈𝐎𝐍 🧿\n"
        f"𝐅𝐔𝐋𝐋 𝐍𝐀𝐌𝐄 : {full_name} 🌷\n"
        f"𝐔𝐒𝐄𝐑 𝐈𝐃 : {user.id} 🦋\n"
        f"𝐔𝐒𝐄𝐑𝐍𝐀𝐌𝐄 : {username} ✨\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"𝐔𝐒𝐄 /help 𝐓𝐎 𝐆𝐄𝐓 𝐁𝐎𝐓 𝐈𝐍𝐅𝐎\n"
    )
    
    await update.message.reply_text(welcome_text)

async def check_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /check command"""
    user_id = update.effective_user.id
    files = get_user_files(user_id)
    
    if not files:
        await update.message.reply_text("𝐍𝐎 𝐅𝐈𝐋𝐄𝐒 𝐔𝐏𝐋𝐎𝐀𝐃𝐄𝐃 𝐘𝐄𝐓 📁")
        return
    
    text = "𝐅𝐈𝐋𝐄 𝐃𝐀𝐒𝐇𝐁𝐎𝐀𝐑𝐃 📊\n\n"
    buttons = []
    
    for file_id, data in files.items():
        status = get_status(user_id, file_id)
        text += f"**{data['name']}**\n"
        text += f"𝐒𝐓𝐀𝐓𝐔𝐒: {status}\n\n"
        
        if is_online(user_id, file_id):
            buttons.append([InlineKeyboardButton(f"𝐒𝐓𝐎𝐏 ⏹ - {data['name']}", callback_data=f"stop|{file_id}")])
        else:
            buttons.append([InlineKeyboardButton(f"𝐒𝐓𝐀𝐑𝐓 ▶️ - {data['name']}", callback_data=f"start|{file_id}")])
    
    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown"
    )

async def logs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /logs command"""
    user_id = update.effective_user.id
    files = get_user_files(user_id)
    
    if not files:
        await update.message.reply_text("𝐍𝐎 𝐅𝐈𝐋𝐄𝐒 𝐔𝐏𝐋𝐎𝐀𝐃𝐄𝐃 𝐘𝐄𝐓 📁")
        return
    
    buttons = []
    for file_id, data in files.items():
        buttons.append([InlineKeyboardButton(f"📄 {data['name']}", callback_data=f"logfile|{file_id}")])
    
    await update.message.reply_text(
        "𝐒𝐄𝐋𝐄𝐂𝐓 𝐅𝐈𝐋𝐄 𝐓𝐎 𝐕𝐈𝐄𝐖 𝐋𝐎𝐆𝐒 📝",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown"
    )

async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /deletefile command"""
    user_id = update.effective_user.id
    files = get_user_files(user_id)
    
    if not files:
        await update.message.reply_text("𝐍𝐎 𝐅𝐈𝐋𝐄𝐒 𝐓𝐎 𝐃𝐄𝐋𝐄𝐓𝐄 📁")
        return
    
    buttons = []
    for file_id, data in files.items():
        buttons.append([InlineKeyboardButton(f"🗑 {data['name']}", callback_data=f"delfile|{file_id}")])
    
    await update.message.reply_text(
        "𝐒𝐄𝐋𝐄𝐂𝐓 𝐅𝐈𝐋𝐄 𝐓𝐎 𝐃𝐄𝐋𝐄𝐓𝐄 🗑",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown"
    )

async def choose_file_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /choosefile command"""
    context.user_data['waiting_for_file'] = True
    await update.message.reply_text(
        "𝐒𝐄𝐍𝐃 𝐘𝐎𝐔𝐑 𝐅𝐈𝐋𝐄 📁\n\n"
        "𝐀𝐂𝐂𝐄𝐏𝐓𝐄𝐃 𝐅𝐎𝐑𝐌𝐀𝐓𝐒:\n"
        "• .py (Python scripts)\n"
        "• .zip (Archives)\n"
        "• .txt, .sh, .json, .env\n\n"
        "𝐒𝐄𝐍𝐃 𝐘𝐎𝐔𝐑 𝐅𝐈𝐋𝐄 𝐍𝐎𝐖 📤",
        parse_mode="Markdown"
    )

async def eren_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /erenadmin command"""
    buttons = [[
        InlineKeyboardButton(
            "𝐄𝐑𝐄𝐍 𝐀𝐃𝐌𝐈𝐍 👑",
            url=f"tg://openmessage?user_id={ADMIN_ID}"
        )
    ]]
    await update.message.reply_text(
        "𝐂𝐎𝐍𝐓𝐀𝐂𝐓 𝐄𝐑𝐄𝐍 𝐀𝐃𝐌𝐈𝐍 🍂",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    help_text = (
        "𝐁𝐎𝐓 𝐇𝐄𝐋𝐏 𝐆𝐔𝐈𝐃𝐄 🆘\n\n"
        "𝐂𝐇𝐄𝐂𝐊 ✅\n"
        "/check - View all files with their status\n"
        "Use START/STOP buttons to control files\n\n"
        "𝐋𝐎𝐆𝐒 📝\n"
        "/logs - Select a file to view live logs\n"
        "Tap LOGS button to stream output\n\n"
        "𝐂𝐇𝐎𝐎𝐒𝐄 𝐅𝐈𝐋𝐄 📁\n"
        "/choosefile - Upload files (.py, .zip, etc.)\n"
        ".zip files will be auto-extracted\n\n"
        "𝐃𝐄𝐋𝐄𝐓𝐄 𝐅𝐈𝐋𝐄 🗑\n"
        "/deletefile - Remove uploaded files\n"
        "Running files will be stopped first\n\n"
        "𝐏𝐈𝐏 𝐈𝐍𝐒𝐓𝐀𝐋𝐋 📦\n"
        "/pip install - Install Python packages\n"
        "/pip uninstall - Remove packages\n\n"
        "𝐀𝐃𝐌𝐈𝐍 👑\n"
        "/erenadmin - Contact bot administrator\n"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle file uploads"""
    if not context.user_data.get('waiting_for_file'):
        return
    
    user_id = update.effective_user.id
    context.user_data['waiting_for_file'] = False
    doc = update.message.document
    
    allowed_extensions = ('.py', '.zip', '.txt', '.sh', '.json', '.env')
    if not doc.file_name.endswith(allowed_extensions):
        await update.message.reply_text(
            f"𝐅𝐈𝐋𝐄 𝐓𝐘𝐏𝐄 𝐍𝐎𝐓 𝐒𝐔𝐏𝐏𝐎𝐑𝐓𝐄𝐃 ❌\n\n"
            f"𝐀𝐋𝐋𝐎𝐖𝐄𝐃: {', '.join(allowed_extensions)}"
        )
        return
    
    file_id = f"{user_id}_{doc.file_unique_id}"
    file = await doc.get_file()
    files = get_user_files(user_id)
    
    if doc.file_name.endswith('.zip'):
        zip_path = f"{UPLOAD_DIR}/{file_id}.zip"
        await file.download_to_drive(zip_path)
        
        extract_dir = f"{UPLOAD_DIR}/{file_id}_extracted"
        os.makedirs(extract_dir, exist_ok=True)
        
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
            
            py_files = []
            for root, dirs, file_list in os.walk(extract_dir):
                for f in file_list:
                    if f.endswith('.py'):
                        py_files.append(os.path.join(root, f))
            
            if py_files:
                main_file = py_files[0]
                files[file_id] = {
                    "path": main_file,
                    "name": os.path.basename(main_file),
                    "process": None
                }
                await update.message.reply_text(
                    f"𝐄𝐗𝐓𝐑𝐀𝐂𝐓𝐄𝐃 𝐀𝐍𝐃 𝐒𝐀𝐕𝐄𝐃 ✅: {os.path.basename(main_file)}"
                )
            else:
                await update.message.reply_text(
                    "𝐍𝐎 𝐏𝐘 𝐅𝐈𝐋𝐄𝐒 𝐅𝐎𝐔𝐍𝐃 𝐈𝐍 𝐀𝐑𝐂𝐇𝐈𝐕𝐄 ❌"
                )
            
            os.remove(zip_path)
            
        except Exception as e:
            await update.message.reply_text(
                f"𝐄𝐑𝐑𝐎𝐑 𝐄𝐗𝐓𝐑𝐀𝐂𝐓𝐈𝐍𝐆 𝐀𝐑𝐂𝐇𝐈𝐕𝐄 ❌: {str(e)}"
            )
        return
    
    if doc.file_name.endswith('.py'):
        path = f"{UPLOAD_DIR}/{file_id}.py"
        await file.download_to_drive(path)
        
        files[file_id] = {
            "path": path,
            "name": doc.file_name,
            "process": None
        }
        
        await update.message.reply_text(
            f"𝐅𝐈𝐋𝐄 𝐒𝐀𝐕𝐄𝐃 ✅: {doc.file_name}\n\n"
            f"𝐔𝐒𝐄 /check 𝐓𝐎 𝐒𝐓𝐀𝐑𝐓/𝐒𝐓𝐎𝐏 𝐈𝐓"
        )
    else:
        path = f"{UPLOAD_DIR}/{file_id}_{doc.file_name}"
        await file.download_to_drive(path)
        
        await update.message.reply_text(
            f"𝐅𝐈𝐋𝐄 𝐒𝐀𝐕𝐄𝐃 ✅: {doc.file_name}"
        )

async def stream_logs_task(bot, chat_id, msg_id, user_id, file_id):
    """Stream logs for a specific file"""
    output = ""
    last_update = asyncio.get_event_loop().time()
    files = get_user_files(user_id)
    
    try:
        while file_id in files:
            process = files[file_id]["process"]
            if not process or process.poll() is not None:
                await asyncio.sleep(1)
                continue
            
            try:
                line = process.stdout.readline()
                if line:
                    output += line
                    output = output[-3500:]
                    
                    # Update only every 0.5 seconds to avoid rate limits
                    current_time = asyncio.get_event_loop().time()
                    if current_time - last_update >= 0.5:
                        try:
                            await bot.edit_message_text(
                                chat_id=chat_id,
                                message_id=msg_id,
                                text=f"𝐋𝐈𝐕𝐄 𝐋𝐎𝐆𝐒 📄 - {files[file_id]['name']}\n\n```\n{output}\n```",
                                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("𝐒𝐓𝐎𝐏 𝐋𝐎𝐆𝐒 ⏹", callback_data=f"stoplogs|{file_id}")]]),
                                parse_mode="Markdown"
                            )
                            last_update = current_time
                        except Exception:
                            pass
                else:
                    await asyncio.sleep(0.3)
            except Exception:
                await asyncio.sleep(0.3)
                
    except asyncio.CancelledError:
        pass

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button callbacks"""
    q = update.callback_query
    await q.answer()
    
    user_id = update.effective_user.id
    files = get_user_files(user_id)
    log_tasks = get_user_log_tasks(user_id)
    
    if "|" not in q.data:
        return
    
    action, file_id = q.data.split("|", 1)
    
    if action == "start":
        await start_process(user_id, file_id)
        text = "𝐅𝐈𝐋𝐄 𝐃𝐀𝐒𝐇𝐁𝐎𝐀𝐑𝐃 📊\n\n"
        buttons = []
        
        for fid, data in files.items():
            status = get_status(user_id, fid)
            text += f"**{data['name']}**\n"
            text += f"𝐒𝐓𝐀𝐓𝐔𝐒: {status}\n\n"
            
            if is_online(user_id, fid):
                buttons.append([InlineKeyboardButton(f"𝐒𝐓𝐎𝐏 ⏹ - {data['name']}", callback_data=f"stop|{fid}")])
            else:
                buttons.append([InlineKeyboardButton(f"𝐒𝐓𝐀𝐑𝐓 ▶️ - {data['name']}", callback_data=f"start|{fid}")])
        
        await q.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown"
        )
    
    elif action == "stop":
        await stop_process(user_id, file_id)
        text = "𝐅𝐈𝐋𝐄 𝐃𝐀𝐒𝐇𝐁𝐎𝐀𝐑𝐃 📊\n\n"
        buttons = []
        
        for fid, data in files.items():
            status = get_status(user_id, fid)
            text += f"**{data['name']}**\n"
            text += f"𝐒𝐓𝐀𝐓𝐔𝐒: {status}\n\n"
            
            if is_online(user_id, fid):
                buttons.append([InlineKeyboardButton(f"𝐒𝐓𝐎𝐏 ⏹ - {data['name']}", callback_data=f"stop|{fid}")])
            else:
                buttons.append([InlineKeyboardButton(f"𝐒𝐓𝐀𝐑𝐓 ▶️ - {data['name']}", callback_data=f"start|{fid}")])
        
        await q.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown"
        )
    
    elif action == "logfile":
        if file_id not in files:
            await q.edit_message_text("𝐅𝐈𝐋𝐄 𝐍𝐎𝐓 𝐅𝐎𝐔𝐍𝐃 ❌")
            return
        
        data = files[file_id]
        status = get_status(user_id, file_id)
        
        await q.edit_message_text(
            f"𝐅𝐈𝐋𝐄 📄: **{data['name']}**\n\n"
            f"𝐒𝐓𝐀𝐓𝐔𝐒: {status}\n"
            f"𝐏𝐀𝐓𝐇: `{data['path']}`",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("𝐕𝐈𝐄𝐖 𝐋𝐎𝐆𝐒 📄", callback_data=f"startlogs|{file_id}")]]),
            parse_mode="Markdown"
        )
    
    elif action == "startlogs":
        if file_id in log_tasks:
            log_tasks[file_id].cancel()
        
        task = asyncio.create_task(stream_logs_task(context.bot, q.message.chat_id, q.message.message_id, user_id, file_id))
        log_tasks[file_id] = task
    
    elif action == "stoplogs":
        if file_id in log_tasks:
            log_tasks[file_id].cancel()
            log_tasks.pop(file_id, None)
        
        if file_id in files:
            await q.edit_message_text(
                f"𝐋𝐎𝐆𝐒 𝐒𝐓𝐎𝐏𝐏𝐄𝐃 ⏹: {files[file_id]['name']}",
                parse_mode="Markdown"
            )
        else:
            await q.edit_message_text("𝐋𝐎𝐆𝐒 𝐒𝐓𝐎𝐏𝐏𝐄𝐃 ⏹")
    
    elif action == "delfile":
        if file_id not in files:
            await q.edit_message_text("𝐅𝐈𝐋𝐄 𝐍𝐎𝐓 𝐅𝐎𝐔𝐍𝐃 ❌")
            return
        
        data = files[file_id]
        buttons = [
            [InlineKeyboardButton("𝐂𝐎𝐍𝐅𝐈𝐑𝐌 𝐃𝐄𝐋𝐄𝐓𝐄 ✅", callback_data=f"confirmdelete|{file_id}")],
            [InlineKeyboardButton("𝐂𝐀𝐍𝐂𝐄𝐋 ❌", callback_data=f"canceldelete|{file_id}")]
        ]
        
        await q.edit_message_text(
            f"𝐃𝐄𝐋𝐄𝐓𝐄 𝐂𝐎𝐍𝐅𝐈𝐑𝐌𝐀𝐓𝐈𝐎𝐍 ⚠️\n\n"
            f"𝐅𝐈𝐋𝐄: {data['name']}\n\n"
            f"𝐀𝐑𝐄 𝐘𝐎𝐔 𝐒𝐔𝐑𝐄?",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown"
        )
    
    elif action == "confirmdelete":
        filename = files[file_id]['name'] if file_id in files else "Unknown"
        await delete_file(user_id, file_id)
        await q.edit_message_text(f"𝐅𝐈𝐋𝐄 𝐃𝐄𝐋𝐄𝐓𝐄𝐃 ✅: {filename}", parse_mode="Markdown")
    
    elif action == "canceldelete":
        await q.edit_message_text("𝐃𝐄𝐋𝐄𝐓𝐈𝐎𝐍 𝐂𝐀𝐍𝐂𝐄𝐋𝐋𝐄𝐃 ❌", parse_mode="Markdown")

async def main():
    """Main function to run the bot"""
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("pip", pip_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, pip_package_handler))
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("check", check_cmd))
    app.add_handler(CommandHandler("logs", logs_cmd))
    app.add_handler(CommandHandler("choosefile", choose_file_cmd))
    app.add_handler(CommandHandler("deletefile", delete_cmd))
    app.add_handler(CommandHandler("erenadmin", eren_admin_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.Document.ALL, file_handler))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    print("𝐁𝐎𝐓 𝐈𝐒 𝐑𝐔𝐍𝐍𝐈𝐍𝐆 ✅")
    
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        print("𝐁𝐎𝐓 𝐒𝐓𝐎𝐏𝐏𝐄𝐃 ❌")

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
