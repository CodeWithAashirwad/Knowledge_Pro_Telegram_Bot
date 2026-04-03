"""
Knowledge Pro AI Telegram Bot
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Single file | Firebase REST | HTML parse mode (no Markdown entity crashes)

ALL POSTERS — editable in Firebase Realtime DB under /media/<key>:
  media/welcome           → /start  welcome photo/gif
  media/help              → /help   photo/gif
  media/autoreply_poster  → /autoreply list header image
  media/shout_config      → /shoutconfig panel image
  media/afk_poster        → /afk embed image
  media/nuke_poster       → /nuke panel image
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

# ─── YouTube ─────────────────────────────────────────────────────────────────
import yt_dlp

# ─── Firebase REST ───────────────────────────────────────────────────────────
import requests as _req

# ─── Keep-Alive ──────────────────────────────────────────────────────────────
from flask import Flask
from waitress import serve

# ═════════════════════════════════════════════════════════════════════════════
# ENV VARIABLES  ← set in Render / Railway / .env
# ═════════════════════════════════════════════════════════════════════════════
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
BOT_OWNER = int(os.environ.get("BOT_OWNER", "0"))
PORT      = int(os.environ.get("PORT", "8080"))

# ═════════════════════════════════════════════════════════════════════════════
# FIREBASE  (Realtime Database — pure REST, no SDK)
# ═════════════════════════════════════════════════════════════════════════════
_FB_BASE = "https://knowledge-pro-c9ee5-default-rtdb.firebaseio.com"

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Firebase helpers ─────────────────────────────────────────────────────────

def fb_get(path: str):
    try:
        r = _req.get(f"{_FB_BASE}/{path}.json", timeout=10)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        logger.error(f"fb_get({path}): {e}")
        return None

def fb_set(path: str, data):
    try:
        r = _req.put(f"{_FB_BASE}/{path}.json", json=data, timeout=10)
        return r.status_code == 200
    except Exception as e:
        logger.error(f"fb_set({path}): {e}")
        return False

def fb_delete(path: str):
    try:
        r = _req.delete(f"{_FB_BASE}/{path}.json", timeout=10)
        return r.status_code == 200
    except Exception as e:
        logger.error(f"fb_delete({path}): {e}")
        return False

# ═════════════════════════════════════════════════════════════════════════════
# HTML / MEDIA UTILITIES
# ═════════════════════════════════════════════════════════════════════════════

def esc(text: str) -> str:
    """Escape &, <, > so dynamic content is safe inside HTML messages."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def mention(user) -> str:
    """Clickable inline HTML mention."""
    name = esc(user.full_name or user.first_name or "User")
    return f'<a href="tg://user?id={user.id}">{name}</a>'

def get_media(key: str) -> str:
    """
    Return the direct URL stored at /media/<key> in Firebase, or '' if not set.

    To change any poster, go to Firebase Console → Realtime Database and set:
        /media/<key>  =  "https://your-image-or-gif-url"
    """
    val = fb_get(f"media/{key}")
    return val if isinstance(val, str) and val.startswith("http") else ""

async def send_embed(message, media_key: str, text: str, reply_markup=None):
    """
    Core embed sender used by every feature that has a poster.

    Strategy:
      1. Fetch poster URL from Firebase at /media/<media_key>
      2. If URL exists  → send as photo with HTML caption
         Telegram caption limit is 1024 chars. If text > 1024 the photo is
         sent first, then the text as a separate message.
      3. If no URL set  → send as plain HTML text message
      4. On any error   → fall back to plain text (bot never crashes)
    """
    url    = get_media(media_key)
    kwargs = dict(parse_mode=ParseMode.HTML, reply_markup=reply_markup)

    try:
        if url:
            if len(text) <= 1024:
                await message.reply_photo(photo=url, caption=text, **kwargs)
            else:
                # Send image without caption, then text separately
                await message.reply_photo(photo=url)
                await message.reply_text(text, **kwargs)
        else:
            await message.reply_text(text, **kwargs)
    except Exception as e:
        logger.warning(f"send_embed({media_key}) error: {e} — falling back to text")
        try:
            await message.reply_text(text, **kwargs)
        except Exception as e2:
            logger.error(f"send_embed plain fallback also failed: {e2}")

