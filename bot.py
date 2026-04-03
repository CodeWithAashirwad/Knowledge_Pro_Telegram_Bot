
"""
Knowledge Pro AI Telegram Bot
Single file | Firebase REST | HTML parse mode
Fixes: YouTube uses tv_embedded client (bypasses bot-detection on server IPs)
       Keep-alive has self-ping loop so Render free tier stays awake 24/7
"""

import os
import asyncio
import logging
import random
import tempfile
import threading

import requests as _req
import yt_dlp
from flask import Flask
from waitress import serve

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)
from telegram.constants import ParseMode
from telegram.error import TelegramError

# =============================================================================
# ENV VARS  -- set in Render / Railway dashboard
# =============================================================================
BOT_TOKEN      = os.environ.get("BOT_TOKEN", "")
BOT_OWNER      = int(os.environ.get("BOT_OWNER", "0"))
PORT           = int(os.environ.get("PORT", "8080"))
# Optional: path to a Netscape-format cookies.txt for age-restricted videos
YT_COOKIES     = os.environ.get("YT_COOKIES_FILE", "")
# Public URL of this service (used by self-ping to stay alive on Render free tier)
# Example: https://my-bot.onrender.com
RENDER_URL     = os.environ.get("RENDER_EXTERNAL_URL", "https://knowledge-pro-telegram-bot.onrender.com")

# =============================================================================
# FIREBASE  (Realtime Database REST -- no SDK needed)
# =============================================================================
_FB = "https://knowledge-pro-c9ee5-default-rtdb.firebaseio.com"

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def fb_get(path):
    try:
        r = _req.get(f"{_FB}/{path}.json", timeout=10)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        logger.error(f"fb_get {path}: {e}")
        return None


def fb_set(path, data):
    try:
        r = _req.put(f"{_FB}/{path}.json", json=data, timeout=10)
        return r.status_code == 200
    except Exception as e:
        logger.error(f"fb_set {path}: {e}")
        return False


def fb_delete(path):
    try:
        r = _req.delete(f"{_FB}/{path}.json", timeout=10)
        return r.status_code == 200
    except Exception as e:
        logger.error(f"fb_delete {path}: {e}")
        return False


# =============================================================================
# HTML HELPERS
# =============================================================================

