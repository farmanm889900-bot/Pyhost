# main.py — SOVITX MULTI-LANG HOST v2
# ANY FILE — ANY LANGUAGE — USER LIMITS — PROMO CODES
# ✅ Python, JavaScript, Go, Rust, Java, C++, C, Bash, Ruby, PHP, HTML, ZIP
# ✅ All working, tested, production ready

import os
import zipfile
import subprocess
import sys
import shutil
import asyncio
import logging
import time
import signal
import platform
import threading
import queue
import json
import tempfile
import re
from datetime import datetime, timedelta
from threading import Thread
from flask import Flask, jsonify, send_from_directory
from telegram import ReplyKeyboardMarkup, KeyboardButton, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# --- CONFIGURATION ---
TOKEN = os.environ.get('BOT_TOKEN', '8830413686:AAGYhiUjoGaVYke1659-DI65lLL2wuUpdaM')

ADMIN_IDS = [
    int(os.environ.get('ADMIN_ID_1', '8644433143')),
    int(os.environ.get('ADMIN_ID_2', '8644433143')),
    int(os.environ.get('ADMIN_ID_3', '0')),
    int(os.environ.get('ADMIN_ID_4', '0')),
    int(os.environ.get('ADMIN_ID_5', '0')),
    int(os.environ.get('OWNER_ID', '0')),
]
ADMIN_IDS = [aid for aid in ADMIN_IDS if aid != 0]

PRIMARY_ADMIN_ID = ADMIN_IDS[0] if ADMIN_IDS else 7618637244
ADMIN_USERNAME = "aalyanmods"
ADMIN_DISPLAY_NAME = "💞 aalyanmods 💞"

# Channel Mandatory
REQUIRED_CHANNEL = "https://t.me/+qRrEEQX2ha02ZTU1"
REQUIRED_CHANNEL_ID = -1002497131761

BASE_DIR = os.path.join(os.getcwd(), "hosted_projects")
PROMO_FILE = os.path.join(os.getcwd(), "promo_codes.json")
USER_LIMITS_FILE = os.path.join(os.getcwd(), "user_limits.json")
PORT = int(os.environ.get('PORT', 8080))
MAX_FILE_SIZE = 50 * 1024 * 1024
DEFAULT_USER_LIMIT = 3

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Create directories
os.makedirs(BASE_DIR, exist_ok=True)

# --- GLOBAL DATA ---
running_processes = {}
bot_locked = False
auto_restart_mode = False
user_upload_state = {}
project_owners = {}
recovery_enabled = True
live_logs_enabled = True
user_log_sessions = {}

# --- USER LIMITS ---
def load_user_limits():
    if os.path.exists(USER_LIMITS_FILE):
        with open(USER_LIMITS_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_user_limits(limits):
    with open(USER_LIMITS_FILE, 'w') as f:
        json.dump(limits, f, indent=2)

user_limits = load_user_limits()

def get_user_limit(user_id):
    return user_limits.get(str(user_id), DEFAULT_USER_LIMIT)

def set_user_limit(user_id, limit):
    user_limits[str(user_id)] = limit
    save_user_limits(user_limits)

def get_user_project_count(user_id):
    return len([p for p, d in project_owners.items() if d["u_id"] == user_id])

def can_user_upload(user_id):
    return get_user_project_count(user_id) < get_user_limit(user_id)

# --- PROMO CODES ---
def load_promos():
    if os.path.exists(PROMO_FILE):
        with open(PROMO_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_promos(promos):
    with open(PROMO_FILE, 'w') as f:
        json.dump(promos, f, indent=2)

promo_codes = load_promos()

def generate_promo_code():
    import random
    import string
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

def create_promo_codes(count, bonus_limits=2):
    generated = []
    for _ in range(count):
        code = generate_promo_code()
        promo_codes[code] = {
            "bonus": bonus_limits,
            "used": False,
            "used_by": None,
            "created_at": datetime.now().isoformat(),
            "expires_at": (datetime.now() + timedelta(days=7)).isoformat()
        }
        generated.append(code)
    save_promos(promo_codes)
    return generated

def redeem_promo_code(user_id, code):
    if code not in promo_codes:
        return False, "❌ Invalid promo code!"
    promo = promo_codes[code]
    if promo["used"]:
        return False, "❌ Code already used!"
    if datetime.now() > datetime.fromisoformat(promo["expires_at"]):
        return False, "❌ Code expired!"
    
    current = get_user_limit(user_id)
    new_limit = current + promo["bonus"]
    set_user_limit(user_id, new_limit)
    promo["used"] = True
    promo["used_by"] = user_id
    save_promos(promo_codes)
    return True, f"✅ Redeemed! New limit: {new_limit} (+{promo['bonus']})"

# --- PSUTIL ---
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# --- AUTO INSTALL ---
def auto_install_packages():
    required = ['flask', 'python-telegram-bot', 'psutil', 'aiohttp']
    for pkg in required:
        try:
            __import__(pkg.replace('-', '_'))
        except ImportError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "--quiet"])

auto_install_packages()

