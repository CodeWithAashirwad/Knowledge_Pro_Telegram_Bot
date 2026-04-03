"""
Knowledge Pro AI Telegram Bot
Single-file implementation with Firebase integration
Parse mode: HTML throughout (avoids all Markdown entity errors)
"""

import os
import logging
import random
import tempfile
from threading import Thread

# ─── Telegram ────────────────────────────────────────────────────────────────
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)
from telegram.constants import ParseMode
from telegram.error import TelegramError

# ─── YouTube Download ────────────────────────────────────────────────────────
import yt_dlp

# ─── HTTP for Firebase REST ──────────────────────────────────────────────────
import requests as _req

# ─── Keep-Alive ──────────────────────────────────────────────────────────────
from flask import Flask
from waitress import serve

# ═════════════════════════════════════════════════════════════════════════════
# ENVIRONMENT VARIABLES
# ═════════════════════════════════════════════════════════════════════════════
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
BOT_OWNER = int(os.environ.get("BOT_OWNER", "0"))
PORT      = int(os.environ.get("PORT", "8080"))

# ═════════════════════════════════════════════════════════════════════════════
# FIREBASE CONFIG
# ═════════════════════════════════════════════════════════════════════════════
_FB_BASE = "https://knowledge-pro-c9ee5-default-rtdb.firebaseio.com"

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ═════════════════════════════════════════════════════════════════════════════
# FIREBASE REST HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def fb_get(path: str):
    try:
        r = _req.get(f"{_FB_BASE}/{path}.json", timeout=10)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        logger.error(f"fb_get({path}) error: {e}")
        return None

def fb_set(path: str, data):
    try:
        r = _req.put(f"{_FB_BASE}/{path}.json", json=data, timeout=10)
        return r.status_code == 200
    except Exception as e:
        logger.error(f"fb_set({path}) error: {e}")
        return False

def fb_delete(path: str):
    try:
        r = _req.delete(f"{_FB_BASE}/{path}.json", timeout=10)
        return r.status_code == 200
    except Exception as e:
        logger.error(f"fb_delete({path}) error: {e}")
        return False

# ═════════════════════════════════════════════════════════════════════════════
# HTML HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def esc(text: str) -> str:
    """Escape HTML special characters so they render as literal text."""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))

def mention_html(user) -> str:
    """Return an HTML inline mention for a Telegram user."""
    name = esc(user.full_name or user.first_name or "User")
    return f'<a href="tg://user?id={user.id}">{name}</a>'

def get_media(key: str) -> str:
    """Fetch a media URL stored in Firebase under /media/<key>."""
    val = fb_get(f"media/{key}")
    return val if isinstance(val, str) and val.startswith("http") else ""

# ═════════════════════════════════════════════════════════════════════════════
# ADMIN / PERMISSION CHECKS
# ═════════════════════════════════════════════════════════════════════════════

async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE,
                   user_id: int = None) -> bool:
    uid = user_id or update.effective_user.id
    if uid == BOT_OWNER:
        return True
    try:
        member = await context.bot.get_chat_member(update.effective_chat.id, uid)
        return member.status in ("administrator", "creator")
    except Exception:
        return False