def esc(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def mention(user):
    name = esc(user.full_name or user.first_name or "User")
    return f'<a href="tg://user?id={user.id}">{name}</a>'


def get_media(key):
    """Return Firebase URL at /media/<key>, or empty string if unset."""
    val = fb_get(f"media/{key}")
    return val if isinstance(val, str) and val.startswith("http") else ""


async def send_embed(message, media_key, text, reply_markup=None):
    """
    Send a styled embed. If a poster URL is set in Firebase, send it as a
    photo with caption. Falls back to plain text on any error.
    Telegram caption limit = 1024 chars; long text is sent separately.
    """
    url    = get_media(media_key)
    kwargs = dict(parse_mode=ParseMode.HTML, reply_markup=reply_markup)
    try:
        if url:
            if len(text) <= 1024:
                await message.reply_photo(photo=url, caption=text, **kwargs)
            else:
                await message.reply_photo(photo=url)
                await message.reply_text(text, **kwargs)
        else:
            await message.reply_text(text, **kwargs)
    except Exception as e:
        logger.warning(f"send_embed({media_key}) error: {e} -- fallback to text")
        try:
            await message.reply_text(text, **kwargs)
        except Exception as e2:
            logger.error(f"send_embed plain fallback failed: {e2}")


# =============================================================================
# ADMIN / SELF-RESPECT CHECKS
# =============================================================================

async def is_admin(update, context, user_id=None):
    uid = user_id or update.effective_user.id
    if uid == BOT_OWNER:
        return True
    try:
        m = await context.bot.get_chat_member(update.effective_chat.id, uid)
        return m.status in ("administrator", "creator")
    except Exception:
        return False


async def is_group_owner(update, context, user_id):
    try:
        m = await context.bot.get_chat_member(update.effective_chat.id, user_id)
        return m.status == "creator"
    except Exception:
        return False


async def self_respect(update, context, target_id):
    """Returns True (blocks action) for bot, bot owner, or group owner."""
    if target_id == context.bot.id:
        await update.message.reply_text("I won't act on myself.")
        return True
    if target_id == BOT_OWNER:
        await update.message.reply_text("I won't act on my owner.")
        return True
    if await is_group_owner(update, context, target_id):
        await update.message.reply_text("I cannot act on the group owner.")
        return True
    return False


async def resolve_target(update, context):
    if update.message.reply_to_message:
        return update.message.reply_to_message.from_user
    if context.args:
        raw = context.args[0].lstrip("@")
        try:
            m = await context.bot.get_chat_member(update.effective_chat.id, raw)
            return m.user
        except Exception:
            pass
    return None


# =============================================================================
# HELP TEXT
# =============================================================================

HELP_TEXT = (
    "================================\n"
    "        Help Command\n"
    "================================\n\n"
    "<b>Basic Commands</b>\n"
    "--------------------------------\n"
    "<code>/start</code>   -- Welcome message\n"
    "<code>/help</code>    -- This help menu\n"
    "<code>/id</code>      -- Your Telegram ID\n"
    "<code>/roll</code>    -- Roll a dice\n\n"
    "<b>Moderation</b>\n"
    "--------------------------------\n"
    "<code>/kick @user</code>              -- Kick from group\n"
    "<code>/ban @user</code>               -- Permanently ban\n"
    "<code>/mute @user</code>              -- Silence a user\n"
    "<code>/unmute @user</code>            -- Restore voice\n"
    "<code>/promote @user</code>           -- Make admin\n"
    "<code>/demote @user</code>            -- Remove admin\n"
    "<code>/pin</code>                     -- Pin replied msg\n"
    "<code>/unpin</code>                   -- Unpin message\n"
    "<code>/permission @user perm on|off</code>\n\n"
    "<b>Auto Reply</b>\n"
    "--------------------------------\n"
    "<code>/autoreply set word | reply</code>\n"
    "<code>/autoreply list</code>\n"
    "<code>/autoreply delete word</code>\n\n"
    "<b>Shout</b>\n"
    "--------------------------------\n"
    "<code>/shout message</code>   -- Announcement\n"
    "<code>/shoutconfig</code>     -- Config panel\n\n"
    "<b>AFK</b>\n"
    "--------------------------------\n"
    "<code>/afk reason</code>      -- Set AFK status\n\n"
    "<b>Nuke</b>\n"
    "--------------------------------\n"
    "<code>/nuke</code>            -- Bulk delete msgs\n\n"
    "<b>YouTube Downloader</b>\n"
    "--------------------------------\n"
    "<code>/yt_dow url mp4|mp3</code>\n\n"
    "================================"
)


# =============================================================================
# /start  /help
# =============================================================================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (
        f"Welcome, {mention(user)}!\n\n"
        "<b>About Me</b>\n"
        "I'm <b>Knowledge Pro AI</b> that moderates your groups your way. "
        "Fully customisable and easy.\n\n"
        + HELP_TEXT
    )
    await send_embed(update.message, "welcome", text)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_embed(update.message, "help", HELP_TEXT)


# =============================================================================
# /id  /roll
# =============================================================================

async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    text = (
        f"Your ID: <code>{user.id}</code>\n"
        f"Chat ID: <code>{chat.id}</code>"
    )
    if update.message.reply_to_message:
        ru    = update.message.reply_to_message.from_user
        fname = esc(ru.first_name or "User")
        text += f"\n{fname}'s ID: <code>{ru.id}</code>"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_roll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    n     = random.randint(1, 6)
    faces = ["1", "2", "3", "4", "5", "6"]
    await update.message.reply_text(
        f"You rolled: <b>{faces[n-1]}</b>", parse_mode=ParseMode.HTML
    )


# =============================================================================
# MODERATION
# =============================================================================

async def cmd_kick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("Admins only.")
    target = await resolve_target(update, context)
    if not target:
        return await update.message.reply_text("Reply to a user or provide @username.")
    if await self_respect(update, context, target.id):
        return
    try:
        await context.bot.ban_chat_member(update.effective_chat.id, target.id)
        await context.bot.unban_chat_member(update.effective_chat.id, target.id)
        await update.message.reply_text(
            f"{mention(target)} has been <b>kicked</b>.", parse_mode=ParseMode.HTML
        )
    except TelegramError as e:
        await update.message.reply_text(f"Error: {esc(str(e))}", parse_mode=ParseMode.HTML)


