import os
import random
import requests
import json
from typing import Dict, Any, List, Optional, Tuple

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember, constants
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)
from telegram.error import BadRequest
import logging

# --- 1. CONFIGURATION AND CONSTANTS ---

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Replace with your actual values. Note: In a production setting, always use environment variables.
BOT_TOKEN = "8014367494:AAGPMX5DMQQueZnPVOXmOF3DRek_SzxWbg8"
ADMIN_CHAT_ID = 8156053366
DEFAULT_PASSWORD = "Sahil@8896"
# NOTE: The provided Gemini API Key is public and may not be functional.
# Replace with a valid, secure key.
GEMINI_API_KEY = "AIzaSyDEDi4LZQsLlxdiMaiekJ1OMkFKGQsNeKw" 
BOT_USERNAME = "@sec_hubbot" # IMPORTANT: Update this (e.g., @MyBotName)

# Content Tiers
TIERS = [
    {"refs_required": 1, "reward_count": 3, "prompt": "1 more friend"},
    {"refs_required": 5, "reward_count": 10, "prompt": "5 more friends"},
    {"refs_required": 10, "reward_count": 15, "prompt": "10 more friends"},
    # You can extend this for more content
]

# --- 2. DATABASE SIMULATION CLASS (In-Memory) ---
# NOTE: This data is NOT persistent. For production, replace this with a proper
# database (e.g., Firebase Firestore, PostgreSQL) interface.

class BotDB:
    """Manages all in-memory data for the bot."""
    def __init__(self):
        # Key: User ID (int)
        # Value: { 'username': str, 'is_admin': bool, 'is_co_admin': bool, 
        #          'referral_count': int, 'referred_users': list[int],
        #          'videos_sent': list[str], 'blocked': bool, 'has_started': bool }
        self._users: Dict[int, Dict[str, Any]] = {}

        # Key: ID or URL (str)
        # Value: { 'type': 'id' or 'url', 'value': str }
        self._channels: Dict[str, Dict[str, str]] = {}

        # Key: Video/File ID (str)
        # Value: { 'type': 'video' or 'document', 'file_id': str, 'used_count': int }
        self._content: Dict[str, Dict[str, Any]] = {}
        
        # Admin password
        self._password: str = DEFAULT_PASSWORD
        
        # Initialize the main admin
        self.get_user(ADMIN_CHAT_ID, init=True)

    # --- User Management ---
    def get_user(self, user_id: int, username: Optional[str] = None, init: bool = False) -> Dict[str, Any]:
        """Retrieves or initializes user data."""
        if user_id not in self._users or init:
            self._users[user_id] = {
                'username': username,
                'is_admin': (user_id == ADMIN_CHAT_ID),
                'is_co_admin': False,
                'referral_count': 0,
                'referred_users': [],
                'videos_sent': [],
                'blocked': False,
                'has_started': False
            }
        
        if username and self._users[user_id]['username'] != username:
             self._users[user_id]['username'] = username

        return self._users[user_id]

    def get_all_users(self) -> Dict[int, Dict[str, Any]]:
        """Returns all users."""
        return self._users
        
    def is_authorized(self, user_id: int) -> bool:
        """Checks if the user is the main admin or a co-admin."""
        user_data = self.get_user(user_id)
        return user_data['is_admin'] or user_data['is_co_admin']
        
    def add_referral(self, referrer_id: int, referred_id: int) -> bool:
        """Increments referral count if not already referred."""
        referrer_data = self.get_user(referrer_id)
        if referred_id not in referrer_data['referred_users']:
            referrer_data['referred_users'].append(referred_id)
            referrer_data['referral_count'] += 1
            return True
        return False

    # --- Password Management ---
    def set_password(self, new_pass: str) -> None:
        """Sets a new admin password."""
        self._password = new_pass

    def check_password(self, input_pass: str) -> bool:
        """Checks if the input matches the current password."""
        return self._password == input_pass
        
    # --- Channel Management ---
    def add_channel(self, key: str, value: str, type: str) -> None:
        """Adds a channel/group to the required list."""
        self._channels[key] = {'type': type, 'value': value}

    def get_channels(self) -> Dict[str, Dict[str, str]]:
        """Returns all required channels/groups."""
        return self._channels
        
    # --- Content Management ---
    def add_content(self, file_id: str, type: str) -> bool:
        """Stores new content."""
        if file_id in self._content:
            return False
        self._content[file_id] = {'type': type, 'file_id': file_id, 'used_count': 0}
        return True

    def get_random_content(self, user_id: int, count: int) -> List[Dict[str, Any]]:
        """Selects random content with repetition limit."""
        user_data = self.get_user(user_id)
        
        # 1. Prioritize content used less than 2 times globally
        available_content = [
            cid for cid, data in self._content.items() 
            if data['used_count'] < 2
        ]
        
        # 2. If pool is exhausted, reset used_count for everyone and allow everything again
        if not available_content and len(self._content) > 0:
            logger.info("Content pool exhausted. Resetting content usage.")
            for data in self._content.values():
                data['used_count'] = 0
            user_data['videos_sent'] = []
            available_content = list(self._content.keys())

        if not available_content:
            return []

        # 3. Filter out content the user has just seen (in the current session's videos_sent list)
        # This is a soft filter, as we mainly rely on global count < 2
        
        # Select random, unique videos from the available pool
        selected_content_ids = random.sample(available_content, min(count, len(available_content)))
        
        # 4. Update tracking
        selected_content = []
        for content_id in selected_content_ids:
            if content_id in self._content:
                self._content[content_id]['used_count'] += 1
                user_data['videos_sent'].append(content_id)
                selected_content.append(self._content[content_id])
                
        return selected_content

    def get_content_count(self) -> int:
        return len(self._content)