async def is_group_owner(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(update.effective_chat.id, user_id)
        return member.status == "creator"
    except Exception:
        return False

async def self_respect_check(update: Update, context: ContextTypes.DEFAULT_TYPE,
                              target_id: int) -> bool:
    """Return True (action blocked) for bot, bot owner, or group owner."""
    if target_id == context.bot.id:
        await update.message.reply_text("I won't act on myself!")
        return True
    if target_id == BOT_OWNER:
        await update.message.reply_text("I won't act on my owner!")
        return True
    if await is_group_owner(update, context, target_id):
        await update.message.reply_text("I cannot act on the group owner!")
        return True
    return False

async def resolve_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resolve target user from replied message or first @username/ID argument."""
    if update.message.reply_to_message:
        return update.message.reply_to_message.from_user
    if context.args:
        raw = context.args[0].lstrip("@")
        try:
            member = await context.bot.get_chat_member(update.effective_chat.id, raw)
            return member.user
        except Exception:
            pass
    return None

# ═════════════════════════════════════════════════════════════════════════════
# HELP TEXT  (pure HTML — no Markdown tokens)
# ═════════════════════════════════════════════════════════════════════════════

HELP_TEXT = (
    "<b>Knowledge Pro AI  Help</b>\n\n"
    "<b>Basic Commands</b>\n"
    "/start  Welcome message\n"
    "/help   This help menu\n"
    "/id     Your ID / Chat ID\n"
    "/roll   Roll a dice\n\n"
    "<b>Moderation</b>\n"
    "/kick @user         Kick a user\n"
    "/ban @user          Ban a user\n"
    "/mute @user         Mute a user\n"
    "/unmute @user       Unmute a user\n"
    "/promote @user      Promote to admin\n"
    "/demote @user       Demote from admin\n"
    "/pin                Pin replied message\n"
    "/unpin              Unpin replied message\n"
    "/permission @user perm on|off\n\n"
    "<b>Auto Reply</b>\n"
    "/autoreply set word | response\n"
    "/autoreply list\n"
    "/autoreply delete word\n\n"
    "<b>Shout</b>\n"
    "/shout message         Announce a message\n"
    "/shoutconfig           Shout config panel\n\n"
    "<b>AFK</b>\n"
    "/afk reason\n\n"
    "<b>Nuke</b>\n"
    "/nuke   Delete messages in bulk\n\n"
    "<b>YouTube Download</b>\n"
    "/yt_dow url mp4|mp3"
)

# ═════════════════════════════════════════════════════════════════════════════
# /start
# ═════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user          = update.effective_user
    welcome_media = get_media("welcome")

    text = (
        f"Welcome, {mention_html(user)}!\n\n"
        "<b>About Me</b>\n"
        "I'm <b>Knowledge Pro AI</b> — fully customisable, easy-to-use group moderation bot!\n\n"
        + HELP_TEXT
    )

    try:
        if welcome_media:
            await update.message.reply_photo(
                photo=welcome_media, caption=text, parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"cmd_start error: {e}")
        await update.message.reply_text("Welcome! Use /help to see all commands.")

# ═════════════════════════════════════════════════════════════════════════════
# /help
# ═════════════════════════════════════════════════════════════════════════════

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_media = get_media("help")
    try:
        if help_media:
            await update.message.reply_photo(
                photo=help_media, caption=HELP_TEXT, parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"cmd_help error: {e}")
        await update.message.reply_text("Use /start to see all commands.")

# ═════════════════════════════════════════════════════════════════════════════
# /id   /roll
# ═════════════════════════════════════════════════════════════════════════════

async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    text = (
        f"Your ID: <code>{user.id}</code>\n"
        f"Chat ID: <code>{chat.id}</code>"
    )
    if update.message.reply_to_message:
        ru    = update.message.reply_to_message.from_user
        fname = esc(ru.first_name or "")
        text += f"\n{fname}'s ID: <code>{ru.id}</code>"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def cmd_roll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = random.randint(1, 6)
    faces  = ["1", "2", "3", "4", "5", "6"]
    await update.message.reply_text(
        f"You rolled: <b>{faces[result - 1]}</b>",
        parse_mode=ParseMode.HTML
    )

# ═════════════════════════════════════════════════════════════════════════════
# MODERATION
# ═════════════════════════════════════════════════════════════════════════════

async def cmd_kick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("Admins only.")
    target = await resolve_target(update, context)
    if not target:
        return await update.message.reply_text("Reply to a user or provide @username.")
    if await self_respect_check(update, context, target.id):
        return
    try:
        await context.bot.ban_chat_member(update.effective_chat.id, target.id)
        await context.bot.unban_chat_member(update.effective_chat.id, target.id)
        await update.message.reply_text(
            f"{mention_html(target)} has been kicked.", parse_mode=ParseMode.HTML
        )
    except TelegramError as e:
        await update.message.reply_text(f"Could not kick: {esc(str(e))}", parse_mode=ParseMode.HTML)

async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("Admins only.")
    target = await resolve_target(update, context)
    if not target:
        return await update.message.reply_text("Reply to a user or provide @username.")
    if await self_respect_check(update, context, target.id):
        return
    try:
        await context.bot.ban_chat_member(update.effective_chat.id, target.id)
        await update.message.reply_text(
            f"{mention_html(target)} has been banned.", parse_mode=ParseMode.HTML
        )
    except TelegramError as e:
        await update.message.reply_text(f"Could not ban: {esc(str(e))}", parse_mode=ParseMode.HTML)

async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("Admins only.")
    target = await resolve_target(update, context)
    if not target:
        return await update.message.reply_text("Reply to a user or provide @username.")
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
        await update.message.reply_text(
            f"{mention_html(target)} has been muted.", parse_mode=ParseMode.HTML
        )
    except TelegramError as e:
        await update.message.reply_text(f"Could not mute: {esc(str(e))}", parse_mode=ParseMode.HTML)

async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("Admins only.")
    target = await resolve_target(update, context)
    if not target:
        return await update.message.reply_text("Reply to a user or provide @username.")
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
        await update.message.reply_text(
            f"{mention_html(target)} has been unmuted.", parse_mode=ParseMode.HTML
        )
    except TelegramError as e:
        await update.message.reply_text(f"Could not unmute: {esc(str(e))}", parse_mode=ParseMode.HTML)

async def cmd_promote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("Admins only.")
    target = await resolve_target(update, context)
    if not target:
        return await update.message.reply_text("Reply to a user or provide @username.")
    try:
        await context.bot.promote_chat_member(
            update.effective_chat.id, target.id,
            can_delete_messages=True,
            can_restrict_members=True,
            can_pin_messages=True,
        )
        await update.message.reply_text(
            f"{mention_html(target)} promoted to admin.", parse_mode=ParseMode.HTML
        )
    except TelegramError as e:
        await update.message.reply_text(f"Could not promote: {esc(str(e))}", parse_mode=ParseMode.HTML)

async def cmd_demote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("Admins only.")
    target = await resolve_target(update, context)
    if not target:
        return await update.message.reply_text("Reply to a user or provide @username.")
    if await self_respect_check(update, context, target.id):
        return
    try:
        await context.bot.promote_chat_member(
            update.effective_chat.id, target.id,
            can_manage_chat=False,
            can_delete_messages=False,
            can_restrict_members=False,
            can_pin_messages=False,
            can_change_info=False,
            can_invite_users=False,
        )
        await update.message.reply_text(
            f"{mention_html(target)} has been demoted.", parse_mode=ParseMode.HTML
        )
    except TelegramError as e:
        await update.message.reply_text(f"Could not demote: {esc(str(e))}", parse_mode=ParseMode.HTML)

PERM_MAP = {
    "messages": "can_send_messages",
    "media":    "can_send_other_messages",
    "polls":    "can_send_polls",
    "invite":   "can_invite_users",
    "pin":      "can_pin_messages",
    "info":     "can_change_info",
}

async def cmd_permission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("Admins only.")
    if len(context.args) < 3:
        return await update.message.reply_text(
            f"Usage: /permission @user perm on|off\nPerms: {', '.join(PERM_MAP.keys())}"
        )
    target    = await resolve_target(update, context)
    perm_name = context.args[1].lower()
    toggle    = context.args[2].lower() == "on"
    if perm_name not in PERM_MAP:
        return await update.message.reply_text(
            f"Unknown perm. Choose: {', '.join(PERM_MAP.keys())}"
        )
    if not target:
        return await update.message.reply_text("User not found.")
    if await self_respect_check(update, context, target.id):
        return
    try:
        await context.bot.restrict_chat_member(
            update.effective_chat.id, target.id,
            ChatPermissions(**{PERM_MAP[perm_name]: toggle})
        )
        state = "ON" if toggle else "OFF"
        await update.message.reply_text(
            f"{mention_html(target)}: <code>{esc(perm_name)}</code> set to {state}",
            parse_mode=ParseMode.HTML
        )
    except TelegramError as e:
        await update.message.reply_text(f"Error: {esc(str(e))}", parse_mode=ParseMode.HTML)

async def cmd_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("Admins only.")
    if not update.message.reply_to_message:
        return await update.message.reply_text("Reply to the message you want to pin.")
    try:
        await context.bot.pin_chat_message(
            update.effective_chat.id, update.message.reply_to_message.message_id
        )
        await update.message.reply_text("Message pinned!")
    except TelegramError as e:
        await update.message.reply_text(f"Error: {esc(str(e))}", parse_mode=ParseMode.HTML)

async def cmd_unpin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("Admins only.")
    try:
        if update.message.reply_to_message:
            await context.bot.unpin_chat_message(
                update.effective_chat.id, update.message.reply_to_message.message_id
            )
        else:
            await context.bot.unpin_chat_message(update.effective_chat.id)
        await update.message.reply_text("Message unpinned!")
    except TelegramError as e:
        await update.message.reply_text(f"Error: {esc(str(e))}", parse_mode=ParseMode.HTML)

# ═════════════════════════════════════════════════════════════════════════════
# AUTO REPLY
# ═════════════════════════════════════════════════════════════════════════════

async def cmd_autoreply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text(
            "Usage:\n"
            "/autoreply set word | response\n"
            "/autoreply list\n"
            "/autoreply delete word"
        )
    sub     = context.args[0].lower()
    chat_id = str(update.effective_chat.id)

    if sub == "set":
        raw = " ".join(context.args[1:])
        if "|" not in raw:
            return await update.message.reply_text("Format: /autoreply set word | response")
        word, response = [x.strip() for x in raw.split("|", 1)]
        if not word or not response:
            return await update.message.reply_text("Word and response cannot be empty.")
        fb_set(f"autoreply/{chat_id}/{word}", response)
        await update.message.reply_text(
            f"Auto-reply set:\n<code>{esc(word)}</code>  then  {esc(response)}",
            parse_mode=ParseMode.HTML
        )

    elif sub == "list":
        data      = fb_get(f"autoreply/{chat_id}")
        ar_media  = get_media("autoreply_poster")
        if not data:
            return await update.message.reply_text("No auto-replies set.")
        lines = ["<b>Auto Reply List</b>\n"]
        for word, resp in data.items():
            lines.append(f"<code>{esc(word)}</code>  {esc(resp)}")
        text = "\n".join(lines)
        try:
            if ar_media:
                await update.message.reply_photo(
                    photo=ar_media, caption=text, parse_mode=ParseMode.HTML
                )
            else:
                await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        except Exception:
            await update.message.reply_text(text, parse_mode=ParseMode.HTML)

    elif sub == "delete":
        if len(context.args) < 2:
            return await update.message.reply_text("Provide the word to delete.")
        word = context.args[1].lower()
        fb_delete(f"autoreply/{chat_id}/{word}")
        await update.message.reply_text(
            f"Auto-reply for <code>{esc(word)}</code> removed.", parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text("Unknown sub-command. Use: set / list / delete")

# ═════════════════════════════════════════════════════════════════════════════
# SHOUT
# ═════════════════════════════════════════════════════════════════════════════

async def cmd_shout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("Admins only.")
    if not context.args:
        return await update.message.reply_text("Usage: /shout message")

    message = " ".join(context.args)
    chat_id = str(update.effective_chat.id)

    blocked = fb_get(f"shout_config/{chat_id}/blocked_words") or {}
    for bw in blocked.keys():
        if bw.lower() in message.lower():
            return await update.message.reply_text(
                f"Message contains a blocked word: <b>{esc(bw)}</b>",
                parse_mode=ParseMode.HTML
            )

    text = (
        "<b>ANNOUNCEMENT</b>\n"
        "-----------------------------\n"
        f"{esc(message)}\n"
        "-----------------------------"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def cmd_shoutconfig(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("Admins only.")

    chat_id     = str(update.effective_chat.id)
    gif_blocked = fb_get(f"shout_config/{chat_id}/gif_blocked") or False
    gif_label   = "GIF Blocker: ON" if gif_blocked else "GIF Blocker: OFF"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Add Blocked Word",    callback_data=f"shout_add|{chat_id}")],
        [InlineKeyboardButton("Remove Blocked Word", callback_data=f"shout_remove|{chat_id}")],
        [InlineKeyboardButton(gif_label,             callback_data=f"shout_gif|{chat_id}")],
    ])

    shout_media = get_media("shout_config_poster")
    text = (
        "<b>Shout Configuration Panel</b>\n\n"
        "Manage shout settings using the buttons below."
    )
    try:
        if shout_media:
            await update.message.reply_photo(
                photo=shout_media, caption=text,
                parse_mode=ParseMode.HTML, reply_markup=keyboard
            )
        else:
            await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except Exception:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

async def shoutconfig_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts   = query.data.split("|")
    action  = parts[0]
    chat_id = parts[1] if len(parts) > 1 else str(update.effective_chat.id)

    if action == "shout_add":
        context.user_data["shout_add_pending"] = chat_id
        await query.message.reply_text("Send the word you want to block from /shout:")

    elif action == "shout_remove":
        blocked = fb_get(f"shout_config/{chat_id}/blocked_words") or {}
        if not blocked:
            return await query.message.reply_text("No blocked words to remove.")
        buttons = [
            [InlineKeyboardButton(w, callback_data=f"shout_del_word|{chat_id}|{w}")]
            for w in blocked
        ]
        await query.message.reply_text(
            "Select word to unblock:", reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif action == "shout_gif":
        current = fb_get(f"shout_config/{chat_id}/gif_blocked") or False
        fb_set(f"shout_config/{chat_id}/gif_blocked", not current)
        state = "ON" if not current else "OFF"
        await query.message.reply_text(f"GIF Blocker is now <b>{state}</b>", parse_mode=ParseMode.HTML)

    elif action == "shout_del_word":
        word = parts[2]
        fb_delete(f"shout_config/{chat_id}/blocked_words/{word}")
        await query.edit_message_text(
            f"Removed <code>{esc(word)}</code> from blocked list.", parse_mode=ParseMode.HTML
        )

# ═════════════════════════════════════════════════════════════════════════════
# AFK
# ═════════════════════════════════════════════════════════════════════════════

async def cmd_afk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user    = update.effective_user
    reason  = " ".join(context.args) if context.args else "No reason provided"
    chat_id = str(update.effective_chat.id)

    fb_set(f"afk/{chat_id}/{user.id}", {"reason": reason, "name": user.full_name})

    afk_media = get_media("afk_poster")
    text = (
        "<b>AFK</b>\n\n"
        f"{mention_html(user)} is now AFK\n"
        f"<b>Reason:</b> {esc(reason)}"
    )
    try:
        if afk_media:
            await update.message.reply_photo(
                photo=afk_media, caption=text, parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    except Exception:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def check_afk_return(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear AFK status when the user sends any message/voice/gif."""
    if not update.effective_user or not update.effective_chat:
        return
    user    = update.effective_user
    chat_id = str(update.effective_chat.id)
    uid     = str(user.id)

    afk_data = fb_get(f"afk/{chat_id}/{uid}")
    if afk_data:
        fb_delete(f"afk/{chat_id}/{uid}")
        await update.message.reply_text(
            f"Welcome back, {mention_html(user)}! AFK removed.",
            parse_mode=ParseMode.HTML
        )

# ═════════════════════════════════════════════════════════════════════════════
# NUKE
# ═════════════════════════════════════════════════════════════════════════════

async def cmd_nuke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("Admins only.")

    chat_id    = str(update.effective_chat.id)
    nuke_media = get_media("nuke_poster")

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Delete 100",  callback_data=f"nuke|{chat_id}|100"),
            InlineKeyboardButton("Delete 200",  callback_data=f"nuke|{chat_id}|200"),
        ],
        [
            InlineKeyboardButton("Delete 8900", callback_data=f"nuke|{chat_id}|8900"),
            InlineKeyboardButton("Cancel",      callback_data=f"nuke|{chat_id}|cancel"),
        ],
    ])

    text = (
        "<b>Nuke Panel</b>\n\n"
        "Select how many messages to delete:"
    )
    try:
        if nuke_media:
            await update.message.reply_photo(
                photo=nuke_media, caption=text,
                parse_mode=ParseMode.HTML, reply_markup=keyboard
            )
        else:
            await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except Exception:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