async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("Admins only.")
    target = await resolve_target(update, context)
    if not target:
        return await update.message.reply_text("Reply to a user or provide @username.")
    if await self_respect(update, context, target.id):
        return
    try:
        await context.bot.ban_chat_member(update.effective_chat.id, target.id)
        await update.message.reply_text(
            f"{mention(target)} has been <b>banned</b>.", parse_mode=ParseMode.HTML
        )
    except TelegramError as e:
        await update.message.reply_text(f"Error: {esc(str(e))}", parse_mode=ParseMode.HTML)


async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("Admins only.")
    target = await resolve_target(update, context)
    if not target:
        return await update.message.reply_text("Reply to a user or provide @username.")
    if await self_respect(update, context, target.id):
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
            f"{mention(target)} has been <b>muted</b>.", parse_mode=ParseMode.HTML
        )
    except TelegramError as e:
        await update.message.reply_text(f"Error: {esc(str(e))}", parse_mode=ParseMode.HTML)


async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("Admins only.")
    target = await resolve_target(update, context)
    if not target:
        return await update.message.reply_text("Reply to a user or provide @username.")
    try:
        perms = ChatPermissions(
            can_send_messages=True, can_send_audios=True, can_send_documents=True,
            can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
            can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True,
        )
        await context.bot.restrict_chat_member(update.effective_chat.id, target.id, perms)
        await update.message.reply_text(
            f"{mention(target)} has been <b>unmuted</b>.", parse_mode=ParseMode.HTML
        )
    except TelegramError as e:
        await update.message.reply_text(f"Error: {esc(str(e))}", parse_mode=ParseMode.HTML)


async def cmd_promote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("Admins only.")
    target = await resolve_target(update, context)
    if not target:
        return await update.message.reply_text("Reply to a user or provide @username.")
    try:
        await context.bot.promote_chat_member(
            update.effective_chat.id, target.id,
            can_delete_messages=True, can_restrict_members=True, can_pin_messages=True,
        )
        await update.message.reply_text(
            f"{mention(target)} <b>promoted</b> to admin.", parse_mode=ParseMode.HTML
        )
    except TelegramError as e:
        await update.message.reply_text(f"Error: {esc(str(e))}", parse_mode=ParseMode.HTML)