# ═════════════════════════════════════════════════════════════════════════════
# ADMIN / SELF-RESPECT CHECKS
# ═════════════════════════════════════════════════════════════════════════════

async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE,
                   user_id: int = None) -> bool:
    uid = user_id or update.effective_user.id
    if uid == BOT_OWNER:
        return True
    try:
        m = await context.bot.get_chat_member(update.effective_chat.id, uid)
        return m.status in ("administrator", "creator")
    except Exception:
        return False

async def is_group_owner(update: Update, context: ContextTypes.DEFAULT_TYPE,
                          user_id: int) -> bool:
    try:
        m = await context.bot.get_chat_member(update.effective_chat.id, user_id)
        return m.status == "creator"
    except Exception:
        return False

async def self_respect(update: Update, context: ContextTypes.DEFAULT_TYPE,
                        target_id: int) -> bool:
    """Returns True (blocks action) when target is the bot, bot owner, or group owner."""
    if target_id == context.bot.id:
        await update.message.reply_text("❌ I won't act on myself.")
        return True
    if target_id == BOT_OWNER:
        await update.message.reply_text("❌ I won't act on my owner.")
        return True
    if await is_group_owner(update, context, target_id):
        await update.message.reply_text("❌ I cannot act on the group owner.")
        return True
    return False