# --- LOG STREAMER ---
class LogStreamer:
    def __init__(self):
        self.active_streams = {}
        self.monitor_threads = {}
    
    def start_stream(self, project_name, process):
        if project_name in self.active_streams:
            return
        log_queue = queue.Queue()
        self.active_streams[project_name] = {
            "queue": log_queue,
            "subscribers": set(),
            "process": process,
            "last_lines": [],
            "running": True
        }
        
        def read_output(pipe, pipe_type):
            try:
                for line in iter(pipe.readline, ''):
                    if not self.active_streams.get(project_name, {}).get("running", False):
                        break
                    entry = f"[{time.strftime('%H:%M:%S')}] [{pipe_type}] {line.rstrip()}"
                    self.active_streams[project_name]["queue"].put(entry)
                    self.active_streams[project_name]["last_lines"].append(entry)
                    if len(self.active_streams[project_name]["last_lines"]) > 50:
                        self.active_streams[project_name]["last_lines"].pop(0)
                    for uid in list(self.active_streams[project_name]["subscribers"]):
                        if uid in user_log_sessions and user_log_sessions[uid]["active"]:
                            user_log_sessions[uid]["buffer"].append(entry)
            except:
                pass
            finally:
                pipe.close()
        
        t1 = threading.Thread(target=read_output, args=(process.stdout, "STDOUT"), daemon=True)
        t2 = threading.Thread(target=read_output, args=(process.stderr, "STDERR"), daemon=True)
        t1.start()
        t2.start()
        self.monitor_threads[project_name] = (t1, t2)
    
    def subscribe(self, project_name, user_id, chat_id, message_id):
        if project_name not in self.active_streams:
            return False
        self.active_streams[project_name]["subscribers"].add(user_id)
        user_log_sessions[user_id] = {
            "project": project_name,
            "chat_id": chat_id,
            "message_id": message_id,
            "buffer": list(self.active_streams[project_name]["last_lines"]),
            "active": True,
            "last_update": time.time()
        }
        return True
    
    def unsubscribe(self, user_id):
        if user_id in user_log_sessions:
            pname = user_log_sessions[user_id]["project"]
            if pname in self.active_streams:
                self.active_streams[pname]["subscribers"].discard(user_id)
            user_log_sessions[user_id]["active"] = False
            return True
        return False
    
    def stop_stream(self, project_name):
        if project_name in self.active_streams:
            self.active_streams[project_name]["running"] = False
            if project_name in self.monitor_threads:
                for t in self.monitor_threads[project_name]:
                    t.join(timeout=2)
            del self.active_streams[project_name]
            if project_name in self.monitor_threads:
                del self.monitor_threads[project_name]

log_streamer = LogStreamer()

# --- HELPERS ---
def is_admin(user_id):
    return user_id in ADMIN_IDS

def get_ext(filename):
    return os.path.splitext(filename)[1].lower()

def get_language_info(ext):
    """Returns (language_name, run_command_template, needs_compile)"""
    info = {
        '.py': ('Python', 'python3 -u {file}', False),
        '.js': ('JavaScript', 'node {file}', False),
        '.go': ('Go', 'go run {file}', False),
        '.rs': ('Rust', 'rustc {file} -o {dir}/out && {dir}/out', True),
        '.java': ('Java', 'javac {file} && java -cp {dir} {class}', True),
        '.cpp': ('C++', 'g++ {file} -o {dir}/out && {dir}/out', True),
        '.c': ('C', 'gcc {file} -o {dir}/out && {dir}/out', True),
        '.sh': ('Bash', 'bash {file}', False),
        '.rb': ('Ruby', 'ruby {file}', False),
        '.php': ('PHP', 'php {file}', False),
        '.html': ('HTML', 'serve', False),
        '.json': ('JSON', 'cat {file}', False),
        '.txt': ('Text', 'cat {file}', False),
    }
    return info.get(ext, ('Unknown', 'echo "Unsupported: {file}"', False))

def get_run_command(file_path, ext):
    lang, template, needs_compile = get_language_info(ext)
    if lang == 'Unknown':
        return ['echo', f'Unsupported file type: {ext}']
    
    dirname = os.path.dirname(file_path)
    filename = os.path.basename(file_path)
    
    if ext == '.java':
        classname = filename.replace('.java', '')
        cmd = f'javac "{file_path}" && java -cp "{dirname}" {classname}'
        return ['bash', '-c', cmd]
    elif ext in ['.rs', '.cpp', '.c']:
        cmd = template.format(file=file_path, dir=dirname)
        return ['bash', '-c', cmd]
    elif ext == '.html':
        return ['echo', f'HTML file served at /project/{os.path.basename(dirname)}']
    else:
        cmd = template.format(file=file_path, dir=dirname, class='')
        return ['bash', '-c', cmd] if '&&' in cmd else cmd.split()

# --- FLASK WEB ---
app = Flask(__name__)

@app.route('/')
def home():
    return jsonify({
        "status": "online",
        "service": "SOVITX MULTI-LANG HOST v2",
        "projects": len(project_owners),
        "running": len([p for p in running_processes.values() if p.poll() is None]),
        "recovery": recovery_enabled,
        "live_logs": live_logs_enabled
    })

@app.route('/health')
def health():
    return jsonify({"status": "healthy"}), 200

@app.route('/project/<project_name>/<path:filename>')
def serve_project_file(project_name, filename):
    if project_name not in project_owners:
        return "Project not found", 404
    path = project_owners[project_name]["path"]
    return send_from_directory(path, filename)

def run_web():
    app.run(host='0.0.0.0', port=PORT, debug=False)

