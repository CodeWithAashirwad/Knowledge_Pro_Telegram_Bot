import os
import logging
import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ChatPermissions
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)
from telegram.constants import ParseMode
import firebase_admin
from firebase_admin import credentials, db

# ─────────────────────────────────────────────
#  ENV  (only BOT_TOKEN comes from env)
# ─────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# ─────────────────────────────────────────────
#  FIREBASE  (Service Account embedded directly)
# ─────────────────────────────────────────────
FIREBASE_SA = {
    "type": "service_account",
    "project_id": "knowledge-pro-c9ee5",
    "private_key_id": "0a85d6728c019df4799077f09e89f5d8bd1b08c7",
    "private_key": (
        "-----BEGIN PRIVATE KEY-----\n"
        "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQCs1N5pGQxcWuP6\n"
        "LLXtwVfEjA59Ncozf7rb7JjhmlK30Hbiuz9Q+/E5A6BOru3gPI2YoCN+Hi0p3PCi\n"
        "TPluyV+vbcjXXcP3MeDfleEF2miORLJ2EUEDTQGTPwvwaJohIE4dBFCVPnSTluNq\n"
        "f0RoiquEIXrmfwyjFKuztQ1yh2Zz68Kghuo69oEw1dvLZ/TuXCjZMYrQPFOf65Lw\n"
        "BJlhcw2X3OQtQSjEefjPgylr1L5y4y09bE6/JfULhcEQTvKFySEU6yl/wP9sgETD\n"
        "soC+jnSJdB+0bG8Jflf1de9SdLbnglJb2ztPQclhfcdPOj3MwyewJIe58BWyDfJ2\n"
        "yoy8C7/rAgMBAAECggEAT+bn3inU45unm0DxaPZCV90yU6O7E/0Ay+0Brwc0J8Pq\n"
        "Op87wfqARo4NHmNUGR+VjNK4JfXYhmqdG0O/636QzJ9SQ3MXhqBaKLP3gMe9H8zV\n"
        "vqzyZA7FZCg5Ik+Rti/jvRmCEcV6isMu50zoOPanHeKGmapyErEbQm05RtIfRQae\n"
        "v+6ymlJxzMaggQGs9uDTE2VUdNMe5DjJEFQpEN+U8u2ThLDS4sA/skqBvuokaX69\n"
        "PChHpHmM9QaKLWokgpeeHAIhXjmO8xBJDp+90E+u7+WVrH69ufUTWtWV+MJewkih\n"
        "fl8/IfvmyQx4FeD5oQHEAMTQZYjASTjOymRBnYuBFQKBgQDeGqTk5c/APKbovrGX\n"
        "eJfvrJpFUtkU74JwicHeF+jiYdTQEvkIkFYDRJYeFSLS7ZD6vA0VU0iZhQ2EoNzU\n"
        "nv2N/9XMboCN+LVL/ASNkU6RgWB1BS+zVZhdfZponrAH5cz75cq+Wc9y82B1Amv8\n"
        "BQ4qF+/1+S2Ty8q1H5dYynLMTQKBgQDHNTOJ/XgANRdcZe5Lk6Ls2I0VN8TyNBdI\n"
        "sMgBxmOWNDkjvoUVFXSxOuSPmHteaacW3U3WYH6bqbloYnK2jn6/dxwaVhnmf7m3\n"
        "pk1ovltzUaYKim3CGN5kfYTfRvOaK2O2FwVVxfuMEhmTy7ejYcWRZsUvf23IsRyd\n"
        "G2vLx1d5FwKBgGtuT9w4HOlLbSCfPJ+bwUI5JtXpYP9zapCs0Y1v20HFOH787mBq\n"
        "EHC8ODCM4K9OIhZl554tDqzTYtqIRMjDrrmEyhF8UcpaRrdeS4V+h5ZyEgoIXC5O\n"
        "dMij/JAmUddAHIqreAnivylG950hcsIQX+2Ubol34cffh0lc4oQcSLLxAoGAU4RG\n"
        "e0+9A7k+dgp2AVGAOPQBEigzdafJKzySXcwi7FIwsn+po9E+/x7FvD4dWtPIrZlS\n"
        "jNIfwntBtDWyCj9rfDIfohr++NgLsKcURRmplYthpYGrynhKpK7LCiDg+H3AbBLy\n"
        "tacvcuYTuxbpgqH3BqKjgOpXyJAYgvWAGAsW7TkCgYEAtAKaU4S668/NgL7q6EKr\n"
        "bxGGtyJEjCZP3KBcAaaVTKY59cB4Jo79/yUVRjZpGPkwdUWF42ZNWMpFTCbMGaAH\n"
        "7XpFiwn7teSpQVjEb5BIY3h/R4XAoqKI69OVKuyNZz3dDEsTDp9KI7+Wwcl7B4/M\n"
        "SErvQqgTTBPSlbgq4kOAODQ=\n"
        "-----END PRIVATE KEY-----\n"
    ),
    "client_email": "firebase-adminsdk-fbsvc@knowledge-pro-c9ee5.iam.gserviceaccount.com",
    "client_id": "110550887388602577465",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
    "client_x509_cert_url": (
        "https://www.googleapis.com/robot/v1/metadata/x509/"
        "firebase-adminsdk-fbsvc%40knowledge-pro-c9ee5.iam.gserviceaccount.com"
    ),
    "universe_domain": "googleapis.com",
}

