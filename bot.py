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
    get_db_connection, get_user, update_balances, set_deposit_address, get_deposit_address, convert_earnings_to_general, add_referral_deposit_bonus, add_referral_task_bonus
)

# Configuration
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

CREATOR_ID = 7112609512  # Replace with your actual Telegram user ID
BOT_USERNAME = "solearnhivebot"
MAIN_CHANNEL_LINK = "https://t.me/SolEarnHiveUpdates"
MIN_WITHDRAW = 0.1  # Minimum allowed
UTC = pytz.utc
NOWPAYMENTS_API_KEY = "5RRXFWG-7ZY41Q9-P19J9DZ-Q3QSZJM"
app = Flask(__name__)

def create_payment(user_id, amount_sol):
    url = "https://api.nowpayments.io/v1/invoice"
    headers = {
        "x-api-key": NOWPAYMENTS_API_KEY,
        "Content-Type": "application/json"
    }

    data = {
        "price_amount": amount_sol,
        "price_currency": "SOL",         # Amount in SOL
        "order_id": f"user_{user_id}",
        "order_description": f"Deposit for user {user_id}",
        # Optional: "ipn_callback_url": "https://yourdomain.com/ipn"
    }

    try:
        response = requests.post(url, json=data, headers=headers)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"[NOWPayments Error]: {e}")
        return {}

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

    
@app.route('/ipn', methods=['POST'])
def ipn_listener():
    data = request.json
    print("IPN Received:", data)

    if data.get("payment_status") == "confirmed":
        order_id = data.get("order_id")  # "user_123456"
        user_id = int(order_id.replace("user_", ""))
        amount = float(data.get("actually_paid", 0))  # already in SOL if you set `price_currency=sol`

        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE clickbotusers
                    SET general_balance = general_balance + %s
                    WHERE id = %s
                """, (amount, user_id))

                # 2️⃣ Check if they have a referrer
                cursor.execute("SELECT referral_id FROM clickbotusers WHERE id = %s", (user_id,))
                ref_row = cursor.fetchone()
                if ref_row and ref_row[0]:
                    referrer_id = ref_row[0]
                    bonus = amount * 0.02  # 2% of deposit
                    cursor.execute("""
                        UPDATE clickbotusers
                        SET payout_balance = payout_balance + %s
                        WHERE id = %s
                    """, (bonus, referrer_id))
                    print(f"🎁 Referral bonus: {bonus:.6f} SOL credited to referrer {referrer_id}")

                conn.commit()

        print(f"✅ Credited {amount:.6f} SOL to user {user_id}")
    else:
        print("⚠️ Payment not confirmed yet.")

    return "OK", 200


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
    user_id = user.id 
    chat_id = update.effective_chat.id
    args = context.args  # e.g., after /start 12345
    referral_id = None

    if args and args[0].isdigit():
        referral_id = int(args[0])
        if referral_id == user_id:
            referral_id = None  # Prevent self-referral

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            # Check if user exists
            cursor.execute("SELECT 1 FROM clickbotusers WHERE id = %s", (user_id,))
            if not cursor.fetchone():
                # Insert only if not exists
                cursor.execute("""
                    INSERT INTO clickbotusers (id, general_balance, payout_balance, referral_id)
                    VALUES (%s, 0, 0, %s)
                """, (user_id, referral_id))
                conn.commit()
    
    
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

    elif data == "task_notification":
        await query.edit_message_text(
            "🔔 Task Notification settings will be available soon.\n\n"
            "Stay tuned for the update!",
            parse_mode="Markdown"
        )

    else:
        await query.answer("Unknown button action.")




async def unified_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id

    if text == "💰 Balance":
        await balance_command (update, context)

    elif text == "🙌 Referrals":
        await referrals_command (update, context)

    elif text == "📜 History":
        await update.message.reply_text("🛠 Transaction history will show here.")

    elif text == "🔁 Convert":
        await handle_convert(update, context)

    elif text == "⚙ Settingst":
        await settings_command(update, context)

    elif text == "🔙 Back":
        await start(update, context)
    else:
        await start(update, context) 


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

ASK_DEPOSIT_AMOUNT = 1

async def start_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Keyboard with "🔙Back" button
    reply_markup = ReplyKeyboardMarkup(
        [["🔙Back"]],
        resize_keyboard=True
    )

    await update.message.reply_text(
        "💸 How much SOL would you like to deposit?\n\nPlease enter the amount (e.g. `0.5`):",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )
    return ASK_DEPOSIT_AMOUNT


async def process_deposit_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    # If user clicks Back
    if text == "🔙Back":
        return await cancel_deposit(update, context)

    try:
        amount = float(text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid amount greater than 0.")
        return ASK_DEPOSIT_AMOUNT

    result = create_payment(user_id, amount)

    if result.get("invoice_url"):
        await update.message.reply_text(
            f"Click below to complete your deposit of *{amount:.6f} SOL*\n"
            f"You can pay in any crypto of your choice:\n\n{result['invoice_url']}\n\n"
            f"💡 Payment in other cryptocurrencies will be automatically converted into SOL",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove()
        )
    else:
        await update.message.reply_text(
            "❌ Failed to generate deposit link. Try again later.",
            reply_markup=ReplyKeyboardRemove()
        )

    return ConversationHandler.END


async def cancel_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❌ Deposit process canceled.",
        reply_markup=ReplyKeyboardRemove()
    )
    # Go back to start menu
    await start(update, context)
    return ConversationHandler.END




ASK_WALLET, ASK_WITHDRAW_AMOUNT = range(2)

async def start_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    wallet_address = user.get("wallet_address")

    # Reply keyboard for canceling withdrawal
    reply_markup = ReplyKeyboardMarkup([["🔙 Cancel"]], resize_keyboard=True)

    if not wallet_address:
        # Show inline button to set wallet
        keyboard = [[InlineKeyboardButton("➕ Set / Change Wallet", callback_data="set_wallet")]]
        await update.message.reply_text(
            "⚠️ You have not set a withdrawal wallet address.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ASK_WALLET

    payout_balance = float(user["payout_balance"])
    if payout_balance < MIN_WITHDRAW:
        await update.message.reply_text(
            f"❌ You must have at least {MIN_WITHDRAW} SOL to withdraw.\n"
            f"💰 Current balance: {payout_balance:.6f} SOL",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

    await update.message.reply_text(
        f"💳 Your withdrawal wallet is:\n`{wallet_address}`\n\n"
        "Enter the amount of SOL you wish to withdraw:",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )
    return ASK_WITHDRAW_AMOUNT


# Step 2: Inline button handler to set wallet
async def withdraw_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "set_wallet":
        await query.edit_message_text(
            "📩 SEND ME YOUR SOLANA WALLET ADDRESS to use for future withdrawals.\n\n"
            "✅ Make sure it's correct — this will be saved in your account."
        )
        return ASK_WALLET


# Step 3: Save wallet address
async def process_wallet_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    wallet_address = update.message.text.strip()

    if len(wallet_address) < 20:
        await update.message.reply_text("❌ Invalid address. Please send a valid Solana address.")
        return ASK_WALLET

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                UPDATE clickbotusers SET wallet_address = %s WHERE id = %s
            """, (wallet_address, user_id))
            conn.commit()

    await update.message.reply_text(
        f"✅ Wallet address saved:\n`{wallet_address}`\n\nNow send me the amount of SOL you want to withdraw:",
        parse_mode="Markdown"
    )
    return ASK_WITHDRAW_AMOUNT