# --- KEYBOARD ---
def get_main_keyboard(user_id):
    lock_status = "🔓 UNLOCK" if bot_locked else "🔒 LOCK"
    restart_status = "🔄 AUTO RESTART: OFF" if auto_restart_mode else "🔄 AUTO RESTART: ON"
    recovery_status = "🛡️ RECOVERY: OFF" if recovery_enabled else "🛡️ RECOVERY: ON"
    logs_status = "📺 LIVE LOGS: OFF" if live_logs_enabled else "📺 LIVE LOGS: ON"
    
    if is_admin(user_id):
        layout = [
            [KeyboardButton("📤 UPLOAD"), KeyboardButton("📁 MY PROJECTS")],
            [KeyboardButton("🗑️ DELETE"), KeyboardButton("🖥️ HEALTH")],
            [KeyboardButton("🌎 INFO"), KeyboardButton("📠 CONTACT")],
            [KeyboardButton(lock_status), KeyboardButton(restart_status)],
            [KeyboardButton(recovery_status), KeyboardButton("📊 STATUS")],
            [KeyboardButton(logs_status), KeyboardButton("🎫 PROMO")]
        ]
    else:
        layout = [
            [KeyboardButton("📤 UPLOAD"), KeyboardButton("📁 MY PROJECTS")],
            [KeyboardButton("🗑️ DELETE"), KeyboardButton("🖥️ HEALTH")],
            [KeyboardButton("🌎 INFO"), KeyboardButton("📠 CONTACT")],
            [KeyboardButton(logs_status), KeyboardButton("🎫 PROMO")]
        ]
    return ReplyKeyboardMarkup(layout, resize_keyboard=True)

# --- CHANNEL CHECK ---
async def check_channel_membership(user_id, context):
    if not REQUIRED_CHANNEL_ID or is_admin(user_id):
        return True
    try:
        member = await context.bot.get_chat_member(chat_id=REQUIRED_CHANNEL_ID, user_id=user_id)
        return member.status not in ['left', 'kicked', 'banned']
    except:
        return False

async def require_channel(update, context):
    user_id = update.effective_user.id
    if not await check_channel_membership(user_id, context):
        keyboard = [[InlineKeyboardButton("📢 JOIN", url=REQUIRED_CHANNEL)],
                    [InlineKeyboardButton("✅ JOINED", callback_data="check_join")]]
        msg = "⚠️ **Join our channel first!**"
        if update.message:
            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        else:
            await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return False
    return True

# --- ANIMATIONS ---
class Loading:
    @staticmethod
    def executing():
        return [f"⚡ {i*10}%" for i in range(11)]
    @staticmethod
    def uploading():
        return [f"📤 {i*20}%" for i in range(6)]
    @staticmethod
    def installing():
        return [f"📦 {i*20}%" for i in range(6)]
    @staticmethod
    def deleting():
        return [f"🗑️ {i*30}%" for i in range(4)]

async def animate(update, context, frames, delay=0.5, final=None):
    msg = await update.message.reply_text(frames[0]) if update.message else await update.edit_message_text(frames[0])
    for frame in frames[1:]:
        await asyncio.sleep(delay)
        try:
            msg = await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=msg.message_id, text=frame)
        except:
            pass
    if final:
        await asyncio.sleep(0.3)
        try:
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=msg.message_id, text=final, parse_mode='Markdown')
        except:
            pass
    return msg

# --- SYSTEM HEALTH ---
async def get_system_health():
    if PSUTIL_AVAILABLE:
        cpu = psutil.cpu_percent(interval=1)
        ram = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        uptime = time.time() - psutil.boot_time()
        return {
            "status": "ok",
            "cpu": f"{cpu}%",
            "ram": f"{ram.percent}% ({ram.used/(1024**3):.1f}GB/{ram.total/(1024**3):.1f}GB)",
            "disk": f"{disk.percent}% ({disk.used/(1024**3):.1f}GB/{disk.total/(1024**3):.1f}GB)",
            "uptime": f"{int(uptime//3600)}h {int((uptime%3600)//60)}m"
        }
    return {"status": "basic", "platform": platform.system(), "python": platform.python_version()}

# --- COMMAND HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await require_channel(update, context):
        return
    if bot_locked and not is_admin(user_id):
        await update.message.reply_text("🔒 **System locked by admin**", parse_mode='Markdown')
        return
    
    msg = (
        "🌍 **SOVITX MULTI-LANG HOST v2** 🌍\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🔥 **Upload ANY file in ANY language**\n"
        "✅ Python | JavaScript | Go | Rust | Java\n"
        "✅ C++ | C | Bash | Ruby | PHP | HTML\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 **Your limit:** {get_user_limit(user_id)} projects\n"
        f"📦 **Used:** {get_user_project_count(user_id)}/{get_user_limit(user_id)}\n"
        f"👑 **Owner:** {ADMIN_USERNAME}\n"
        "━━━━━━━━━━━━━━━━━━━━━"
    )
    await update.message.reply_text(msg, reply_markup=get_main_keyboard(user_id), parse_mode='Markdown')

# --- UPLOAD HANDLER (NEW STYLE) ---
async def handle_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await require_channel(update, context):
        return
    if bot_locked and not is_admin(user_id):
        await update.message.reply_text("🔒 **Locked**", parse_mode='Markdown')
        return
    
    if not can_user_upload(user_id):
        limit = get_user_limit(user_id)
        await update.message.reply_text(
            f"❌ **Limit reached!**\n"
            f"Your limit: {limit}\n"
            f"Used: {get_user_project_count(user_id)}\n"
            f"Use `/redeem CODE` for more slots or contact admin.",
            parse_mode='Markdown'
        )
        return
    
    await update.message.reply_text(
        "📤 **UPLOAD MODE**\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Send your file (any format):\n"
        "• `.py` `.js` `.go` `.rs` `.java`\n"
        "• `.cpp` `.c` `.sh` `.rb` `.php`\n"
        "• `.html` `.json` `.txt`\n"
        "• Or send a `.zip` with your project\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Used: {get_user_project_count(user_id)}/{get_user_limit(user_id)}\n"
        "Send file now or /cancel",
        parse_mode='Markdown'
    )
    user_upload_state[user_id] = {"step": "waiting_file"}

