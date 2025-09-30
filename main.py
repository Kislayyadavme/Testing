#!/usr/bin/env python3
# main.py - Mega Telegram Group Management Bot
# Works with: python-telegram-bot v20.6 and aiosqlite
# pip install python-telegram-bot==20.6 aiosqlite

import logging
import random
import time
import asyncio
from typing import Optional, Tuple, List
import aiosqlite

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
    ParseMode,
    InputMediaVideo,
)
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ---------------------------- CONFIG - EDIT BEFORE RUNNING ----------------------------
BOT_TOKEN = "8014367494:AAGPMX5DMQQueZnPVOXmOF3DRek_SzxWbg8"            # <-- replace with your bot token
ADMIN_CHAT_ID = 8156053366                        # <-- replace with admin Telegram numeric id
DB_PATH = "bot_data.db"
DEFAULT_PASSWORD = "Sahil@8896"                 # default password; can be changed by admin
MAX_SEND_PER_FILE_PER_USER = 2                  # do not send same file more than this to same user
VIDEO_BATCH_SLEEP = 0.8                          # seconds sleep between sending videos to avoid rate limits
MEDIA_GROUP_LIMIT = 10                           # Telegram's send_media_group limit
# ---------------------------------------------------------------------------------------

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ---------------------------- DATABASE INITIALIZATION ----------------------------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(
            """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            first_seen INTEGER,
            last_seen INTEGER,
            is_coadmin INTEGER DEFAULT 0,
            is_blocked INTEGER DEFAULT 0,
            muted_until INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS channels (
            chat_id INTEGER PRIMARY KEY,
            title TEXT,
            username TEXT
        );

        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_type TEXT,
            file_id TEXT,
            file_name TEXT,
            added_by INTEGER,
            added_on INTEGER DEFAULT (strftime('%s','now'))
        );

        CREATE TABLE IF NOT EXISTS referrals (
            referrer_id INTEGER,
            referred_id INTEGER,
            timestamp INTEGER DEFAULT (strftime('%s','now')),
            PRIMARY KEY (referrer_id, referred_id)
        );

        CREATE TABLE IF NOT EXISTS user_file_send_count (
            user_id INTEGER,
            file_db_id INTEGER,
            send_count INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, file_db_id)
        );

        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS admin_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            type TEXT,
            data TEXT,
            status TEXT DEFAULT 'pending',
            created_on INTEGER DEFAULT (strftime('%s','now'))
        );
        """
        )
        # ensure password meta exists
        cur = await db.execute("SELECT value FROM meta WHERE key = 'password'")
        row = await cur.fetchone()
        if not row:
            await db.execute("INSERT INTO meta (key, value) VALUES (?, ?)", ("password", DEFAULT_PASSWORD))
        await db.commit()


# ---------------------------- HELPERS ----------------------------
async def get_password(db) -> str:
    cur = await db.execute("SELECT value FROM meta WHERE key = 'password'")
    row = await cur.fetchone()
    return row[0] if row else DEFAULT_PASSWORD


async def set_password(db, new_pass: str):
    await db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", ("password", new_pass))
    await db.commit()


async def is_admin(user_id: int) -> bool:
    return int(user_id) == int(ADMIN_CHAT_ID)


async def is_auth_user(db, user_id: int) -> bool:
    # admin OR co-admin
    if await is_admin(user_id):
        return True
    cur = await db.execute("SELECT is_coadmin FROM users WHERE user_id = ?", (user_id,))
    row = await cur.fetchone()
    return bool(row and row[0] == 1)


def make_welcome_keyboard(bot_username: str) -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton("🌟 Join Channels", callback_data="join_channels")],
        [
            InlineKeyboardButton("📣 Request Admin", callback_data="request_admin"),
            InlineKeyboardButton("🔄 My Profile", callback_data="my_profile"),
        ],
    ]
    return InlineKeyboardMarkup(kb)


async def register_user_if_new(db, user_id: int):
    cur = await db.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    row = await cur.fetchone()
    now = int(time.time())
    if not row:
        await db.execute("INSERT INTO users (user_id, first_seen, last_seen) VALUES (?, ?, ?)", (user_id, now, now))
    else:
        await db.execute("UPDATE users SET last_seen = ? WHERE user_id = ?", (now, user_id))
    await db.commit()