async def cmd_demote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("Admins only.")
    target = await resolve_target(update, context)
    if not target:
        return await update.message.reply_text("Reply to a user or provide @username.")
    if await self_respect(update, context, target.id):
        return
    try:
        await context.bot.promote_chat_member(
            update.effective_chat.id, target.id,
            can_manage_chat=False, can_delete_messages=False,
            can_restrict_members=False, can_pin_messages=False,
            can_change_info=False, can_invite_users=False,
        )
        await update.message.reply_text(
            f"{mention(target)} has been <b>demoted</b>.", parse_mode=ParseMode.HTML
        )
    except TelegramError as e:
        await update.message.reply_text(f"Error: {esc(str(e))}", parse_mode=ParseMode.HTML)


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
            f"Usage: /permission @user perm on|off\nPerms: {', '.join(PERM_MAP)}"
        )
    target    = await resolve_target(update, context)
    perm_name = context.args[1].lower()
    toggle    = context.args[2].lower() == "on"
    if perm_name not in PERM_MAP:
        return await update.message.reply_text(f"Unknown perm. Available: {', '.join(PERM_MAP)}")
    if not target:
        return await update.message.reply_text("User not found.")
    if await self_respect(update, context, target.id):
        return
    try:
        await context.bot.restrict_chat_member(
            update.effective_chat.id, target.id,
            ChatPermissions(**{PERM_MAP[perm_name]: toggle})
        )
        state = "ON" if toggle else "OFF"
        await update.message.reply_text(
            f"{mention(target)}: <code>{esc(perm_name)}</code> set to {state}",
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


# =============================================================================
# AUTO REPLY
# =============================================================================

async def cmd_autoreply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text(
            "Usage:\n/autoreply set word | response\n/autoreply list\n/autoreply delete word"
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
            f"Auto-reply saved:\nTrigger: <code>{esc(word)}</code>\nReply: {esc(response)}",
            parse_mode=ParseMode.HTML
        )

    elif sub == "list":
        data = fb_get(f"autoreply/{chat_id}")
        if not data:
            return await update.message.reply_text("No auto-replies saved yet.")
        lines = ["================================\n"
                 "       Auto Reply List\n"
                 "================================\n"]
        for word, resp in data.items():
            lines.append(f"<code>{esc(word)}</code>  --  {esc(resp)}")
        lines.append("\n================================")
        await send_embed(update.message, "autoreply_poster", "\n".join(lines))

    elif sub == "delete":
        if len(context.args) < 2:
            return await update.message.reply_text("Provide the word to delete.")
        word = context.args[1].lower()
        fb_delete(f"autoreply/{chat_id}/{word}")
        await update.message.reply_text(
            f"Auto-reply for <code>{esc(word)}</code> removed.", parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text("Sub-commands: set / list / delete")


# =============================================================================
# SHOUT
# =============================================================================

async def cmd_shout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("Admins only.")
    if not context.args:
        return await update.message.reply_text("Usage: /shout message")
    msg     = " ".join(context.args)
    chat_id = str(update.effective_chat.id)
    blocked = fb_get(f"shout_config/{chat_id}/blocked_words") or {}
    for bw in blocked:
        if bw.lower() in msg.lower():
            return await update.message.reply_text(
                f"Blocked word detected: <b>{esc(bw)}</b>", parse_mode=ParseMode.HTML
            )
    await update.message.reply_text(
        f"<b>-- ANNOUNCEMENT --</b>\n"
        f"--------------------------------\n"
        f"{esc(msg)}\n"
        f"--------------------------------",
        parse_mode=ParseMode.HTML
    )


async def cmd_shoutconfig(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("Admins only.")
    chat_id     = str(update.effective_chat.id)
    gif_blocked = fb_get(f"shout_config/{chat_id}/gif_blocked") or False
    gif_label   = "GIF Blocker: ON (tap to turn OFF)" if gif_blocked else "GIF Blocker: OFF (tap to turn ON)"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Add Blocked Word",    callback_data=f"shout_add|{chat_id}")],
        [InlineKeyboardButton("Remove Blocked Word", callback_data=f"shout_remove|{chat_id}")],
        [InlineKeyboardButton(gif_label,             callback_data=f"shout_gif|{chat_id}")],
    ])
    text = (
        "================================\n"
        "   Shout Configuration Panel\n"
        "================================\n\n"
        "<b>Add Blocked Word</b>\n"
        "  Bot refuses to shout messages with that word.\n\n"
        "<b>Remove Blocked Word</b>\n"
        "  Unblock a previously blocked word.\n\n"
        "<b>GIF Blocker</b>\n"
        "  Prevent GIFs from being sent in the group.\n\n"
        f"GIF Blocker status: <b>{'ON' if gif_blocked else 'OFF'}</b>\n\n"
        "================================"
    )
    await send_embed(update.message, "shout_config", text, reply_markup=keyboard)


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
            return await query.message.reply_text("No blocked words saved.")
        buttons = [
            [InlineKeyboardButton(f"Remove: {w}", callback_data=f"shout_del_word|{chat_id}|{w}")]
            for w in blocked
        ]
        await query.message.reply_text("Select a word to unblock:", reply_markup=InlineKeyboardMarkup(buttons))

    elif action == "shout_gif":
        current = fb_get(f"shout_config/{chat_id}/gif_blocked") or False
        new_val = not current
        fb_set(f"shout_config/{chat_id}/gif_blocked", new_val)
        state = "ON" if new_val else "OFF"
        await query.message.reply_text(
            f"GIF Blocker is now <b>{state}</b>", parse_mode=ParseMode.HTML
        )

    elif action == "shout_del_word":
        word = parts[2]
        fb_delete(f"shout_config/{chat_id}/blocked_words/{word}")
        await query.edit_message_text(
            f"Unblocked: <code>{esc(word)}</code>", parse_mode=ParseMode.HTML
        )


# =============================================================================
# AFK
# =============================================================================

async def cmd_afk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user    = update.effective_user
    reason  = " ".join(context.args) if context.args else "No reason provided"
    chat_id = str(update.effective_chat.id)
    fb_set(f"afk/{chat_id}/{user.id}", {"reason": reason, "name": user.full_name})
    text = (
        "================================\n"
        "             AFK\n"
        "================================\n\n"
        f"{mention(user)} is now AFK\n"
        f"<b>Reason:</b> {esc(reason)}\n\n"
        "================================"
    )
    await send_embed(update.message, "afk_poster", text)


async def check_afk_return(update, context):
    if not update.effective_user or not update.effective_chat:
        return
    user    = update.effective_user
    chat_id = str(update.effective_chat.id)
    uid     = str(user.id)
    data    = fb_get(f"afk/{chat_id}/{uid}")
    if data:
        fb_delete(f"afk/{chat_id}/{uid}")
        await update.message.reply_text(
            f"Welcome back, {mention(user)}! AFK removed.", parse_mode=ParseMode.HTML
        )


# =============================================================================
# NUKE
# =============================================================================

async def cmd_nuke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("Admins only.")
    chat_id  = str(update.effective_chat.id)
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
        "================================\n"
        "          Nuke Panel\n"
        "================================\n\n"
        "Choose how many messages to delete.\n"
        "<b>This action cannot be undone.</b>\n\n"
        "================================"
    )
    await send_embed(update.message, "nuke_poster", text, reply_markup=keyboard)


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
        return await query.message.reply_text("Nuke cancelled.")
    count = int(amount)
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    status  = await query.message.reply_text(f"Nuking <b>{count}</b> messages...", parse_mode=ParseMode.HTML)
    deleted = 0
    msg_id  = query.message.message_id
    for i in range(msg_id, max(msg_id - count - 100, 0), -1):
        try:
            await context.bot.delete_message(chat_id, i)
            deleted += 1
            if deleted >= count:
                break
        except Exception:
            continue
    try:
        await status.edit_text(
            f"Done. Deleted approximately <b>{deleted}</b> messages.", parse_mode=ParseMode.HTML
        )
    except Exception:
        pass