async def nuke_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not await is_admin(update, context):
        return await query.answer("Admins only.", show_alert=True)

    parts   = query.data.split("|")
    chat_id = int(parts[1])
    amount  = parts[2]

    if amount == "cancel":
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await query.message.reply_text("Nuke cancelled.")
        return

    count = int(amount)
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    status_msg = await query.message.reply_text(f"Nuking {count} messages...")
    deleted    = 0
    msg_id     = query.message.message_id

    for i in range(msg_id, max(msg_id - count - 100, 0), -1):
        try:
            await context.bot.delete_message(chat_id, i)
            deleted += 1
            if deleted >= count:
                break
        except Exception:
            continue

    try:
        await status_msg.edit_text(f"Done. Deleted approximately {deleted} messages.")
    except Exception:
        pass

# ═════════════════════════════════════════════════════════════════════════════
# YouTube Download
# ═════════════════════════════════════════════════════════════════════════════

async def cmd_yt_dow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        return await update.message.reply_text(
            "Usage: /yt_dow url mp4|mp3"
        )

    url = context.args[0]
    fmt = context.args[1].lower()

    if fmt not in ("mp4", "mp3"):
        return await update.message.reply_text("Format must be mp4 or mp3.")

    status = await update.message.reply_text("Downloading, please wait...")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            outtmpl = os.path.join(tmpdir, "%(title)s.%(ext)s")

            if fmt == "mp4":
                ydl_opts = {
                    "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                    "outtmpl": outtmpl,
                    "quiet":   True,
                }
            else:
                ydl_opts = {
                    "format": "bestaudio/best",
                    "outtmpl": outtmpl,
                    "postprocessors": [{
                        "key":              "FFmpegExtractAudio",
                        "preferredcodec":   "mp3",
                        "preferredquality": "192",
                    }],
                    "quiet": True,
                }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info  = ydl.extract_info(url, download=True)
                title = info.get("title", "video")

            files = os.listdir(tmpdir)
            if not files:
                return await status.edit_text("Download failed — no file produced.")

            filepath = os.path.join(tmpdir, files[0])
            fsize    = os.path.getsize(filepath)

            if fsize > 50 * 1024 * 1024:
                return await status.edit_text("File too large (over 50 MB) to send via Telegram.")

            await status.edit_text(
                f"Uploading <b>{esc(title)}</b>...", parse_mode=ParseMode.HTML
            )

            with open(filepath, "rb") as f:
                if fmt == "mp4":
                    await update.message.reply_video(video=f, caption=f"Video: {title}")
                else:
                    await update.message.reply_audio(audio=f, title=title, caption=f"Audio: {title}")

            await status.delete()

    except Exception as e:
        logger.error(f"yt_dow error: {e}")
        await status.edit_text(f"Error: {esc(str(e))}", parse_mode=ParseMode.HTML)

