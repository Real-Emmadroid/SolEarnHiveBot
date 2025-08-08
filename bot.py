import os
import re
import shlex
import logging
import sqlite3  
import json
import random
import asyncio
import string
import requests
import psycopg
from telegram.error import BadRequest, TelegramError
import traceback
import html
import time, hmac, hashlib
import pytz
from pytz import timezone as pytz_timezone  # to handle 'Africa/Lagos'
from flask import Flask
from coinpayments import CoinPaymentsAPI
from telegram.helpers import escape_markdown
from functools import lru_cache
from io import BytesIO
import threading
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from telegram import MessageEntity, InputMediaPhoto, Update, ChatMember, Poll, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, CallbackQuery, ChatMember, ChatPermissions, BotCommand, Bot
from telegram.ext import ApplicationBuilder, Application, CommandHandler, ConversationHandler, CallbackContext, CallbackQueryHandler, MessageHandler, filters, JobQueue, ContextTypes, ChatMemberHandler
from telegram.constants import ChatAction, ChatMemberStatus, ParseMode, MessageEntityType
from database import init_databases
from database import (
    get_db_connection, get_user, update_balances, set_deposit_address, get_deposit_address, convert_earnings_to_general
)

# Configuration
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

CREATOR_ID = 7112609512  # Replace with your actual Telegram user ID
BOT_USERNAME = "solearnhivebot"
MIN_WITHDRAW = 0.1  # Minimum allowed
UTC = pytz.utc
COINPAYMENTS_API_URL = "https://www.coinpayments.net/api.php"
API_PUBLIC_KEY = "97189cb2811dc275b1512b6a6e670d7a2fb5e0bb8d325466006d6a30a9320670"
API_PRIVATE_KEY = "b0a865a0aFCdeEf0c6ba8c26c6dF781510A5B2C3FE0ce2D45f4957aB48167957"

def coinpayments_api_call(cmd, params={}):
    params.update({
        'version': 1,
        'cmd': cmd,
        'key': API_PUBLIC_KEY,
        'format': 'json'
    })

    post_data = '&'.join([f'{k}={v}' for k, v in params.items()])
    hmac_sig = hmac.new(API_PRIVATE_KEY.encode(), post_data.encode(), hashlib.sha512).hexdigest()

    headers = {
        'HMAC': hmac_sig,
        'Content-Type': 'application/x-www-form-urlencoded'
    }

    response = requests.post('https://www.coinpayments.net/api.php', data=params, headers=headers)
    return response.json()


# Rate limiting storage
user_last_request = {}


# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

def get_db_connection():
    """Get a connection to the Supabase PostgreSQL database."""
    conn = psycopg.connect(os.getenv("DATABASE_URL"), sslmode="require")
    return conn

# Initialize databases
init_databases()
    


# Check if user is admin
async def is_admin(chat_id: int, user_id: int, bot) -> bool:
    """
    Check if a user is an admin or owner in a specific chat.

    Args:
        chat_id (int): The ID of the chat (group or supergroup).
        user_id (int): The ID of the user to check.
        bot: The bot instance.

    Returns:
        bool: True if the user is an admin or owner, False otherwise.
    """
    try:
        chat_member = await bot.get_chat_member(chat_id, user_id)
        return chat_member.status in ["administrator", "creator"]
    except Exception as e:
        print(f"Error checking admin status: {e}")
        return False

# Send a message to the chat and return the Message object
async def send_message(update: Update, text: str, reply_markup=None):
    """
    Send a message to the chat and return the Message object.

    Args:
        update (Update): The update object from Telegram.
        text (str): The text to send.
        reply_markup (Optional): InlineKeyboardMarkup or ReplyKeyboardMarkup.

    Returns:
        Message: The sent message object.
    """
    return await update.message.reply_text(text, reply_markup=reply_markup)



# Command Handlers
START_TEXT = """🔥 Welcome to @SolEarnHiveBot 🔥

This bot lets you earn TRX by completing simple tasks:
🖥️ Visit sites to earn
🤖 Message bots to earn
📣 Join chats to earn
👁️ Watch ads to earn

You can also create your own ads with /newad

Use the /help command or visit @SolEarnHiveUpdates for more info.
"""