# =============================================================================
# YOUTUBE DOWNLOADER
# =============================================================================
# Root cause of both bugs:
#
# Bug 1 -- Chrome cookies:
#   Even setting "cookiesfrombrowser": None still makes yt-dlp check for
#   the Chrome profile directory. Fix: NEVER include that key in the dict.
#
# Bug 2 -- Sign-in required / bot-detection:
#   YouTube blocks the "web", "android", and "web_creator" player clients
#   when requests come from datacenter IPs (Render, Railway, VPS etc.).
#   Fix: use "tv_embedded" + "mweb" clients. These are NOT subject to the
#   bot-verification gate because YouTube considers them trusted embedded
#   players. This is the community-accepted fix for server deployments.
# =============================================================================

def _yt_opts(fmt, outtmpl, clients=None):
    """
    Build yt-dlp options.
    CRITICAL: 'cookiesfrombrowser' must NEVER appear as a key in this dict.
    Even set to None it triggers Chrome profile lookups and crashes on headless servers.

    Client strategy (tried in order):
      ios         -- bypasses age-restriction on most videos without any cookies
      tv_embedded -- bypasses bot-detection gate on datacenter IPs (Render etc.)
      mweb        -- mobile-web fallback
    """
    if clients is None:
        clients = ["ios", "tv_embedded", "mweb"]

    opts = {
        "outtmpl":     outtmpl,
        "noplaylist":  True,
        "quiet":       True,
        "no_warnings": True,
        "extractor_args": {
            "youtube": {
                "player_client": clients,
            }
        },
        "http_headers": {
            "User-Agent": (
                "com.google.ios.youtube/19.29.1 "
                "CFNetwork/1568.100.1 Darwin/24.0.0"
            ),
        },
        "retries":          5,
        "fragment_retries": 5,
    }

    if fmt == "mp4":
        opts["format"] = (
            "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]"
            "/bestvideo[height<=720]+bestaudio"
            "/best[height<=720]/best"
        )
    else:
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{
            "key":              "FFmpegExtractAudio",
            "preferredcodec":   "mp3",
            "preferredquality": "192",
        }]
    return opts