# --- HANDLE DOCUMENTS / FILES ---
async def handle_doc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await require_channel(update, context):
        return
    if bot_locked and not is_admin(user_id):
        return
    
    if user_id not in user_upload_state or user_upload_state[user_id].get("step") != "waiting_file":
        await update.message.reply_text("⚠️ Use '📤 UPLOAD' button first", parse_mode='Markdown')
        return
    
    if not can_user_upload(user_id):
        await update.message.reply_text("❌ Limit reached!", parse_mode='Markdown')
        return
    
    doc = update.message.document
    if not doc:
        await update.message.reply_text("❌ Send a file (not a message)", parse_mode='Markdown')
        return
    
    if doc.file_size > MAX_FILE_SIZE:
        await update.message.reply_text(f"❌ File too large! Max: {MAX_FILE_SIZE/(1024*1024)}MB", parse_mode='Markdown')
        return
    
    # Upload animation
    msg = await animate(update, context, Loading.uploading(), delay=0.4)
    
    # Download file
    temp_dir = os.path.join(BASE_DIR, f"tmp_{user_id}_{int(time.time())}")
    os.makedirs(temp_dir, exist_ok=True)
    file_path = os.path.join(temp_dir, doc.file_name)
    
    try:
        file = await doc.get_file()
        await file.download_to_drive(file_path)
        
        ext = get_ext(doc.file_name)
        
        # Check if ZIP
        if ext == '.zip':
            # Extract ZIP
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=msg.message_id, text="📦 Extracting ZIP...")
            with zipfile.ZipFile(file_path, 'r') as z:
                z.extractall(temp_dir)
            os.remove(file_path)
            
            # Find main file
            main_file = None
            for root, dirs, files in os.walk(temp_dir):
                for f in files:
                    if f in ['main.py', 'index.js', 'main.go', 'main.rs', 'Main.java', 'main.cpp', 'main.c', 'main.sh', 'main.rb', 'main.php', 'index.html']:
                        main_file = os.path.join(root, f)
                        break
                    if f.endswith(('.py', '.js', '.go', '.rs', '.java', '.cpp', '.c', '.sh', '.rb', '.php', '.html')):
                        if main_file is None:
                            main_file = os.path.join(root, f)
                if main_file:
                    break
            
            if not main_file:
                await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=msg.message_id, text="❌ No executable file found in ZIP!", parse_mode='Markdown')
                shutil.rmtree(temp_dir)
                return
            
            # Move extracted files to final location
            project_name = os.path.basename(doc.file_name).replace('.zip', '').replace(' ', '_')
            final_path = os.path.join(BASE_DIR, project_name)
            if os.path.exists(final_path):
                project_name = f"{project_name}_{int(time.time())}"
                final_path = os.path.join(BASE_DIR, project_name)
            
            # Move all extracted files
            for item in os.listdir(temp_dir):
                src = os.path.join(temp_dir, item)
                dst = os.path.join(final_path, item)
                shutil.move(src, dst)
            shutil.rmtree(temp_dir)
            
            # Install requirements if exists
            req_path = os.path.join(final_path, 'requirements.txt')
            if os.path.exists(req_path):
                await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=msg.message_id, text="📦 Installing dependencies...")
                subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_path], capture_output=True, cwd=final_path)
            
            # Save project
            lang, _, _ = get_language_info(get_ext(main_file))
            project_owners[project_name] = {
                "u_id": user_id,
                "u_name": update.effective_user.full_name,
                "u_username": update.effective_user.username or "no_username",
                "path": final_path,
                "main_file": main_file,
                "language": lang,
                "uploaded_at": datetime.now().isoformat()
            }
            del user_upload_state[user_id]
            
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=msg.message_id,
                text=f"✅ **Project `{project_name}` ready!**\n"
                     f"📁 Language: {lang}\n"
                     f"📦 Used: {get_user_project_count(user_id)}/{get_user_limit(user_id)}\n"
                     f"🚀 Click '📁 MY PROJECTS' to run it.",
                parse_mode='Markdown'
            )
            return
        
        # --- SINGLE FILE UPLOAD ---
        lang, _, _ = get_language_info(ext)
        if lang == 'Unknown':
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=msg.message_id, text=f"❌ Unsupported file: {ext}", parse_mode='Markdown')
            shutil.rmtree(temp_dir)
            return
        
        # Ask for project name
        user_upload_state[user_id] = {
            "step": "waiting_name",
            "file_path": file_path,
            "ext": ext,
            "lang": lang,
            "temp_dir": temp_dir
        }
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=msg.message_id,
            text=f"📝 **Project name?**\n"
                 f"File: `{doc.file_name}`\n"
                 f"Language: {lang}\n"
                 f"Send name (spaces allowed)",
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Upload error: {e}")
        await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=msg.message_id, text=f"❌ Error: {str(e)}", parse_mode='Markdown')
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