# Initialize the global database instance
DB = BotDB()

# --- 3. UI/KEYBOARD GENERATION FUNCTIONS (Best Look UI) ---

def _get_start_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Generates the main start keyboard for channel joining and co-admin request."""
    user_data = DB.get_user(user_id)
    
    channel_buttons = []
    channels = DB.get_channels()
    
    if channels:
        for i, (key, channel) in enumerate(channels.items()):
            btn_text = f"🔗 Channel {i+1} ({key})"
            # Format the URL correctly
            url = channel['value'] if channel['type'] == 'url' else f"https://t.me/c/{channel['value'].replace('-100', '')}"
            channel_buttons.append([InlineKeyboardButton(btn_text, url=url)])

    # Co-Admin Request Button
    co_admin_btn = []
    if not user_data['is_admin'] and not user_data['is_co_admin']:
        co_admin_btn = [
            [
                InlineKeyboardButton("👑 Request Co-Admin Status 💖", callback_data=f"request_admin_{user_id}")
            ]
        ]
    
    # Check Joined Button (MANDATORY for proceeding)
    check_btn = [
        [
            InlineKeyboardButton("✅ I Have Joined (Verify Now)", callback_data="check_joined")
        ]
    ]
    
    # Combined keyboard
    keyboard = channel_buttons + co_admin_btn + check_btn
    return InlineKeyboardMarkup(keyboard)

def _get_referral_keyboard(user_id: int, required_refs: int, reward_count: int, referral_link: str) -> InlineKeyboardMarkup:
    """Generates the keyboard for content rewards and sharing."""
    
    # Share button setup
    share_button = InlineKeyboardButton(
        "💌 Share Bot Link & Start Bot", 
        url=f"https://t.me/share/url?url={referral_link}&text=🌟%20Unlock%20awesome%20content%20in%20this%20bot!%20Join%20me%20now!%20"
    )
    
    # Check progress button
    progress_button = InlineKeyboardButton(
        f"🎁 Show More Content (Need {required_refs} more)", 
        callback_data=f"show_more_{user_id}"
    )

    keyboard = [
        [share_button],
        [progress_button]
    ]
    return InlineKeyboardMarkup(keyboard)

def _get_admin_decision_keyboard(target_id: int) -> InlineKeyboardMarkup:
    """Keyboard for admin to approve/reject co-admin requests."""
    keyboard = [
        [
            InlineKeyboardButton("✅ Make Co-Admin 👑", callback_data=f"approve_admin_{target_id}"),
            InlineKeyboardButton("❌ Reject Request 🚫", callback_data=f"reject_admin_{target_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def _get_admin_user_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Generates the list of users for the admin panel."""
    keyboard = []
    
    # Sort users by username for better navigation
    sorted_users: List[Tuple[int, Dict[str, Any]]] = sorted([
        (uid, data) for uid, data in DB.get_all_users().items() 
        if uid != user_id
    ], key=lambda x: x[1].get('username', 'z'))

    # Display up to 20 users per page (simplified for single-file demo)
    for uid, user_data in sorted_users[:20]:
        status = "👑" if user_data.get('is_co_admin') else "👤"
        status += " 🚫" if user_data.get('blocked') else ""
        name = user_data.get('username') or f"User {uid}"
        
        btn_text = f"{status} {name} (ID: {uid})"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"user_list_action_{uid}")])

    # Back button is added in the handler if needed
    return InlineKeyboardMarkup(keyboard)