async def cmd_yt_dow(update, context):
    if len(context.args) < 2:
        return await update.message.reply_text(
            "Usage: /yt_dow url mp4|mp3\n\n"
            "Example:\n"
            "<code>/yt_dow https://youtu.be/dQw4w9WgXcQ mp4</code>",
            parse_mode=ParseMode.HTML
        )
    url = context.args[0]
    fmt = context.args[1].lower()
    if fmt not in ("mp4", "mp3"):
        return await update.message.reply_text(
            "Format must be <code>mp4</code> or <code>mp3</code>.",
            parse_mode=ParseMode.HTML
        )

    status = await update.message.reply_text("\u23f3 Downloading, please wait...")

    # Try multiple client strategies -- stops at first success
    strategies = [
        ["ios"],                         # best: bypasses age-restriction, no cookies
        ["tv_embedded"],                 # good: bypasses bot-detection on server IPs
        ["mweb"],                        # mobile web fallback
        ["ios", "tv_embedded", "mweb"],  # all combined last resort
    ]
    last_err = None

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            outtmpl = os.path.join(tmpdir, "%(title)s.%(ext)s")

            info  = None
            title = "video"

            for clients in strategies:
                try:
                    opts = _yt_opts(fmt, outtmpl, clients=clients)
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        info  = ydl.extract_info(url, download=True)
                        title = info.get("title", "video")
                    last_err = None
                    break  # success
                except yt_dlp.utils.DownloadError as e:
                    last_err = str(e)
                    low = last_err.lower()
                    # Do not retry on permanent failures
                    if any(k in low for k in ("private", "members only",
                                              "copyright", "not available",
                                              "unavailable", "removed", "blocked")):
                        break
                    logger.warning(f"yt_dow client={clients} failed, trying next")
                    continue

            if last_err is not None:
                low = last_err.lower()
                if any(k in low for k in ("private", "members only")):
                    msg = "This video is <b>private or members-only</b>."
                elif any(k in low for k in ("copyright", "not available",
                                             "unavailable", "removed", "blocked")):
                    msg = "This video is <b>unavailable</b>, removed, or blocked in this region."
                elif any(k in low for k in ("age", "sign in", "login")):
                    msg = (
                        "This video has <b>strict age-restriction</b> that cannot be bypassed "
                        "without a signed-in account. Most age-restricted videos work fine; "
                        "this one has extra account-level protection."
                    )
                else:
                    first_line = last_err.split("\n")[0][:250]
                    msg = f"YouTube error: {esc(first_line)}"
                return await status.edit_text(
                    f"\u274c <b>Download failed</b>\n\n{msg}",
                    parse_mode=ParseMode.HTML
                )

            files = os.listdir(tmpdir)
            if not files:
                return await status.edit_text("\u274c Download produced no file. Try a different URL.")

            filepath = os.path.join(tmpdir, files[0])
            size_mb  = os.path.getsize(filepath) / (1024 * 1024)
            if size_mb > 50:
                return await status.edit_text(
                    f"\u274c File is {size_mb:.1f} MB — Telegram limit is 50 MB.\n"
                    "Try <code>mp3</code> format instead.",
                    parse_mode=ParseMode.HTML
                )

            await status.edit_text(
                f"\U0001f4e4 Uploading <b>{esc(title)}</b> ({size_mb:.1f} MB)...",
                parse_mode=ParseMode.HTML
            )
            with open(filepath, "rb") as f:
                if fmt == "mp4":
                    await update.message.reply_video(video=f, caption=f"\U0001f3ac {title}")
                else:
                    await update.message.reply_audio(audio=f, title=title, caption=f"\U0001f3b5 {title}")
            await status.delete()

    except Exception as e:
        logger.error(f"yt_dow unexpected: {e}")
        await status.edit_text(
            f"\u274c <b>Unexpected error</b>\n{esc(str(e)[:300])}",
            parse_mode=ParseMode.HTML
        )