async def resolve_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resolve target from replied message or first @username / numeric ID arg."""
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

# ═════════════════════════════════════════════════════════════════════════════
# HELP TEXT  — styled embed exactly as specified in the original prompt
# Uses a bold monospace-style font via Unicode bold chars in the header,
# <code> tags for commands (monospace font), and separator lines.
# ═════════════════════════════════════════════════════════════════════════════

HELP_TEXT = (
    # ── Header ────────────────────────────────────────────────────────────────
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "          <b>Help Command</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

    # ── Basic ──────────────────────────────────────────────────────────────────
    "📌 <b>Basic Commands</b>\n"
    "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
    "<code>/start</code>   ➜  Welcome message\n"
    "<code>/help</code>    ➜  This help menu\n"
    "<code>/id</code>      ➜  Your Telegram ID\n"
    "<code>/roll</code>    ➜  Roll a dice 🎲\n\n"

    # ── Moderation ────────────────────────────────────────────────────────────
    "🛡 <b>Moderation</b>\n"
    "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
    "<code>/kick @user</code>                ➜  Kick from group\n"
    "<code>/ban @user</code>                 ➜  Permanently ban\n"
    "<code>/mute @user</code>                ➜  Silence a user\n"
    "<code>/unmute @user</code>              ➜  Restore voice\n"
    "<code>/promote @user</code>             ➜  Make admin\n"
    "<code>/demote @user</code>              ➜  Remove admin\n"
    "<code>/pin</code>                       ➜  Pin replied message\n"
    "<code>/unpin</code>                     ➜  Unpin message\n"
    "<code>/permission @user perm on|off</code>\n\n"

    # ── Auto Reply ────────────────────────────────────────────────────────────
    "💬 <b>Auto Reply</b>\n"
    "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
    "<code>/autoreply set word | reply</code>\n"
    "<code>/autoreply list</code>\n"
    "<code>/autoreply delete word</code>\n\n"

    # ── Shout ─────────────────────────────────────────────────────────────────
    "📢 <b>Shout</b>\n"
    "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
    "<code>/shout message</code>    ➜  Make announcement\n"
    "<code>/shoutconfig</code>      ➜  Open config panel\n\n"

    # ── AFK ───────────────────────────────────────────────────────────────────
    "💤 <b>AFK</b>\n"
    "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
    "<code>/afk reason</code>       ➜  Set AFK status\n\n"

    # ── Nuke ──────────────────────────────────────────────────────────────────
    "💣 <b>Nuke</b>\n"
    "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
    "<code>/nuke</code>             ➜  Bulk delete messages\n\n"

    # ── YouTube ───────────────────────────────────────────────────────────────
    "📥 <b>YouTube Downloader</b>\n"
    "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
    "<code>/yt_dow url mp4|mp3</code>\n\n"

    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
)

# ═════════════════════════════════════════════════════════════════════════════
# /start  — welcome poster from Firebase /media/welcome
# ═════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    welcome_text = (
        f"👋 <b>Welcome, {mention(user)}!</b>\n\n"
        "🤖 <b>About Me</b>\n"
        "I'm <b>Knowledge Pro AI</b> that moderates your groups your way. "
        "Fully customisable and easy.\n\n"
        + HELP_TEXT
    )

    # poster key: "welcome"  ← set /media/welcome in Firebase to any image/gif URL
    await send_embed(update.message, "welcome", welcome_text)

# ═════════════════════════════════════════════════════════════════════════════
# /help  — help poster from Firebase /media/help
#          Styled embed with separator lines and all commands shown
# ═════════════════════════════════════════════════════════════════════════════

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # poster key: "help"  ← set /media/help in Firebase to any image/gif URL
    await send_embed(update.message, "help", HELP_TEXT)

# ═════════════════════════════════════════════════════════════════════════════
# /id   /roll
# ═════════════════════════════════════════════════════════════════════════════

async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    text = (
        f"👤 <b>Your ID:</b> <code>{user.id}</code>\n"
        f"💬 <b>Chat ID:</b> <code>{chat.id}</code>"
    )
    if update.message.reply_to_message:
        ru    = update.message.reply_to_message.from_user
        fname = esc(ru.first_name or "User")
        text += f"\n🔍 <b>{fname}'s ID:</b> <code>{ru.id}</code>"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def cmd_roll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    n     = random.randint(1, 6)
    faces = ["⚀ 1", "⚁ 2", "⚂ 3", "⚃ 4", "⚄ 5", "⚅ 6"]
    await update.message.reply_text(
        f"🎲 You rolled: <b>{faces[n - 1]}</b>", parse_mode=ParseMode.HTML
    )

# ═════════════════════════════════════════════════════════════════════════════
# MODERATION
# ═════════════════════════════════════════════════════════════════════════════

async def cmd_kick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("❌ Admins only.")
    target = await resolve_target(update, context)
    if not target:
        return await update.message.reply_text("❓ Reply to a user or provide @username.")
    if await self_respect(update, context, target.id):
        return
    try:
        await context.bot.ban_chat_member(update.effective_chat.id, target.id)
        await context.bot.unban_chat_member(update.effective_chat.id, target.id)
        await update.message.reply_text(
            f"👢 {mention(target)} has been <b>kicked</b>.", parse_mode=ParseMode.HTML
        )
    except TelegramError as e:
        await update.message.reply_text(f"❌ {esc(str(e))}", parse_mode=ParseMode.HTML)

async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("❌ Admins only.")
    target = await resolve_target(update, context)
    if not target:
        return await update.message.reply_text("❓ Reply to a user or provide @username.")
    if await self_respect(update, context, target.id):
        return
    try:
        await context.bot.ban_chat_member(update.effective_chat.id, target.id)
        await update.message.reply_text(
            f"🔨 {mention(target)} has been <b>banned</b>.", parse_mode=ParseMode.HTML
        )
    except TelegramError as e:
        await update.message.reply_text(f"❌ {esc(str(e))}", parse_mode=ParseMode.HTML)

async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("❌ Admins only.")
    target = await resolve_target(update, context)
    if not target:
        return await update.message.reply_text("❓ Reply to a user or provide @username.")
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
            f"🔇 {mention(target)} has been <b>muted</b>.", parse_mode=ParseMode.HTML
        )
    except TelegramError as e:
        await update.message.reply_text(f"❌ {esc(str(e))}", parse_mode=ParseMode.HTML)

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
        await update.message.reply_text(
            f"🔊 {mention(target)} has been <b>unmuted</b>.", parse_mode=ParseMode.HTML
        )
    except TelegramError as e:
        await update.message.reply_text(f"❌ {esc(str(e))}", parse_mode=ParseMode.HTML)

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
        await update.message.reply_text(
            f"⭐ {mention(target)} <b>promoted</b> to admin.", parse_mode=ParseMode.HTML
        )
    except TelegramError as e:
        await update.message.reply_text(f"❌ {esc(str(e))}", parse_mode=ParseMode.HTML)

async def cmd_demote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("❌ Admins only.")
    target = await resolve_target(update, context)
    if not target:
        return await update.message.reply_text("❓ Reply to a user or provide @username.")
    if await self_respect(update, context, target.id):
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
            f"👇 {mention(target)} has been <b>demoted</b>.", parse_mode=ParseMode.HTML
        )
    except TelegramError as e:
        await update.message.reply_text(f"❌ {esc(str(e))}", parse_mode=ParseMode.HTML)

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
        return await update.message.reply_text("❌ Admins only.")
    if len(context.args) < 3:
        return await update.message.reply_text(
            f"Usage: /permission @user perm on|off\nAvailable: {', '.join(PERM_MAP)}"
        )
    target    = await resolve_target(update, context)
    perm_name = context.args[1].lower()
    toggle    = context.args[2].lower() == "on"
    if perm_name not in PERM_MAP:
        return await update.message.reply_text(f"❓ Unknown perm. Available: {', '.join(PERM_MAP)}")
    if not target:
        return await update.message.reply_text("❓ User not found.")
    if await self_respect(update, context, target.id):
        return
    try:
        await context.bot.restrict_chat_member(
            update.effective_chat.id, target.id,
            ChatPermissions(**{PERM_MAP[perm_name]: toggle})
        )
        state = "ON ✅" if toggle else "OFF ❌"
        await update.message.reply_text(
            f"🔧 {mention(target)}: <code>{esc(perm_name)}</code> → {state}",
            parse_mode=ParseMode.HTML
        )
    except TelegramError as e:
        await update.message.reply_text(f"❌ {esc(str(e))}", parse_mode=ParseMode.HTML)

async def cmd_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("❌ Admins only.")
    if not update.message.reply_to_message:
        return await update.message.reply_text("❓ Reply to the message you want to pin.")
    try:
        await context.bot.pin_chat_message(
            update.effective_chat.id, update.message.reply_to_message.message_id
        )
        await update.message.reply_text("📌 Message <b>pinned</b>!", parse_mode=ParseMode.HTML)
    except TelegramError as e:
        await update.message.reply_text(f"❌ {esc(str(e))}", parse_mode=ParseMode.HTML)

async def cmd_unpin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("❌ Admins only.")
    try:
        if update.message.reply_to_message:
            await context.bot.unpin_chat_message(
                update.effective_chat.id,
                update.message.reply_to_message.message_id
            )
        else:
            await context.bot.unpin_chat_message(update.effective_chat.id)
        await update.message.reply_text("📌 Message <b>unpinned</b>!", parse_mode=ParseMode.HTML)
    except TelegramError as e:
        await update.message.reply_text(f"❌ {esc(str(e))}", parse_mode=ParseMode.HTML)

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
            return await update.message.reply_text("❓ Format: /autoreply set word | response")
        word, response = [x.strip() for x in raw.split("|", 1)]
        if not word or not response:
            return await update.message.reply_text("❓ Word and response cannot be empty.")
        fb_set(f"autoreply/{chat_id}/{word}", response)
        await update.message.reply_text(
            f"✅ Auto-reply saved:\n"
            f"Trigger → <code>{esc(word)}</code>\n"
            f"Reply   → {esc(response)}",
            parse_mode=ParseMode.HTML
        )

    elif sub == "list":
        data = fb_get(f"autoreply/{chat_id}")
        if not data:
            return await update.message.reply_text("📭 No auto-replies saved yet.")

        lines = [
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "        <b>Auto Reply List</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n",
        ]
        for word, resp in data.items():
            lines.append(f"• <code>{esc(word)}</code>  ➜  {esc(resp)}")
        lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        text = "\n".join(lines)
        # poster key: "autoreply_poster"  ← set /media/autoreply_poster in Firebase
        await send_embed(update.message, "autoreply_poster", text)

    elif sub == "delete":
        if len(context.args) < 2:
            return await update.message.reply_text("❓ Provide the word to delete.")
        word = context.args[1].lower()
        fb_delete(f"autoreply/{chat_id}/{word}")
        await update.message.reply_text(
            f"🗑 Auto-reply for <code>{esc(word)}</code> removed.",
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text("❓ Sub-commands: set / list / delete")

# ═════════════════════════════════════════════════════════════════════════════
# SHOUT
# ═════════════════════════════════════════════════════════════════════════════

async def cmd_shout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("❌ Admins only.")
    if not context.args:
        return await update.message.reply_text("❓ Usage: /shout message")

    msg     = " ".join(context.args)
    chat_id = str(update.effective_chat.id)

    blocked = fb_get(f"shout_config/{chat_id}/blocked_words") or {}
    for bw in blocked:
        if bw.lower() in msg.lower():
            return await update.message.reply_text(
                f"🚫 Blocked word detected: <b>{esc(bw)}</b>",
                parse_mode=ParseMode.HTML
            )

    await update.message.reply_text(
        "📢 <b>— ANNOUNCEMENT —</b>\n"
        "──────────────────────────────\n"
        f"{esc(msg)}\n"
        "──────────────────────────────",
        parse_mode=ParseMode.HTML
    )

# ─── /shoutconfig ─────────────────────────────────────────────────────────────

async def cmd_shoutconfig(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("❌ Admins only.")

    chat_id     = str(update.effective_chat.id)
    gif_blocked = fb_get(f"shout_config/{chat_id}/gif_blocked") or False
    gif_label   = "🎞 GIF Blocker: ON ✅  (tap to turn OFF)" if gif_blocked \
                  else "🎞 GIF Blocker: OFF ❌  (tap to turn ON)"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Blocked Word",    callback_data=f"shout_add|{chat_id}")],
        [InlineKeyboardButton("➖ Remove Blocked Word", callback_data=f"shout_remove|{chat_id}")],
        [InlineKeyboardButton(gif_label,                callback_data=f"shout_gif|{chat_id}")],
    ])

    text = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "    <b>Shout Configuration Panel</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "• <b>Add Blocked Word</b>\n"
        "  Bot will refuse to shout messages containing that word.\n\n"
        "• <b>Remove Blocked Word</b>\n"
        "  Unblock a previously blocked word.\n\n"
        "• <b>GIF Blocker</b>\n"
        "  Prevent any GIF from being sent in the group.\n\n"
        f"GIF Blocker status: <b>{'ON ✅' if gif_blocked else 'OFF ❌'}</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    # poster key: "shout_config"  ← set /media/shout_config in Firebase
    await send_embed(update.message, "shout_config", text, reply_markup=keyboard)

async def shoutconfig_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts   = query.data.split("|")
    action  = parts[0]
    chat_id = parts[1] if len(parts) > 1 else str(update.effective_chat.id)

    if action == "shout_add":
        context.user_data["shout_add_pending"] = chat_id
        await query.message.reply_text("✏️ Send the word you want to block from /shout:")

    elif action == "shout_remove":
        blocked = fb_get(f"shout_config/{chat_id}/blocked_words") or {}
        if not blocked:
            return await query.message.reply_text("📭 No blocked words saved.")
        buttons = [
            [InlineKeyboardButton(f"🗑 {w}", callback_data=f"shout_del_word|{chat_id}|{w}")]
            for w in blocked
        ]
        await query.message.reply_text(
            "Select a word to unblock:", reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif action == "shout_gif":
        current = fb_get(f"shout_config/{chat_id}/gif_blocked") or False
        new_val = not current
        fb_set(f"shout_config/{chat_id}/gif_blocked", new_val)
        state = "ON ✅" if new_val else "OFF ❌"
        await query.message.reply_text(
            f"🎞 GIF Blocker is now <b>{state}</b>", parse_mode=ParseMode.HTML
        )

    elif action == "shout_del_word":
        word = parts[2]
        fb_delete(f"shout_config/{chat_id}/blocked_words/{word}")
        await query.edit_message_text(
            f"✅ Unblocked: <code>{esc(word)}</code>", parse_mode=ParseMode.HTML
        )

# ═════════════════════════════════════════════════════════════════════════════
# AFK  — poster from Firebase /media/afk_poster
# ═════════════════════════════════════════════════════════════════════════════

async def cmd_afk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user    = update.effective_user
    reason  = " ".join(context.args) if context.args else "No reason provided"
    chat_id = str(update.effective_chat.id)

    fb_set(f"afk/{chat_id}/{user.id}", {"reason": reason, "name": user.full_name})

    text = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "              <b>💤  AFK</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 {mention(user)} is now AFK\n"
        f"📝 <b>Reason:</b> {esc(reason)}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    # poster key: "afk_poster"  ← set /media/afk_poster in Firebase
    await send_embed(update.message, "afk_poster", text)

async def check_afk_return(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove AFK status when the user sends any message, voice note, or GIF."""
    if not update.effective_user or not update.effective_chat:
        return
    user    = update.effective_user
    chat_id = str(update.effective_chat.id)
    uid     = str(user.id)

    afk_data = fb_get(f"afk/{chat_id}/{uid}")
    if afk_data:
        fb_delete(f"afk/{chat_id}/{uid}")
        await update.message.reply_text(
            f"👋 Welcome back, {mention(user)}! Your AFK status has been removed.",
            parse_mode=ParseMode.HTML
        )