def _get_user_management_keyboard(target_id: int) -> InlineKeyboardMarkup:
    """Generates the sub-menu for managing a specific user."""
    target_data = DB.get_user(target_id)
    is_blocked = target_data.get('blocked', False)
    
    block_status = "✅ Unblock User" if is_blocked else "🚫 Block User"
    
    keyboard = [
        [InlineKeyboardButton(block_status, callback_data=f"admin_action_toggleblock_{target_id}")],
        [InlineKeyboardButton("📣 Single Broadcast 💬", callback_data=f"admin_action_broadcast_{target_id}")],
        # Mute/Kick are context-dependent (groups), simplifying to primary actions
        [InlineKeyboardButton("⬅️ Back to User List", callback_data="admin_action_back")]
    ]
    return InlineKeyboardMarkup(keyboard)

# --- 4. GEMINI AI INTEGRATION ---

async def gemini_ai_response(text_prompt: str) -> str:
    """Fetches a response from the Gemini 2.5 Flash API."""
    if not GEMINI_API_KEY:
        logger.error("Gemini API Key is not set.")
        return "🤖 Gemini AI is not configured. Please set a valid API key."

    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    
    # Stylish and friendly system instruction
    system_instruction = "You are a highly stylish, enthusiastic, and sophisticated AI assistant. Respond attractively, using relevant emojis (like 💡, ✨, 🚀) and maintaining a helpful, concise tone. You are integrated into a powerful Telegram Bot designed for exclusive community management."

    payload = {
        "contents": [{"parts": [{"text": text_prompt}]}],
        "systemInstruction": {"parts": [{"text": system_instruction}]},
    }
    
    # Exponential backoff retry logic (simplified for single-file context)
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.post(api_url, headers={'Content-Type': 'application/json'}, json=payload, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            if data.get('candidates') and data['candidates'][0].get('content'):
                return data['candidates'][0]['content']['parts'][0]['text']
            else:
                logger.warning(f"Gemini returned empty or malformed content: {data}")
                return "❌ I'm having trouble processing that thought right now. Try a different question! 🧐"

        except requests.exceptions.RequestException as e:
            logger.error(f"Gemini API Error (Attempt {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                import time
                time.sleep(2 ** attempt) # Exponential backoff
            else:
                return f"🚨 Error connecting to the AI after multiple attempts. Please try again later. ({e})"
    
    return "Something went wrong in the AI processing pipeline." # Should be unreachable

# --- 5. COMMAND HANDLERS ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a stylish welcome message, checks user status, and handles referrals."""
    user = update.effective_user
    user_id = user.id
    username = user.username or user.first_name
    
    user_data = DB.get_user(user_id, username=username)

    referral_check_message = ""
    # 1. Referral Check (if start payload exists and user is not referring themselves)
    if context.args and user_id != ADMIN_CHAT_ID: # Prevent admin self-referral
        try:
            referrer_id = int(context.args[0])
            if DB.add_referral(referrer_id, user_id):
                referrer_data = DB.get_user(referrer_id)
                referrer_name = referrer_data.get('username', f"User {referrer_id}")
                
                # Notify the referrer
                await context.bot.send_message(
                    chat_id=referrer_id, 
                    text=f"🥳 **Success!** User @{username} has started the bot using your link! Your referral count is now **{referrer_data['referral_count']}**! You have earned more videos!",
                    parse_mode='Markdown'
                )
                referral_check_message = "\n\n🎉 You were referred by a friend! Welcome to our exclusive community! ✨"
        except (ValueError, BadRequest):
            logger.warning(f"Invalid referral ID in start payload or referrer blocked: {context.args[0]}")
            
    # 2. Welcome Message Style
    if not user_data['has_started']:
        # First-time user
        user_data['has_started'] = True
        welcome_text = (
            f"💖 **HELLO, {username.upper()}!** 💖\n\n"
            f"Welcome to the **Advanced Bot Hub!** I'm thrilled to have you here. This is your personal gateway to exclusive content.\n"
            f"To unlock everything, please **join the channels** below and verify your membership. Let's get started!"
        )
    else:
        # Returning user
        welcome_text = (
            f"🌟 **Welcome Back, {username}!** 🌟\n\n"
            f"You've already started the bot and are part of the exclusive club. Great to see you again!\n"
            f"Ready to dive back into the exclusive content? Click 'Verify Now' to check your membership status and continue."
        )

    # 3. Send message with stylish keyboard
    reply_markup = _get_start_keyboard(user_id)

    await update.message.reply_text(
        f"{welcome_text}{referral_check_message}",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    
async def change_password_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Allows the main admin to change the authentication password."""
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("🚫 **ACCESS DENIED.** Only the main admin can change the bot password.")
        return
    
    if len(context.args) == 0:
        await update.message.reply_text("🔑 **Usage:** `/chgpass <new_secret_password>`")
        return
    
    new_pass = context.args[0]
    DB.set_password(new_pass)
    
    await update.message.reply_text(
        f"✅ **PASSWORD CHANGED!** Your new secret authentication password is: `{new_pass}`. "
        "Use `/auth <new_password>` for co-admin authentication.",
        parse_mode='Markdown'
    )

async def authenticate_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Authenticates a user as a co-admin."""
    user_id = update.effective_user.id
    
    if DB.is_authorized(user_id):
        await update.message.reply_text("🌟 You are already authenticated as an admin or co-admin!")
        return
    
    if len(context.args) == 0:
        await update.message.reply_text("🔑 **Usage:** `/auth <secret_password>`")
        return
    
    input_pass = context.args[0]

    if DB.check_password(input_pass):
        user_data = DB.get_user(user_id)
        user_data['is_co_admin'] = True
        await update.message.reply_text(
            "🎉 **AUTHENTICATION SUCCESS!** You are now a co-admin. 👑 "
            "You have access to powerful commands like `/advid`, `/addfile`, `/addchn`, `/admin` and more! "
            "Welcome to the management team! 🚀"
        )
    else:
        await update.message.reply_text("❌ **Authentication Failed.** The provided password is incorrect. Please try again.")

async def add_channel_url_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Adds a channel/group via URL."""
    if not DB.is_authorized(update.effective_user.id):
        await update.message.reply_text("🔒 You must be authenticated as an admin or co-admin to use this command. Use `/auth <password>`.")
        return
    
    if len(context.args) == 0:
        await update.message.reply_text("🔗 **Usage:** `/addchn <Channel or Group URL>` (e.g., `https://t.me/telegram` or `@channelusername`)")
        return
    
    url = context.args[0]
    key = url.strip().replace('https://t.me/', '').replace('@', '')
    DB.add_channel(key, url, 'url')
    
    await update.message.reply_text(
        f"✅ **CHANNEL ADDED!** URL stored successfully: `{url}`. \n"
        "Remember to add the bot as an admin to this channel/group so it can verify user membership!",
        parse_mode='Markdown'
    )

async def add_channel_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Adds a channel/group via Chat ID."""
    if not DB.is_authorized(update.effective_user.id):
        await update.message.reply_text("🔒 You must be authenticated as an admin or co-admin to use this command. Use `/auth <password>`.")
        return
    
    if len(context.args) == 0:
        await update.message.reply_text("🆔 **Usage:** `/addchid <Channel or Group Chat ID>` (e.g., `-100123456789`)")
        return
    
    chat_id = context.args[0]
    DB.add_channel(chat_id, chat_id, 'id')
    
    await update.message.reply_text(
        f"✅ **CHANNEL ADDED!** Chat ID stored successfully: `{chat_id}`. \n"
        "Remember to add the bot as an admin to this channel/group so it can verify user membership!",
        parse_mode='Markdown'
    )

async def add_video_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stores a video that the admin replies to."""
    if not DB.is_authorized(update.effective_user.id):
        await update.message.reply_text("🔒 You must be authenticated to use this command.")
        return
    
    if not update.message.reply_to_message or not update.message.reply_to_message.video:
        await update.message.reply_text("🎥 **Usage:** Reply directly to the video you want to store and use `/advid`.")
        return
    
    video_file_id = update.message.reply_to_message.video.file_id
    
    if DB.add_content(video_file_id, 'video'):
        await update.message.reply_text("🎉 **Video Stored!** This video is now part of the exclusive content pool. Users can unlock it through referrals.")
    else:
        await update.message.reply_text("👀 **Duplicate Content!** This video has already been stored.")

async def add_file_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stores a file (document) that the admin replies to."""
    if not DB.is_authorized(update.effective_user.id):
        await update.message.reply_text("🔒 You must be authenticated to use this command.")
        return
    
    if not update.message.reply_to_message or not update.message.reply_to_message.document:
        await update.message.reply_text("📄 **Usage:** Reply directly to the file (document) you want to store and use `/addfile`.")
        return
    
    file_id = update.message.reply_to_message.document.file_id
    
    if DB.add_content(file_id, 'document'):
        await update.message.reply_text("💾 **File Stored!** This file is now part of the exclusive content pool. Users can unlock it through referrals.")
    else:
        await update.message.reply_text("👀 **Duplicate Content!** This file has already been stored.")

async def admin_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows a list of all users for the admin to manage."""
    if not DB.is_authorized(update.effective_user.id):
        await update.message.reply_text("🔒 **ACCESS DENIED.** Only authenticated admins/co-admins can access the user panel.")
        return

    # Check if there are users other than the current user
    if len(DB.get_all_users()) <= 1:
        await update.message.reply_text("The user database is currently empty (only you are registered).")
        return

    reply_markup = _get_admin_user_keyboard(update.effective_user.id)
    
    await update.message.reply_text(
        "🛠 **ADMIN USER MANAGEMENT PANEL** 🛠\n\n"
        "**User List:** Select a user below to perform management actions (Block, Broadcast, etc.):",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def cancel_broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancels the active single broadcast mode."""
    if context.user_data.get('next_message_is_broadcast'):
        del context.user_data['next_message_is_broadcast']
        await update.message.reply_text("❌ **Single Broadcast Mode Cancelled.** You can now send normal messages or use other commands.")
    else:
        await update.message.reply_text("📢 **No Active Broadcast.** Nothing to cancel.")

# --- 6. CALLBACK QUERY HANDLERS (Inline Button Actions) ---

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles all inline button clicks."""
    query = update.callback_query
    await query.answer() # Always answer the query to dismiss the loading state
    data = query.data
    user_id = query.from_user.id
    
    logger.info(f"Callback received: {data} from user {user_id}")
    
    # 1. Co-Admin Request Flow
    if data.startswith("request_admin_"):
        await _handle_admin_request(query, context, user_id, int(data.split('_')[-1]))
    elif data.startswith("approve_admin_") or data.startswith("reject_admin_"):
        await _handle_admin_decision(query, context, data)
        
    # 2. Channel Verification Flow
    elif data == "check_joined":
        await _check_joined(query, context)
        
    # 3. Referral/Content Flow
    elif data.startswith("show_more_"):
        await _send_referral_prompt(query, context, initial=False)
        
    # 4. Admin Management Flow
    elif data.startswith("user_list_action_"):
        target_id = int(data.split('_')[-1])
        await query.edit_message_text(
            f"**Manage User ID:** `{target_id}`",
            reply_markup=_get_user_management_keyboard(target_id),
            parse_mode='Markdown'
        )
    elif data.startswith("admin_action_"):
        await _handle_admin_user_action(query, context, data)

# --- Co-Admin Request Logic ---

async def _handle_admin_request(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE, requester_id: int, original_requester_id: int) -> None:
    """Sends the co-admin request to the main admin."""
    # Safety check
    if requester_id != original_requester_id:
        await query.message.reply_text("🚫 You can only request co-admin status for yourself.")
        return

    requester_user = DB.get_user(requester_id)
    requester_name = query.from_user.full_name
    
    if requester_user['is_admin'] or requester_user['is_co_admin']:
        await query.message.reply_text("🌟 You are already an admin or co-admin! No request needed.")
        try:
             await query.edit_message_reply_markup(reply_markup=None)
        except BadRequest:
             pass
        return

    # Message to Admin
    reply_markup = _get_admin_decision_keyboard(requester_id)

    admin_message = (
        f"👑 **NEW CO-ADMIN REQUEST RECEIVED** 💖\n\n"
        f"**User Details:**\n"
        f"  - Name: {requester_name}\n"
        f"  - ID: `{requester_id}`\n"
        f"  - Username: @{query.from_user.username or 'N/A'}\n\n"
        f"**Action Required:** Please approve or reject the request below."
    )
    
    try:
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=admin_message,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        await query.message.reply_text("💌 **REQUEST SENT!** Your request for co-admin status has been successfully sent to the main admin. Please wait for their attractive reply! ⏳")
    except BadRequest as e:
        logger.error(f"Failed to send admin message: {e}")
        await query.message.reply_text("🚨 **REQUEST FAILED!** Could not reach the main admin. They might have blocked the bot or the Chat ID is wrong.")
    
async def _handle_admin_decision(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    """Processes the main admin's decision on a co-admin request."""
    admin_id = query.from_user.id
    if admin_id != ADMIN_CHAT_ID:
        await query.message.reply_text("🚫 **ACCESS DENIED.** Only the main admin can make this decision.")
        return
        
    action, _, target_id_str = data.split('_')
    target_id = int(target_id_str)
    target_data = DB.get_user(target_id)
    target_name = target_data.get('username', f'User {target_id}')

    # Decision logic
    if action == "approve":
        target_data['is_co_admin'] = True
        response_text = f"✅ **APPROVED!** User @{target_name} (`{target_id}`) is now a co-admin. 👑"
        user_notification = "🎉 **CONGRATULATIONS!** The admin has approved your request! You are officially a co-admin and can now use management commands! 🚀"
    else: # reject
        target_data['is_co_admin'] = False # Ensure false
        response_text = f"❌ **REJECTED!** Co-Admin request from @{target_name} (`{target_id}`) has been rejected."
        user_notification = "😔 **Request Rejected.** The admin has declined your co-admin request. You can try again later or contact them for more details."

    # 1. Notify the admin and update the inline message
    await query.edit_message_text(f"**Action Completed:**\n{response_text}", parse_mode='Markdown')
    
    # 2. Notify the user
    try:
        await context.bot.send_message(chat_id=target_id, text=user_notification, parse_mode='Markdown')
    except BadRequest:
        await query.message.reply_text(f"⚠️ Could not notify user {target_id}. They might have blocked the bot.")

# --- Channel Membership & Content Delivery Logic ---

async def _check_joined(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Verifies channel membership for all required channels/groups."""
    user_id = query.from_user.id
    all_joined = True
    
    channels = DB.get_channels()
    if not channels:
        await query.message.reply_text("⚠️ No required channels have been configured by the admin yet. Proceeding to content...")
        await _send_initial_content(query, context)
        return

    # Check membership for all required channels
    for key, channel in channels.items():
        channel_id = channel['value']
        try:
            member: ChatMember = await context.bot.get_chat_member(chat_id=channel_id, user_id=user_id)
            if member.status in [ChatMember.LEFT, ChatMember.KICKED, ChatMember.BANNED]:
                all_joined = False
                break
        except Exception as e:
            logger.error(f"Failed to check membership for {channel_id}: {e}")
            await query.message.reply_text(f"🚨 **VERIFICATION ERROR!** Bot is unable to verify membership for channel: `{channel_id}`. Please ensure the bot is an **Admin** there and try again. Error: {e}", parse_mode='Markdown')
            return # Stop and return error

    # Respond to user
    if all_joined:
        await query.message.reply_text("🎉 **MEMBERSHIP VERIFIED SUCCESSFULLY!** All checks passed. Proceeding to your exclusive content...")
        await _send_initial_content(query, context)
    else:
        # Re-send the start message with original buttons to re-prompt joining
        await query.message.reply_text("❌ **VERIFICATION FAILED!** Please ensure you have **joined ALL** the required channels and groups, then click 'Verify Now' again.", reply_markup=_get_start_keyboard(user_id))

async def _send_content(context: ContextTypes.DEFAULT_TYPE, user_id: int, content_list: List[Dict[str, Any]], message_header: str) -> None:
    """Sends a list of content to the user."""
    await context.bot.send_message(chat_id=user_id, text=message_header, parse_mode='Markdown')
    
    for content in content_list:
        try:
            if content['type'] == 'video':
                await context.bot.send_video(chat_id=user_id, video=content['file_id'])
            elif content['type'] == 'document':
                await context.bot.send_document(chat_id=user_id, document=content['file_id'])
        except BadRequest as e:
            logger.error(f"Failed to send content {content['file_id']} to {user_id}: {e}")
            await context.bot.send_message(chat_id=user_id, text=f"⚠️ **Content Error:** Failed to send a file. The bot might have lost access to the file. ({e})")

async def _send_initial_content(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends the initial 10 videos/files."""
    user_id = query.from_user.id
    
    videos_to_send = DB.get_random_content(user_id, 10)
    
    if not videos_to_send:
        await query.message.reply_text("😞 **Empty Library:** I'm sorry, the exclusive content library is currently empty. Please check back later!")
        return

    header = f"🎬 **Here are your first {len(videos_to_send)} exclusive content pieces!** Enjoy the show! ✨"
    await _send_content(context, user_id, videos_to_send, header)
    
    # Then immediately prompt for referral to unlock more
    await _send_referral_prompt(query, context, initial=True)

async def _send_referral_prompt(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE, initial: bool = False) -> None:
    """Sends the referral prompt based on current referral count and checks for rewards."""
    user_id = query.from_user.id
    user_data = DB.get_user(user_id)
    current_refs = user_data['referral_count']
    
    # 1. Determine next goal and reward based on TIERS
    next_tier = None
    for tier in TIERS:
        if current_refs < tier['refs_required']:
            next_tier = tier
            break
    
    # If no next tier is found, it means the user has completed all defined tiers
    if not next_tier:
        await query.message.reply_text("🏆 **MAX REFERRAL TIER REACHED!** You've unlocked all current referral rewards. Thank you for your support! More content and tiers will be added soon. 🎉")
        return

    # 2. Check for reward unlock (only if not initial call and user met previous tier's requirement)
    if not initial:
        previous_tier_index = TIERS.index(next_tier) - 1
        # Calculate the required refs for the reward they just hit (not the next one)
        # For the first reward (tier 0), refs required is 1. If current_refs is 1, they get it.
        # If current_refs is 6, they passed tier 1 (req 5) and should get that reward.
        
        # Determine the total refs needed to unlock the *current* reward
        refs_needed_for_reward = TIERS[previous_tier_index]['refs_required'] if previous_tier_index >= 0 else 0
        
        if current_refs >= refs_needed_for_reward and refs_needed_for_reward > user_data.get('last_reward_ref_count', 0):
            reward_tier = TIERS[previous_tier_index] if previous_tier_index >= 0 else TIERS[0]
            reward_count = reward_tier['reward_count']
            
            reward_content = DB.get_random_content(user_id, reward_count)
            
            if not reward_content:
                await query.message.reply_text("🎉 You earned more content, but the library is currently empty or fully used. Check back soon!")
                user_data['last_reward_ref_count'] = current_refs # Mark as rewarded
                return
                
            header = f"🥳 **REWARD UNLOCKED!** You referred {current_refs} members and earned **{len(reward_content)}** more exclusive items! Claim your prize! 🎁"
            await _send_content(context, user_id, reward_content, header)
            
            # Update the last reward count to prevent repeated reward sending
            user_data['last_reward_ref_count'] = current_refs

    # 3. Generate prompt for the NEXT required action (always send this)
    required_refs = next_tier['refs_required'] - current_refs
    reward_count = next_tier['reward_count']
    referral_link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
    
    content_left = DB.get_content_count()
    
    prompt_text = (
        f"🔗 **SHARE TO UNLOCK {reward_count} MORE ITEMS!** 🔗\n\n"
        f"You currently have **{current_refs}** verified referrals. 🤩\n"
        f"To get your next **{reward_count}** exclusive videos/files, you need **{required_refs}** more users to join via your link!\n\n"
        f"**Your Personal Referral Link:**\n`{referral_link}`\n\n"
        f"*{content_left} unique items remaining in the library. Keep sharing!*"
    )
    
    reply_markup = _get_referral_keyboard(user_id, required_refs, reward_count, referral_link)
    
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=prompt_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    except BadRequest as e:
        logger.error(f"Failed to send referral prompt to user {user_id}: {e}")

# --- Admin User Management Logic ---

async def _handle_admin_user_action(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    """Handles block/unblock/broadcast initiation in the admin panel."""
    if not DB.is_authorized(query.from_user.id):
        await query.message.reply_text("🚫 Unauthorized action.")
        return

    parts = data.split('_')
    action = parts[2]
    
    if action == "back":
        await admin_users_command(query, context)
        return

    target_id = int(parts[3])
    target_data = DB.get_user(target_id)
    target_name = target_data.get('username', f'User {target_id}')

    if action == "toggleblock":
        target_data['blocked'] = not target_data.get('blocked', False)
        status = "BLOCKED" if target_data['blocked'] else "UNBLOCKED"
        
        # Notify target user
        try:
            await context.bot.send_message(
                chat_id=target_id, 
                text=f"🚨 **ACCOUNT STATUS UPDATE:** You have been **{status}** by the admin. You can no longer use the AI chat or unlock content.",
                parse_mode='Markdown'
            )
        except BadRequest:
             pass # User likely blocked the bot
             
        # Update admin message
        await query.edit_message_text(
            f"✅ **USER UPDATE:** User @{target_name} (`{target_id}`) has been **{status}**.",
            reply_markup=_get_user_management_keyboard(target_id),
            parse_mode='Markdown'
        )
    
    elif action == "broadcast":
        # Store context for the next message
        context.user_data['next_message_is_broadcast'] = target_id
        await query.message.reply_text(
            f"✍️ **SINGLE BROADCAST MODE ACTIVE** 📣\n\n"
            f"Your next message will be sent directly to user @{target_name} (`{target_id}`).\n"
            "**WARNING:** Use this responsibly. To cancel, send `/cancel_broadcast`."
        )
        try:
             await query.edit_message_reply_markup(reply_markup=None)
        except BadRequest:
             pass

# --- 7. MESSAGE HANDLER (Broadcast and AI Chat) ---

async def _handle_general_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles general text messages (for broadcast or Gemini AI)."""
    user_id = update.effective_user.id
    text = update.message.text
    
    if not text:
        return

    # 1. Handle single broadcast (Admin action)
    if 'next_message_is_broadcast' in context.user_data and DB.is_authorized(user_id):
        target_id = context.user_data.pop('next_message_is_broadcast')
        try:
            await context.bot.send_message(chat_id=target_id, text=f"**👑 Admin Message:**\n\n{text}", parse_mode='Markdown')
            await update.message.reply_text(f"✅ **MESSAGE SENT!** Your message was successfully broadcast to user `{target_id}`.")
        except BadRequest as e:
            await update.message.reply_text(f"❌ **BROADCAST FAILED!** Could not send message to user `{target_id}`. Error: {e}")
        return

    # 2. Handle Gemini AI Chat (General user interaction)
    user_data = DB.get_user(user_id)
    if user_data.get('blocked'):
        await update.message.reply_text("🚫 **BLOCKED.** You are currently blocked from using the bot's features, including the AI chat.")
        return
        
    # Send 'typing' status for better UX
    await context.bot.send_chat_action(chat_id=user_id, action=constants.ChatAction.TYPING)
    
    # Get AI response
    ai_response = await gemini_ai_response(text)
    
    await update.message.reply_text(ai_response, parse_mode='Markdown')

# --- 8. MAIN EXECUTION ---

def main() -> None:
    """Start the bot."""
    logger.info("Initializing Advanced Telegram Bot...")
    
    if BOT_USERNAME == "YourBotUsername":
        logger.error("!!! WARNING: Please update the BOT_USERNAME constant with your bot's actual username for referral links to work correctly. !!!")
        
    application = Application.builder().token(BOT_TOKEN).build()

    # --- Register Handlers ---

    # Commands
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("auth", authenticate_command))
    application.add_handler(CommandHandler("chgpass", change_password_command))
    application.add_handler(CommandHandler("addchn", add_channel_url_command))
    application.add_handler(CommandHandler("addchid", add_channel_id_command))
    application.add_handler(CommandHandler("advid", add_video_command))
    application.add_handler(CommandHandler("addfile", add_file_command))
    application.add_handler(CommandHandler("admin", admin_users_command))
    application.add_handler(CommandHandler("cancel_broadcast", cancel_broadcast_command))
    
    # Inline Button Callback Handler (for all inline buttons)
    application.add_handler(CallbackQueryHandler(handle_callback))

    # General Message Handler (for Gemini AI and Broadcast)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_general_message))

    # Start the Bot
    logger.info("Bot is fully configured and starting to poll for updates...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