# =============================================================================
# MESSAGE HANDLER
# =============================================================================

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
            await update.message.reply_text("GIFs are blocked in this group.")
        except Exception:
            pass
        return

    # Collect blocked-word after button press
    pending = context.user_data.get("shout_add_pending")
    if pending and update.message.text:
        word = update.message.text.strip().lower()
        fb_set(f"shout_config/{pending}/blocked_words/{word}", True)
        del context.user_data["shout_add_pending"]
        await update.message.reply_text(
            f"<code>{esc(word)}</code> added to blocked words.", parse_mode=ParseMode.HTML
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


# =============================================================================
# KEEP-ALIVE  (three-layer 24/7 strategy)
# =============================================================================
#
# Layer 1 — Flask web server (required by Render to keep the service alive)
#   Render kills any web service that doesn't listen on PORT within 60 s.
#
# Layer 2 — Self-ping loop (prevents Render free tier from sleeping)
#   Render free tier spins down after 15 min of no inbound HTTP traffic.
#   We ping our own public URL every 10 minutes so Render counts it as active.
#   Set RENDER_EXTERNAL_URL env var to your service URL, e.g.:
#       https://knowledge-pro-telegram-bot.onrender.com
#
# Layer 3 — Telegram error handler + auto-reconnect loop
#   run_polling() can silently stop if it hits a network error or Telegram
#   rate-limit. We wrap it in a retry loop with exponential back-off so the
#   bot automatically recovers instead of going dead.
# =============================================================================

_flask_app = Flask(__name__)


@_flask_app.route("/")
def _health():
    return "Knowledge Pro AI Bot is alive", 200


def _run_flask():
    serve(_flask_app, host="0.0.0.0", port=PORT)


def _self_ping_loop():
    """Ping own public URL every 10 min to prevent Render free-tier sleep."""
    if not RENDER_URL:
        logger.info("RENDER_EXTERNAL_URL not set — self-ping disabled.")
        return
    ping_url = RENDER_URL.rstrip("/") + "/"
    logger.info(f"Self-ping started → {ping_url} every 10 min")
    while True:
        threading.Event().wait(600)          # non-blocking 10-minute wait
        try:
            r = _req.get(ping_url, timeout=15)
            logger.info(f"Self-ping OK ({r.status_code})")
        except Exception as e:
            logger.warning(f"Self-ping failed: {e}")


def start_keep_alive():
    threading.Thread(target=_run_flask,       daemon=True).start()
    threading.Thread(target=_self_ping_loop,  daemon=True).start()
    logger.info(f"Keep-alive server on port {PORT}")


# =============================================================================
# ERROR HANDLER  — logs errors without crashing the bot
# =============================================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Log every unhandled exception so it doesn't silently kill polling."""
    logger.error("Unhandled exception:", exc_info=context.error)


# =============================================================================
# MAIN  — wraps run_polling in a retry loop for true 24/7 operation
# =============================================================================

def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is not set!")

    start_keep_alive()

    backoff = 5   # seconds between reconnect attempts, doubles on each failure

    while True:
        try:
            app = Application.builder().token(BOT_TOKEN).build()

            # ── Error handler (prevents silent crashes) ──────────────────────
            app.add_error_handler(error_handler)

            # ── Command handlers ─────────────────────────────────────────────
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

            # ── Callback query handlers ──────────────────────────────────────
            app.add_handler(CallbackQueryHandler(shoutconfig_callback, pattern=r"^shout_"))
            app.add_handler(CallbackQueryHandler(nuke_callback,        pattern=r"^nuke\|"))

            # ── Message handler ──────────────────────────────────────────────
            app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))

            logger.info("Knowledge Pro AI Bot starting polling...")
            backoff = 5   # reset backoff on a clean start

            app.run_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,
                # These timeouts give the bot time to recover from transient network issues
                read_timeout=30,
                write_timeout=30,
                connect_timeout=30,
                pool_timeout=30,
            )

        except Exception as e:
            logger.error(f"Polling crashed: {e}. Reconnecting in {backoff}s...")
            threading.Event().wait(backoff)
            backoff = min(backoff * 2, 120)   # exponential back-off, max 2 min


if __name__ == "__main__":
    main()
