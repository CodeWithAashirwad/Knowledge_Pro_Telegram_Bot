import os
import logging
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
#  ENV  — only BOT_TOKEN via environment
# ─────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# ─────────────────────────────────────────────
#  FIREBASE  — service account hardcoded
# ─────────────────────────────────────────────
FIREBASE_CRED = {
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
    "universe_domain": "googleapis.com"
}

FIREBASE_DB_URL = "https://knowledge-pro-c9ee5-default-rtdb.firebaseio.com"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

try:
    cred = credentials.Certificate(FIREBASE_CRED)
    firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})
    logger.info("✅ Firebase connected.")
except Exception as e:
    logger.error(f"❌ Firebase init failed: {e}")

# ─────────────────────────────────────────────
#  FIREBASE HELPERS
# ─────────────────────────────────────────────

def fb_get(path, default=None):
    try:
        val = db.reference(path).get()
        return val if val is not None else default
    except Exception as e:
        logger.error(f"FB get {path}: {e}")
        return default

def fb_set(path, value):
    try:
        db.reference(path).set(value)
        return True
    except Exception as e:
        logger.error(f"FB set {path}: {e}")
        return False

def fb_delete(path):
    try:
        db.reference(path).delete()
        return True
    except Exception as e:
        logger.error(f"FB delete {path}: {e}")
        return False

def fb_push(path, value):
    try:
        db.reference(path).push(value)
        return True
    except Exception as e:
        logger.error(f"FB push {path}: {e}")
        return False

# ─────────────────────────────────────────────
#  DEFAULT MEDIA  (all editable via Firebase)
#  Set at /config/<key> in your Realtime DB
# ─────────────────────────────────────────────
DEFAULT_WELCOME_PHOTO    = "https://media.giphy.com/media/3oEjI6SIIHBdRxXI40/giphy.gif"
DEFAULT_HELP_PHOTO       = "https://media.giphy.com/media/26ufdipQqU2lhNA4g/giphy.gif"
DEFAULT_AUTOREPLY_POSTER = "https://media.giphy.com/media/l0HlBO7eyXzSZkJri/giphy.gif"
DEFAULT_SHOUT_POSTER     = "https://media.giphy.com/media/3o7TKSjRrfIPjeiVyM/giphy.gif"
DEFAULT_AFK_POSTER       = "https://media.giphy.com/media/l4FGni1RBAR2OWsGk/giphy.gif"
DEFAULT_NUKE_POSTER      = "https://media.giphy.com/media/26tPnAAJxXTvgMp96/giphy.gif"

def get_media(key, default):
    val = fb_get(f"config/{key}")
    return val if val else default

# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────

def mention(user):
    name = user.full_name or user.first_name or "User"
    return f"[{name}](tg://user?id={user.id})"

async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if update.effective_chat.type == "private":
        return True
    try:
        admins = await context.bot.get_chat_administrators(update.effective_chat.id)
        return any(a.user.id == update.effective_user.id for a in admins)
    except Exception:
        return False

