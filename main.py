import os
import random
import requests
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

# --- CONFIGURATION (REPLACE WITH YOUR ACTUAL VALUES) ---
# NOTE: It is HIGHLY RECOMMENDED to use environment variables for sensitive data.
BOT_TOKEN = "8014367494:AAGPMX5DMQQueZnPVOXmOF3DRek_SzxWbg8"  # Your Telegram Bot Token
ADMIN_CHAT_ID = 8156053366  # Your specific Admin Chat ID
DEFAULT_PASSWORD = "Sahil@8896" # Default password for /auth
GEMINI_API_KEY = "AIzaSyDEDi4LZQsLlxdiMaiekJ1OMkFKGQsNeKw" # Your Gemini API Key
BOT_USERNAME = "@sec_hubbot" # IMPORTANT: Replace with your bot's actual username (e.g., @MyAwesomeBot)

# --- DATABASE SIMULATION (Replace with Firestore/SQL in a production environment) ---
# In a real app, this data would be stored persistently (e.g., Firebase Firestore, SQLite).
# Key: User ID (int)
# Value: { 'username': str, 'is_admin': bool, 'is_co_admin': bool, 'referral_count': int, 'blocked': bool, 'videos_sent': list[str] }
USERS_DB = {}

# Key: ID or URL (str)
# Value: { 'type': 'id' or 'url', 'value': str }
CHANNELS_DB = {} # Stored channels/groups that users MUST join

# Key: Video/File ID (str)
# Value: { 'type': 'video' or 'document', 'file_id': str, 'used_count': int }
CONTENT_DB = {}

# Key: User ID (int)
# Value: str (password hash in a real app)
ADMIN_PASSWORD = DEFAULT_PASSWORD

# --- UTILITY FUNCTIONS ---

def get_user_data(user_id):
    """Retrieves user data or initializes a new user."""
    return USERS_DB.setdefault(user_id, {
        'username': None,
        'is_admin': (user_id == ADMIN_CHAT_ID), # Admin is fixed
        'is_co_admin': False,
        'referral_count': 0,
        'blocked': False,
        'videos_sent': []
    })

def is_authorized(user_id):
    """Checks if the user is the main admin or a co-admin."""
    user_data = get_user_data(user_id)
    return user_data['is_admin'] or user_data['is_co_admin']

# --- GEMINI AI INTEGRATION ---

async def gemini_ai_response(text_prompt: str) -> str:
    """Fetches a response from the Gemini 2.0 Flash API."""
    if not GEMINI_API_KEY:
        return "🤖 Gemini AI is not configured. Please set a valid API key."

    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    
    # System instruction to guide the AI persona
    system_instruction = "You are a stylish, friendly, and helpful AI assistant. Respond attractively, using emojis and positive, concise language. You are integrated into a Telegram Bot."

    payload = {
        "contents": [{"parts": [{"text": text_prompt}]}],
        "systemInstruction": {"parts": [{"text": system_instruction}]},
    }
    
    try:
        # Using synchronous requests library as we cannot guarantee async fetch context
        # In a real-world scenario, you should use an aiohttp/httpx client for async operations.
        # For simplicity in this single-file bot demo, we use requests and await it.
        response = requests.post(api_url, headers={'Content-Type': 'application/json'}, json=payload)
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        
        data = response.json()
        
        # Extract the text
        if data.get('candidates') and data['candidates'][0].get('content'):
            return data['candidates'][0]['content']['parts'][0]['text']
        else:
            return "❌ I couldn't process that request with Gemini. The response was empty or malformed."

    except requests.exceptions.RequestException as e:
        print(f"Gemini API Error: {e}")
        return f"🚨 Error connecting to the AI: {e}"

