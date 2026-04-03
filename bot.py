"""
Knowledge Pro AI Telegram Bot
Single-file implementation with Firebase integration
"""

import os
import logging
import asyncio
import random
import re
from datetime import datetime
from threading import Thread

# ─── Telegram ───────────────────────────────────────────────────────────────
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)
from telegram.constants import ParseMode
from telegram.error import TelegramError

# ─── Firebase ────────────────────────────────────────────────────────────────
import firebase_admin
from firebase_admin import credentials, db as rtdb

# ─── YouTube Download ────────────────────────────────────────────────────────
import yt_dlp

# ─── Keep-Alive ──────────────────────────────────────────────────────────────
from flask import Flask
from waitress import serve

# ═════════════════════════════════════════════════════════════════════════════
# ENVIRONMENT VARIABLES  (set these in your environment / .env / hosting)
# ═════════════════════════════════════════════════════════════════════════════
BOT_TOKEN   = os.environ.get("BOT_TOKEN", "")      # Your Telegram bot token
BOT_OWNER   = int(os.environ.get("BOT_OWNER", "0")) # Your Telegram user ID
PORT        = int(os.environ.get("PORT", "8080"))   # Keep-alive port

# ═════════════════════════════════════════════════════════════════════════════
# FIREBASE CONFIG  (hard-coded as requested — not in env)
# ═════════════════════════════════════════════════════════════════════════════
FIREBASE_CONFIG = {
    "apiKey": "AIzaSyBD473HnZjcmuBlEwd7uaI0MB-hKU4_Nfs",
    "authDomain": "knowledge-pro-c9ee5.firebaseapp.com",
    "databaseURL": "https://knowledge-pro-c9ee5-default-rtdb.firebaseio.com",
    "projectId": "knowledge-pro-c9ee5",
    "storageBucket": "knowledge-pro-c9ee5.firebasestorage.app",
    "messagingSenderId": "14035001235",
    "appId": "1:14035001235:web:cb7c8a0d7c0ce48d729266",
    "measurementId": "G-ZXZC2BH72M"
}

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ═════════════════════════════════════════════════════════════════════════════
# FIREBASE INIT
# ═════════════════════════════════════════════════════════════════════════════
def init_firebase():
    """Initialize Firebase Admin SDK with a certificate-less approach."""
    try:
        if not firebase_admin._apps:
            cred = credentials.Certificate({
                "type": "service_account",
                "project_id": FIREBASE_CONFIG["projectId"],
                # We use the REST DB via requests; admin SDK only for Realtime DB
            }) if False else None  # placeholder

            # Use application default or anonymous approach via REST
            # Since we only need Realtime Database, we use requests directly
        logger.info("Firebase will be accessed via REST API.")
    except Exception as e:
        logger.error(f"Firebase init error: {e}")

# ─── Firebase REST helpers ────────────────────────────────────────────────────
import requests as _req

_FB_BASE = FIREBASE_CONFIG["databaseURL"]

def fb_get(path: str):
    """GET a value from Firebase Realtime DB."""
    try:
        r = _req.get(f"{_FB_BASE}/{path}.json", timeout=10)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        logger.error(f"fb_get error: {e}")
        return None

def fb_set(path: str, data):
    """PUT (overwrite) a value in Firebase Realtime DB."""
    try:
        r = _req.put(f"{_FB_BASE}/{path}.json", json=data, timeout=10)
        return r.status_code == 200
    except Exception as e:
        logger.error(f"fb_set error: {e}")
        return False

def fb_delete(path: str):
    """DELETE a node in Firebase Realtime DB."""
    try:
        r = _req.delete(f"{_FB_BASE}/{path}.json", timeout=10)
        return r.status_code == 200
    except Exception as e:
        logger.error(f"fb_delete error: {e}")
        return False

def fb_push(path: str, data):
    """POST (push) a new child in Firebase Realtime DB."""
    try:
        r = _req.post(f"{_FB_BASE}/{path}.json", json=data, timeout=10)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        logger.error(f"fb_push error: {e}")
        return None