# Step 4: Process withdrawal
async def process_withdraw_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    try:
        amount = float(text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid withdrawal amount.")
        return ASK_WITHDRAW_AMOUNT

    user = get_user(user_id)
    payout_balance = float(user["payout_balance"])
    wallet_address = user["wallet_address"]

    if amount < MIN_WITHDRAW:
        await update.message.reply_text(f"❌ Minimum withdrawal is {MIN_WITHDRAW} SOL")
        return ASK_WITHDRAW_AMOUNT

    if amount > payout_balance:
        await update.message.reply_text("❌ Insufficient payout balance.")
        return ASK_WITHDRAW_AMOUNT

    # Deduct balance
    new_balance = payout_balance - amount
    update_balances(user_id, payout=new_balance)

    # Save withdrawal request
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO withdrawals (user_id, amount, address, status)
                VALUES (%s, %s, %s, %s)
            """, (user_id, amount, wallet_address, "pending"))
            conn.commit()

    await update.message.reply_text(
        f"✅ Withdrawal request submitted:\n💸 *{amount:.6f} SOL* to `{wallet_address}`\n\n⏳ Awaiting manual processing.",
        parse_mode="Markdown"
    )

    # Notify admin
    await context.bot.send_message(
        chat_id=CREATOR_ID,
        text=f"🔔 New withdrawal request\nUser ID: {user_id}\nAmount: {amount} SOL\nAddress: {wallet_address}"
    )

    return ConversationHandler.END

async def cancel_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Withdrawal process canceled.")
    # Go back to start menu
    await start(update, context)
    return ConversationHandler.END




async def referrals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_id = user.id 
    bot_username = (await context.bot.get_me()).username

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT payout_balance FROM clickbotusers WHERE user_id = %s", (user_id,))
            payout_balance = cursor.fetchone()[0] or 0

            cursor.execute("SELECT COUNT(*) FROM clickbotusers WHERE referral_id = %s", (user_id,))
            total_refs = cursor.fetchone()[0]

    referral_link = f"https://t.me/{bot_username}?start={user_id}"

    # Share message template
    share_text = (
        "🎙 Click2Earn With SOL EarnHive!\n\n"
        "Earn CRYPTO based on your social media activity — Viewing, liking, commenting, or joining TG channels. 🌍\n\n"
        "→ #dotask2earn\n→ #startbot2earn\n→ #comment2earn\n→ #like2earn\n→ #follow2earn\n→ #click2earn\n\n"
        "PS: You can also create your own tasks and reward others to complete them.\n\n"
        f"Start using SOL EarnHive today!\n\n{referral_link} 👈"
    )

    share_url = f"https://t.me/share/url?url={referral_link}&text={share_text.replace(' ', '+')}"

    # Inline button for sharing
    keyboard = [
        [InlineKeyboardButton("📤 Share", url=share_url)]
    ]

    text = (
        f"🔍 You have *{total_refs}* referrals, and earned *{payout_balance:.6f} SOL*.\n\n"
        f"To refer people to the bot, send them this link:\n"
        f"`{referral_link}`\n\n"
        "💰 You will earn 15% of your friends' earnings from tasks, "
        "and 2% if your friend deposits.\n\n"
        "_You can withdraw affiliate income or spend it on ADS!_"
    )

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📢 Main Channel", url=MAIN_CHANNEL_LINK)],
        [InlineKeyboardButton("⚙ Task Notification", callback_data="task_notification")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "⚙ **Settings**\n\n"
        "Here you can manage your preferences and notifications.\n"
        "Select an option below:",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )
        

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

    withdraw_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➖ Withdraw$"), start_withdraw)],
        states={
            ASK_WALLET: [
                CallbackQueryHandler(withdraw_button_handler, pattern="^set_wallet$"),
                MessageHandler(filters.Regex("^🔙 Cancel$"), cancel_withdraw),
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_wallet_address)
            ],
            ASK_WITHDRAW_AMOUNT: [
                MessageHandler(filters.Regex("^🔙 Cancel$"), cancel_withdraw),
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_withdraw_amount)
            ]
        },
        fallbacks=[MessageHandler(filters.Regex("^🔙 Cancel$"), cancel_withdraw)],
    )


    deposit_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ Deposit$"), start_deposit)],
        states={
            ASK_DEPOSIT_AMOUNT: [
                MessageHandler(filters.Regex("^🔙Back$"), cancel_deposit),  # Back button
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_deposit_amount)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel_deposit)],
    )
    application.add_handler(deposit_conv_handler)
    application.add_handler(withdraw_conv_handler)
   
   
    # Add command handlers
    handlers = [
        ("start", start),
        ("help", help_command),
        ("broadcast", broadcast),
        ("balance", balance_command),
        ("withdraw", start_withdraw),
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







