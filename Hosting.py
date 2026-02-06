import nest_asyncio
nest_asyncio.apply()

import os
import subprocess
import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

# ================= CONFIG =================
BOT_TOKEN = "8596265497:AAHAdZZ4g0rbreAgnHQivDK8047e2rrFNyA"
UPLOAD_DIR = "uploads"

os.makedirs(UPLOAD_DIR, exist_ok=True)

# ================= GLOBALS =================
running_files = {}   # file_id : {path, name, process}
log_tasks = {}
pip_process = None

# ================= BUTTONS =================
def file_buttons(file_id):
    status = get_status(file_id)
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("▶️ START", callback_data=f"start|{file_id}"),
            InlineKeyboardButton("⏹ STOP", callback_data=f"stop|{file_id}"),
            InlineKeyboardButton("📄 LOGS", callback_data=f"logs|{file_id}")
        ],
        [
            InlineKeyboardButton("🗑 DEL FILE", callback_data=f"delete|{file_id}"),
            InlineKeyboardButton("❌ DEL LOGS", callback_data=f"dellogs|{file_id}")
        ],
        [
            InlineKeyboardButton(status, callback_data="status")
        ]
    ])

# ================= PROCESS CONTROL =================
def start_file(file_id):
    data = running_files[file_id]
    if data["process"] is None:
        data["process"] = subprocess.Popen(
            ["python3", data["path"]],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

def stop_file(file_id):
    data = running_files[file_id]
    if data["process"]:
        data["process"].terminate()
        data["process"] = None

def get_status(file_id):
    p = running_files[file_id]["process"]
    if p and p.poll() is None:
        return "🟢 ONLINE"
    running_files[file_id]["process"] = None
    return "🔴 OFFLINE"

def delete_file(file_id):
    data = running_files.get(file_id)
    if not data:
        return

    if data["process"]:
        data["process"].terminate()

    if file_id in log_tasks:
        log_tasks[file_id].cancel()
        log_tasks.pop(file_id, None)

    if os.path.exists(data["path"]):
        os.remove(data["path"])

    running_files.pop(file_id, None)

# ================= STREAM PROCESS (COMMON) =================
async def stream_process(bot, chat_id, msg_id, process, title):
    output = ""
    try:
        while process and process.poll() is None:
            line = process.stdout.readline()
            if not line:
                await asyncio.sleep(0.5)
                continue

            output += line
            output = output[-3500:]

            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=f"{title}\n```{output}```",
                parse_mode="Markdown"
            )
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        pass

# ================= LOG STREAM =================
async def stream_logs(bot, chat_id, msg_id, file_id):
    process = running_files[file_id]["process"]
    await stream_process(bot, chat_id, msg_id, process, "📄 **Live Logs**")

# ================= /pip COMMAND =================
async def pip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global pip_process

    if not context.args:
        await update.message.reply_text(
            "❌ Usage:\n"
            "`/pip install requests flask`\n"
            "`/pip uninstall requests`",
            parse_mode="Markdown"
        )
        return

    action = context.args[0].lower()
    packages = context.args[1:]

    if action not in ("install", "uninstall") or not packages:
        await update.message.reply_text(
            "❌ Wrong format\n"
            "`/pip install requests`\n"
            "`/pip uninstall requests`",
            parse_mode="Markdown"
        )
        return

    cmd = ["pip", action] + packages

    msg = await update.message.reply_text(
        f"📦 **pip {action} started...**",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ CANCEL", callback_data="pip_cancel")]
        ]),
        parse_mode="Markdown"
    )

    pip_process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

    await stream_process(
        context.bot,
        update.message.chat_id,
        msg.message_id,
        pip_process,
        f"📦 **pip {action} logs**"
    )

# ================= COMMANDS =================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.first_name
    await update.message.reply_text(
        f"👋 WELCOME {user}\n\n"
        "Commands:\n"
        "/check - File menu\n"
        "/pip install <pkg>\n"
        "/pip uninstall <pkg>"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        """
🤖 **Bot Help**

/start - Welcome
/help - Help
/check - File control panel
/pip install <pkg>
/pip uninstall <pkg>

📌 Example:
`/pip install requests flask`
        """,
        parse_mode="Markdown"
    )

# ================= FILE UPLOAD =================
async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document

    if not doc.file_name.endswith(".py"):
        await update.message.reply_text("❌ Only .py files allowed")
        return

    file_id = doc.file_unique_id
    path = f"{UPLOAD_DIR}/{file_id}.py"

    file = await doc.get_file()
    await file.download_to_drive(path)

    running_files[file_id] = {
        "path": path,
        "name": doc.file_name,
        "process": None
    }

    start_file(file_id)

    await update.message.reply_text(
        f"🚀 **Started** `{doc.file_name}`",
        reply_markup=file_buttons(file_id),
        parse_mode="Markdown"
    )

# ================= CHECK =================
async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not running_files:
        await update.message.reply_text("⚠️ No files uploaded")
        return

    for file_id, data in running_files.items():
        await update.message.reply_text(
            f"📂 **File Name : {data['name']}**\n\nStatus : **{get_status(file_id)}**",
            reply_markup=file_buttons(file_id),
            parse_mode="Markdown"
        )

# ================= BUTTON HANDLER =================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global pip_process

    q = update.callback_query
    await q.answer()

    # Pip cancel
    if q.data == "pip_cancel":
        if pip_process and pip_process.poll() is None:
            pip_process.terminate()
            pip_process = None
            await q.edit_message_text("❌ Pip process cancelled")
        return

    if q.data == "status":
        return

    action, file_id = q.data.split("|")

    if action == "start":
        start_file(file_id)

    elif action == "stop":
        stop_file(file_id)

    elif action == "logs":
        msg = await q.message.reply_text("📄 Loading logs...")
        task = asyncio.create_task(
            stream_logs(context.bot, q.message.chat_id, msg.message_id, file_id)
        )
        log_tasks[file_id] = task
        return

    elif action == "dellogs":
        if file_id in log_tasks:
            log_tasks[file_id].cancel()
            log_tasks.pop(file_id, None)
        await q.message.reply_text("❌ Logs stopped")

    elif action == "delete":
        delete_file(file_id)
        await q.edit_message_text("🗑 File deleted & stopped")
        return

    await q.edit_message_text(
        f"📂 **File Name : {running_files[file_id]['name']}**\n\nStatus : **{get_status(file_id)}**",
        reply_markup=file_buttons(file_id),
        parse_mode="Markdown"
    )

# ================= MAIN =================
async def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("check", check_command))
    app.add_handler(CommandHandler("pip", pip_cmd))

    app.add_handler(MessageHandler(filters.Document.ALL, file_handler))
    app.add_handler(CallbackQueryHandler(button_handler))

    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    print("Bot running...")

asyncio.get_event_loop().run_until_complete(main())