# ═════════════════════════════════════════════════════════════════════════════
# HELPER UTILITIES
# ═════════════════════════════════════════════════════════════════════════════

def get_media(key: str, default: str = "") -> str:
    """Fetch a media URL (photo/gif) stored in Firebase under /media/<key>."""
    val = fb_get(f"media/{key}")
    return val if isinstance(val, str) else default

async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int = None) -> bool:
    """Return True if user is admin/owner in the chat."""
    uid = user_id or update.effective_user.id
    if uid == BOT_OWNER:
        return True
    try:
        member = await context.bot.get_chat_member(update.effective_chat.id, uid)
        return member.status in ("administrator", "creator")
    except Exception:
        return False

async def is_group_owner(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """Return True if user is the group creator."""
    try:
        member = await context.bot.get_chat_member(update.effective_chat.id, user_id)
        return member.status == "creator"
    except Exception:
        return False

async def bot_is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if the bot itself is an admin in the chat."""
    try:
        bot_member = await context.bot.get_chat_member(
            update.effective_chat.id, context.bot.id
        )
        return bot_member.status in ("administrator", "creator")
    except Exception:
        return False

def mention(user) -> str:
    """Return a Markdown mention string for a user."""
    name = user.full_name or user.first_name or "User"
    return f"[{name}](tg://user?id={user.id})"

# ═════════════════════════════════════════════════════════════════════════════
# /start
# ═════════════════════════════════════════════════════════════════════════════

HELP_TEXT = """
╔══════════════════════════════╗
║      𝗞𝗻𝗼𝘄𝗹𝗲𝗱𝗴𝗲 𝗣𝗿𝗼 𝗔𝗜 — 𝗛𝗲𝗹𝗽  ║
╚══════════════════════════════╝

📌 *Basic Commands*
/start – Welcome message
/help – This help menu
/id – Your ID / Chat ID
/roll – Roll a dice 🎲

🛡️ *Moderation*
/kick @user – Kick a user
/ban @user – Ban a user
/mute @user – Mute a user
/unmute @user – Unmute a user
/promote @user – Promote to admin
/demote @user – Demote from admin
/pin – Pin replied message
/unpin – Unpin replied message
/permission @user perm on|off – Toggle permission

💬 *Auto Reply*
/autoreply set word | response – Set auto-reply
/autoreply list – List all auto-replies
/autoreply delete word – Remove auto-reply

📢 *Shout*
/shout <message> – Announce message
/shoutconfig – Shout configuration panel

💤 *AFK*
/afk <reason> – Set AFK status

💣 *Nuke*
/nuke – Delete messages in bulk

📥 *YouTube*
/yt_dow <url> <mp4|mp3> – Download YouTube video/audio
"""

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    welcome_media = get_media("welcome")

    text = (
        f"👋 *Welcome, {mention(user)}!*\n\n"
        f"🤖 *About Me*\n"
        f"I'm *Knowledge Pro AI* — your fully customisable, easy-to-use group moderation bot!\n\n"
        + HELP_TEXT
    )

    try:
        if welcome_media:
            await update.message.reply_photo(
                photo=welcome_media,
                caption=text,
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ═════════════════════════════════════════════════════════════════════════════
# /help
# ═════════════════════════════════════════════════════════════════════════════

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_media = get_media("help")
    try:
        if help_media:
            await update.message.reply_photo(
                photo=help_media,
                caption=HELP_TEXT,
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.MARKDOWN)

# ═════════════════════════════════════════════════════════════════════════════
# MODERATION HELPERS
# ═════════════════════════════════════════════════════════════════════════════

async def resolve_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resolve target user from reply or @mention argument."""
    if update.message.reply_to_message:
        return update.message.reply_to_message.from_user
    if context.args:
        username = context.args[0].lstrip("@")
        try:
            member = await context.bot.get_chat_member(update.effective_chat.id, username)
            return member.user
        except Exception:
            pass
    return None

async def self_respect_check(update: Update, context: ContextTypes.DEFAULT_TYPE, target_id: int) -> bool:
    """Return True (block action) if target is bot, bot owner, or group owner."""
    if target_id == context.bot.id:
        await update.message.reply_text("❌ I won't moderate myself!")
        return True
    if target_id == BOT_OWNER:
        await update.message.reply_text("❌ I won't moderate my owner!")
        return True
    if await is_group_owner(update, context, target_id):
        await update.message.reply_text("❌ I cannot moderate the group owner!")
        return True
    return False

# ─── /kick ────────────────────────────────────────────────────────────────────

async def cmd_kick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("❌ Admins only.")
    target = await resolve_target(update, context)
    if not target:
        return await update.message.reply_text("❓ Reply to a user or provide @username.")
    if await self_respect_check(update, context, target.id):
        return
    try:
        await context.bot.ban_chat_member(update.effective_chat.id, target.id)
        await context.bot.unban_chat_member(update.effective_chat.id, target.id)
        await update.message.reply_text(f"👢 {mention(target)} has been kicked.", parse_mode=ParseMode.MARKDOWN)
    except TelegramError as e:
        await update.message.reply_text(f"❌ Could not kick: {e}")

# ─── /ban ─────────────────────────────────────────────────────────────────────

async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("❌ Admins only.")
    target = await resolve_target(update, context)
    if not target:
        return await update.message.reply_text("❓ Reply to a user or provide @username.")
    if await self_respect_check(update, context, target.id):
        return
    try:
        await context.bot.ban_chat_member(update.effective_chat.id, target.id)
        await update.message.reply_text(f"🔨 {mention(target)} has been banned.", parse_mode=ParseMode.MARKDOWN)
    except TelegramError as e:
        await update.message.reply_text(f"❌ Could not ban: {e}")

# ─── /mute ────────────────────────────────────────────────────────────────────

async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("❌ Admins only.")
    target = await resolve_target(update, context)
    if not target:
        return await update.message.reply_text("❓ Reply to a user or provide @username.")
    if await self_respect_check(update, context, target.id):
        return
    try:
        perms = ChatPermissions(
            can_send_messages=False,
            can_send_audios=False,
            can_send_documents=False,
            can_send_photos=False,
            can_send_videos=False,
            can_send_video_notes=False,
            can_send_voice_notes=False,
            can_send_polls=False,
            can_send_other_messages=False,
        )
        await context.bot.restrict_chat_member(update.effective_chat.id, target.id, perms)
        await update.message.reply_text(f"🔇 {mention(target)} has been muted.", parse_mode=ParseMode.MARKDOWN)
    except TelegramError as e:
        await update.message.reply_text(f"❌ Could not mute: {e}")

# ─── /unmute ──────────────────────────────────────────────────────────────────

async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("❌ Admins only.")
    target = await resolve_target(update, context)
    if not target:
        return await update.message.reply_text("❓ Reply to a user or provide @username.")
    try:
        perms = ChatPermissions(
            can_send_messages=True,
            can_send_audios=True,
            can_send_documents=True,
            can_send_photos=True,
            can_send_videos=True,
            can_send_video_notes=True,
            can_send_voice_notes=True,
            can_send_polls=True,
            can_send_other_messages=True,
        )
        await context.bot.restrict_chat_member(update.effective_chat.id, target.id, perms)
        await update.message.reply_text(f"🔊 {mention(target)} has been unmuted.", parse_mode=ParseMode.MARKDOWN)
    except TelegramError as e:
        await update.message.reply_text(f"❌ Could not unmute: {e}")

# ─── /promote ─────────────────────────────────────────────────────────────────

async def cmd_promote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("❌ Admins only.")
    target = await resolve_target(update, context)
    if not target:
        return await update.message.reply_text("❓ Reply to a user or provide @username.")
    try:
        await context.bot.promote_chat_member(
            update.effective_chat.id, target.id,
            can_delete_messages=True,
            can_restrict_members=True,
            can_pin_messages=True,
        )
        await update.message.reply_text(f"⭐ {mention(target)} promoted to admin.", parse_mode=ParseMode.MARKDOWN)
    except TelegramError as e:
        await update.message.reply_text(f"❌ Could not promote: {e}")

# ─── /demote ──────────────────────────────────────────────────────────────────

async def cmd_demote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("❌ Admins only.")
    target = await resolve_target(update, context)
    if not target:
        return await update.message.reply_text("❓ Reply to a user or provide @username.")
    if await self_respect_check(update, context, target.id):
        return
    try:
        await context.bot.promote_chat_member(
            update.effective_chat.id, target.id,
            can_delete_messages=False,
            can_restrict_members=False,
            can_pin_messages=False,
            can_change_info=False,
            can_invite_users=False,
            can_manage_chat=False,
        )
        await update.message.reply_text(f"👇 {mention(target)} has been demoted.", parse_mode=ParseMode.MARKDOWN)
    except TelegramError as e:
        await update.message.reply_text(f"❌ Could not demote: {e}")

# ─── /permission ─────────────────────────────────────────────────────────────

PERM_MAP = {
    "messages":       "can_send_messages",
    "media":          "can_send_other_messages",
    "polls":          "can_send_polls",
    "links":          "can_add_web_page_previews",
    "invite":         "can_invite_users",
    "pin":            "can_pin_messages",
    "info":           "can_change_info",
}

async def cmd_permission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: /permission @user perm on|off"""
    if not await is_admin(update, context):
        return await update.message.reply_text("❌ Admins only.")
    if len(context.args) < 3:
        return await update.message.reply_text(
            "Usage: `/permission @user <perm> on|off`\n"
            f"Perms: {', '.join(PERM_MAP.keys())}", parse_mode=ParseMode.MARKDOWN
        )
    target = await resolve_target(update, context)
    perm_name = context.args[1].lower()
    toggle = context.args[2].lower() == "on"

    if perm_name not in PERM_MAP:
        return await update.message.reply_text(f"❓ Unknown permission. Choose: {', '.join(PERM_MAP.keys())}")
    if not target:
        return await update.message.reply_text("❓ User not found.")
    if await self_respect_check(update, context, target.id):
        return

    perm_kwargs = {PERM_MAP[perm_name]: toggle}
    try:
        await context.bot.restrict_chat_member(
            update.effective_chat.id, target.id, ChatPermissions(**perm_kwargs)
        )
        state = "ON ✅" if toggle else "OFF ❌"
        await update.message.reply_text(
            f"🔧 {mention(target)}: `{perm_name}` → {state}", parse_mode=ParseMode.MARKDOWN
        )
    except TelegramError as e:
        await update.message.reply_text(f"❌ Error: {e}")

# ─── /pin / /unpin ────────────────────────────────────────────────────────────

async def cmd_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("❌ Admins only.")
    if not update.message.reply_to_message:
        return await update.message.reply_text("❓ Reply to a message to pin it.")
    try:
        await context.bot.pin_chat_message(
            update.effective_chat.id, update.message.reply_to_message.message_id
        )
        await update.message.reply_text("📌 Message pinned!")
    except TelegramError as e:
        await update.message.reply_text(f"❌ {e}")

async def cmd_unpin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("❌ Admins only.")
    try:
        if update.message.reply_to_message:
            await context.bot.unpin_chat_message(
                update.effective_chat.id, update.message.reply_to_message.message_id
            )
        else:
            await context.bot.unpin_chat_message(update.effective_chat.id)
        await update.message.reply_text("📌 Message unpinned!")
    except TelegramError as e:
        await update.message.reply_text(f"❌ {e}")

# ─── /id ─────────────────────────────────────────────────────────────────────

async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    text = (
        f"👤 *Your ID:* `{user.id}`\n"
        f"💬 *Chat ID:* `{chat.id}`"
    )
    if update.message.reply_to_message:
        ru = update.message.reply_to_message.from_user
        text += f"\n🔍 *{ru.first_name}'s ID:* `{ru.id}`"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ─── /roll ────────────────────────────────────────────────────────────────────

async def cmd_roll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = random.randint(1, 6)
    dice_emoji = ["⚀", "⚁", "⚂", "⚃", "⚄", "⚅"][result - 1]
    await update.message.reply_text(f"🎲 You rolled: {dice_emoji} *{result}*", parse_mode=ParseMode.MARKDOWN)

# ═════════════════════════════════════════════════════════════════════════════
# AUTO REPLY
# ═════════════════════════════════════════════════════════════════════════════

async def cmd_autoreply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /autoreply set word | response
    /autoreply list
    /autoreply delete word
    """
    if not context.args:
        return await update.message.reply_text(
            "Usage:\n`/autoreply set word | response`\n`/autoreply list`\n`/autoreply delete word`",
            parse_mode=ParseMode.MARKDOWN
        )
    sub = context.args[0].lower()

    if sub == "set":
        raw = " ".join(context.args[1:])
        if "|" not in raw:
            return await update.message.reply_text("❓ Format: `/autoreply set word | response`", parse_mode=ParseMode.MARKDOWN)
        word, response = [x.strip() for x in raw.split("|", 1)]
        if not word or not response:
            return await update.message.reply_text("❓ Word and response cannot be empty.")
        chat_id = str(update.effective_chat.id)
        fb_set(f"autoreply/{chat_id}/{word}", response)
        await update.message.reply_text(f"✅ Auto-reply set: *{word}* → `{response}`", parse_mode=ParseMode.MARKDOWN)

    elif sub == "list":
        chat_id = str(update.effective_chat.id)
        data = fb_get(f"autoreply/{chat_id}")
        ar_media = get_media("autoreply_poster")

        if not data:
            return await update.message.reply_text("📭 No auto-replies set.")

        lines = [
            "╔══════════════════════════╗",
            "║    📋  Auto Reply List    ║",
            "╚══════════════════════════╝\n",
        ]
        for word, resp in data.items():
            lines.append(f"• *{word}* → `{resp}`")
        text = "\n".join(lines)

        try:
            if ar_media:
                await update.message.reply_photo(photo=ar_media, caption=text, parse_mode=ParseMode.MARKDOWN)
            else:
                await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    elif sub == "delete":
        if len(context.args) < 2:
            return await update.message.reply_text("❓ Provide the word to delete.")
        word = context.args[1].lower()
        chat_id = str(update.effective_chat.id)
        fb_delete(f"autoreply/{chat_id}/{word}")
        await update.message.reply_text(f"🗑️ Auto-reply for *{word}* removed.", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text("❓ Unknown sub-command. Use: set / list / delete")

# ═════════════════════════════════════════════════════════════════════════════
# /shout
# ═════════════════════════════════════════════════════════════════════════════

async def cmd_shout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("❌ Admins only.")
    if not context.args:
        return await update.message.reply_text("❓ Usage: `/shout <message>`", parse_mode=ParseMode.MARKDOWN)

    message = " ".join(context.args)
    chat_id = str(update.effective_chat.id)

    # Check blocked words
    blocked = fb_get(f"shout_config/{chat_id}/blocked_words") or {}
    for bw in blocked.keys():
        if bw.lower() in message.lower():
            return await update.message.reply_text(f"🚫 Message contains a blocked word: *{bw}*", parse_mode=ParseMode.MARKDOWN)

    text = (
        "📢 *ANNOUNCEMENT*\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"{message}\n"
        "━━━━━━━━━━━━━━━━━━"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ─── /shoutconfig ─────────────────────────────────────────────────────────────

async def cmd_shoutconfig(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("❌ Admins only.")

    chat_id = str(update.effective_chat.id)
    gif_blocked = fb_get(f"shout_config/{chat_id}/gif_blocked") or False

    gif_btn_label = "🎞️ GIF Blocker: ON ✅" if gif_blocked else "🎞️ GIF Blocker: OFF ❌"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Blocked Word", callback_data=f"shout_add|{chat_id}")],
        [InlineKeyboardButton("➖ Remove Blocked Word", callback_data=f"shout_remove|{chat_id}")],
        [InlineKeyboardButton(gif_btn_label, callback_data=f"shout_gif|{chat_id}")],
    ])

    shout_media = get_media("shout_config_poster")
    text = (
        "╔══════════════════════════════╗\n"
        "║   📢  Shout Configuration    ║\n"
        "╚══════════════════════════════╝\n\n"
        "Use the buttons below to manage shout settings."
    )
    try:
        if shout_media:
            await update.message.reply_photo(photo=shout_media, caption=text, reply_markup=keyboard)
        else:
            await update.message.reply_text(text, reply_markup=keyboard)
    except Exception:
        await update.message.reply_text(text, reply_markup=keyboard)

async def shoutconfig_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("|")
    action = parts[0]
    chat_id = parts[1] if len(parts) > 1 else str(update.effective_chat.id)

    if action == "shout_add":
        context.user_data["shout_add_pending"] = chat_id
        await query.message.reply_text("✏️ Send the word you want to block from `/shout`:", parse_mode=ParseMode.MARKDOWN)

    elif action == "shout_remove":
        blocked = fb_get(f"shout_config/{chat_id}/blocked_words") or {}
        if not blocked:
            await query.message.reply_text("📭 No blocked words to remove.")
            return
        buttons = [[InlineKeyboardButton(w, callback_data=f"shout_del_word|{chat_id}|{w}")] for w in blocked]
        await query.message.reply_text("Select word to unblock:", reply_markup=InlineKeyboardMarkup(buttons))

    elif action == "shout_gif":
        current = fb_get(f"shout_config/{chat_id}/gif_blocked") or False
        fb_set(f"shout_config/{chat_id}/gif_blocked", not current)
        state = "ON ✅" if not current else "OFF ❌"
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(f"🎞️ GIF Blocker is now *{state}*", parse_mode=ParseMode.MARKDOWN)

    elif action == "shout_del_word":
        word = parts[2]
        fb_delete(f"shout_config/{chat_id}/blocked_words/{word}")
        await query.edit_message_text(f"✅ Removed *{word}* from blocked list.", parse_mode=ParseMode.MARKDOWN)

# ═════════════════════════════════════════════════════════════════════════════
# /afk
# ═════════════════════════════════════════════════════════════════════════════

async def cmd_afk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    reason = " ".join(context.args) if context.args else "No reason provided"
    chat_id = str(update.effective_chat.id)

    fb_set(f"afk/{chat_id}/{user.id}", {"reason": reason, "name": user.full_name})

    afk_media = get_media("afk_poster")
    text = (
        "╔══════════════════════╗\n"
        "║        💤  AFK       ║\n"
        "╚══════════════════════╝\n\n"
        f"👤 *{mention(user)}* is now AFK\n"
        f"📝 *Reason:* {reason}"
    )
    try:
        if afk_media:
            await update.message.reply_photo(photo=afk_media, caption=text, parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def check_afk_return(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove AFK when the user sends any message/voice/gif."""
    if not update.effective_user or not update.effective_chat:
        return
    user = update.effective_user
    chat_id = str(update.effective_chat.id)
    uid = str(user.id)

    afk_data = fb_get(f"afk/{chat_id}/{uid}")
    if afk_data:
        fb_delete(f"afk/{chat_id}/{uid}")
        await update.message.reply_text(
            f"👋 Welcome back, {mention(user)}! AFK removed.", parse_mode=ParseMode.MARKDOWN
        )

# ═════════════════════════════════════════════════════════════════════════════
# /nuke
# ═════════════════════════════════════════════════════════════════════════════

async def cmd_nuke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("❌ Admins only.")

    chat_id = str(update.effective_chat.id)
    nuke_media = get_media("nuke_poster")

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💥 100", callback_data=f"nuke|{chat_id}|100"),
            InlineKeyboardButton("💥 200", callback_data=f"nuke|{chat_id}|200"),
        ],
        [
            InlineKeyboardButton("☢️ 8900", callback_data=f"nuke|{chat_id}|8900"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"nuke|{chat_id}|cancel"),
        ],
    ])

    text = (
        "╔══════════════════════╗\n"
        "║    ☢️  Nuke Panel    ║\n"
        "╚══════════════════════╝\n\n"
        "⚠️ Select how many messages to delete:"
    )
    try:
        if nuke_media:
            await update.message.reply_photo(photo=nuke_media, caption=text, reply_markup=keyboard)
        else:
            await update.message.reply_text(text, reply_markup=keyboard)
    except Exception:
        await update.message.reply_text(text, reply_markup=keyboard)

async def nuke_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not await is_admin(update, context):
        return await query.answer("❌ Admins only.", show_alert=True)

    parts = query.data.split("|")
    chat_id_str = parts[1]
    amount = parts[2]

    if amount == "cancel":
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("❌ Nuke cancelled.")
        return

    count = int(amount)
    await query.edit_message_reply_markup(reply_markup=None)
    status_msg = await query.message.reply_text(f"☢️ Nuking {count} messages...")

    deleted = 0
    msg_id = query.message.message_id

    for i in range(msg_id, max(msg_id - count - 50, 0), -1):
        try:
            await context.bot.delete_message(int(chat_id_str), i)
            deleted += 1
            if deleted >= count:
                break
        except Exception:
            continue

    try:
        await status_msg.edit_text(f"✅ Deleted approximately {deleted} messages.")
    except Exception:
        pass

# ═════════════════════════════════════════════════════════════════════════════
# /yt_dow
# ═════════════════════════════════════════════════════════════════════════════

async def cmd_yt_dow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        return await update.message.reply_text(
            "❓ Usage: `/yt_dow <YouTube URL> <mp4|mp3>`", parse_mode=ParseMode.MARKDOWN
        )

    url = context.args[0]
    fmt = context.args[1].lower()

    if fmt not in ("mp4", "mp3"):
        return await update.message.reply_text("❓ Format must be `mp4` or `mp3`.", parse_mode=ParseMode.MARKDOWN)

    status = await update.message.reply_text("⏳ Downloading, please wait...")

    try:
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmpdir:
            outtmpl = os.path.join(tmpdir, "%(title)s.%(ext)s")

            if fmt == "mp4":
                ydl_opts = {
                    "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                    "outtmpl": outtmpl,
                    "quiet": True,
                }
            else:
                ydl_opts = {
                    "format": "bestaudio/best",
                    "outtmpl": outtmpl,
                    "postprocessors": [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }],
                    "quiet": True,
                }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                title = info.get("title", "video")

            files = os.listdir(tmpdir)
            if not files:
                await status.edit_text("❌ Download failed — no file produced.")
                return

            filepath = os.path.join(tmpdir, files[0])
            fsize = os.path.getsize(filepath)

            if fsize > 50 * 1024 * 1024:
                await status.edit_text("❌ File too large to send via Telegram (>50MB).")
                return

            await status.edit_text(f"📤 Uploading *{title}*...", parse_mode=ParseMode.MARKDOWN)

            with open(filepath, "rb") as f:
                if fmt == "mp4":
                    await update.message.reply_video(video=f, caption=f"🎬 {title}")
                else:
                    await update.message.reply_audio(audio=f, title=title, caption=f"🎵 {title}")

            await status.delete()

    except Exception as e:
        logger.error(f"yt_dow error: {e}")
        await status.edit_text(f"❌ Error: {e}")

# ═════════════════════════════════════════════════════════════════════════════
# MESSAGE HANDLER — Auto-Reply + AFK check + GIF blocker
# ═════════════════════════════════════════════════════════════════════════════

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return

    user = update.effective_user
    chat_id = str(update.effective_chat.id)

    # ── AFK return check ─────────────────────────────────────────────────────
    await check_afk_return(update, context)

    # ── GIF blocker ──────────────────────────────────────────────────────────
    gif_blocked = fb_get(f"shout_config/{chat_id}/gif_blocked") or False
    if gif_blocked and update.message.animation:
        try:
            await update.message.delete()
            await update.message.reply_text("🚫 GIFs are blocked in this chat.")
        except Exception:
            pass
        return

    # ── Shout pending word collection ────────────────────────────────────────
    pending_chat = context.user_data.get("shout_add_pending")
    if pending_chat:
        word = update.message.text.strip().lower()
        fb_set(f"shout_config/{pending_chat}/blocked_words/{word}", True)
        del context.user_data["shout_add_pending"]
        await update.message.reply_text(f"✅ Word *{word}* added to blocked list.", parse_mode=ParseMode.MARKDOWN)
        return

    # ── Auto-reply ────────────────────────────────────────────────────────────
    if update.message.text:
        data = fb_get(f"autoreply/{chat_id}") or {}
        msg_lower = update.message.text.lower()
        for word, response in data.items():
            if word.lower() in msg_lower:
                await update.message.reply_text(response)
                break

# ═════════════════════════════════════════════════════════════════════════════
# KEEP-ALIVE  (Flask + waitress)
# ═════════════════════════════════════════════════════════════════════════════

flask_app = Flask(__name__)

@flask_app.route("/")
def health():
    return "Knowledge Pro AI Bot is running! ✅", 200

def run_flask():
    serve(flask_app, host="0.0.0.0", port=PORT)

def start_keep_alive():
    t = Thread(target=run_flask, daemon=True)
    t.start()
    logger.info(f"Keep-alive server started on port {PORT}")

# ═════════════════════════════════════════════════════════════════════════════
# BOT SETUP & MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is not set!")

    init_firebase()
    start_keep_alive()

    app = Application.builder().token(BOT_TOKEN).build()

    # ── Command handlers ──────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("help",        cmd_help))
    app.add_handler(CommandHandler("id",          cmd_id))
    app.add_handler(CommandHandler("roll",        cmd_roll))

    # Moderation
    app.add_handler(CommandHandler("kick",        cmd_kick))
    app.add_handler(CommandHandler("ban",         cmd_ban))
    app.add_handler(CommandHandler("mute",        cmd_mute))
    app.add_handler(CommandHandler("unmute",      cmd_unmute))
    app.add_handler(CommandHandler("promote",     cmd_promote))
    app.add_handler(CommandHandler("demote",      cmd_demote))
    app.add_handler(CommandHandler("permission",  cmd_permission))
    app.add_handler(CommandHandler("pin",         cmd_pin))
    app.add_handler(CommandHandler("unpin",       cmd_unpin))

    # Auto-reply
    app.add_handler(CommandHandler("autoreply",   cmd_autoreply))

    # Shout
    app.add_handler(CommandHandler("shout",       cmd_shout))
    app.add_handler(CommandHandler("shoutconfig", cmd_shoutconfig))

    # AFK
    app.add_handler(CommandHandler("afk",         cmd_afk))

    # Nuke
    app.add_handler(CommandHandler("nuke",        cmd_nuke))

    # YouTube download
    app.add_handler(CommandHandler("yt_dow",      cmd_yt_dow))

    # ── Callback query handlers ───────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(shoutconfig_callback, pattern=r"^shout_"))
    app.add_handler(CallbackQueryHandler(nuke_callback,        pattern=r"^nuke\|"))

    # ── Message handler (auto-reply, afk, gif blocker) ────────────────────────
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))

    logger.info("🤖 Knowledge Pro AI Bot is starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