# ─────────────────────────────────────────────
#  /start
# ─────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user  = update.effective_user
    name  = user.first_name or "User"
    photo = get_media("welcome_photo", DEFAULT_WELCOME_PHOTO)

    text = (
        f"👋 *Welcome, {name}!*\n\n"
        "🤖 *I'm Knowledge Pro AI*\n"
        "_Moderates your groups your way — Fully Customisable & Easy._\n\n"
        "```\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "         Help Commands\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "```\n"
        "🛡 *Moderation*\n"
        "• `/kick` • `/ban` • `/mute`\n"
        "• `/promote` • `/demote`\n"
        "• `/permission` • `/pin` • `/unpin`\n"
        "• `/id` • `/dice`\n\n"
        "🤖 *Auto Reply*\n"
        "• `/autoreply set` word | reply\n"
        "• `/autoreply list`\n"
        "• `/autoreply delete` word\n\n"
        "📢 *Shout*\n"
        "• `/shout` message\n"
        "• `/shoutconfig`\n\n"
        "💤 *AFK*\n"
        "• `/afk` reason\n\n"
        "💣 *Nuke*\n"
        "• `/nuke`\n"
        "```\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "```\n"
        "ℹ️ *About Me*\n"
        "I'm Knowledge Pro AI that moderates your groups your way — "
        "fully customisable and easy."
    )
    try:
        await update.message.reply_photo(
            photo=photo, caption=text, parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ─────────────────────────────────────────────
#  /help
# ─────────────────────────────────────────────

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = get_media("help_photo", DEFAULT_HELP_PHOTO)
    text = (
        "```\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "           Help Command\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "```\n"
        "🛡 *Moderation Commands*\n"
        "• `/kick` — Remove member\n"
        "• `/ban` — Permanently ban\n"
        "• `/mute` — Silence user\n"
        "• `/promote` — Make admin\n"
        "• `/demote` — Remove admin\n"
        "• `/permission` @user perm on|off\n"
        "• `/pin` — Pin replied message\n"
        "• `/unpin` — Unpin message\n"
        "• `/id` — Show user/chat ID\n"
        "• `/dice` — Roll a dice 🎲\n\n"
        "🤖 *Auto Reply*\n"
        "• `/autoreply set` word | reply\n"
        "• `/autoreply list` — View all\n"
        "• `/autoreply delete` word\n\n"
        "📢 *Shout*\n"
        "• `/shout` message — Announce\n"
        "• `/shoutconfig` — Config panel\n\n"
        "💤 *AFK*\n"
        "• `/afk` reason — Set AFK\n\n"
        "💣 *Nuke*\n"
        "• `/nuke` — Open delete panel\n"
        "```\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "```"
    )
    try:
        await update.message.reply_photo(
            photo=photo, caption=text, parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

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
            ChatPermissions(can_send_messages=False)
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
            can_manage_chat=True
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
            can_manage_chat=False
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
            "Perms: `messages` `media` `stickers` `gifs` `polls` `links`",
            parse_mode=ParseMode.MARKDOWN
        )
    target    = update.message.reply_to_message.from_user
    perm_name = context.args[0].lower()
    state     = context.args[1].lower()
    allow     = state == "on"
    perm_map  = {
        "messages": ChatPermissions(can_send_messages=allow),
        "media":    ChatPermissions(can_send_media_messages=allow),
        "stickers": ChatPermissions(can_send_other_messages=allow),
        "gifs":     ChatPermissions(can_send_other_messages=allow),
        "polls":    ChatPermissions(can_send_polls=allow),
        "links":    ChatPermissions(can_add_web_page_previews=allow),
    }
    perms = perm_map.get(perm_name)
    if not perms:
        return await update.message.reply_text("❌ Unknown permission.")
    try:
        await context.bot.restrict_chat_member(update.effective_chat.id, target.id, perms)
        await update.message.reply_text(
            f"✅ `{perm_name}` → `{state}` for {mention(target)}.",
            parse_mode=ParseMode.MARKDOWN
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
            update.message.reply_to_message.message_id
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
        parse_mode=ParseMode.MARKDOWN
    )

async def cmd_dice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_dice(update.effective_chat.id)

# ─────────────────────────────────────────────
#  AUTO REPLY
# ─────────────────────────────────────────────