# ═════════════════════════════════════════════════════════════════════════════
# NUKE  — panel poster from Firebase /media/nuke_poster
# ═════════════════════════════════════════════════════════════════════════════

async def cmd_nuke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("❌ Admins only.")

    chat_id  = str(update.effective_chat.id)
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💥 100",   callback_data=f"nuke|{chat_id}|100"),
            InlineKeyboardButton("💥 200",   callback_data=f"nuke|{chat_id}|200"),
        ],
        [
            InlineKeyboardButton("☢️ 8900",  callback_data=f"nuke|{chat_id}|8900"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"nuke|{chat_id}|cancel"),
        ],
    ])

    text = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "           <b>☢️  Nuke Panel</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "⚠️ Choose how many messages to delete.\n"
        "This action <b>cannot</b> be undone.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    # poster key: "nuke_poster"  ← set /media/nuke_poster in Firebase
    await send_embed(update.message, "nuke_poster", text, reply_markup=keyboard)

async def nuke_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not await is_admin(update, context):
        return await query.answer("❌ Admins only.", show_alert=True)

    parts   = query.data.split("|")
    chat_id = int(parts[1])
    amount  = parts[2]

    if amount == "cancel":
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return await query.message.reply_text("❌ Nuke cancelled.")

    count = int(amount)
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    status  = await query.message.reply_text(
        f"☢️ Nuking <b>{count}</b> messages...", parse_mode=ParseMode.HTML
    )
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
            f"✅ Done — deleted approximately <b>{deleted}</b> messages.",
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass

# ═════════════════════════════════════════════════════════════════════════════
# YOUTUBE DOWNLOADER
# ─────────────────────────────────────────────────────────────────────────────
# Fixes applied:
#   1. "cookiesfrombrowser" key is NEVER present in the opts dict at all.
#      Setting it to None still triggers Chrome lookup in some yt-dlp versions.
#      The only safe fix is to never include the key.
#   2. extractor_args bypass YouTube's sign-in / bot-detection (player_client
#      android + web_creator avoids the "sign in to confirm you're not a bot"
#      gate that was causing the login error).
#   3. Optional cookies.txt support via YT_COOKIES_FILE env var for
#      age-restricted videos — file must exist for it to be used.
#   4. All DownloadError messages are caught and shown cleanly to the user.
# ═════════════════════════════════════════════════════════════════════════════

_YT_COOKIES_FILE = os.environ.get("YT_COOKIES_FILE", "")  # optional — Netscape cookies.txt


def _build_ydl_opts(fmt: str, outtmpl: str) -> dict:
    """
    Build a clean yt-dlp options dict.
    IMPORTANT: 'cookiesfrombrowser' must NOT appear as a key at all.
    Setting it to None still causes yt-dlp to attempt a browser lookup.
    """
    opts = {
        # ── output ────────────────────────────────────────────────────────────
        "outtmpl":      outtmpl,
        "noplaylist":   True,

        # ── silence ───────────────────────────────────────────────────────────
        "quiet":        True,
        "no_warnings":  True,

        # ── bypass YouTube bot-detection WITHOUT needing a browser / login ────
        # android + web_creator clients are not blocked by YouTube's
        # "sign in to confirm you're not a bot" challenge.
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web_creator"],
                "skip":          ["translated_subs"],
            }
        },

        # ── spoof a real browser User-Agent ───────────────────────────────────
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.6367.82 Mobile Safari/537.36"
            ),
        },

        # ── retries ───────────────────────────────────────────────────────────
        "retries":          3,
        "fragment_retries": 3,
    }

    # Only add cookiefile if user has placed a valid cookies.txt on disk
    if _YT_COOKIES_FILE and os.path.isfile(_YT_COOKIES_FILE):
        opts["cookiefile"] = _YT_COOKIES_FILE

    # ── format ────────────────────────────────────────────────────────────────
    if fmt == "mp4":
        # cap at 720p so files stay under Telegram's 50 MB limit
        opts["format"] = (
            "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]"
            "/bestvideo[height<=720]+bestaudio"
            "/best[height<=720]/best"
        )
    else:  # mp3
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{
            "key":              "FFmpegExtractAudio",
            "preferredcodec":   "mp3",
            "preferredquality": "192",
        }]

    return opts