# ---------------------------- BOT COMMANDS ----------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start; also parse referral param like 'ref1234'"""
    user = update.effective_user
    args = context.args or []
    db = await aiosqlite.connect(DB_PATH)
    try:
        await register_user_if_new(db, user.id)

        # referral handling
        if args:
            arg = args[0]
            if arg.startswith("ref"):
                try:
                    ref = int(arg[3:])
                    # do not self-ref
                    if ref != user.id:
                        cur = await db.execute("SELECT 1 FROM referrals WHERE referrer_id=? AND referred_id=?", (ref, user.id))
                        if not await cur.fetchone():
                            await db.execute("INSERT INTO referrals (referrer_id, referred_id) VALUES (?, ?)", (ref, user.id))
                            await db.commit()
                            # notify referrer (best-effort)
                            try:
                                await context.bot.send_message(
                                    chat_id=ref,
                                    text=f"🎉 You got a referral! [{user.full_name}](tg://user?id={user.id}) started the bot using your link.",
                                    parse_mode=ParseMode.MARKDOWN,
                                )
                            except Exception:
                                logger.info("Could not notify referrer %s", ref)
                except Exception:
                    pass

        # check if first-time or returning (we'll check first_seen)
        cur = await db.execute("SELECT first_seen FROM users WHERE user_id = ?", (user.id,))
        row = await cur.fetchone()
        first_time = False
        if row and row[0]:
            # first_seen exists; but we consider first_time if equals last_seen maybe - we'll say first_time if last_seen == first_seen
            first_time = (row[0] == int(time.time()))  # not perfect but we still show first-time text
        # build welcome text
        welcome_text = (
            f"✨ *Welcome {user.first_name}!* ✨\n\n"
            "Thanks for starting this bot. I can help you join channels, unlock media by referrals, "
            "and request co-admin privileges in a single tap.\n\n"
            "Tap the buttons below to begin."
        )
        kb = make_welcome_keyboard(context.bot.username)
        await update.message.reply_text(welcome_text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    finally:
        await db.close()


async def auth_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ /auth <password> -> mark user as co-admin if correct """
    user = update.effective_user
    if not context.args:
        await update.message.reply_text("Usage: /auth <password>")
        return
    password = context.args[0]
    db = await aiosqlite.connect(DB_PATH)
    try:
        real = await get_password(db)
        if password == real:
            await db.execute("INSERT OR REPLACE INTO users (user_id, is_coadmin) VALUES (?, 1)", (user.id,))
            await db.commit()
            await update.message.reply_text("✅ Authenticated. You are now a co-admin.")
        else:
            await update.message.reply_text("❌ Wrong password.")
    finally:
        await db.close()


async def chgpass_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ /chgpass <newpw> (admin only) """
    user = update.effective_user
    if not await is_admin(user.id):
        await update.message.reply_text("Only the main admin can change the password.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /chgpass <new_password>")
        return
    newpw = context.args[0]
    db = await aiosqlite.connect(DB_PATH)
    try:
        await set_password(db, newpw)
        await update.message.reply_text("🔐 Password updated successfully.")
    finally:
        await db.close()


async def addchn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ /addchn <https://t.me/username> - add channel by url """
    user = update.effective_user
    db = await aiosqlite.connect(DB_PATH)
    try:
        if not await is_auth_user(db, user.id):
            await update.message.reply_text("Only admin/co-admins can add channels.")
            return
        if not context.args:
            await update.message.reply_text("Usage: /addchn <channel_or_group_url>")
            return
        url = context.args[0].rstrip("/")
        username = url.split("/")[-1]
        try:
            chat = await context.bot.get_chat(f"@{username}")
            await db.execute("INSERT OR REPLACE INTO channels (chat_id, title, username) VALUES (?, ?, ?)", (chat.id, chat.title or username, chat.username or username))
            await db.commit()
            await update.message.reply_text(f"✅ Registered channel/group: {chat.title} ({chat.id})")
        except Exception as e:
            await update.message.reply_text(f"❌ Failed to find/add channel: {e}")
    finally:
        await db.close()


async def addchid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ /addchid <chat_id> """
    user = update.effective_user
    db = await aiosqlite.connect(DB_PATH)
    try:
        if not await is_auth_user(db, user.id):
            await update.message.reply_text("Only admin/co-admins can add channels.")
            return
        if not context.args:
            await update.message.reply_text("Usage: /addchid <chat_id>")
            return
        try:
            chat_id = int(context.args[0])
            chat = await context.bot.get_chat(chat_id)
            await db.execute("INSERT OR REPLACE INTO channels (chat_id, title, username) VALUES (?, ?, ?)", (chat.id, chat.title or "", chat.username or None))
            await db.commit()
            await update.message.reply_text(f"✅ Registered: {chat.title} ({chat.id})")
        except Exception as e:
            await update.message.reply_text(f"❌ Failed to add chat id: {e}")
    finally:
        await db.close()


async def advid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ /advid (reply to a video) -> saves the video's file_id into DB """
    user = update.effective_user
    db = await aiosqlite.connect(DB_PATH)
    try:
        if not await is_auth_user(db, user.id):
            await update.message.reply_text("Only admin or authenticated co-admins can add videos.")
            return
        if not update.message.reply_to_message or not update.message.reply_to_message.video:
            await update.message.reply_text("Reply to a video with /advid to save it.")
            return
        vid = update.message.reply_to_message.video
        file_id = vid.file_id
        fname = getattr(vid, "file_name", None) or "video"
        await db.execute("INSERT INTO files (file_type, file_id, file_name, added_by) VALUES (?, ?, ?, ?)", ("video", file_id, fname, user.id))
        await db.commit()
        await update.message.reply_text("✅ Video added successfully.")
    finally:
        await db.close()


async def addfile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ /addfile (reply to a document/audio) """
    user = update.effective_user
    db = await aiosqlite.connect(DB_PATH)
    try:
        if not await is_auth_user(db, user.id):
            await update.message.reply_text("Only admin/co-admins can add files.")
            return
        if not update.message.reply_to_message:
            await update.message.reply_text("Reply to a file (document/audio) with /addfile to save it.")
            return
        r = update.message.reply_to_message
        if r.document:
            fid = r.document.file_id
            ftype = "document"
            fname = r.document.file_name or "document"
        elif r.audio:
            fid = r.audio.file_id
            ftype = "audio"
            fname = getattr(r.audio, "file_name", "audio")
        else:
            await update.message.reply_text("Reply to a document or audio to save with /addfile.")
            return
        await db.execute("INSERT INTO files (file_type, file_id, file_name, added_by) VALUES (?, ?, ?, ?)", (ftype, fid, fname, user.id))
        await db.commit()
        await update.message.reply_text("✅ File added successfully.")
    finally:
        await db.close()


# ---------------------------- SEND VIDEO LOGIC ----------------------------
async def fetch_available_video_rows(db) -> List[Tuple[int, str]]:
    cur = await db.execute("SELECT id, file_id FROM files WHERE file_type = 'video'")
    rows = await cur.fetchall()
    return rows


async def increment_send_count(db, user_id: int, file_db_id: int):
    await db.execute(
        """
        INSERT INTO user_file_send_count (user_id, file_db_id, send_count)
        VALUES (?, ?, 1)
        ON CONFLICT(user_id, file_db_id) DO UPDATE SET send_count = user_file_send_count.send_count + 1
        """,
        (user_id, file_db_id),
    )
    await db.commit()


async def get_send_count(db, user_id: int, file_db_id: int) -> int:
    cur = await db.execute("SELECT send_count FROM user_file_send_count WHERE user_id=? AND file_db_id=?", (user_id, file_db_id))
    row = await cur.fetchone()
    return int(row[0]) if row else 0


# semaphore to limit concurrent sends and reduce rate limit risk
send_semaphore = asyncio.Semaphore(3)


async def send_random_videos(context: ContextTypes.DEFAULT_TYPE, user_id: int, count: int = 10):
    """Sends up to `count` random videos to `user_id` respecting per-file send limits."""
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await fetch_available_video_rows(db)
        if not rows:
            await context.bot.send_message(chat_id=user_id, text="No videos available at the moment. Please try later.")
            return
        # prefer videos that the user hasn't seen much
        random.shuffle(rows)
        chosen = []
        for (fid_db_id, file_id) in rows:
            sc = await get_send_count(db, user_id, fid_db_id)
            if sc < MAX_SEND_PER_FILE_PER_USER:
                chosen.append((fid_db_id, file_id))
            if len(chosen) >= count:
                break
        if not chosen:
            await context.bot.send_message(chat_id=user_id, text="You have already received all available videos the allowed number of times.")
            return

        # send in small groups if possible (media_group)
        medias = []
        sent = 0
        for fid_db_id, file_id in chosen:
            # send individually but respect semaphore
            async with send_semaphore:
                try:
                    await context.bot.send_chat_action(chat_id=user_id, action=ChatAction.UPLOAD_VIDEO)
                    await context.bot.send_video(chat_id=user_id, video=file_id)
                    await increment_send_count(db, user_id, fid_db_id)
                    sent += 1
                    # small pause
                    await asyncio.sleep(VIDEO_BATCH_SLEEP)
                except Exception as e:
                    logger.exception("Failed to send video to %s: %s", user_id, e)
        await context.bot.send_message(chat_id=user_id, text=f"✅ Sent {sent} videos. Use the buttons in chat to request more.")


# ---------------------------- CALLBACK QUERY HANDLER (UI) ----------------------------
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles many inline-button actions."""
    query = update.callback_query
    if not query:
        return
    await query.answer()
    data = query.data or ""
    user = query.from_user

    # JOIN CHANNELS -> show registered channels and 'I've Joined'
    if data == "join_channels":
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT chat_id, title, username FROM channels")
            rows = await cur.fetchall()
            if not rows:
                await query.message.reply_text("No channels/groups have been registered yet by admin.")
                return
            kb = []
            for (chat_id, title, username) in rows:
                # create join url if public username exists
                if username:
                    join_url = f"https://t.me/{username}"
                    kb.append([InlineKeyboardButton(f"🔗 Join: {title or username}", url=join_url)])
                else:
                    # private or by id - attempt to show a textual hint
                    kb.append([InlineKeyboardButton(f"🔗 Join: {title or chat_id}", callback_data="no_op")])
            kb.append([InlineKeyboardButton("✅ I've Joined", callback_data="verify_joined")])
            await query.message.reply_text("Please join the channels below then press 'I've Joined':", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data == "verify_joined":
        # verify membership for all registered channels - requires bot be able to see membership
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT chat_id FROM channels")
            rows = await cur.fetchall()
            if not rows:
                await query.message.reply_text("No channels registered to verify.")
                return
            missing = []
            for (chat_id,) in rows:
                try:
                    mem = await context.bot.get_chat_member(chat_id, user.id)
                    if mem.status in ("left", "kicked"):
                        ch = await context.bot.get_chat(chat_id)
                        missing.append(ch.title or ch.username or str(chat_id))
                except Exception:
                    # could not check - treat as missing
                    try:
                        ch = await context.bot.get_chat(chat_id)
                        missing.append(ch.title or ch.username or str(chat_id))
                    except Exception:
                        missing.append(str(chat_id))
            if missing:
                await query.message.reply_text("❌ You didn't join these:\n" + "\n".join(missing) + "\nPlease join them and press 'I've Joined' again.")
                return
            # verified
            await query.message.reply_text("✅ Verified! Sending your initial videos...")
            # send 10 random videos
            await send_random_videos(context, user.id, 10)
            # send unlock button for more (referral unlock)
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔁 More videos (share/referral)", callback_data="more_videos_step1")]])
            await context.bot.send_message(chat_id=user.id, text="Want more videos? Share your referral link to unlock more.", reply_markup=kb)
        return

    if data == "more_videos_step1":
        # create referral link that uses /start=ref<user_id>
        link = f"https://t.me/{context.bot.username}?start=ref{user.id}"
        text = (
            f"Share this referral link with 1 friend and ask them to /start the bot and join the channels:\n\n"
            f"{link}\n\nWhen one friend does that, you'll get *3 more videos*."
        )
        await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        return

    if data.startswith("admin_request_accept:"):
        if not await is_admin(user.id):
            await query.message.reply_text("Only admin can accept requests.")
            return
        tid = int(data.split(":", 1)[1])
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR REPLACE INTO users (user_id, is_coadmin) VALUES (?, 1)", (tid,))
            await db.commit()
        await query.message.reply_text("✅ Request accepted. User promoted to co-admin.")
        try:
            await context.bot.send_message(tid, "🎉 Your request was accepted. You are now a co-admin.")
        except Exception:
            pass
        return

    if data.startswith("admin_request_reject:"):
        if not await is_admin(user.id):
            await query.message.reply_text("Only admin can reject requests.")
            return
        tid = int(data.split(":", 1)[1])
        await query.message.reply_text("❌ Request rejected.")
        try:
            await context.bot.send_message(tid, "Your co-admin request was rejected by the admin.")
        except Exception:
            pass
        return

    if data == "request_admin":
        # send admin a nicely formatted request with accept/reject inline buttons
        info = f"User: [{user.full_name}](tg://user?id={user.id})\nID: `{user.id}`\nUsername: @{user.username or '—'}"
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Make Admin", callback_data=f"admin_request_accept:{user.id}"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"admin_request_reject:{user.id}"),
                ]
            ]
        )
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text="Co-admin request:\n\n" + info, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        await query.message.reply_text("✅ Your request has been sent to the admin.")
        return

    if data == "my_profile":
        await query.message.reply_text(f"Name: {user.full_name}\nID: `{user.id}`\nUsername: @{user.username or '—'}", parse_mode=ParseMode.MARKDOWN)
        return

    # Admin user management UI: open manage menu
    if data.startswith("user_action:"):
        if not await is_admin(user.id):
            await query.message.reply_text("Only admin may manage users.")
            return
        target = int(data.split(":", 1)[1])
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Block 🚫", callback_data=f"block_user:{target}"),
                 InlineKeyboardButton("Unblock ✅", callback_data=f"unblock_user:{target}")],
                [InlineKeyboardButton("Mute 🔇 (1h)", callback_data=f"mute_user:{target}"),
                 InlineKeyboardButton("Kick ❌", callback_data=f"kick_user:{target}")],
                [InlineKeyboardButton("Message ✉️", callback_data=f"msg_user:{target}")]
            ]
        )
        await query.message.reply_text(f"Manage user `{target}`", parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        return

    if data.startswith("block_user:"):
        if not await is_admin(user.id):
            await query.message.reply_text("Only admin may block users.")
            return
        tid = int(data.split(":", 1)[1])
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR REPLACE INTO users (user_id, is_blocked) VALUES (?, 1)", (tid,))
            await db.commit()
        await query.message.reply_text("User blocked.")
        try:
            await context.bot.send_message(tid, "You have been blocked by the admin.")
        except Exception:
            pass
        return

    if data.startswith("unblock_user:"):
        if not await is_admin(user.id):
            await query.message.reply_text("Only admin may unblock users.")
            return
        tid = int(data.split(":", 1)[1])
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR REPLACE INTO users (user_id, is_blocked) VALUES (?, 0)", (tid,))
            await db.commit()
        await query.message.reply_text("User unblocked.")
        try:
            await context.bot.send_message(tid, "You have been unblocked by the admin.")
        except Exception:
            pass
        return

    if data.startswith("mute_user:"):
        if not await is_admin(user.id):
            await query.message.reply_text("Only admin may mute users.")
            return
        tid = int(data.split(":", 1)[1])
        until = int(time.time()) + 3600  # 1 hour mute
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR REPLACE INTO users (user_id, muted_until) VALUES (?, ?)", (tid, until))
            await db.commit()
        await query.message.reply_text("User muted for 1 hour.")
        try:
            await context.bot.send_message(tid, "You are muted for 1 hour by the admin.")
        except Exception:
            pass
        return

    if data.startswith("kick_user:"):
        if not await is_admin(user.id):
            await query.message.reply_text("Only admin may kick users.")
            return
        tid = int(data.split(":", 1)[1])
        # Kicking from groups requires context of which group; inform admin
        await query.message.reply_text("Kick operation noted. To kick from a group, use group admin tools or give bot admin rights in that group and implement a kick command.")
        return

    if data.startswith("msg_user:"):
        if not await is_admin(user.id):
            await query.message.reply_text("Only admin may message users this way.")
            return
        tid = int(data.split(":", 1)[1])
        await query.message.reply_text("Use /singlemsg <user_id> <message> to send a message to this user.")
        return

    # no-op placeholder
    if data == "no_op":
        await query.message.reply_text("This button is an informational placeholder.")
        return

    # unknown callback
    await query.message.reply_text("Unknown action.")


# ---------------------------- ADMIN FUNCTIONS ----------------------------
async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/users -> list users with small 'Manage' button for admin"""
    user = update.effective_user
    if not await is_admin(user.id):
        await update.message.reply_text("Only admin can use this command.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, is_coadmin, is_blocked, muted_until, first_seen FROM users ORDER BY first_seen DESC")
        rows = await cur.fetchall()
        if not rows:
            await update.message.reply_text("No users tracked yet.")
            return
        # show up to 20 users (pagination can be added)
        for uid, is_co, is_blk, muted_until, first_seen in rows:
            text = f"• User: `{uid}`\nCo-admin: {bool(is_co)}\nBlocked: {bool(is_blk)}\nMuted until: {muted_until or '—'}"
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("Manage", callback_data=f"user_action:{uid}")]])
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def singlemsg_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/singlemsg <user_id> <message> - admin only direct message"""
    user = update.effective_user
    if not await is_admin(user.id):
        await update.message.reply_text("Only admin may use this command.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /singlemsg <user_id> <message>")
        return
    try:
        tid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user id.")
        return
    message_text = " ".join(context.args[1:])
    try:
        await context.bot.send_message(tid, message_text)
        await update.message.reply_text("Message sent.")
    except Exception as e:
        await update.message.reply_text(f"Failed to send: {e}")


# ---------------------------- UNKNOWN / DEFAULT HANDLERS ----------------------------
async def unknown_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Unknown command. Use /start to begin.")


# ---------------------------- STARTUP & TEARDOWN ----------------------------
async def on_startup(app: Application):
    logger.info("Bot startup: initializing DB and commands.")
    await init_db()
    # optionally set bot commands
    commands = [
        BotCommand("start", "Start / welcome"),
        BotCommand("auth", "Authenticate as co-admin: /auth <password>"),
        BotCommand("chgpass", "Change admin password (admin only)"),
        BotCommand("addchn", "Add channel by URL"),
        BotCommand("addchid", "Add channel by chat id"),
        BotCommand("advid", "Save a video (reply to video)"),
        BotCommand("addfile", "Save a file (reply to file)"),
        BotCommand("users", "Admin: list users"),
        BotCommand("singlemsg", "Admin: send a direct message to a user"),
    ]
    try:
        await app.bot.set_my_commands(commands)
    except Exception as e:
        logger.warning("Failed to set commands: %s", e)


async def on_shutdown(app: Application):
    logger.info("Bot shutting down...")


# ---------------------------- APPLICATION SETUP ----------------------------
def build_application() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    # register handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("auth", auth_command))
    app.add_handler(CommandHandler("chgpass", chgpass_command))
    app.add_handler(CommandHandler("addchn", addchn_command))
    app.add_handler(CommandHandler("addchid", addchid_command))
    app.add_handler(CommandHandler("advid", advid_command))
    app.add_handler(CommandHandler("addfile", addfile_command))
    app.add_handler(CommandHandler("users", users_command))
    app.add_handler(CommandHandler("singlemsg", singlemsg_command))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_handler))

    app.post_init = on_startup
    app.post_shutdown = on_shutdown
    return app


# ---------------------------- ENTRYPOINT ----------------------------
if __name__ == "__main__":
    # Safeguard: ensure user has edited BOT_TOKEN and ADMIN_CHAT_ID
    if BOT_TOKEN.startswith("PUT_YOUR_") or ADMIN_CHAT_ID == 123456789:
        print("Please edit main.py and set BOT_TOKEN and ADMIN_CHAT_ID before running.")
        print("Exiting.")
        raise SystemExit(1)

    app = build_application()
    # run_polling manages the asyncio event loop itself (works in Termux/Jupyter better than asyncio.run wrapper)
    print("Starting bot with app.run_polling() ...")
    try:
        app.run_polling()
    except (KeyboardInterrupt, SystemExit):
        print("Exiting...")