async def cmd_autoreply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text(
            "Usage:\n"
            "`/autoreply set word | reply`\n"
            "`/autoreply list`\n"
            "`/autoreply delete word`",
            parse_mode=ParseMode.MARKDOWN
        )
    sub = context.args[0].lower()

    if sub == "set":
        full = update.message.text.split(None, 2)
        if len(full) < 3 or "|" not in full[2]:
            return await update.message.reply_text(
                "Usage: `/autoreply set word | reply text`",
                parse_mode=ParseMode.MARKDOWN
            )
        parts      = full[2].split("|", 1)
        trigger    = parts[0].strip().lower()
        reply_text = parts[1].strip()
        fb_set(f"autoreply/{trigger}", reply_text)
        await update.message.reply_text(
            f"✅ Auto reply saved!\n🔑 Trigger: `{trigger}`\n💬 Reply: {reply_text}",
            parse_mode=ParseMode.MARKDOWN
        )

    elif sub == "list":
        data   = fb_get("autoreply", {})
        poster = get_media("autoreply_poster", DEFAULT_AUTOREPLY_POSTER)
        body   = "\n".join([f"  • `{k}` → {v}" for k, v in data.items()]) if data else "  No auto replies saved yet."
        text   = (
            "```\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "        Auto Reply List\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "```\n"
            f"{body}\n"
            "```\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n```"
        )
        try:
            await update.message.reply_photo(
                photo=poster, caption=text, parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    elif sub == "delete":
        if len(context.args) < 2:
            return await update.message.reply_text(
                "Usage: `/autoreply delete word`", parse_mode=ParseMode.MARKDOWN
            )
        trigger = context.args[1].lower()
        fb_delete(f"autoreply/{trigger}")
        await update.message.reply_text(
            f"🗑️ Auto reply for `{trigger}` removed.", parse_mode=ParseMode.MARKDOWN
        )

    else:
        await update.message.reply_text(
            "Unknown subcommand. Use: `set` / `list` / `delete`",
            parse_mode=ParseMode.MARKDOWN
        )

# ─────────────────────────────────────────────
#  /shout
# ─────────────────────────────────────────────

async def cmd_shout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("⛔ Admins only.")
    if not context.args:
        return await update.message.reply_text(
            "Usage: `/shout your message here`", parse_mode=ParseMode.MARKDOWN
        )
    msg     = " ".join(context.args)
    blocked = fb_get("shout_config/blocked_words", {})
    if blocked:
        for word in blocked.values():
            if str(word).lower() in msg.lower():
                return await update.message.reply_text(
                    f"🚫 Message contains blocked word: `{word}`",
                    parse_mode=ParseMode.MARKDOWN
                )
    poster = get_media("shout_poster", DEFAULT_SHOUT_POSTER)
    text   = (
        "📢 *A N N O U N C E M E N T*\n"
        "```\n━━━━━━━━━━━━━━━━━━━━━━━\n```\n"
        f"{msg}\n"
        "```\n━━━━━━━━━━━━━━━━━━━━━━━\n```"
    )
    try:
        await update.message.reply_photo(
            photo=poster, caption=text, parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ─────────────────────────────────────────────
#  /shoutconfig
# ─────────────────────────────────────────────

async def cmd_shoutconfig(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("⛔ Admins only.")
    poster    = get_media("shout_poster", DEFAULT_SHOUT_POSTER)
    gif_block = fb_get("shout_config/gif_block", False)
    gif_label = "🟢 GIF Blocker: ON  — tap to turn OFF" if gif_block else "🔴 GIF Blocker: OFF — tap to turn ON"
    keyboard  = [
        [InlineKeyboardButton("➕ Add Blocked Word",    callback_data="shout_add_word")],
        [InlineKeyboardButton("➖ Remove Blocked Word", callback_data="shout_remove_word")],
        [InlineKeyboardButton(gif_label,               callback_data="shout_toggle_gif")],
    ]
    text = (
        "```\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "    Shout Configuration Panel\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "```\n"
        "Manage your shout settings below:"
    )
    try:
        await update.message.reply_photo(
            photo=poster, caption=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        await update.message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

# ─────────────────────────────────────────────
#  /afk
# ─────────────────────────────────────────────

async def cmd_afk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user   = update.effective_user
    reason = " ".join(context.args) if context.args else "No reason given"
    poster = get_media("afk_poster", DEFAULT_AFK_POSTER)
    fb_set(f"afk/{user.id}", {
        "reason": reason,
        "name":   user.full_name or user.first_name,
        "time":   str(datetime.utcnow())
    })
    text = (
        "```\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "            AFK\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "```\n"
        f"💤 *{user.full_name or user.first_name}* is now AFK\n"
        f"📝 *Reason:* {reason}\n"
        "```\n━━━━━━━━━━━━━━━━━━━━━━━━\n```"
    )
    try:
        await update.message.reply_photo(
            photo=poster, caption=text, parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ─────────────────────────────────────────────
#  /nuke
# ─────────────────────────────────────────────

async def cmd_nuke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("⛔ Admins only.")
    poster   = get_media("nuke_poster", DEFAULT_NUKE_POSTER)
    keyboard = [
        [
            InlineKeyboardButton("💥 100",    callback_data="nuke_100"),
            InlineKeyboardButton("💥 200",    callback_data="nuke_200"),
        ],
        [
            InlineKeyboardButton("☢️ 8900",   callback_data="nuke_8900"),
            InlineKeyboardButton("❌ Cancel", callback_data="nuke_cancel"),
        ],
    ]
    text = (
        "```\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "         Nuke Panel\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "```\n"
        "⚠️ *Select how many messages to delete:*\n\n"
        "• 💥 100 — Delete 100 messages\n"
        "• 💥 200 — Delete 200 messages\n"
        "• ☢️ 8900 — Delete 8900 messages"
    )
    try:
        await update.message.reply_photo(
            photo=poster, caption=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        await update.message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

# ─────────────────────────────────────────────
#  CALLBACK QUERY HANDLER
# ─────────────────────────────────────────────

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    data    = query.data
    chat_id = query.message.chat.id

    # ── NUKE ──
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
            await query.edit_message_caption(f"💣 Nuking {count} messages... please wait.")
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

        try:
            await query.edit_message_caption(f"✅ Nuke complete. Deleted {deleted} messages.")
        except Exception:
            pass

    # ── SHOUT CONFIG ──
    elif data == "shout_toggle_gif":
        current = fb_get("shout_config/gif_block", False)
        fb_set("shout_config/gif_block", not current)
        status  = "ON 🟢" if not current else "OFF 🔴"
        await query.answer(f"GIF Blocker turned {status}", show_alert=True)

    elif data == "shout_add_word":
        context.user_data["awaiting_shout_add"] = True
        await query.answer(
            "Send the word you want to block in your next message.",
            show_alert=True
        )

    elif data == "shout_remove_word":
        context.user_data["awaiting_shout_remove"] = True
        await query.answer(
            "Send the word you want to unblock in your next message.",
            show_alert=True
        )

# ─────────────────────────────────────────────
#  MESSAGE HANDLER
# ─────────────────────────────────────────────

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user = update.effective_user
    chat = update.effective_chat
    msg  = update.message

    # ── Awaiting shout add word ──
    if context.user_data.get("awaiting_shout_add") and msg.text:
        word = msg.text.strip().lower()
        fb_push("shout_config/blocked_words", word)
        context.user_data.pop("awaiting_shout_add", None)
        return await msg.reply_text(
            f"✅ `{word}` added to shout blocked words.",
            parse_mode=ParseMode.MARKDOWN
        )

    # ── Awaiting shout remove word ──
    if context.user_data.get("awaiting_shout_remove") and msg.text:
        word    = msg.text.strip().lower()
        blocked = fb_get("shout_config/blocked_words", {})
        removed = False
        for k, v in (blocked or {}).items():
            if str(v).lower() == word:
                fb_delete(f"shout_config/blocked_words/{k}")
                removed = True
                break
        context.user_data.pop("awaiting_shout_remove", None)
        resp = f"🗑️ `{word}` unblocked." if removed else f"❌ `{word}` not found in blocked list."
        return await msg.reply_text(resp, parse_mode=ParseMode.MARKDOWN)

    # ── GIF blocker ──
    gif_block = fb_get("shout_config/gif_block", False)
    if gif_block and (msg.animation or (msg.sticker and msg.sticker.is_animated)):
        try:
            await msg.delete()
            await context.bot.send_message(
                chat.id,
                f"🚫 {mention(user)} GIFs are blocked in this group.",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            pass
        return

    # ── AFK removal ──
    afk_data = fb_get(f"afk/{user.id}")
    if afk_data:
        fb_delete(f"afk/{user.id}")
        await msg.reply_text(
            f"👋 Welcome back, *{user.first_name}*! Your AFK has been removed.",
            parse_mode=ParseMode.MARKDOWN
        )

    # ── Auto reply ──
    if msg.text:
        replies    = fb_get("autoreply", {})
        text_lower = msg.text.lower()
        for trigger, reply_text in (replies or {}).items():
            if str(trigger).lower() in text_lower:
                await msg.reply_text(reply_text)
                break

# ─────────────────────────────────────────────
#  KEEP-ALIVE  — background HTTP server for 24/7
# ─────────────────────────────────────────────

class KeepAliveHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Knowledge Pro AI Bot is alive!")

    def log_message(self, format, *args):
        pass  # suppress access logs

def _run_server():
    port   = int(os.getenv("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), KeepAliveHandler)
    logger.info(f"✅ Keep-alive server on port {port}")
    server.serve_forever()

def start_keep_alive():
    t = threading.Thread(target=_run_server, daemon=True)
    t.start()

# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
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

    logger.info("🚀 Knowledge Pro AI Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