FIREBASE_DB_URL = "https://knowledge-pro-c9ee5-default-rtdb.firebaseio.com"

firebase_ok = False
try:
    cred = credentials.Certificate(FIREBASE_SA)
    firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})
    firebase_ok = True
    print("✅ Firebase connected successfully.")
except Exception as _fe:
    print(f"❌ Firebase init failed: {_fe}")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  FIREBASE HELPERS
# ─────────────────────────────────────────────

def fb_get(path, default=None):
    if not firebase_ok:
        return default
    try:
        val = db.reference(path).get()
        return val if val is not None else default
    except Exception as e:
        logger.error("FB get %s: %s", path, e)
        return default


def fb_set(path, value):
    if not firebase_ok:
        return False
    try:
        db.reference(path).set(value)
        return True
    except Exception as e:
        logger.error("FB set %s: %s", path, e)
        return False


def fb_delete(path):
    if not firebase_ok:
        return False
    try:
        db.reference(path).delete()
        return True
    except Exception as e:
        logger.error("FB delete %s: %s", path, e)
        return False


def fb_push(path, value):
    if not firebase_ok:
        return False
    try:
        db.reference(path).push(value)
        return True
    except Exception as e:
        logger.error("FB push %s: %s", path, e)
        return False

# ─────────────────────────────────────────────
#  DEFAULT MEDIA  (override any key via Firebase /config/<key>)
# ─────────────────────────────────────────────
_DEFAULT_MEDIA = {
    "welcome_photo":    "https://media.giphy.com/media/3o7abKhOpu0NwenH3O/giphy.gif",
    "help_photo":       "https://media.giphy.com/media/26tn33aiTi1jkl6H6/giphy.gif",
    "autoreply_poster": "https://media.giphy.com/media/xT9IgzoKnwFNmISR8I/giphy.gif",
    "shout_poster":     "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif",
    "afk_poster":       "https://media.giphy.com/media/3oEjI6SIIHBdRxXI40/giphy.gif",
    "nuke_poster":      "https://media.giphy.com/media/HoffxyN8ghVuw/giphy.gif",
}


def get_media(key: str) -> str:
    val = fb_get(f"config/{key}")
    return val if val else _DEFAULT_MEDIA.get(key, "")

# ─────────────────────────────────────────────
#  UTILITIES
# ─────────────────────────────────────────────

def mention(user) -> str:
    name = (user.full_name or user.first_name or "User").replace("[", "").replace("]", "")
    return f"[{name}](tg://user?id={user.id})"