# --- HANDLER FUNCTIONS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message and checks user status."""
    user_id = update.effective_user.id
    user_data = get_user_data(user_id)
    username = update.effective_user.username or update.effective_user.first_name
    
    # Update username in DB
    user_data['username'] = username

    # Check for referral (if start payload exists)
    referral_check_message = ""
    if context.args and user_id != int(context.args[0]):
        try:
            referrer_id = int(context.args[0])
            referrer_data = get_user_data(referrer_id)
            # Only count if the referrer hasn't referred this user before
            if user_id not in referrer_data.get('referred_users', []):
                referrer_data.setdefault('referred_users', []).append(user_id)
                referrer_data['referral_count'] += 1
                
                # Notify the referrer (optional)
                await context.bot.send_message(
                    chat_id=referrer_id, 
                    text=f"🥳 **Success!** User @{username} has started the bot using your link! You have earned more videos!"
                )
                referral_check_message = "\n\n🎉 You were referred by a friend! Welcome to the club!"
        except Exception:
            pass # Ignore invalid referral arguments

    # 1. Welcome Message Style
    if 'has_started' not in user_data:
        # First-time user
        user_data['has_started'] = True
        welcome_text = (
            f"💖 **HELLO, {username.upper()}!** 💖\n\n"
            f"Welcome to the **Advanced Bot Hub!** I'm thrilled to have you here.\n"
            f"To unlock exclusive content, you first need to **join my channels** below. Let's get started!"
        )
    else:
        # Returning user
        welcome_text = (
            f"🌟 **Welcome Back, {username}!** 🌟\n\n"
            f"You've already started the bot! Great to see you again.\n"
            f"Ready to dive back into the exclusive content? Let's check your membership status."
        )

    # 2. Add Inline Buttons (Channels and Co-Admin Request)
    channel_buttons = []
    if CHANNELS_DB:
        for i, (key, channel) in enumerate(CHANNELS_DB.items()):
            # Using the stored URL or the bot's username to show to the user
            btn_text = f"Channel {i+1} ({channel['type'].upper()})"
            url = channel['value'] if channel['type'] == 'url' else f"https://t.me/{channel['value']}"
            channel_buttons.append([InlineKeyboardButton(btn_text, url=url)])

    # Co-Admin Request Button
    if not user_data['is_admin'] and not user_data['is_co_admin']:
        co_admin_btn = [InlineKeyboardButton("👑 Request Co-Admin Status", callback_data=f"request_admin_{user_id}")]
    else:
        co_admin_btn = []

    # Check Joined Button (MANDATORY for proceeding)
    check_btn = [InlineKeyboardButton("✅ I Have Joined (Verify Now)", callback_data="check_joined")]
    
    # Combined keyboard
    keyboard = channel_buttons + co_admin_btn + check_btn
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"{welcome_text}{referral_check_message}",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

# --- AUTHENTICATION AND ADMIN MANAGEMENT ---

async def change_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Allows the main admin to change the authentication password."""
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("🚫 Only the main admin can change the bot password.")
        return
    
    if len(context.args) == 0:
        await update.message.reply_text("🔑 Usage: `/chgpass <new_password>`")
        return
    
    new_pass = context.args[0]
    global ADMIN_PASSWORD
    ADMIN_PASSWORD = new_pass
    
    await update.message.reply_text(
        f"✅ Password successfully changed to: `{new_pass}`. "
        "Use `/auth <new_password>` to authenticate new co-admins.",
        parse_mode='Markdown'
    )

async def authenticate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Authenticates a user as a co-admin."""
    user_id = update.effective_user.id
    user_data = get_user_data(user_id)

    if user_data['is_admin'] or user_data['is_co_admin']:
        await update.message.reply_text("🌟 You are already authenticated as an admin or co-admin!")
        return
    
    if len(context.args) == 0:
        await update.message.reply_text("🔑 Usage: `/auth <password>`")
        return
    
    input_pass = context.args[0]

    if input_pass == ADMIN_PASSWORD:
        user_data['is_co_admin'] = True
        await update.message.reply_text(
            "🎉 **Authentication Successful!** You are now a co-admin. "
            "You can use admin commands like `/advid`, `/addfile`, `/addchn`, etc."
        )
    else:
        await update.message.reply_text("❌ Authentication Failed. The password is incorrect.")

# --- CHANNEL MANAGEMENT ---

async def add_channel_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Adds a channel/group via URL."""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("🔒 You must be authenticated to use this command. Use `/auth <password>`.")
        return
    
    if len(context.args) == 0:
        await update.message.reply_text("🔗 Usage: `/addchn <Channel or Group URL>` (e.g., `https://t.me/telegram` or `@channelusername`)")
        return
    
    url = context.args[0]
    key = url.strip().replace('https://t.me/', '').replace('@', '')
    CHANNELS_DB[key] = {'type': 'url', 'value': url}
    
    await update.message.reply_text(
        f"✅ Channel/Group URL stored successfully: `{url}`. "
        "Remember to add the bot as an admin to verify membership.",
        parse_mode='Markdown'
    )

async def add_channel_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Adds a channel/group via Chat ID."""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("🔒 You must be authenticated to use this command. Use `/auth <password>`.")
        return
    
    if len(context.args) == 0:
        await update.message.reply_text("🆔 Usage: `/addchid <Channel or Group Chat ID>` (e.g., `-100123456789`)")
        return
    
    chat_id = context.args[0]
    CHANNELS_DB[chat_id] = {'type': 'id', 'value': chat_id}
    
    await update.message.reply_text(
        f"✅ Channel/Group Chat ID stored successfully: `{chat_id}`. "
        "Remember to add the bot as an admin to verify membership.",
        parse_mode='Markdown'
    )