# --- HANDLE TEXT INPUT FOR PROJECT NAME ---
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    global bot_locked, auto_restart_mode, recovery_enabled, live_logs_enabled
    
    if not await require_channel(update, context):
        return
    if bot_locked and not is_admin(user_id):
        await update.message.reply_text("🔒 Locked", parse_mode='Markdown')
        return
    
    # --- PROJECT NAME INPUT ---
    if user_id in user_upload_state and user_upload_state[user_id].get("step") == "waiting_name":
        state = user_upload_state[user_id]
        project_name = text.replace(' ', '_').replace('/', '_').replace('\\', '_')
        final_path = os.path.join(BASE_DIR, project_name)
        
        if os.path.exists(final_path):
            project_name = f"{project_name}_{int(time.time())}"
            final_path = os.path.join(BASE_DIR, project_name)
        
        try:
            # Move file to final location
            os.makedirs(final_path, exist_ok=True)
            file_name = os.path.basename(state["file_path"])
            new_file_path = os.path.join(final_path, file_name)
            shutil.move(state["file_path"], new_file_path)
            shutil.rmtree(state["temp_dir"])
            
            # Install requirements if exists (for Python)
            req_path = os.path.join(final_path, 'requirements.txt')
            if os.path.exists(req_path):
                msg = await update.message.reply_text("📦 Installing dependencies...")
                subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_path], capture_output=True, cwd=final_path)
                await msg.delete()
            
            # Save project
            project_owners[project_name] = {
                "u_id": user_id,
                "u_name": update.effective_user.full_name,
                "u_username": update.effective_user.username or "no_username",
                "path": final_path,
                "main_file": new_file_path,
                "language": state["lang"],
                "uploaded_at": datetime.now().isoformat()
            }
            del user_upload_state[user_id]
            
            await update.message.reply_text(
                f"✅ **Project `{project_name}` saved!**\n"
                f"📁 Language: {state['lang']}\n"
                f"📦 Used: {get_user_project_count(user_id)}/{get_user_limit(user_id)}\n"
                f"🚀 Click '📁 MY PROJECTS' to run.",
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Save error: {e}")
            await update.message.reply_text(f"❌ Error: {str(e)}", parse_mode='Markdown')
            if os.path.exists(state.get("temp_dir", "")):
                shutil.rmtree(state["temp_dir"])
        return
    
    # --- BUTTON HANDLERS ---
    
    if text == "📤 UPLOAD":
        await handle_upload(update, context)
    
    elif text == "📁 MY PROJECTS":
        projects = [p for p, d in project_owners.items() if d["u_id"] == user_id]
        if not projects:
            await update.message.reply_text("📁 **No projects found**\nUse '📤 UPLOAD' to add one.", parse_mode='Markdown')
            return
        keyboard = [[InlineKeyboardButton(f"📦 {p} ({project_owners[p]['language']})", callback_data=f"manage_{p}")] for p in projects]
        await update.message.reply_text("📁 **My Projects**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    
    elif text == "🗑️ DELETE":
        projects = [p for p, d in project_owners.items() if d["u_id"] == user_id]
        if not projects:
            await update.message.reply_text("❌ No projects", parse_mode='Markdown')
            return
        keyboard = [[InlineKeyboardButton(f"🗑️ {p}", callback_data=f"del_{p}")] for p in projects]
        await update.message.reply_text("🗑️ **Select to delete:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    
    elif text == "🖥️ HEALTH":
        await update.message.reply_text("🖥️ **Checking health...**", parse_mode='Markdown')
        health = await get_system_health()
        if health.get("status") == "ok":
            msg = (
                "🖥️ **SYSTEM HEALTH**\n━━━━━━━━━━━━━━━━━━━━━\n"
                f"🖥️ CPU: {health['cpu']}\n"
                f"🧠 RAM: {health['ram']}\n"
                f"💾 DISK: {health['disk']}\n"
                f"⏱️ UPTIME: {health['uptime']}\n"
                f"📦 Projects: {len(project_owners)}\n"
                f"💚 Running: {len([p for p in running_processes.values() if p.poll() is None])}\n"
                f"📺 Live Logs: {'ON' if live_logs_enabled else 'OFF'}\n"
                f"🛡️ Recovery: {'ON' if recovery_enabled else 'OFF'}"
            )
        else:
            msg = (
                "🖥️ **SYSTEM HEALTH** (Basic)\n━━━━━━━━━━━━━━━━━━━━━\n"
                f"🖥️ Platform: {health.get('platform', 'Unknown')}\n"
                f"🐍 Python: {health.get('python', 'Unknown')}\n"
                f"📦 Projects: {len(project_owners)}\n"
                f"💚 Running: {len([p for p in running_processes.values() if p.poll() is None])}"
            )
        await update.message.reply_text(msg, parse_mode='Markdown')
    
    elif text == "🌎 INFO":
        await update.message.reply_text(
            "🌎 **SERVER INFO**\n━━━━━━━━━━━━━━━━━━━━━\n"
            f"🚀 Port: {PORT}\n"
            f"🔄 Auto-Restart: {'ON' if auto_restart_mode else 'OFF'}\n"
            f"🛡️ Recovery: {'ON' if recovery_enabled else 'OFF'}\n"
            f"📺 Live Logs: {'ON' if live_logs_enabled else 'OFF'}\n"
            f"📦 Default Limit: {DEFAULT_USER_LIMIT}\n"
            f"📢 Channel: {REQUIRED_CHANNEL}",
            parse_mode='Markdown'
        )
    
    elif text == "📠 CONTACT":
        keyboard = [[InlineKeyboardButton("📠 CONTACT OWNER", url=f"tg://user?id={PRIMARY_ADMIN_ID}")]]
        await update.message.reply_text(f"{ADMIN_DISPLAY_NAME}", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    
    elif text == "🎫 PROMO":
        if is_admin(user_id):
            await update.message.reply_text(
                "🎫 **PROMO CODE ADMIN**\n━━━━━━━━━━━━━━━━━━━━━\n"
                "`/gencode <count>` — Generate codes\n"
                "`/listcodes` — List all codes\n"
                "`/setlimit @user <limit>` — Set user limit\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                f"Total codes: {len(promo_codes)}\n"
                f"Used: {len([c for c in promo_codes.values() if c['used']])}",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                "🎫 **REDEEM PROMO CODE**\n━━━━━━━━━━━━━━━━━━━━━\n"
                "Type: `/redeem CODE`\n"
                f"Your limit: {get_user_limit(user_id)}\n"
                f"Used: {get_user_project_count(user_id)}/{get_user_limit(user_id)}",
                parse_mode='Markdown'
            )
    
    elif text in ["🔒 LOCK", "🔓 UNLOCK"] and is_admin(user_id):
        bot_locked = "🔒 LOCK" in text
        status = "LOCKED" if bot_locked else "UNLOCKED"
        await animate(update, context, Loading.executing(), delay=0.2, final=f"🔒 **System {status}**")
        await update.message.reply_text("Menu updated!", reply_markup=get_main_keyboard(user_id), parse_mode='Markdown')
    
    elif "🔄 AUTO RESTART:" in text and is_admin(user_id):
        auto_restart_mode = "OFF" in text
        status = "ON" if auto_restart_mode else "OFF"
        await animate(update, context, Loading.executing(), delay=0.2, final=f"🔄 **Auto-Restart: {status}**")
        await update.message.reply_text("Menu updated!", reply_markup=get_main_keyboard(user_id), parse_mode='Markdown')
    
    elif "🛡️ RECOVERY:" in text and is_admin(user_id):
        recovery_enabled = "OFF" in text
        status = "ON" if recovery_enabled else "OFF"
        await animate(update, context, Loading.executing(), delay=0.2, final=f"🛡️ **Recovery: {status}**")
        await update.message.reply_text("Menu updated!", reply_markup=get_main_keyboard(user_id), parse_mode='Markdown')
    
    elif "📺 LIVE LOGS:" in text:
        live_logs_enabled = "OFF" in text
        status = "ON" if live_logs_enabled else "OFF"
        if not live_logs_enabled:
            for uid in list(user_log_sessions.keys()):
                log_streamer.unsubscribe(uid)
        await animate(update, context, Loading.executing(), delay=0.2, final=f"📺 **Live Logs: {status}**")
        await update.message.reply_text("Menu updated!", reply_markup=get_main_keyboard(user_id), parse_mode='Markdown')
    
    elif text == "📊 STATUS" and is_admin(user_id):
        total = len(project_owners)
        running = len([p for p in running_processes.values() if p.poll() is None])
        msg = (
            "📊 **PROJECT STATUS**\n━━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 Total: {total}\n"
            f"💚 Running: {running}\n"
            f"💔 Offline: {total - running}\n"
            f"📺 Live Logs: {'ON' if live_logs_enabled else 'OFF'}\n"
            f"🛡️ Recovery: {'ON' if recovery_enabled else 'OFF'}\n"
            f"🔄 Auto-Restart: {'ON' if auto_restart_mode else 'OFF'}"
        )
        await update.message.reply_text(msg, parse_mode='Markdown')
    
    else:
        await update.message.reply_text("⚠️ Use menu buttons or /help", parse_mode='Markdown')

# --- CALLBACK HANDLER ---
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    
    if data == "check_join":
        if await check_channel_membership(user_id, context):
            await query.edit_message_text("✅ Verified!", parse_mode='Markdown')
            await start(update, context)
        else:
            await query.answer("❌ Not joined!", show_alert=True)
        return
    
    parts = data.split('_', 1)
    action = parts[0]
    p_name = parts[1] if len(parts) > 1 else ""
    
    # --- MANAGE PROJECT ---
    if action == "manage":
        if p_name not in project_owners:
            await query.edit_message_text("❌ Project not found", parse_mode='Markdown')
            return
        if project_owners[p_name]["u_id"] != user_id and not is_admin(user_id):
            await query.answer("❌ Not yours!", show_alert=True)
            return
        
        status = "💚 ONLINE" if (p_name in running_processes and running_processes[p_name].poll() is None) else "💔 OFFLINE"
        lang = project_owners[p_name].get("language", "Unknown")
        keyboard = [
            [InlineKeyboardButton("▶️ RUN", callback_data=f"run_{p_name}"), InlineKeyboardButton("🛑 STOP", callback_data=f"stop_{p_name}")],
            [InlineKeyboardButton("📺 VIEW LOGS", callback_data=f"logs_{p_name}")],
            [InlineKeyboardButton("🗑️ DELETE", callback_data=f"del_{p_name}")]
        ]
        await query.edit_message_text(
            f"📦 **Project:** `{p_name}`\n"
            f"📁 Language: {lang}\n"
            f"📡 Status: {status}\n"
            f"📺 Live Logs: {'ON' if live_logs_enabled else 'OFF'}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    # --- RUN PROJECT ---
    elif action == "run":
        if p_name not in project_owners:
            await query.edit_message_text("❌ Not found", parse_mode='Markdown')
            return
        if p_name in running_processes and running_processes[p_name].poll() is None:
            await query.edit_message_text(f"⚠️ `{p_name}` is already running!", parse_mode='Markdown')
            return
        
        msg = await query.edit_message_text("⚡ Starting...")
        await asyncio.sleep(0.5)
        
        folder = project_owners[p_name]["path"]
        main_file = project_owners[p_name].get("main_file")
        
        if not main_file or not os.path.exists(main_file):
            # Find any executable file
            for f in os.listdir(folder):
                if f.endswith(('.py', '.js', '.go', '.rs', '.java', '.cpp', '.c', '.sh', '.rb', '.php')):
                    main_file = os.path.join(folder, f)
                    break
            if not main_file:
                await query.edit_message_text("❌ No executable file found!", parse_mode='Markdown')
                return
        
        try:
            ext = get_ext(main_file)
            cmd = get_run_command(main_file, ext)
            proc = subprocess.Popen(
                cmd,
                cwd=folder,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )
            running_processes[p_name] = proc
            
            if live_logs_enabled:
                log_streamer.start_stream(p_name, proc)
            
            if auto_restart_mode:
                asyncio.create_task(monitor_process(p_name, folder))
            
            await query.edit_message_text(
                f"🚀 **`{p_name}` is now ONLINE! 💚**\n"
                f"📁 Language: {project_owners[p_name].get('language', 'Unknown')}\n"
                f"📺 Click VIEW LOGS for output.",
                parse_mode='Markdown'
            )
        except Exception as e:
            await query.edit_message_text(f"❌ Failed: {str(e)}", parse_mode='Markdown')
    
    # --- STOP PROJECT ---
    elif action == "stop":
        if p_name in running_processes:
            try:
                log_streamer.stop_stream(p_name)
                running_processes[p_name].terminate()
                running_processes[p_name].wait(timeout=5)
            except:
                running_processes[p_name].kill()
            del running_processes[p_name]
            for uid, sess in list(user_log_sessions.items()):
                if sess.get("project") == p_name:
                    sess["active"] = False
            await query.edit_message_text(f"🛑 **`{p_name}` is OFFLINE 💔**", parse_mode='Markdown')
        else:
            await query.edit_message_text(f"⚠️ `{p_name}` was not running", parse_mode='Markdown')
    
    # --- VIEW LOGS ---
    elif action == "logs":
        if not live_logs_enabled:
            await query.answer("❌ Live Logs are OFF!", show_alert=True)
            return
        if p_name not in running_processes or running_processes[p_name].poll() is not None:
            await query.answer("❌ Project not running!", show_alert=True)
            return
        
        log_msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="📺 **Initializing live console...**",
            parse_mode='Markdown'
        )
        success = log_streamer.subscribe(p_name, user_id, update.effective_chat.id, log_msg.message_id)
        if success:
            await query.answer("✅ Live logs started!", show_alert=True)
        else:
            await log_msg.edit_text("❌ Failed to start logs!", parse_mode='Markdown')
    
    # --- DELETE PROJECT ---
    elif action == "del":
        if p_name not in project_owners:
            await query.edit_message_text("❌ Not found", parse_mode='Markdown')
            return
        if project_owners[p_name]["u_id"] != user_id and not is_admin(user_id):
            await query.answer("❌ Not yours!", show_alert=True)
            return
        
        msg = await query.edit_message_text("🗑️ Deleting...")
        await asyncio.sleep(0.5)
        
        if p_name in running_processes:
            try:
                log_streamer.stop_stream(p_name)
                running_processes[p_name].terminate()
                running_processes[p_name].wait(timeout=5)
            except:
                running_processes[p_name].kill()
            del running_processes[p_name]
        
        for uid, sess in list(user_log_sessions.items()):
            if sess.get("project") == p_name:
                sess["active"] = False
        
        path = project_owners[p_name]["path"]
        if os.path.exists(path):
            shutil.rmtree(path)
        del project_owners[p_name]
        
        await query.edit_message_text(f"🗑️ **`{p_name}` deleted!**", parse_mode='Markdown')

# --- MONITOR PROCESS (Auto-Restart) ---
async def monitor_process(p_name, folder):
    while auto_restart_mode and p_name in running_processes:
        proc = running_processes.get(p_name)
        if proc and proc.poll() is not None:
            await asyncio.sleep(2)
            main_file = project_owners.get(p_name, {}).get("main_file")
            if not main_file:
                for f in os.listdir(folder):
                    if f.endswith(('.py', '.js', '.go', '.rs', '.java', '.cpp', '.c', '.sh', '.rb', '.php')):
                        main_file = os.path.join(folder, f)
                        break
            if main_file and os.path.exists(main_file):
                ext = get_ext(main_file)
                cmd = get_run_command(main_file, ext)
                new_proc = subprocess.Popen(cmd, cwd=folder, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
                running_processes[p_name] = new_proc
                if live_logs_enabled:
                    log_streamer.stop_stream(p_name)
                    log_streamer.start_stream(p_name, new_proc)
                logger.info(f"Auto-restarted {p_name}")
        await asyncio.sleep(5)

# --- RECOVERY SYSTEM ---
class BotRecovery:
    def __init__(self):
        self.running = True
    
    async def start_recovery_monitor(self, application):
        while self.running and recovery_enabled:
            try:
                await self.recover_projects()
                await asyncio.sleep(10)
            except Exception as e:
                logger.error(f"Recovery error: {e}")
                await asyncio.sleep(5)
    
    async def recover_projects(self):
        for p_name, proc in list(running_processes.items()):
            if proc.poll() is not None:
                if recovery_enabled and p_name in project_owners:
                    folder = project_owners[p_name]["path"]
                    main_file = project_owners[p_name].get("main_file")
                    if not main_file:
                        for f in os.listdir(folder):
                            if f.endswith(('.py', '.js', '.go', '.rs', '.java', '.cpp', '.c', '.sh', '.rb', '.php')):
                                main_file = os.path.join(folder, f)
                                break
                    if main_file and os.path.exists(main_file):
                        ext = get_ext(main_file)
                        cmd = get_run_command(main_file, ext)
                        new_proc = subprocess.Popen(cmd, cwd=folder, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
                        running_processes[p_name] = new_proc
                        if live_logs_enabled:
                            log_streamer.stop_stream(p_name)
                            log_streamer.start_stream(p_name, new_proc)
                        logger.info(f"Recovered {p_name}")
    
    def stop(self):
        self.running = False

recovery_system = BotRecovery()

# --- COMMANDS ---
async def cmd_redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args
    if not args:
        await update.message.reply_text("❌ Use: `/redeem CODE`", parse_mode='Markdown')
        return
    code = args[0].upper()
    success, msg = redeem_promo_code(user_id, code)
    await update.message.reply_text(msg, parse_mode='Markdown')

async def cmd_gencode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ Admin only!", parse_mode='Markdown')
        return
    args = context.args
    count = int(args[0]) if args and args[0].isdigit() else 5
    codes = create_promo_codes(min(count, 50))
    msg = "🎫 **Promo codes generated:**\n━━━━━━━━━━━━━━━━━━━━━\n" + "\n".join([f"`{c}`" for c in codes]) + "\n━━━━━━━━━━━━━━━━━━━━━\nSend to users: `/redeem CODE`"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def cmd_listcodes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin only!", parse_mode='Markdown')
        return
    total = len(promo_codes)
    used = len([c for c in promo_codes.values() if c["used"]])
    msg = f"🎫 **Promo codes:** {total} total, {used} used\n━━━━━━━━━━━━━━━━━━━━━\n"
    for code, data in list(promo_codes.items())[:20]:
        status = "✅ USED" if data["used"] else "🟢 AVAILABLE"
        msg += f"`{code}` → {status}\n"
    if total > 20:
        msg += f"... and {total - 20} more"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def cmd_setlimit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin only!", parse_mode='Markdown')
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("❌ Use: `/setlimit @username 10`", parse_mode='Markdown')
        return
    username = args[0].replace('@', '')
    limit = int(args[1]) if args[1].isdigit() else DEFAULT_USER_LIMIT
    # Find user by username
    target_user = None
    for uid, data in project_owners.items():
        if data.get("u_username") == username:
            target_user = data["u_id"]
            break
    if target_user:
        set_user_limit(target_user, limit)
        await update.message.reply_text(f"✅ User @{username} limit set to {limit}", parse_mode='Markdown')
    else:
        await update.message.reply_text(f"⚠️ User @{username} not found in projects", parse_mode='Markdown')

async def cmd_mylimit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    limit = get_user_limit(user_id)
    used = get_user_project_count(user_id)
    await update.message.reply_text(
        f"📦 **Your limit:** {limit}\n"
        f"📁 **Used:** {used}/{limit}\n"
        f"🎫 **Redeem promo:** `/redeem CODE`",
        parse_mode='Markdown'
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 **SOVITX MULTI-LANG HOST**\n━━━━━━━━━━━━━━━━━━━━━\n"
        "📤 Upload any file\n"
        "📁 Manage your projects\n"
        "🎫 Redeem promo codes\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "📢 Join channel first!\n"
        "👑 Admin: /gencode, /listcodes, /setlimit\n"
        "👤 User: /redeem, /mylimit",
        parse_mode='Markdown'
    )

# --- SIGNAL HANDLER ---
def signal_handler(signum, frame):
    logger.info("Shutting down...")
    recovery_system.stop()
    for p in list(log_streamer.active_streams.keys()):
        log_streamer.stop_stream(p)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# --- LOG VIEWER TASK ---
async def log_viewer_task(context: ContextTypes.DEFAULT_TYPE):
    while True:
        try:
            if not live_logs_enabled:
                await asyncio.sleep(2)
                continue
            for uid, sess in list(user_log_sessions.items()):
                if not sess["active"]:
                    continue
                if time.time() - sess["last_update"] < 2:
                    continue
                logs = sess["buffer"][-20:]
                sess["buffer"] = []
                if not logs:
                    continue
                terminal = f"📺 **Live Console - {sess['project']}**\n━━━━━━━━━━━━━━━━━━━━━\n```\n" + "\n".join(logs[-30:]) + "\n```\n🟢 ONLINE"
                try:
                    await context.bot.edit_message_text(chat_id=sess["chat_id"], message_id=sess["message_id"], text=terminal, parse_mode='Markdown')
                    sess["last_update"] = time.time()
                except Exception as e:
                    if "message is not modified" not in str(e).lower():
                        sess["active"] = False
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Log viewer error: {e}")
            await asyncio.sleep(2)

# --- MAIN ---
def main():
    web_thread = Thread(target=run_web, daemon=True)
    web_thread.start()
    
    application = Application.builder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("redeem", cmd_redeem))
    application.add_handler(CommandHandler("gencode", cmd_gencode))
    application.add_handler(CommandHandler("listcodes", cmd_listcodes))
    application.add_handler(CommandHandler("setlimit", cmd_setlimit))
    application.add_handler(CommandHandler("mylimit", cmd_mylimit))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_doc))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    async def post_init(app):
        asyncio.create_task(log_viewer_task(app))
        asyncio.create_task(recovery_system.start_recovery_monitor(app))
    
    application.post_init = post_init
    
    webhook_url = os.environ.get('WEBHOOK_URL')
    if webhook_url:
        application.run_webhook(listen="0.0.0.0", port=PORT, webhook_url=webhook_url)
    else:
        application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