async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if update.effective_chat.type == "private":
        return True
    try:
        admins = await context.bot.get_chat_administrators(update.effective_chat.id)
        return any(a.user.id == update.effective_user.id for a in admins)
    except Exception:
        return False


async def safe_photo_reply(update: Update, photo: str, text: str, reply_markup=None):
    """Send photo+caption; silently fall back to text-only if photo fails."""
    kwargs = {"caption": text, "parse_mode": ParseMode.MARKDOWN}
    if reply_markup:
        kwargs["reply_markup"] = reply_markup
    try:
        await update.message.reply_photo(photo=photo, **kwargs)
    except Exception:
        tkw = {"parse_mode": ParseMode.MARKDOWN}
        if reply_markup:
            tkw["reply_markup"] = reply_markup
        await update.message.reply_text(text, **tkw)

# ─────────────────────────────────────────────
#  /start
# ─────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = user.first_name or "User"

    text = (
        f"👋 *Welcome, {name}!*\n\n"
        "🤖 *I'm Knowledge Pro AI*\n"
        "_Moderate your groups your way — fully customisable and easy._\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📋 *Commands*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🛡 *Moderation*\n"
        "`/kick` `/ban` `/mute` `/promote` `/demote`\n"
        "`/pin` `/unpin` `/permission` `/id` `/dice`\n\n"
        "🤖 *Auto Reply*\n"
        "`/autoreply set` · `/autoreply list` · `/autoreply delete`\n\n"
        "📢 *Shout*\n"
        "`/shout` · `/shoutconfig`\n\n"
        "💤 *AFK* — `/afk reason`\n\n"
        "💣 *Nuke* — `/nuke`\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "ℹ️ *About Me*\n"
        "I'm Knowledge Pro AI that moderates your groups your way — "
        "fully customisable and easy."
    )
    await safe_photo_reply(update, get_media("welcome_photo"), text)