def _friendly_yt_error(raw: str) -> str:
    """Convert a raw yt-dlp error string into a user-friendly message."""
    msg = raw.lower()
    if any(k in msg for k in ("sign in", "login required", "confirm your age",
                               "age-restrict", "age restrict")):
        return (
            "This video is age-restricted or requires sign-in.\n"
            "Only public, unrestricted videos can be downloaded."
        )
    if any(k in msg for k in ("private video", "private", "members only")):
        return "This video is private or members-only and cannot be downloaded."
    if any(k in msg for k in ("copyright", "not available", "unavailable",
                               "removed", "blocked")):
        return "This video is unavailable, removed, or blocked in this region."
    if "confirm you" in msg or "bot" in msg:
        return (
            "YouTube is requesting bot verification for this video.\n"
            "Please try again in a few minutes or try a different video."
        )
    # Return a trimmed version of the raw error for anything else
    return raw.split("\n")[0][:300]


async def cmd_yt_dow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        return await update.message.reply_text(
            "❓ Usage: /yt_dow url mp4|mp3\n\n"
            "Example:\n"
            "<code>/yt_dow https://youtu.be/dQw4w9WgXcQ mp4</code>",
            parse_mode=ParseMode.HTML
        )

    url = context.args[0]
    fmt = context.args[1].lower()
    if fmt not in ("mp4", "mp3"):
        return await update.message.reply_text("❓ Format must be <code>mp4</code> or <code>mp3</code>.",
                                               parse_mode=ParseMode.HTML)

    status = await update.message.reply_text("⏳ Fetching video info...")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            outtmpl  = os.path.join(tmpdir, "%(title)s.%(ext)s")
            ydl_opts = _build_ydl_opts(fmt, outtmpl)

            try:
                await status.edit_text("⏳ Downloading, please wait...")
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info  = ydl.extract_info(url, download=True)
                    title = info.get("title", "video")

            except yt_dlp.utils.DownloadError as dl_err:
                friendly = _friendly_yt_error(str(dl_err))
                return await status.edit_text(
                    f"❌ <b>Download failed</b>\n\n{esc(friendly)}",
                    parse_mode=ParseMode.HTML
                )

            files = os.listdir(tmpdir)
            if not files:
                return await status.edit_text("❌ Download produced no file. Try a different URL.")

            filepath = os.path.join(tmpdir, files[0])
            size_mb  = os.path.getsize(filepath) / (1024 * 1024)

            if size_mb > 50:
                return await status.edit_text(
                    f"❌ File is {size_mb:.1f} MB — Telegram limit is 50 MB.\n"
                    "Try downloading as mp3 instead."
                )

            await status.edit_text(
                f"📤 Uploading <b>{esc(title)}</b> ({size_mb:.1f} MB)...",
                parse_mode=ParseMode.HTML
            )

            with open(filepath, "rb") as f:
                if fmt == "mp4":
                    await update.message.reply_video(video=f, caption=f"🎬 {title}")
                else:
                    await update.message.reply_audio(audio=f, title=title, caption=f"🎵 {title}")

            await status.delete()

    except Exception as e:
        logger.error(f"yt_dow unexpected error: {e}")
        await status.edit_text(
            f"❌ <b>Unexpected error</b>\n{esc(str(e)[:300])}",
            parse_mode=ParseMode.HTML
        )