# ═════════════════════════════════════════════════════════════════════════════
# MESSAGE HANDLER — AFK check + GIF blocker + auto-reply + pending word input
# ═════════════════════════════════════════════════════════════════════════════

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return

    chat_id = str(update.effective_chat.id)

    # AFK return
    await check_afk_return(update, context)

    # GIF blocker
    gif_blocked = fb_get(f"shout_config/{chat_id}/gif_blocked") or False
    if gif_blocked and update.message.animation:
        try:
            await update.message.delete()
            await update.message.reply_text("GIFs are currently blocked in this chat.")
        except Exception:
            pass
        return

    # Collect blocked-word after "Add Blocked Word" button
    pending_chat = context.user_data.get("shout_add_pending")
    if pending_chat and update.message.text:
        word = update.message.text.strip().lower()
        fb_set(f"shout_config/{pending_chat}/blocked_words/{word}", True)
        del context.user_data["shout_add_pending"]
        await update.message.reply_text(
            f"Word <code>{esc(word)}</code> added to blocked list.",
            parse_mode=ParseMode.HTML
        )
        return

    # Auto-reply
    if update.message.text:
        data      = fb_get(f"autoreply/{chat_id}") or {}
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
    return "Knowledge Pro AI Bot is running!", 200

def run_flask():
    serve(flask_app, host="0.0.0.0", port=PORT)