# ─────────────────────────────────────────────
#  /help
# ─────────────────────────────────────────────

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "```\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "          HELP COMMAND\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "```\n"
        "🛡 *Moderation*\n"
        "• `/kick` — Remove a member\n"
        "• `/ban` — Permanently ban\n"
        "• `/mute` — Silence a user\n"
        "• `/promote` — Make admin\n"
        "• `/demote` — Remove admin\n"
        "• `/permission` perm on|off\n"
        "• `/pin` — Pin replied message\n"
        "• `/unpin` — Unpin message\n"
        "• `/id` — Get user / chat ID\n"
        "• `/dice` — Roll a dice 🎲\n\n"
        "🤖 *Auto Reply*\n"
        "• `/autoreply set` word | reply\n"
        "• `/autoreply list` — View all\n"
        "• `/autoreply delete` word\n\n"
        "📢 *Shout*\n"
        "• `/shout` message\n"
        "• `/shoutconfig` — Settings panel\n\n"
        "💤 *AFK*\n"
        "• `/afk` reason\n\n"
        "💣 *Nuke*\n"
        "• `/nuke` — Delete messages panel\n"
        "```\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "```"
    )
    await safe_photo_reply(update, get_media("help_photo"), text)

# ─────────────────────────────────────────────
#  MODERATION
# ─────────────────────────────────────────────

async def cmd_kick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("⛔ Admins only.")
    if not update.message.reply_to_message:
        return await update.message.reply_text("↩️ Reply to a user to kick them.")
    target = update.message.reply_to_message.from_user
    try:
        await context.bot.ban_chat_member(update.effective_chat.id, target.id)
        await context.bot.unban_chat_member(update.effective_chat.id, target.id)
        await update.message.reply_text(
            f"👢 {mention(target)} has been kicked.", parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Failed: {e}")


async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("⛔ Admins only.")
    if not update.message.reply_to_message:
        return await update.message.reply_text("↩️ Reply to a user to ban them.")
    target = update.message.reply_to_message.from_user
    try:
        await context.bot.ban_chat_member(update.effective_chat.id, target.id)
        await update.message.reply_text(
            f"🔨 {mention(target)} has been banned.", parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Failed: {e}")


async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("⛔ Admins only.")
    if not update.message.reply_to_message:
        return await update.message.reply_text("↩️ Reply to a user to mute them.")
    target = update.message.reply_to_message.from_user
    try:
        await context.bot.restrict_chat_member(
            update.effective_chat.id, target.id,
            ChatPermissions(can_send_messages=False),
        )
        await update.message.reply_text(
            f"🔇 {mention(target)} has been muted.", parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Failed: {e}")


async def cmd_promote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("⛔ Admins only.")
    if not update.message.reply_to_message:
        return await update.message.reply_text("↩️ Reply to a user to promote.")
    target = update.message.reply_to_message.from_user
    try:
        await context.bot.promote_chat_member(
            update.effective_chat.id, target.id,
            can_delete_messages=True,
            can_restrict_members=True,
            can_pin_messages=True,
            can_manage_chat=True,
        )
        await update.message.reply_text(
            f"⭐ {mention(target)} promoted to admin.", parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Failed: {e}")


async def cmd_demote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("⛔ Admins only.")
    if not update.message.reply_to_message:
        return await update.message.reply_text("↩️ Reply to a user to demote.")
    target = update.message.reply_to_message.from_user
    try:
        await context.bot.promote_chat_member(
            update.effective_chat.id, target.id,
            can_delete_messages=False,
            can_restrict_members=False,
            can_pin_messages=False,
            can_manage_chat=False,
        )
        await update.message.reply_text(
            f"📉 {mention(target)} has been demoted.", parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Failed: {e}")


async def cmd_permission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("⛔ Admins only.")
    if not update.message.reply_to_message or len(context.args) < 2:
        return await update.message.reply_text(
            "Usage: Reply to user + `/permission perm on|off`\n"
            "Perms: `messages` `media` `stickers` `polls` `links`",
            parse_mode=ParseMode.MARKDOWN,
        )
    target = update.message.reply_to_message.from_user
    perm_name = context.args[0].lower()
    state     = context.args[1].lower()
    allow     = state == "on"

    perm_map = {
        "messages": ChatPermissions(can_send_messages=allow),
        "media":    ChatPermissions(can_send_media_messages=allow),
        "stickers": ChatPermissions(can_send_other_messages=allow),
        "polls":    ChatPermissions(can_send_polls=allow),
        "links":    ChatPermissions(can_add_web_page_previews=allow),
    }
    perms = perm_map.get(perm_name)
    if not perms:
        return await update.message.reply_text(
            "❌ Unknown permission. Use: `messages` `media` `stickers` `polls` `links`",
            parse_mode=ParseMode.MARKDOWN,
        )
    try:
        await context.bot.restrict_chat_member(update.effective_chat.id, target.id, perms)
        await update.message.reply_text(
            f"✅ `{perm_name}` → `{state}` for {mention(target)}.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Failed: {e}")


async def cmd_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("⛔ Admins only.")
    if not update.message.reply_to_message:
        return await update.message.reply_text("↩️ Reply to a message to pin it.")
    try:
        await context.bot.pin_chat_message(
            update.effective_chat.id,
            update.message.reply_to_message.message_id,
        )
        await update.message.reply_text("📌 Message pinned.")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed: {e}")


async def cmd_unpin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("⛔ Admins only.")
    try:
        await context.bot.unpin_chat_message(update.effective_chat.id)
        await update.message.reply_text("📌 Message unpinned.")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed: {e}")


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user   = update.effective_user
    chat   = update.effective_chat
    target = update.message.reply_to_message.from_user if update.message.reply_to_message else user
    await update.message.reply_text(
        f"👤 *User ID:* `{target.id}`\n💬 *Chat ID:* `{chat.id}`",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_dice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_dice(update.effective_chat.id)

# ─────────────────────────────────────────────
#  /autoreply
# ─────────────────────────────────────────────

async def cmd_autoreply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text(
            "Usage:\n"
            "`/autoreply set` word | reply\n"
            "`/autoreply list`\n"
            "`/autoreply delete` word",
            parse_mode=ParseMode.MARKDOWN,
        )

    sub = context.args[0].lower()

    # ── SET ──────────────────────────────────
    if sub == "set":
        full_text = update.message.text or ""
        try:
            payload = full_text.split(None, 2)[2]
        except IndexError:
            return await update.message.reply_text(
                "Usage: `/autoreply set` word | reply", parse_mode=ParseMode.MARKDOWN
            )
        if "|" not in payload:
            return await update.message.reply_text(
                "Format: `/autoreply set` word | reply", parse_mode=ParseMode.MARKDOWN
            )
        parts       = payload.split("|", 1)
        trigger     = parts[0].strip().lower()
        reply_msg   = parts[1].strip()
        if not trigger or not reply_msg:
            return await update.message.reply_text("Both word and reply must be non-empty.")
        fb_set(f"autoreply/{trigger}", reply_msg)
        await update.message.reply_text(
            f"✅ Auto reply set:\n`{trigger}` → {reply_msg}", parse_mode=ParseMode.MARKDOWN
        )

    # ── LIST ─────────────────────────────────
    elif sub == "list":
        data = fb_get("autoreply", {})
        if not data:
            body = "  (none saved yet)"
        else:
            body = "\n".join([f"  • `{k}` → {v}" for k, v in data.items()])
        text = (
            "```\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "      Auto Reply List\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "```\n"
            + body
            + "\n```\n━━━━━━━━━━━━━━━━━━━━━━━━━\n```"
        )
        await safe_photo_reply(update, get_media("autoreply_poster"), text)

    # ── DELETE ────────────────────────────────
    elif sub == "delete":
        if len(context.args) < 2:
            return await update.message.reply_text(
                "Usage: `/autoreply delete` word", parse_mode=ParseMode.MARKDOWN
            )
        trigger = context.args[1].lower()
        fb_delete(f"autoreply/{trigger}")
        await update.message.reply_text(
            f"🗑️ Auto reply for `{trigger}` removed.", parse_mode=ParseMode.MARKDOWN
        )

    else:
        await update.message.reply_text(
            "Unknown subcommand. Use: `set` / `list` / `delete`",
            parse_mode=ParseMode.MARKDOWN,
        )

# ─────────────────────────────────────────────
#  /shout
# ─────────────────────────────────────────────

async def cmd_shout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("⛔ Admins only.")
    if not context.args:
        return await update.message.reply_text(
            "Usage: `/shout` your message", parse_mode=ParseMode.MARKDOWN
        )

    msg = " ".join(context.args)

    blocked = fb_get("shout_config/blocked_words", {})
    for word in (blocked or {}).values():
        if str(word).lower() in msg.lower():
            return await update.message.reply_text(
                f"🚫 Message contains blocked word: `{word}`", parse_mode=ParseMode.MARKDOWN
            )

    text = (
        "📢 *ANNOUNCEMENT*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{msg}\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    await safe_photo_reply(update, get_media("shout_poster"), text)

# ─────────────────────────────────────────────
#  /shoutconfig
# ─────────────────────────────────────────────

async def cmd_shoutconfig(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("⛔ Admins only.")

    gif_block = fb_get("shout_config/gif_block", False)
    gif_label = (
        "🟢 GIF Blocker: ON  (tap to disable)"
        if gif_block
        else "🔴 GIF Blocker: OFF  (tap to enable)"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Blocked Word",    callback_data="shout_add_word")],
        [InlineKeyboardButton("➖ Remove Blocked Word", callback_data="shout_remove_word")],
        [InlineKeyboardButton(gif_label,               callback_data="shout_toggle_gif")],
    ])
    text = (
        "```\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "    Shout Configuration Panel\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "```\n"
        "Manage your shout settings below:"
    )
    await safe_photo_reply(update, get_media("shout_poster"), text, reply_markup=keyboard)

# ─────────────────────────────────────────────
#  /afk
# ─────────────────────────────────────────────

async def cmd_afk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user   = update.effective_user
    reason = " ".join(context.args) if context.args else "No reason given"

    fb_set(f"afk/{user.id}", {
        "reason": reason,
        "name":   user.full_name or user.first_name or "Unknown",
        "time":   str(datetime.utcnow()),
    })

    text = (
        "```\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "           AFK\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "```\n"
        f"💤 *{user.full_name or user.first_name}* is now AFK\n"
        f"📝 *Reason:* {reason}\n"
        "```\n━━━━━━━━━━━━━━━━━━━━━━━\n```"
    )
    await safe_photo_reply(update, get_media("afk_poster"), text)

# ─────────────────────────────────────────────
#  /nuke
# ─────────────────────────────────────────────

async def cmd_nuke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("⛔ Admins only.")

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💥 Delete 100",  callback_data="nuke_100"),
            InlineKeyboardButton("💥 Delete 200",  callback_data="nuke_200"),
        ],
        [
            InlineKeyboardButton("☢️ Delete 8900", callback_data="nuke_8900"),
            InlineKeyboardButton("❌ Cancel",       callback_data="nuke_cancel"),
        ],
    ])
    text = (
        "```\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "        Nuke Panel\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "```\n"
        "⚠️ *Choose how many messages to delete:*"
    )
    await safe_photo_reply(update, get_media("nuke_poster"), text, reply_markup=keyboard)

# ─────────────────────────────────────────────
#  CALLBACK QUERY HANDLER
# ─────────────────────────────────────────────

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    data    = query.data
    chat_id = query.message.chat.id

    # ── NUKE ─────────────────────────────────
    if data.startswith("nuke_"):
        try:
            admins = await context.bot.get_chat_administrators(chat_id)
            if not any(a.user.id == query.from_user.id for a in admins):
                return await query.answer("⛔ Admins only.", show_alert=True)
        except Exception:
            pass

        if data == "nuke_cancel":
            try:
                await query.edit_message_caption("❌ Nuke cancelled.")
            except Exception:
                await query.edit_message_text("❌ Nuke cancelled.")
            return

        count  = int(data.split("_")[1])
        try:
            await query.edit_message_caption(f"💣 Nuking {count} messages…")
        except Exception:
            pass

        deleted = 0
        msg_id  = query.message.message_id - 1
        while deleted < count and msg_id > 0:
            try:
                await context.bot.delete_message(chat_id, msg_id)
                deleted += 1
            except Exception:
                pass
            msg_id -= 1
            await asyncio.sleep(0.05)

        result = f"✅ Deleted {deleted} messages."
        try:
            await query.edit_message_caption(result)
        except Exception:
            try:
                await query.edit_message_text(result)
            except Exception:
                pass

    # ── SHOUT CONFIG ─────────────────────────
    elif data == "shout_toggle_gif":
        current = fb_get("shout_config/gif_block", False)
        fb_set("shout_config/gif_block", not current)
        status = "ON 🟢" if not current else "OFF 🔴"
        await query.answer(f"GIF Blocker turned {status}", show_alert=True)

    elif data == "shout_add_word":
        context.user_data["awaiting"] = "shout_add"
        await query.answer("Send the word you want to block.", show_alert=True)

    elif data == "shout_remove_word":
        context.user_data["awaiting"] = "shout_remove"
        await query.answer("Send the word you want to unblock.", show_alert=True)

# ─────────────────────────────────────────────
#  MESSAGE HANDLER  (auto-reply + AFK + GIF block)
# ─────────────────────────────────────────────

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user = update.effective_user
    chat = update.effective_chat
    msg  = update.message

    # ── Awaiting shout-word input ─────────────
    awaiting = context.user_data.get("awaiting")
    if awaiting and msg.text:
        word = msg.text.strip().lower()
        if awaiting == "shout_add":
            fb_push("shout_config/blocked_words", word)
            await msg.reply_text(
                f"✅ `{word}` added to blocked words.", parse_mode=ParseMode.MARKDOWN
            )
        elif awaiting == "shout_remove":
            blocked = fb_get("shout_config/blocked_words", {})
            removed = False
            for k, v in (blocked or {}).items():
                if str(v).lower() == word:
                    fb_delete(f"shout_config/blocked_words/{k}")
                    removed = True
                    break
            if removed:
                await msg.reply_text(
                    f"🗑️ `{word}` removed from blocked list.", parse_mode=ParseMode.MARKDOWN
                )
            else:
                await msg.reply_text(
                    f"❌ `{word}` not found in blocked list.", parse_mode=ParseMode.MARKDOWN
                )
        context.user_data.pop("awaiting", None)
        return

    # ── GIF blocker ───────────────────────────
    gif_block = fb_get("shout_config/gif_block", False)
    if gif_block and (msg.animation or (msg.sticker and msg.sticker.is_animated)):
        try:
            await msg.delete()
            await context.bot.send_message(
                chat.id,
                f"🚫 {mention(user)} GIFs are blocked in this group.",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass
        return

    # ── AFK removal ───────────────────────────
    afk_data = fb_get(f"afk/{user.id}")
    if afk_data:
        fb_delete(f"afk/{user.id}")
        try:
            await msg.reply_text(
                f"👋 Welcome back, *{user.first_name}*! AFK status removed.",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass

    # ── Auto reply ────────────────────────────
    if msg.text:
        replies    = fb_get("autoreply", {})
        text_lower = msg.text.lower()
        for trigger, reply_text in (replies or {}).items():
            if str(trigger).lower() in text_lower:
                try:
                    await msg.reply_text(str(reply_text))
                except Exception:
                    pass
                break

# ─────────────────────────────────────────────
#  KEEP-ALIVE  (daemon HTTP server for 24/7 uptime)
# ─────────────────────────────────────────────

class _KeepAliveHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Knowledge Pro AI Bot is alive!")

    def log_message(self, *args):
        pass  # suppress noisy access logs


def _run_keep_alive():
    port   = int(os.getenv("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), _KeepAliveHandler)
    logger.info("Keep-alive HTTP server on port %d", port)
    server.serve_forever()


def start_keep_alive():
    t = threading.Thread(target=_run_keep_alive, daemon=True, name="keep-alive")
    t.start()

# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        raise RuntimeError("Set the BOT_TOKEN environment variable before running.")

    start_keep_alive()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("help",        cmd_help))
    app.add_handler(CommandHandler("kick",        cmd_kick))
    app.add_handler(CommandHandler("ban",         cmd_ban))
    app.add_handler(CommandHandler("mute",        cmd_mute))
    app.add_handler(CommandHandler("promote",     cmd_promote))
    app.add_handler(CommandHandler("demote",      cmd_demote))
    app.add_handler(CommandHandler("permission",  cmd_permission))
    app.add_handler(CommandHandler("pin",         cmd_pin))
    app.add_handler(CommandHandler("unpin",       cmd_unpin))
    app.add_handler(CommandHandler("id",          cmd_id))
    app.add_handler(CommandHandler("dice",        cmd_dice))
    app.add_handler(CommandHandler("autoreply",   cmd_autoreply))
    app.add_handler(CommandHandler("shout",       cmd_shout))
    app.add_handler(CommandHandler("shoutconfig", cmd_shoutconfig))
    app.add_handler(CommandHandler("afk",         cmd_afk))
    app.add_handler(CommandHandler("nuke",        cmd_nuke))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, message_handler))

    logger.info("Knowledge Pro AI Bot started.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