# --- CONTENT MANAGEMENT ---

async def add_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stores a video that the admin replies to."""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("🔒 You must be authenticated to use this command. Use `/auth <password>`.")
        return
    
    if not update.message.reply_to_message or not update.message.reply_to_message.video:
        await update.message.reply_text("🎥 Usage: Reply to a video and use `/advid` to store it.")
        return
    
    video_file_id = update.message.reply_to_message.video.file_id
    
    if video_file_id not in CONTENT_DB:
        CONTENT_DB[video_file_id] = {'type': 'video', 'file_id': video_file_id, 'used_count': 0}
        await update.message.reply_text("🎉 Video stored successfully! It will now be part of the random pool.")
    else:
        await update.message.reply_text("👀 This video is already stored.")

async def add_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stores a file (document) that the admin replies to."""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("🔒 You must be authenticated to use this command. Use `/auth <password>`.")
        return
    
    if not update.message.reply_to_message or not update.message.reply_to_message.document:
        await update.message.reply_text("📄 Usage: Reply to a file (document) and use `/addfile` to store it.")
        return
    
    file_id = update.message.reply_to_message.document.file_id
    
    if file_id not in CONTENT_DB:
        CONTENT_DB[file_id] = {'type': 'document', 'file_id': file_id, 'used_count': 0}
        await update.message.reply_text("💾 File stored successfully! It will now be part of the random pool.")
    else:
        await update.message.reply_text("👀 This file is already stored.")

# --- CALLBACK QUERY HANDLERS (Inline Button Actions) ---

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles all inline button clicks."""
    query = update.callback_query
    await query.answer() # Always answer the query to dismiss the loading state
    data = query.data
    user_id = query.from_user.id
    
    if data.startswith("request_admin_"):
        await handle_admin_request(query, context, user_id, int(data.split('_')[-1]))
    elif data in ["approve_admin", "reject_admin"]:
        await handle_admin_decision(query, context, data)
    elif data == "check_joined":
        await check_joined(query, context)
    elif data.startswith("show_more_"):
        await send_referral_prompt(query, context)
    elif data.startswith("admin_action_"):
        await handle_admin_user_action(query, context, data)
    elif data.startswith("user_list_action_"):
        await show_user_management_buttons(query, context, int(data.split('_')[-1]))
    
    # After handling, edit the original message to remove the buttons if needed
    if data in ["check_joined", "approve_admin", "reject_admin"]:
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass # Ignore if message is too old or unchanged

# --- CO-ADMIN REQUEST LOGIC ---

async def handle_admin_request(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE, requester_id: int, original_requester_id: int) -> None:
    """Sends the co-admin request to the main admin."""
    # Ensure the request is coming from the person who clicked the button
    if requester_id != original_requester_id:
        await query.message.reply_text("🚫 You can only request co-admin status for yourself.")
        return

    requester_user = get_user_data(requester_id)
    requester_name = query.from_user.full_name
    
    # Check if user is already co-admin (shouldn't happen, but safety check)
    if requester_user['is_admin'] or requester_user['is_co_admin']:
        await query.message.reply_text("🌟 You are already an admin or co-admin!")
        return

    # Message to Admin
    keyboard = [
        [
            InlineKeyboardButton("✅ Make Admin", callback_data=f"approve_admin_{requester_id}"),
            InlineKeyboardButton("❌ Reject Request", callback_data=f"reject_admin_{requester_id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    admin_message = (
        f"👑 **NEW CO-ADMIN REQUEST** 👑\n\n"
        f"**User Details:**\n"
        f"  - Name: {requester_name}\n"
        f"  - ID: `{requester_id}`\n"
        f"  - Username: @{query.from_user.username or 'N/A'}\n\n"
        f"**Action Required:** Please approve or reject the request."
    )
    
    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=admin_message,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    
    await query.message.reply_text("💌 Your request for co-admin status has been sent to the main admin! Please wait for approval. ⏳")

async def handle_admin_decision(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    """Processes the main admin's decision on a co-admin request."""
    admin_id = query.from_user.id
    if admin_id != ADMIN_CHAT_ID:
        await query.message.reply_text("🚫 Only the main admin can make this decision.")
        return
        
    action, _, target_id_str = data.split('_')
    target_id = int(target_id_str)
    target_data = get_user_data(target_id)
    target_name = target_data['username'] or target_id

    # Prevent accidental double-action
    if target_data['is_co_admin'] and action == "approve":
        await query.edit_message_text(f"❌ User @{target_name} is already a co-admin.")
        return

    if action == "approve":
        target_data['is_co_admin'] = True
        response_text = f"✅ Request Approved! User @{target_name} (`{target_id}`) is now a co-admin. 👑"
        user_notification = "🎉 Congratulations! The admin has approved your request. You are now a co-admin and can use admin commands."
    else: # reject
        target_data['is_co_admin'] = False # Ensure false
        response_text = f"❌ Request Rejected. User @{target_name} (`{target_id}`) will not be granted co-admin status."
        user_notification = "😔 The admin has rejected your co-admin request."

    # Notify the admin
    await query.edit_message_text(f"**Action Completed:**\n{response_text}", parse_mode='Markdown')
    
    # Notify the user
    try:
        await context.bot.send_message(chat_id=target_id, text=user_notification)
    except Exception:
        await query.message.reply_text(f"⚠️ Could not notify user {target_id}.")

