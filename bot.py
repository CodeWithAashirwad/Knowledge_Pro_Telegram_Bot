#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════╗
║         TeleBot - All-in-One Telegram Bot            ║
║         Firebase RTDB + OpenRouter AI                ║
║         Single file deployment                       ║
╚══════════════════════════════════════════════════════╝

Setup:
  pip install pyTelegramBotAPI firebase-admin requests psutil

Usage:
  Set BOT_TOKEN in config or Firebase: config/bot/token
  Set OWNER_ID in config or Firebase: config/bot/owner_id
  Run: python bot.py
"""

import os, sys, time, random, re, json, logging, threading
import requests
import psutil
from datetime import datetime, timedelta

# ── Telegram
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# ── Firebase
import firebase_admin
from firebase_admin import credentials, db as rtdb

# ════════════════════════════════════════════
#  CONFIGURATION  (edit here or use Firebase)
# ════════════════════════════════════════════

BOT_TOKEN   = os.getenv("BOT_TOKEN", "8313479416:AAFXPcpTzfTbccdngl489jHtSMBnomrgCCU")
OWNER_ID    = int(os.getenv("OWNER_ID", "6920845760"))   # Your Telegram user ID
ADMIN_PASS  = os.getenv("ADMIN_PASS", "admin123")

FIREBASE_URL = "https://knowledge-pro-c9ee5-default-rtdb.firebaseio.com"
FIREBASE_CRED_PATH = os.getenv("FIREBASE_CRED", "firebase_credentials.json")

# OpenRouter (loaded from Firebase: config/ai)
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "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")
OPENROUTER_MODEL   = os.getenv("OPENROUTER_MODEL", "google/gemma-4-31b-it")
AI_INSTRUCTION     = (
    "You are a helpful Telegram bot assistant with self-respect. "
    "If the user is angry or disrespectful, respond firmly and calmly—don't take insults. "
    "If the user is happy or polite, respond warmly and helpfully. "
    "Answer basic questions honestly and concisely."
)

BOT_START_TIME = time.time()

# ════════════════════════════════════════════
#  LOGGING
# ════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger("TeleBot")

# ════════════════════════════════════════════
#  FIREBASE INIT
# ════════════════════════════════════════════

firebase_ok = False

def init_firebase():
    global firebase_ok
    try:
        if os.path.exists(FIREBASE_CRED_PATH):
            cred = credentials.Certificate(FIREBASE_CRED_PATH)
            firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_URL})
        else:
            # Use anonymous access (limited but works for reading public data)
            firebase_admin.initialize_app(options={"databaseURL": FIREBASE_URL})
        firebase_ok = True
        log.info("✅ Firebase connected")
        fb_log("INFO", "Bot started and Firebase connected")
        load_remote_config()
    except Exception as e:
        log.warning(f"⚠️ Firebase failed: {e} — running without Firebase")

def fb_ref(path: str):
    if not firebase_ok:
        return None
    try:
        return rtdb.reference(path)
    except Exception:
        return None

def fb_get(path: str, default=None):
    ref = fb_ref(path)
    if ref is None:
        return default
    try:
        val = ref.get()
        return val if val is not None else default
    except Exception:
        return default

def fb_set(path: str, value):
    ref = fb_ref(path)
    if ref:
        try:
            ref.set(value)
            return True
        except Exception:
            pass
    return False

def fb_push(path: str, value: dict):
    ref = fb_ref(path)
    if ref:
        try:
            ref.push(value)
            return True
        except Exception:
            pass
    return False

def fb_delete(path: str):
    ref = fb_ref(path)
    if ref:
        try:
            ref.delete()
            return True
        except Exception:
            pass
    return False

def fb_log(level: str, msg: str):
    fb_push("logs", {
        "type": level,
        "msg": msg,
        "time": int(time.time() * 1000)
    })

# ════════════════════════════════════════════
#  REMOTE CONFIG LOADER
# ════════════════════════════════════════════

def load_remote_config():
    global BOT_TOKEN, OWNER_ID, ADMIN_PASS, OPENROUTER_API_KEY, OPENROUTER_MODEL, AI_INSTRUCTION
    try:
        bot_cfg = fb_get("config/bot", {})
        ai_cfg  = fb_get("config/ai", {})
        admin_p = fb_get("config/admin_pass")

        if bot_cfg.get("owner_id"):
            OWNER_ID = int(bot_cfg["owner_id"])
        if admin_p:
            ADMIN_PASS = admin_p
        if ai_cfg.get("api_key"):
            OPENROUTER_API_KEY = ai_cfg["api_key"]
        if ai_cfg.get("model"):
            OPENROUTER_MODEL = ai_cfg["model"]
        if ai_cfg.get("instruction"):
            AI_INSTRUCTION = ai_cfg["instruction"]

        log.info(f"✅ Remote config loaded | Owner: {OWNER_ID}")
    except Exception as e:
        log.warning(f"⚠️ Remote config load failed: {e}")

# ════════════════════════════════════════════
#  IN-MEMORY STATE  (synced with Firebase)
# ════════════════════════════════════════════

# {chat_id: {user_id: warn_count}}
warnings = {}
# {user_id: True}
banned_users = set()
# {chat_id: [word, ...]}
blocked_words = {}
# {word_lower: reply}
auto_replies = {}
# {user_id: {reason, time}}
afk_users = {}
# {chat_id: {media, msg, link, all}}
permissions = {}
# {user_id: True}  panel-authenticated users
panel_auth = set()

def load_state_from_firebase():
    """Load persisted state on startup."""
    global auto_replies, banned_users, blocked_words

    try:
        # Auto replies
        ar = fb_get("autoreply", {}) or {}
        auto_replies = {str(k).lower(): str(v) for k, v in ar.items()}
        log.info(f"  AutoReplies loaded: {len(auto_replies)}")

        # Banned users
        bn = fb_get("banned", {}) or {}
        banned_users = {int(v["user_id"]) for v in bn.values() if isinstance(v, dict) and "user_id" in v}
        log.info(f"  Banned users loaded: {len(banned_users)}")

        # Blocked words per global scope
        bw = fb_get("blocked_words") or []
        if isinstance(bw, list):
            blocked_words["global"] = [w.lower() for w in bw]
        elif isinstance(bw, dict):
            blocked_words["global"] = [w.lower() for w in bw.values()]
        log.info(f"  Blocked words loaded: {len(blocked_words.get('global', []))}")

    except Exception as e:
        log.warning(f"⚠️ State load error: {e}")

# ════════════════════════════════════════════
#  BOT INIT
# ════════════════════════════════════════════

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)

def update_stat(key: str, delta=1):
    """Increment a Firebase stats counter."""
    try:
        cur = fb_get(f"stats/{key}", 0) or 0
        fb_set(f"stats/{key}", int(cur) + delta)
    except Exception:
        pass

def is_admin(chat_id: int, user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    try:
        member = bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID

def admin_required(func):
    """Decorator: restrict to admins."""
    def wrapper(message, *args, **kwargs):
        if not is_admin(message.chat.id, message.from_user.id):
            bot.reply_to(message, "🚫 Admin only command.")
            return
        return func(message, *args, **kwargs)
    wrapper.__name__ = func.__name__
    return wrapper

def owner_required(func):
    """Decorator: restrict to owner."""
    def wrapper(message, *args, **kwargs):
        if not is_owner(message.from_user.id):
            bot.reply_to(message, "👑 Owner only command.")
            return
        return func(message, *args, **kwargs)
    wrapper.__name__ = func.__name__
    return wrapper

def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ════════════════════════════════════════════
#  START / HELP
# ════════════════════════════════════════════

@bot.message_handler(commands=["start"])
def cmd_start(message):
    txt = (
        "🙏 *Namaste Bhai!* System active & Firebase is Active! 🚀\n\n"
        "*Available Commands:*\n"
        "👮 /warn, /ban, /kick, /mute — Moderation\n"
        "🔐 /permission [media/msg/link/all] [off/on]\n"
        "☢️ /nuke — Chat clear (with confirmation)\n"
        "📢 /shout [msg/media] — Broadcast\n"
        "🚫 /shoutconfig [word] delete — Block words\n"
        "🤖 /setautoreply [word] | [reply]\n"
        "❌ /deleteautoreply [word]\n"
        "💤 /afk [reason] — Offline w/ DM system\n"
        "📌 /pin & /unpin\n"
        "🎲 /roll  🕺 /bala\n"
        "🔑 /login [pass] — Secret access\n"
        "🧠 /ai [message] — AI chat\n\n"
        "👑 *Owner Only:*\n"
        "📡 /announcement [msg]\n"
        "📊 /ServerStatus\n"
    )
    bot.reply_to(message, txt, parse_mode="Markdown")
    update_stat("total_messages")

# ════════════════════════════════════════════
#  LOGIN (Panel Auth)
# ════════════════════════════════════════════

@bot.message_handler(commands=["login"])
def cmd_login(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "🔑 Usage: /login [password]")
        return
    password = parts[1].strip()
    # Refresh pass from Firebase
    remote_pass = fb_get("config/admin_pass") or ADMIN_PASS
    if password == remote_pass:
        panel_auth.add(message.from_user.id)
        bot.reply_to(message, "✅ *Login successful!* You now have admin access.", parse_mode="Markdown")
        fb_log("INFO", f"Admin login: user {message.from_user.id}")
    else:
        bot.reply_to(message, "❌ Wrong password.")
        fb_log("WARN", f"Failed login attempt: user {message.from_user.id}")

# ════════════════════════════════════════════
#  MODERATION: WARN
# ════════════════════════════════════════════

@bot.message_handler(commands=["warn"])
@admin_required
def cmd_warn(message):
    if not message.reply_to_message:
        bot.reply_to(message, "⚠️ Reply to a message to warn the user.")
        return
    target = message.reply_to_message.from_user
    chat_id = str(message.chat.id)
    user_id = str(target.id)
    reason_parts = message.text.split(maxsplit=1)
    reason = reason_parts[1] if len(reason_parts) > 1 else "No reason"

    warnings.setdefault(chat_id, {})
    warnings[chat_id][user_id] = warnings[chat_id].get(user_id, 0) + 1
    count = warnings[chat_id][user_id]

    fb_set(f"warnings/{chat_id}/{user_id}", count)
    fb_push("mod_log", {"user": target.first_name, "user_id": target.id, "action": "warn", "reason": reason, "time": int(time.time() * 1000)})
    update_stat("total_warns")

    bot.reply_to(message, f"⚠️ *{target.first_name}* warned! ({count}/3)\nReason: {reason}", parse_mode="Markdown")

    if count >= 3:
        try:
            bot.ban_chat_member(message.chat.id, target.id)
            bot.send_message(message.chat.id, f"🚫 *{target.first_name}* auto-banned after 3 warnings!", parse_mode="Markdown")
            fb_log("WARN", f"Auto-banned {target.first_name} after 3 warns")
        except Exception as e:
            log.error(f"Auto-ban failed: {e}")

# ════════════════════════════════════════════
#  MODERATION: BAN
# ════════════════════════════════════════════

@bot.message_handler(commands=["ban"])
@admin_required
def cmd_ban(message):
    if not message.reply_to_message:
        bot.reply_to(message, "🚫 Reply to a message to ban the user.")
        return
    target = message.reply_to_message.from_user
    reason_parts = message.text.split(maxsplit=1)
    reason = reason_parts[1] if len(reason_parts) > 1 else "No reason"

    try:
        bot.ban_chat_member(message.chat.id, target.id)
        banned_users.add(target.id)
        fb_set(f"banned/{target.id}", {"user": target.first_name, "user_id": target.id, "reason": reason, "date": int(time.time() * 1000)})
        fb_push("mod_log", {"user": target.first_name, "user_id": target.id, "action": "ban", "reason": reason, "time": int(time.time() * 1000)})
        update_stat("banned_count")
        bot.reply_to(message, f"🚫 *{target.first_name}* has been banned!\nReason: {reason}", parse_mode="Markdown")
        fb_log("WARN", f"Banned {target.first_name}: {reason}")
    except Exception as e:
        bot.reply_to(message, f"❌ Failed to ban: {e}")

# ════════════════════════════════════════════
#  MODERATION: KICK
# ════════════════════════════════════════════

@bot.message_handler(commands=["kick"])
@admin_required
def cmd_kick(message):
    if not message.reply_to_message:
        bot.reply_to(message, "👢 Reply to a message to kick the user.")
        return
    target = message.reply_to_message.from_user
    try:
        bot.ban_chat_member(message.chat.id, target.id)
        bot.unban_chat_member(message.chat.id, target.id)
        fb_push("mod_log", {"user": target.first_name, "user_id": target.id, "action": "kick", "time": int(time.time() * 1000)})
        bot.reply_to(message, f"👢 *{target.first_name}* has been kicked!", parse_mode="Markdown")
        fb_log("WARN", f"Kicked {target.first_name}")
    except Exception as e:
        bot.reply_to(message, f"❌ Failed to kick: {e}")

# ════════════════════════════════════════════
#  MODERATION: MUTE
# ════════════════════════════════════════════

@bot.message_handler(commands=["mute"])
@admin_required
def cmd_mute(message):
    if not message.reply_to_message:
        bot.reply_to(message, "🔇 Reply to a message to mute the user.")
        return
    target = message.reply_to_message.from_user
    parts = message.text.split()
    # Optional duration in minutes
    duration = 10
    if len(parts) > 1:
        try:
            duration = int(parts[1])
        except ValueError:
            pass

    until = datetime.now() + timedelta(minutes=duration)
    try:
        from telebot.types import ChatPermissions
        bot.restrict_chat_member(
            message.chat.id, target.id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until
        )
        fb_push("mod_log", {"user": target.first_name, "user_id": target.id, "action": "mute", "duration": duration, "time": int(time.time() * 1000)})
        bot.reply_to(message, f"🔇 *{target.first_name}* muted for {duration} minute(s).", parse_mode="Markdown")
        fb_log("INFO", f"Muted {target.first_name} for {duration}m")
    except Exception as e:
        bot.reply_to(message, f"❌ Failed to mute: {e}")

# ════════════════════════════════════════════
#  PERMISSIONS
# ════════════════════════════════════════════

@bot.message_handler(commands=["permission"])
@admin_required
def cmd_permission(message):
    parts = message.text.split()
    if len(parts) < 3:
        bot.reply_to(message, "🔐 Usage: /permission [media|msg|link|all] [on|off]")
        return
    ptype  = parts[1].lower()
    status = parts[2].lower() == "on"
    chat_id = str(message.chat.id)

    permissions.setdefault(chat_id, {})
    permissions[chat_id][ptype] = status
    fb_set(f"settings/perm_{ptype}", status)

    try:
        from telebot.types import ChatPermissions
        cur = permissions[chat_id]
        can_media = cur.get("media", True)
        can_msg   = cur.get("msg",   True)

        if ptype == "all":
            cp = ChatPermissions(
                can_send_messages=status,
                can_send_media_messages=status,
                can_send_other_messages=status,
                can_add_web_page_previews=status
            )
        elif ptype == "media":
            cp = ChatPermissions(can_send_media_messages=status)
        elif ptype == "msg":
            cp = ChatPermissions(can_send_messages=status)
        else:
            cp = ChatPermissions()

        bot.set_chat_permissions(message.chat.id, cp)
    except Exception:
        pass

    emoji = "✅" if status else "❌"
    bot.reply_to(message, f"🔐 Permission *{ptype}* set to {emoji} *{'ON' if status else 'OFF'}*", parse_mode="Markdown")
    fb_log("INFO", f"Permission {ptype} → {status}")

# ════════════════════════════════════════════
#  NUKE
# ════════════════════════════════════════════

nuke_pending = {}

@bot.message_handler(commands=["nuke"])
@admin_required
def cmd_nuke(message):
    parts = message.text.split()
    chat_id = message.chat.id

    if len(parts) > 1 and parts[1].lower() == "confirm":
        # Execute nuke
        bot.send_message(chat_id, "☢️ NUKING CHAT...")
        count = 0
        msg_id = message.message_id
        for i in range(msg_id, max(msg_id - 100, 1), -1):
            try:
                bot.delete_message(chat_id, i)
                count += 1
            except Exception:
                pass
        bot.send_message(chat_id, f"☢️ Nuke complete! Deleted ~{count} messages.")
        fb_log("WARN", f"Nuke executed in chat {chat_id}: ~{count} messages deleted")
    else:
        # Ask confirmation
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton("☢️ YES, NUKE IT!", callback_data=f"nuke_confirm_{chat_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data="nuke_cancel")
        )
        bot.send_message(chat_id, "⚠️ *Are you sure you want to NUKE this chat?*\nThis will delete recent messages!", parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("nuke_"))
def cb_nuke(call):
    if call.data.startswith("nuke_confirm_"):
        chat_id = int(call.data.split("_")[-1])
        if not is_admin(chat_id, call.from_user.id):
            bot.answer_callback_query(call.id, "⛔ Admins only!")
            return
        bot.answer_callback_query(call.id, "☢️ NUKING...")
        bot.edit_message_text("☢️ Nuke in progress...", chat_id, call.message.message_id)
        count = 0
        for i in range(call.message.message_id, max(call.message.message_id - 100, 1), -1):
            try:
                bot.delete_message(chat_id, i)
                count += 1
            except Exception:
                pass
        try:
            bot.send_message(chat_id, f"☢️ Nuke complete! ~{count} messages deleted.")
        except Exception:
            pass
        fb_log("WARN", f"Nuke executed in {chat_id}")
    else:
        bot.answer_callback_query(call.id, "❌ Cancelled")
        bot.delete_message(call.message.chat.id, call.message.message_id)

# ════════════════════════════════════════════
#  SHOUT (BROADCAST)
# ════════════════════════════════════════════

@bot.message_handler(commands=["shout"])
@admin_required
def cmd_shout(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "📢 Usage: /shout [message]")
        return
    content = parts[1]
    chat_id = message.chat.id
    txt = f"📢 *SHOUT:*\n\n{content}"
    bot.send_message(chat_id, txt, parse_mode="Markdown")
    fb_push("broadcast", {"msg": content, "type": "msg", "time": int(time.time() * 1000)})
    fb_log("INFO", f"Shout in {chat_id}: {content[:40]}")

# ════════════════════════════════════════════
#  SHOUTCONFIG (BLOCKED WORDS)
# ════════════════════════════════════════════

@bot.message_handler(commands=["shoutconfig"])
@admin_required
def cmd_shoutconfig(message):
    parts = message.text.split()
    if len(parts) < 3 or parts[2].lower() != "delete":
        bot.reply_to(message, "🚫 Usage: /shoutconfig [word] delete")
        return
    word = parts[1].lower()
    bw_list = blocked_words.get("global", [])
    if word not in bw_list:
        bw_list.append(word)
        blocked_words["global"] = bw_list
        fb_set("blocked_words", bw_list)
        bot.reply_to(message, f"🚫 Word *{word}* has been blocked.", parse_mode="Markdown")
        fb_log("INFO", f"Blocked word: {word}")
    else:
        bot.reply_to(message, f"⚠️ *{word}* is already blocked.", parse_mode="Markdown")

# ════════════════════════════════════════════
#  AUTO REPLY: SET
# ════════════════════════════════════════════

@bot.message_handler(commands=["setautoreply"])
@admin_required
def cmd_setautoreply(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or "|" not in parts[1]:
        bot.reply_to(message, "🤖 Usage: /setautoreply [word] | [reply]")
        return
    word, reply = [x.strip() for x in parts[1].split("|", 1)]
    key = re.sub(r"\s+", "", word.lower())
    auto_replies[key] = reply
    fb_set(f"autoreply/{key}", reply)
    update_stat("autoreply_count")
    bot.reply_to(message, f"🤖 AutoReply set: *{key}* → {reply}", parse_mode="Markdown")
    fb_log("INFO", f"AutoReply: {key} → {reply}")

# ════════════════════════════════════════════
#  AUTO REPLY: DELETE
# ════════════════════════════════════════════

@bot.message_handler(commands=["deleteautoreply"])
@admin_required
def cmd_deleteautoreply(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "❌ Usage: /deleteautoreply [word]")
        return
    word = re.sub(r"\s+", "", parts[1].lower())
    if word in auto_replies:
        del auto_replies[word]
        fb_delete(f"autoreply/{word}")
        bot.reply_to(message, f"❌ AutoReply for *{word}* deleted.", parse_mode="Markdown")
        fb_log("INFO", f"AutoReply deleted: {word}")
    else:
        bot.reply_to(message, f"⚠️ No autoreply found for *{word}*", parse_mode="Markdown")

# ════════════════════════════════════════════
#  AFK
# ════════════════════════════════════════════

@bot.message_handler(commands=["afk"])
def cmd_afk(message):
    parts = message.text.split(maxsplit=1)
    reason = parts[1] if len(parts) > 1 else "AFK"
    user_id = message.from_user.id
    afk_users[user_id] = {"reason": reason, "time": time.time()}
    fb_set(f"afk/{user_id}", {"reason": reason, "time": int(time.time() * 1000)})
    bot.reply_to(message, f"💤 *{message.from_user.first_name}* is now AFK: _{reason}_", parse_mode="Markdown")

# ════════════════════════════════════════════
#  PIN / UNPIN
# ════════════════════════════════════════════

@bot.message_handler(commands=["pin"])
@admin_required
def cmd_pin(message):
    if message.reply_to_message:
        try:
            bot.pin_chat_message(message.chat.id, message.reply_to_message.message_id)
            bot.reply_to(message, "📌 Message pinned!")
        except Exception as e:
            bot.reply_to(message, f"❌ Failed: {e}")
    else:
        bot.reply_to(message, "📌 Reply to a message to pin it.")

@bot.message_handler(commands=["unpin"])
@admin_required
def cmd_unpin(message):
    try:
        bot.unpin_chat_message(message.chat.id)
        bot.reply_to(message, "📌 Message unpinned!")
    except Exception as e:
        bot.reply_to(message, f"❌ Failed: {e}")

# ════════════════════════════════════════════
#  ROLL
# ════════════════════════════════════════════

@bot.message_handler(commands=["roll"])
def cmd_roll(message):
    result = random.randint(1, 6)
    faces = ["⚀","⚁","⚂","⚃","⚄","⚅"]
    bot.reply_to(message, f"🎲 {faces[result-1]} You rolled a *{result}*!", parse_mode="Markdown")

# ════════════════════════════════════════════
#  BALA
# ════════════════════════════════════════════

@bot.message_handler(commands=["bala"])
def cmd_bala(message):
    dances = ["🕺", "💃", "🎉", "🪩", "🎊"]
    dance = random.choice(dances)
    msgs = [
        f"{dance} *BALA BALA!* Let's dance!",
        f"{dance} Party time! Get on the floor!",
        f"{dance} Bala Bala Shaitan Ka Sala! 🎵"
    ]
    bot.send_message(message.chat.id, random.choice(msgs), parse_mode="Markdown")

# ════════════════════════════════════════════
#  AI  (/ai)
# ════════════════════════════════════════════

@bot.message_handler(commands=["ai"])
def cmd_ai(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "🧠 Usage: /ai [your message]")
        return

    user_msg = parts[1].strip()
    user_name = message.from_user.first_name

    # Get fresh config from Firebase
    ai_cfg = fb_get("config/ai", {}) or {}
    api_key  = ai_cfg.get("api_key", OPENROUTER_API_KEY)
    model    = ai_cfg.get("model", OPENROUTER_MODEL)
    instr    = ai_cfg.get("instruction", AI_INSTRUCTION)

    if not api_key:
        bot.reply_to(message, "🧠 AI is not configured yet. Ask admin to set API key.")
        return

    # Detect tone
    angry_words = ["stupid", "idiot", "fool", "shut up", "useless", "dumb", "hate", "bakwas", "bekar", "chup"]
    is_angry = any(w in user_msg.lower() for w in angry_words)

    if is_angry:
        sys_prompt = instr + "\n\n[User seems angry/rude. Respond firmly and maintain your self-respect. Don't be submissive.]"
    else:
        sys_prompt = instr + "\n\n[User seems polite. Respond warmly and helpfully.]"

    try:
        bot.send_chat_action(message.chat.id, "typing")
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://t.me/bot",
                "X-Title": "TeleBot"
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": f"{user_name}: {user_msg}"}
                ],
                "max_tokens": 500
            },
            timeout=30
        )
        data = resp.json()
        reply = data["choices"][0]["message"]["content"].strip()
        bot.reply_to(message, f"🧠 {reply}")
        update_stat("total_messages")
        fb_log("INFO", f"AI query from {user_name}: {user_msg[:40]}")
    except Exception as e:
        log.error(f"AI error: {e}")
        bot.reply_to(message, "❌ AI temporarily unavailable. Try again later.")

# ════════════════════════════════════════════
#  OWNER: ANNOUNCEMENT
# ════════════════════════════════════════════

@bot.message_handler(commands=["announcement"])
@owner_required
def cmd_announcement(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "📡 Usage: /announcement [message]")
        return
    msg_text = parts[1]
    priority = "urgent"
    fb_push("announcements", {"msg": msg_text, "priority": priority, "time": int(time.time() * 1000)})
    bot.reply_to(message, f"📡 *Announcement queued for all groups!*\n\n_{msg_text}_", parse_mode="Markdown")
    fb_log("INFO", f"Announcement sent: {msg_text[:60]}")

    # If you have stored group IDs, broadcast to them
    groups = fb_get("groups", {}) or {}
    sent = 0
    for gid in groups.keys():
        try:
            bot.send_message(int(gid), f"📡 *ANNOUNCEMENT:*\n\n{msg_text}", parse_mode="Markdown")
            sent += 1
        except Exception:
            pass
    if sent > 0:
        bot.send_message(message.chat.id, f"✅ Sent to {sent} groups.")

# ════════════════════════════════════════════
#  OWNER: SERVER STATUS
# ════════════════════════════════════════════

@bot.message_handler(commands=["ServerStatus"])
@owner_required
def cmd_server_status(message):
    uptime_sec = int(time.time() - BOT_START_TIME)
    h, rem = divmod(uptime_sec, 3600)
    m, s   = divmod(rem, 60)

    try:
        cpu  = psutil.cpu_percent(interval=1)
        mem  = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        mem_used  = mem.used  // (1024**2)
        mem_total = mem.total // (1024**2)
        disk_used  = disk.used  // (1024**3)
        disk_total = disk.total // (1024**3)
    except Exception:
        cpu = mem_used = mem_total = disk_used = disk_total = 0

    status_txt = (
        "📊 *SERVER STATUS*\n"
        "─────────────────\n"
        f"⏰ Uptime:  {h}h {m}m {s}s\n"
        f"⚡ CPU:     {cpu:.1f}%\n"
        f"💾 Memory:  {mem_used}/{mem_total} MB\n"
        f"💿 Disk:    {disk_used}/{disk_total} GB\n"
        f"🔥 Firebase: {'✅ Connected' if firebase_ok else '❌ Offline'}\n"
        f"🤖 AutoReplies: {len(auto_replies)}\n"
        f"🚫 Banned: {len(banned_users)}\n"
        f"🕒 Checked: {now_str()}"
    )
    bot.reply_to(message, status_txt, parse_mode="Markdown")
    fb_log("INFO", f"ServerStatus checked by owner")

# ════════════════════════════════════════════
#  MESSAGE HANDLER (auto-reply, AFK, blocked words)
# ════════════════════════════════════════════

@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text(message):
    update_stat("total_messages")
    text_lower = message.text.lower()
    user_id = message.from_user.id
    chat_id = message.chat.id

    # ── Check if user is coming back from AFK
    if user_id in afk_users:
        afk_info = afk_users.pop(user_id)
        fb_delete(f"afk/{user_id}")
        elapsed = int(time.time() - afk_info["time"])
        h, rem = divmod(elapsed, 3600)
        m, s   = divmod(rem, 60)
        bot.reply_to(message, f"👋 *{message.from_user.first_name}* is back after {h}h {m}m {s}s!", parse_mode="Markdown")

    # ── Notify if mentioning an AFK user
    if message.entities:
        for entity in message.entities:
            if entity.type == "mention":
                mention = message.text[entity.offset:entity.offset + entity.length]
                # Try match by username
                for uid, info in afk_users.items():
                    try:
                        chat_member = bot.get_chat_member(chat_id, uid)
                        if chat_member.user.username and f"@{chat_member.user.username}" == mention:
                            bot.reply_to(message, f"💤 *{chat_member.user.first_name}* is AFK: _{info['reason']}_", parse_mode="Markdown")
                    except Exception:
                        pass

    # ── Check blocked words
    bw = blocked_words.get("global", [])
    for word in bw:
        if word in text_lower:
            try:
                bot.delete_message(chat_id, message.message_id)
                bot.send_message(chat_id, f"🚫 *{message.from_user.first_name}*, restricted word detected.", parse_mode="Markdown")
                fb_log("WARN", f"Blocked word '{word}' detected from {message.from_user.first_name}")
            except Exception:
                pass
            return

    # ── Auto reply (exact word, ignoring spaces)
    compact = re.sub(r"\s+", "", text_lower)
    for key, reply in auto_replies.items():
        if key in compact or key == compact:
            bot.reply_to(message, reply)
            fb_log("INFO", f"AutoReply triggered: {key}")
            return

# ════════════════════════════════════════════
#  TRACK GROUPS
# ════════════════════════════════════════════

@bot.message_handler(content_types=["new_chat_members"])
def track_group_join(message):
    gid = str(message.chat.id)
    fb_set(f"groups/{gid}", {"title": message.chat.title, "joined": int(time.time() * 1000)})
    cur = int(fb_get("stats/total_groups", 0) or 0) + 1
    fb_set("stats/total_groups", cur)
    log.info(f"Bot added to group: {message.chat.title}")

    # Welcome new users (if actual users joined, not bot)
    for member in message.new_chat_members:
        if not member.is_bot:
            welcome = fb_get("settings/welcome_enabled", True)
            if welcome is not False:
                bot.send_message(
                    message.chat.id,
                    f"🙏 Welcome *{member.first_name}*! Type /start to see commands.",
                    parse_mode="Markdown"
                )
            update_stat("total_users")

# ════════════════════════════════════════════
#  PANEL COMMAND LISTENER (Firebase → Telegram)
# ════════════════════════════════════════════

last_cmd_check = 0

def poll_panel_commands():
    """Poll Firebase for commands queued by the web panel."""
    global last_cmd_check
    while True:
        try:
            if firebase_ok and OWNER_ID:
                cmds_ref = fb_ref("commands")
                if cmds_ref:
                    cmds = cmds_ref.get() or {}
                    for key, cmd_data in cmds.items():
                        if isinstance(cmd_data, dict):
                            cmd = cmd_data.get("cmd", "")
                            ts  = cmd_data.get("time", 0)
                            if ts > last_cmd_check:
                                log.info(f"Panel command: {cmd}")
                                fb_delete(f"commands/{key}")
                                # Notify owner
                                try:
                                    bot.send_message(OWNER_ID, f"📤 Panel command: `{cmd}`", parse_mode="Markdown")
                                except Exception:
                                    pass
                    last_cmd_check = int(time.time() * 1000)
        except Exception as e:
            pass
        time.sleep(5)

# ════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════

if __name__ == "__main__":
    log.info("═" * 50)
    log.info("  TeleBot Starting...")
    log.info("═" * 50)

    # Init Firebase
    init_firebase()

    if firebase_ok:
        load_state_from_firebase()

    # Start panel command poller in background thread
    t = threading.Thread(target=poll_panel_commands, daemon=True)
    t.start()

    # Update start stat
    fb_set("stats/last_start", int(time.time() * 1000))

    log.info(f"✅ Bot ready | Owner: {OWNER_ID}")
    log.info("📡 Polling for messages...")

    try:
        bot.infinity_polling(timeout=30, long_polling_timeout=20)
    except KeyboardInterrupt:
        log.info("👋 Bot stopped by user")
    except Exception as e:
        log.error(f"Fatal error: {e}")
        sys.exit(1)
