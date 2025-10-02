👑 Advanced Telegram GitHub Bot 🚀
This is a high-fidelity, feature-rich Telegram bot built using python-telegram-bot and integrated with the Gemini AI for interactive chat and content access. It features an advanced referral system, channel verification, and robust administrator tools with inline UI.
Features
 * Stylish Inline UI: Uses InlineKeyboardMarkup extensively for a modern, responsive user experience.
 * Membership Verification: Requires users to join specified channels/groups before accessing content.
 * Tiered Referral System: Users unlock exclusive content by referring new members via their unique start links.
 * Admin Authentication: Secure access to management commands via a password (/auth).
 * Co-Admin Management: Main admin can approve/reject co-admin requests.
 * User Management Panel (/admin): Allows authorized users to block, unblock, and send single broadcasts.
 * Gemini AI Integration: Handles general chat messages using a sophisticated AI persona.
 * Content Locker: Admins can securely store videos (/advid) and documents (/addfile) for referral rewards.
🛠️ Setup and Installation
Prerequisites
 * Python 3.8+
 * A Telegram Bot Token (from BotFather).
 * Your Telegram User ID (for the main admin, e.g., from @userinfobot).
 * A Gemini API Key (for the AI chat functionality).
Step 1: Clone and Install Dependencies
git clone <repository-url>
cd advanced-telegram-github-bot
pip install -r requirements.txt

Step 2: Configure Environment Variables
For security and portability, you should set the following values in your environment (or inside the Python file if testing locally, though this is not recommended for production).
Edit the top section of advanced_github_bot.py or use environment variables corresponding to the keys below:
| Variable | Description | Example Value |
|---|---|---|
| BOT_TOKEN | Your unique Telegram Bot Token | 123456:AAG... |
| ADMIN_CHAT_ID | Your numeric Telegram User ID | 12345678 |
| GEMINI_API_KEY | Your Google Gemini API Key | AIzaSy... |
| BOT_USERNAME | Your bot's username (Crucial for referral links) | @MyCoolBot |
| DEFAULT_PASSWORD | The initial password for co-admin authentication | Sahil@8896 |
Step 3: Running the Bot
Local Run (For Development)
python advanced_github_bot.py

Docker Deployment (Recommended for Production)
This assumes you have Docker and Docker Compose installed.
 * Create a simple Dockerfile in the root directory:
   FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY advanced_github_bot.py .
CMD ["python", "advanced_github_bot.py"]

 * Ensure your deployment.yml (provided above) is configured with the correct environment variables.
 * Run the container:
   docker-compose -f deployment.yml up --build -d

📜 Bot Commands and Usage
User Commands
| Command | Description |
|---|---|
| /start | Starts the bot, checks initial membership, and generates a unique referral link. |
Admin/Co-Admin Management Commands
| Command | Description |
|---|---|
| /auth <pass> | Authenticate as a co-admin using the secret password. |
| /admin | Opens the interactive User Management Panel for blocking/broadcasting. |
| /advid | (Reply to a video) Stores the video file ID for use in the content pool. |
| /addfile | (Reply to a document) Stores the file ID for the content pool. |
| /addchn <URL> | Adds a required channel/group using a public URL or @username. |
| /addchid <ID> | Adds a required channel/group using its numeric Chat ID (e.g., -100...). |
| /cancel_broadcast | Cancels an active single-user broadcast session. |
Main Admin Only Commands
| Command | Description |
|---|---|
| /chgpass <new_pass> | (Main Admin Only) Resets the secret authentication password. |
User Flow Guide
 * User sends /start.
 * Bot prompts the user to join all listed channels.
 * User clicks "✅ I Have Joined (Verify Now)".
 * If verified, the user receives their first 10 pieces of content.
 * The bot then provides their unique referral link and the next tier goal (e.g., "Need 5 more referrals").
 * The user shares the link. When a new user joins via that link, the original user's referral count increases, and they are notified.
 * Once a tier goal is met, clicking the referral button delivers the corresponding reward content.