REPLY_KEYBOARD = [
    ["🤖 Message Bots", "🖥 Visit Sites"],
    ["📣 Join Chats", "👁 Watch Ads"],
    ["💰 Balance", "🙌 Referrals", "⚙ Settings"],
    ["📊 My Ads"]
]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    
    await update.message.reply_text(
        text=START_TEXT,
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(REPLY_KEYBOARD, resize_keyboard=True)
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
<b>I am your all in one trending agent, these are my useful commands:</b>

<blockquote>
<b>📊 Commands for project community</b>
├─ /add Submit your project for listing
├─ /vote Cast a vote for a project
├─ /topvoters Show the most active voters
├─ /boostvote Temporarily boost a vote
├─ /boosttrend Temporarily boost trend visibility
├─ /review Submit reviews on shill teams

<b>🛠️ Shill Team Setup Commands</b>
├─ /register Register your shill team
├─ /linkproject Link your team to a trending project
├─ /settrendlink Set your shill community trend link
├─ /removetrendlink Remove your shill community trend link
├─ /settrendimage Set your shill community trend image
├─ /removetrendimage Remove your shill community trend image
├─ /poll Start a trend vote for your shill community
├─ /shillstat View project stats
├─ /setshilltarget Set shill target

<b>🎖️ PREMIUM FEATURES</b>
├─ /premium Explore premium access
├─ /gent Subscribe to premium
├─ /buyfollowers Buy Twitter followers
</blockquote>
    """

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📚 Docs", url=f"https://t.me/stfinfoportal/235"),
            InlineKeyboardButton("🔗 𝕏", url=f"https://x.com/stftrending"),
            InlineKeyboardButton("📊 Trending", url=f"https://t.me/stftrending")
        ],
        [
            InlineKeyboardButton("📢 Updates", url=f"https://t.me/stfinfoportal"),
            InlineKeyboardButton("💬 Support", url=f"https://t.me/iam_emmadroid")
        ]
    ])

    await update.message.reply_text(
        text=help_text,
        parse_mode='HTML',
        reply_markup=keyboard
    )


BALANCE_MENU_KEYBOARD = ReplyKeyboardMarkup([
    ["➕ Deposit", "➖ Withdraw"],
    ["📜 History", "🔁 Convert"],
    ["🔙 Back"]
], resize_keyboard=True)

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)

    general = float(user["general_balance"])
    payout = float(user["payout_balance"])

    message = (
        f"💰 *Your Balance*\n\n"
        f"• 🪙 *General Balance:* {general:.6f} SOL\n"
        f"• 💸 *Available for Payout:* {payout:.6f} SOL\n\n"
        f"Use the options below to manage your wallet."
    )

    await update.message.reply_text(
        text=message,
        parse_mode="Markdown",
        reply_markup=BALANCE_MENU_KEYBOARD
    )


async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "enter_password":
        context.user_data['expecting_password'] = True
        await password_button_callback(update, context)

    elif data.startswith("popup_yes_") or data.startswith("popup_no_"):
        try:
            chat_id = int(data.split("_")[-1])
            if data.startswith("popup_yes_"):
                update_vote_popup_preference(chat_id, True)
                await query.edit_message_text("✅ You will now receive vote notifications.")
            else:
                update_vote_popup_preference(chat_id, False)
                await query.edit_message_text("❌ You will not receive vote notifications.")
        except Exception as e:
            logger.error(f"Popup callback error: {e}")
            await query.answer("Something went wrong.", show_alert=True)

    else:
        await query.answer("Unknown button action.")




async def unified_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id

    if text == "➕ Deposit":
        await handle_deposit(update, context)

    elif text == "💰 Balance":
        await balance_command (update, context)

    elif text == "➖ Withdraw":
        await handle_withdraw (update, context)

    elif text == "📜 History":
        await update.message.reply_text("🛠 Transaction history will show here.")

    elif text == "🔁 Convert":
        await handle_convert(update, context)

    elif text == "🔙 Back":
        await update.message.reply_text("🔙 Back to main menu", reply_markup=ReplyKeyboardMarkup([...], resize_keyboard=True))

    else:
        await update.message.reply_text("❓ Unrecognized option.")


async def handle_convert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    success, amount = convert_earnings_to_general(user_id)  # Not async now

    if success:
        await update.message.reply_text(
            f"🔁 Converted *{amount:.6f} SOL* from `Available for Payout` to `General Balance` ✅",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "⚠️ Nothing to convert. Your payout balance is empty.",
            parse_mode="Markdown"
        )


async def handle_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    existing_address = get_deposit_address(user_id)
    if existing_address:
        await update.message.reply_text(
            f"📥 Deposit SOL to:\n`{existing_address}`\n\nFunds will reflect after confirmation.",
            parse_mode="Markdown"
        )
        return

    result = api_call("get_callback_address", {
        "currency": "SOL"
    })

    if result['error'] == 'ok':
        address = result['result']['address']
        set_deposit_address(user_id, address)

        await update.message.reply_text(
            f"📥 Deposit SOL to:\n`{address}`\n\nFunds will reflect after confirmation.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("❌ Error generating deposit address.")


async def handle_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = update.message.text.split()

    if len(args) != 3:
        await update.message.reply_text("Usage: /withdraw <amount> <wallet_address>")
        return

    try:
        amount = float(args[1])
        address = args[2]
    except:
        await update.message.reply_text("❌ Invalid amount or address.")
        return

    user = get_user(user_id)
    payout = float(user['payout_balance'])

    if amount < MIN_WITHDRAW:
        await update.message.reply_text(f"❌ Minimum withdraw is {MIN_WITHDRAW} SOL")
        return

    if amount > payout:
        await update.message.reply_text("❌ Insufficient payout balance.")
        return

    result = api_call("create_withdrawal", {
        "amount": str(amount),
        "currency": "SOL",
        "address": address
    })

    if result['error'] == "ok":
        update_balances(user_id, payout=payout - amount)
        await update.message.reply_text(f"✅ Withdrawal of {amount} SOL sent to {address}")
    else:
        await update.message.reply_text("❌ Withdrawal failed: " + result['error'])

        

async def ultstat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != CREATOR_ID:
        return

    try:
        with get_db_connection() as conn:
            cur = conn.cursor()

            # 1. Total unique groups using the bot
            cur.execute("SELECT COUNT(DISTINCT chat_id) FROM group_users")
            total_groups = cur.fetchone()[0]

            # 2. Total unique users
            cur.execute("SELECT COUNT(DISTINCT user_id) FROM group_users")
            total_users = cur.fetchone()[0]

            # 3. Total links tracked
            cur.execute("SELECT COUNT(*) FROM project_links")
            total_links = cur.fetchone()[0]

            # 4. Shill Teams: total, verified, unverified
            cur.execute("SELECT COUNT(*) FROM group_votes")
            total_shill_teams = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM group_votes WHERE verified = TRUE")
            verified_teams = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM group_votes WHERE verified = FALSE")
            unverified_teams = cur.fetchone()[0]

            # 5. Hyped Projects
            cur.execute("SELECT COUNT(*) FROM hyped_projects")
            total_hyped_projects = cur.fetchone()[0]

        # ✅ Format stats
        stats_text = (
            f"📊 <b>Ultimate Bot Statistics</b>\n\n"
            f"👥 <b>Total Groups:</b> {total_groups}\n"
            f"🙋‍♂️ <b>Total Users:</b> {total_users}\n"
            f"🔗 <b>Total Links Tracked:</b> {total_links}\n\n"
            f"🛡 <b>Total Shill Teams:</b> {total_shill_teams}\n"
            f"✅ <b>Verified Teams:</b> {verified_teams}\n"
            f"❌ <b>Unverified Teams:</b> {unverified_teams}\n\n"
            f"🚀 <b>Total Hyped Projects:</b> {total_hyped_projects}"
        )

        await update.message.reply_text(stats_text, parse_mode="HTML")

    except Exception as e:
        await update.message.reply_text(f"⚠️ Error fetching statistics:\n<code>{e}</code>", parse_mode="HTML")
        
async def error_handler(update: Update, context: CallbackContext):
    error = context.error
    if update and update.message:
        await update.message.reply_text(f"⚠️ Error: {str(error)}")
    else:
        print(f"Unhandled error: {error}")


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send an exact copy of the replied message (text/media) to all registered chats."""

    if update.effective_user.id != CREATOR_ID:
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("❗Please reply to the message you want to broadcast.")
        return

    original = update.message.reply_to_message

    # Ensure broadcast table exists
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS broadcast_chats (
                    chat_id BIGINT PRIMARY KEY
                )
            ''')
            conn.commit()

    # Fetch all chat IDs
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute('SELECT chat_id FROM broadcast_chats')
            chat_ids = [row[0] for row in cursor.fetchall()]

    success, failed = 0, 0

    for chat_id in chat_ids:
        try:
            if original.photo:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=original.photo[-1].file_id,
                    caption=original.caption or "",
                    caption_entities=original.caption_entities or None
                )
            elif original.video:
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=original.video.file_id,
                    caption=original.caption or "",
                    caption_entities=original.caption_entities or None
                )
            elif original.text:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=original.text,
                    entities=original.entities or None
                )
            else:
                # Fallback: forward message as-is
                await context.bot.forward_message(
                    chat_id=chat_id,
                    from_chat_id=original.chat.id,
                    message_id=original.message_id
                )
            success += 1
        except Exception as e:
            logger.error(f"Broadcast failed to {chat_id}: {e}")
            failed += 1

    await update.message.reply_text(
        f"📢 Broadcast complete!\n\n✅ Sent: {success}\n❌ Failed: {failed}"
    )

    
async def track_chats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Track all chats where the bot is added."""
    chat_id = update.effective_chat.id

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute('''
                INSERT INTO broadcast_chats (chat_id) 
                VALUES (%s) 
                ON CONFLICT(chat_id) DO NOTHING
            ''', (chat_id,))
            conn.commit()

async def track_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user = update.effective_user
    chat = update.effective_chat

    # Skip if not in group
    if chat.type not in ["group", "supergroup"]:
        return

    if user.is_bot:
        return

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO group_users (chat_id, user_id, username)
                VALUES (%s, %s, %s)
                ON CONFLICT (chat_id, user_id) DO UPDATE SET username = EXCLUDED.username
            """, (chat.id, user.id, user.username or user.full_name))
            conn.commit()


async def promotrack_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return

    user_id = update.effective_user.id
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute('''
                INSERT INTO broadcast_users (user_id) 
                VALUES (%s) 
                ON CONFLICT(user_id) DO NOTHING
            ''', (user_id,))
            conn.commit()

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != CREATOR_ID:
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("❗Please reply to the message you want to broadcast.")
        return

    original = update.message.reply_to_message

    # Ensure table exists
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS broadcast_users (
                    user_id BIGINT PRIMARY KEY
                )
            ''')
            conn.commit()

    # Fetch all user IDs
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute('SELECT user_id FROM broadcast_users')
            user_ids = [row[0] for row in cursor.fetchall()]

    success, failed = 0, 0

    for user_id in user_ids:
        try:
            if original.photo:
                await context.bot.send_photo(
                    chat_id=user_id,
                    photo=original.photo[-1].file_id,
                    caption=original.caption or "",
                    caption_entities=original.caption_entities or None
                )
            elif original.video:
                await context.bot.send_video(
                    chat_id=user_id,
                    video=original.video.file_id,
                    caption=original.caption or "",
                    caption_entities=original.caption_entities or None
                )
            elif original.text:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=original.text,
                    entities=original.entities or None
                )
            else:
                await context.bot.forward_message(
                    chat_id=user_id,
                    from_chat_id=original.chat.id,
                    message_id=original.message_id
                )
            success += 1
        except Exception as e:
            logger.error(f"Broadcast failed to {user_id}: {e}")
            failed += 1

    await update.message.reply_text(
        f"📢 Broadcast complete!\n\n✅ Sent: {success}\n❌ Failed: {failed}"
    )

            

# Initialize Flask
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run():
    app.run(host="0.0.0.0", port=8080)

# Run the web server in a separate thread
t = threading.Thread(target=run)
t.start()

# Main Function
def main():
    application = ApplicationBuilder().token(TOKEN).build()
    application.add_error_handler(error_handler)
   
   
    # Add command handlers
    handlers = [
        ("start", start),
        ("help", help_command),
        ("broadcast", broadcast),
        ("balance", balance_command),
        ("withdraw", handle_withdraw),
        ("promo", broadcast_command),
    ]
    for command, handler in handlers:
        application.add_handler(CommandHandler(command, handler))

    # Add message handlers
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unified_message_handler))
    
    # Add callback handlers
    application.add_handler(CallbackQueryHandler(callback_query_handler))
   
    

    # Run the bot
    application.run_polling()

if __name__ == "__main__":
    main()