# --- CHANNEL MEMBERSHIP CHECK & CONTENT DELIVERY ---

async def check_joined(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Verifies channel membership for all required channels/groups."""
    user_id = query.from_user.id
    all_joined = True
    
    if not CHANNELS_DB:
        await query.message.reply_text("⚠️ No required channels have been configured by the admin yet.")
        await send_initial_content(query, context) # Proceed if no channels are required
        return

    # Check membership for all required channels
    for key, channel in CHANNELS_DB.items():
        channel_id = channel['value']
        try:
            # get_chat_member returns an object if the user is a member/admin/creator
            member = await context.bot.get_chat_member(chat_id=channel_id, user_id=user_id)
            status = member.status
            if status not in ['member', 'administrator', 'creator']:
                all_joined = False
                break
        except Exception:
            # If the bot is not an admin, or the ID is wrong, it fails.
            await query.message.reply_text(f"🚨 Bot is unable to verify membership for channel: `{channel_id}`. Please ensure the bot is an admin there.")
            return

    if all_joined:
        await query.message.reply_text("🎉 **Joined Successfully!** All memberships verified. Proceeding to content...")
        await send_initial_content(query, context)
    else:
        # Re-send the start message with original buttons to re-prompt joining
        await query.message.reply_text("❌ **Verification Failed!** Please ensure you have joined **ALL** the required channels and then click 'Verify Now' again.", reply_markup=query.message.reply_markup)

async def get_random_content(user_id: int, count: int) -> list:
    """Selects random content (videos/files) that hasn't been used too often."""
    user_data = get_user_data(user_id)
    available_content = [
        content_id for content_id, data in CONTENT_DB.items() 
        if data['used_count'] < 2 or content_id not in user_data['videos_sent']
    ]
    
    if not available_content:
        # Reset if everything has been used twice, allowing reuse.
        for data in CONTENT_DB.values():
            data['used_count'] = 0
        user_data['videos_sent'] = []
        available_content = list(CONTENT_DB.keys())

    if not available_content:
        return []

    # Select random, unique videos
    selected_content_ids = random.sample(available_content, min(count, len(available_content)))
    
    # Update tracking
    for content_id in selected_content_ids:
        CONTENT_DB[content_id]['used_count'] += 1
        user_data['videos_sent'].append(content_id)
        
    return [CONTENT_DB[cid] for cid in selected_content_ids]

async def send_initial_content(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends the initial 10 videos."""
    user_id = query.from_user.id
    
    videos_to_send = await get_random_content(user_id, 10)
    
    if not videos_to_send:
        await query.message.reply_text("😞 I'm sorry, no content has been stored by the admin yet.")
        return

    await query.message.reply_text("🎬 **Here are your first 10 exclusive videos!** Enjoy!")
    
    for content in videos_to_send:
        if content['type'] == 'video':
            await context.bot.send_video(chat_id=user_id, video=content['file_id'])
        elif content['type'] == 'document':
            await context.bot.send_document(chat_id=user_id, document=content['file_id'])
    
    # Send the "Show More Video" button
    await send_referral_prompt(query, context, initial=True)

async def send_referral_prompt(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE, initial: bool = False) -> None:
    """Sends the referral prompt based on current referral count."""
    user_id = query.from_user.id
    user_data = get_user_data(user_id)
    
    # Determine next required referral count
    current_refs = user_data['referral_count']
    
    if current_refs < 1:
        # Next goal: 1 user (This is after the initial 10 videos)
        required_refs = 1
        video_reward = 3
    elif current_refs < 6: # 1 + 5 = 6
        # Next goal: 5 more users (total 6)
        required_refs = 6
        video_reward = 10
    else:
        # Default next goal: 10 more users
        required_refs = current_refs + 10
        video_reward = 10

    # If this is the 'Show More Videos' click, check for earned videos
    if not initial:
        if current_refs >= required_refs:
            # User has met the current goal, send reward and update goal (but only if goal was 1 or 6)
            reward_content = await get_random_content(user_id, video_reward)
            
            if not reward_content:
                await query.message.reply_text("🎉 You earned more videos, but the content library is currently empty or fully used. Try again later!")
                return
                
            await query.message.reply_text(f"🥳 **REWARD UNLOCKED!** You referred {current_refs} members and earned **{len(reward_content)}** more exclusive videos/files!")
            
            for content in reward_content:
                if content['type'] == 'video':
                    await context.bot.send_video(chat_id=user_id, video=content['file_id'])
                elif content['type'] == 'document':
                    await context.bot.send_document(chat_id=user_id, document=content['file_id'])
            
            # Recalculate next goal
            if current_refs == 1:
                required_refs = 6
            elif current_refs >= 6:
                required_refs = current_refs + 10
            
            # Send prompt for next goal
            video_reward = 10 # Default reward for subsequent tiers
        
        elif current_refs < required_refs:
            # User has not met the goal, display progress
            pass # Use calculated 'required_refs' and 'video_reward' below
            
    # Always display the referral prompt
    
    # Generate referral link
    referral_link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
    
    if len(CONTENT_DB) < 10 and len(CONTENT_DB) > 0:
        remaining_text = f"Only **{len(CONTENT_DB)}** unique videos/files remaining in the pool."
    elif len(CONTENT_DB) == 0:
        remaining_text = "The content pool is currently empty. More videos will be added soon!"
    else:
        remaining_text = ""

    prompt_text = (
        f"🔗 **SHARE TO UNLOCK MORE!** 🔗\n\n"
        f"You currently have **{current_refs}** verified referrals.\n"
        f"To get **{video_reward}** more exclusive videos/files, you need **{required_refs - current_refs}** more users to join via your link!\n\n"
        f"**Your Personal Referral Link:**\n`{referral_link}`\n\n"
        f"{remaining_text}"
    )
    
    keyboard = [
        [InlineKeyboardButton("💌 Share Bot Link", url=f"https://t.me/share/url?url={referral_link}&text=Check%20out%20this%20awesome%20bot!")],
        [InlineKeyboardButton("🎁 Show More Videos (Check Progress)", callback_data=f"show_more_{user_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await context.bot.send_message(
        chat_id=user_id,
        text=prompt_text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

# --- ADMIN USER MANAGEMENT ---

async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows a list of all users for the admin to manage."""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("🔒 You must be authenticated to use the admin panel.")
        return

    keyboard = []
    
    # Get all users (excluding the current admin/co-admin from the list)
    sorted_users = sorted([
        (user_id, data) for user_id, data in USERS_DB.items() 
        if user_id != update.effective_user.id
    ], key=lambda x: x[1].get('username', 'z'))

    for user_id, user_data in sorted_users:
        status = "👑" if user_data.get('is_co_admin') else "👤"
        status += " 🚫" if user_data.get('blocked') else ""
        
        # Limit user list for display
        if len(keyboard) < 20: # Show max 20 users for simplicity
            btn_text = f"{status} {user_data.get('username', f'User {user_id}')} ({user_id})"
            # Callback data to open sub-menu for this user
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"user_list_action_{user_id}")])

    if not keyboard:
        await update.message.reply_text("The user database is currently empty.")
        return

    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🛠 **Admin User Management Panel** 🛠\n\n"
        "Select a user to view detailed management options (Block, Broadcast, etc.):",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_user_management_buttons(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE, target_id: int) -> None:
    """Shows the management sub-menu for a specific user."""
    target_data = get_user_data(target_id)
    is_blocked = target_data.get('blocked', False)
    
    block_status = "✅ Unblock User" if is_blocked else "🚫 Block User"
    
    keyboard = [
        [InlineKeyboardButton(block_status, callback_data=f"admin_action_toggleblock_{target_id}")],
        [InlineKeyboardButton("📣 Single Broadcast", callback_data=f"admin_action_broadcast_{target_id}")],
        # Mute/Kick are complex without a specific group context, so they are omitted for simple chat control
        [InlineKeyboardButton("⬅️ Back to List", callback_data="admin_action_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"**Manage User:** @{target_data['username'] or 'N/A'} (`{target_id}`)",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def handle_admin_user_action(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    """Handles block/unblock/broadcast initiation."""
    if not is_authorized(query.from_user.id):
        await query.message.reply_text("🚫 Unauthorized action.")
        return

    if data == "admin_action_back":
        await admin_users(query, context)
        return

    parts = data.split('_')
    action = parts[2]
    target_id = int(parts[3])
    target_data = get_user_data(target_id)
    target_name = target_data.get('username', f'User {target_id}')

    if action == "toggleblock":
        target_data['blocked'] = not target_data.get('blocked', False)
        status = "Blocked" if target_data['blocked'] else "Unblocked"
        await query.edit_message_text(f"✅ User @{target_name} (`{target_id}`) has been **{status}**.")
    
    elif action == "broadcast":
        # Store context for the next message
        context.user_data['next_message_is_broadcast'] = target_id
        await query.message.reply_text(
            f"✍️ **BROADCAST MODE ACTIVE** ✍️\n\n"
            f"Your next message will be sent directly to user @{target_name} (`{target_id}`).\n"
            "To cancel, send `/cancel_broadcast`."
        )

# --- BROADCAST CANCEL AND MESSAGE HANDLER ---

async def cancel_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancels the active single broadcast mode."""
    if 'next_message_is_broadcast' in context.user_data:
        del context.user_data['next_message_is_broadcast']
        await update.message.reply_text("❌ Single broadcast mode cancelled.")
    else:
        await update.message.reply_text("📢 No active single broadcast to cancel.")

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles general text messages (for broadcast or Gemini AI)."""
    user_id = update.effective_user.id
    text = update.message.text
    
    if not text:
        return

    # 1. Handle single broadcast
    if 'next_message_is_broadcast' in context.user_data and is_authorized(user_id):
        target_id = context.user_data.pop('next_message_is_broadcast')
        try:
            await context.bot.send_message(chat_id=target_id, text=f"**💌 Admin Message:**\n\n{text}", parse_mode='Markdown')
            await update.message.reply_text(f"✅ Message sent successfully to user `{target_id}`.")
        except Exception as e:
            await update.message.reply_text(f"❌ Failed to send message to user `{target_id}`. Error: {e}")
        return

    # 2. Handle Gemini AI Chat
    # Ignore messages from blocked users
    if get_user_data(user_id).get('blocked'):
        await update.message.reply_text("🚫 You are currently blocked from using the bot.")
        return
        
    # Send 'typing' status
    await context.bot.send_chat_action(chat_id=user_id, action="typing")
    
    # Get AI response
    ai_response = await gemini_ai_response(text)
    
    await update.message.reply_text(ai_response, parse_mode='Markdown')

# --- MAIN EXECUTION ---

def main() -> None:
    """Start the bot."""
    print("Starting bot application...")
    # Create the Application and pass it your bot's token.
    application = Application.builder().token(BOT_TOKEN).build()

    # --- Register Handlers ---

    # Commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("auth", authenticate))
    application.add_handler(CommandHandler("chgpass", change_password))
    application.add_handler(CommandHandler("addchn", add_channel_url))
    application.add_handler(CommandHandler("addchid", add_channel_id))
    application.add_handler(CommandHandler("advid", add_video))
    application.add_handler(CommandHandler("addfile", add_file))
    application.add_handler(CommandHandler("admin", admin_users))
    application.add_handler(CommandHandler("cancel_broadcast", cancel_broadcast))
    
    # Inline Button Callback Handler (for all inline buttons)
    application.add_handler(CallbackQueryHandler(handle_callback))

    # General Message Handler (for Gemini AI and Broadcast)
    # The filters.TEXT is crucial here for the AI chat
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    # Run the bot until the user presses Ctrl-C
    print("Bot is running. Press Ctrl-C to stop.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