# ═════════════════════════════════════════════════════════════════════════════
# MESSAGE HANDLER — AFK return · GIF blocker · blocked-word input · auto-reply
# ═════════════════════════════════════════════════════════════════════════════

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return

    chat_id = str(update.effective_chat.id)

    # 1. AFK return check
    await check_afk_return(update, context)

    # 2. GIF blocker
    gif_blocked = fb_get(f"shout_config/{chat_id}/gif_blocked") or False
    if gif_blocked and update.message.animation:
        try:
            await update.message.delete()
            await update.message.reply_text("🚫 GIFs are blocked in this group.")
        except Exception:
            pass
        return

    # 3. Collect blocked-word text after "Add Blocked Word" button press
    pending = context.user_data.get("shout_add_pending")
    if pending and update.message.text:
        word = update.message.text.strip().lower()
        fb_set(f"shout_config/{pending}/blocked_words/{word}", True)
        del context.user_data["shout_add_pending"]
        await update.message.reply_text(
            f"✅ <code>{esc(word)}</code> added to blocked words.",
            parse_mode=ParseMode.HTML
        )
        return

    # 4. Auto-reply
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

_flask_app = Flask(__name__)

@_flask_app.route("/")
def _health():
    return "Knowledge Pro AI Bot is alive ✅", 200

def _run_flask():
    serve(_flask_app, host="0.0.0.0", port=PORT)

def start_keep_alive():
    Thread(target=_run_flask, daemon=True).start()
    logger.info(f"Keep-alive server on port {PORT}")

# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is not set!")

    start_keep_alive()

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

    logger.info("Knowledge Pro AI Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