def start_keep_alive():
    t = Thread(target=run_flask, daemon=True)
    t.start()
    logger.info(f"Keep-alive server started on port {PORT}")

# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is not set!")

    start_keep_alive()
    logger.info("Firebase REST API ready.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("help",        cmd_help))
    app.add_handler(CommandHandler("id",          cmd_id))
    app.add_handler(CommandHandler("roll",        cmd_roll))
    app.add_handler(CommandHandler("kick",        cmd_kick))
    app.add_handler(CommandHandler("ban",         cmd_ban))
    app.add_handler(CommandHandler("mute",        cmd_mute))
    app.add_handler(CommandHandler("unmute",      cmd_unmute))
    app.add_handler(CommandHandler("promote",     cmd_promote))
    app.add_handler(CommandHandler("demote",      cmd_demote))
    app.add_handler(CommandHandler("permission",  cmd_permission))
    app.add_handler(CommandHandler("pin",         cmd_pin))
    app.add_handler(CommandHandler("unpin",       cmd_unpin))
    app.add_handler(CommandHandler("autoreply",   cmd_autoreply))
    app.add_handler(CommandHandler("shout",       cmd_shout))
    app.add_handler(CommandHandler("shoutconfig", cmd_shoutconfig))
    app.add_handler(CommandHandler("afk",         cmd_afk))
    app.add_handler(CommandHandler("nuke",        cmd_nuke))
    app.add_handler(CommandHandler("yt_dow",      cmd_yt_dow))

    app.add_handler(CallbackQueryHandler(shoutconfig_callback, pattern=r"^shout_"))
    app.add_handler(CallbackQueryHandler(nuke_callback,        pattern=r"^nuke\|"))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))

    logger.info("Knowledge Pro AI Bot is starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
