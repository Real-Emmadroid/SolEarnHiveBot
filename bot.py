import os
import re
import shlex
import logging
import sqlite3  
import json
import random
import urllib.parse
from urllib.parse import urlparse
import asyncio
import string
import requests
import psycopg
import math
from decimal import Decimal
from telegram.error import BadRequest, TelegramError
import traceback
import html
import time
import hmac, hashlib
import pytz
from psycopg.rows import dict_row  # ✅ for psycopg3
from psycopg import sql
from pytz import timezone as pytz_timezone  # to handle 'Africa/Lagos'
from flask import Flask
from coinpayments import CoinPaymentsAPI
from telegram.helpers import escape_markdown
from functools import lru_cache
from io import BytesIO
import threading
from collections import defaultdict
from datetime import datetime, time, timezone, timedelta
from telegram import MessageEntity, MessageOriginUser, MessageOriginChat, MessageOriginChannel, InputMediaPhoto, Update, ChatMember, Poll, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, CallbackQuery, ChatMember, ChatPermissions, BotCommand, Bot
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
MAX_ADS_PER_USER = 50
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

                # 2️⃣ Store deposit record
                cursor.execute("""
                    INSERT INTO deposits (user_id, amount)
                    VALUES (%s, %s)
                """, (user_id, amount))

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

This bot lets you earn SOL by completing simple tasks:
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

    # Handle referral code
    if args and args[0].isdigit():
        referral_id = int(args[0])
        if referral_id == user_id:
            referral_id = None  # Prevent self-referral

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # ✅ Track only private chat users for DM broadcast
                if update.effective_chat.type == "private":
                    cursor.execute('''
                        CREATE TABLE IF NOT EXISTS broadcast_clickbot (
                            user_id BIGINT PRIMARY KEY
                        )
                    ''')
                    cursor.execute('''
                        INSERT INTO broadcast_clickbot (user_id)
                        VALUES (%s)
                        ON CONFLICT(user_id) DO NOTHING
                    ''', (user_id,))
                    logger.info(f"✅ Tracked user {user_id} for broadcast.")

                # ✅ Main user table insert
                cursor.execute("SELECT 1 FROM clickbotusers WHERE id = %s", (user_id,))
                if not cursor.fetchone():
                    cursor.execute("""
                        INSERT INTO clickbotusers (id, general_balance, payout_balance, referral_id)
                        VALUES (%s, 0, 0, %s)
                    """, (user_id, referral_id))

                conn.commit()

    except Exception as e:
        logger.error(f"❌ Failed in /start for user {user_id}: {e}")

    # ✅ Reply to user
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
        f"🔸️ *Balance:* \n  {general:.6f} SOL\n\n"
        f"🔸️ *Available for Payout:* \n  {payout:.6f} SOL\n"
        f"----------------------------------------------\n"
        f"Click《Deposit》to generate balance topup invoice.\n\n"
        f"💱 *Top-up Methods*\n"
        f"• *Multi coins*"
    )

    await update.message.reply_text(
        text=message,
        parse_mode="Markdown",
        reply_markup=BALANCE_MENU_KEYBOARD
    )


async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    try:
        # Always answer callback query first
        await query.answer()

        if data == "enter_password":
            context.user_data['expecting_password'] = True
            await password_button_callback(update, context)

        elif data == "toggle_task_notification":
            await toggle_task_notification(update, context)
            
        elif data.startswith("watch_skip:"):
            _, ad_id = data.split(":")
            await watch_skip(update, context, int(ad_id))

        elif data.startswith("watch_watched:"):
            _, ad_id = data.split(":")
            await handle_watched_ad(update, context, int(ad_id))

        elif data.startswith("bot_skip:"):
            _, ad_id = data.split(":")
            await bot_skip(update, context, int(ad_id))

        elif data.startswith("bot_started:"):
            _, ad_id = data.split(":")
            await handle_bot_started(update, context, int(ad_id))

        elif data.startswith("link_skip:"):
            _, ad_id = data.split(":")
            await link_skip(update, context, int(ad_id))

        elif data.startswith("link_visited:"):
            _, ad_id = data.split(":")
            await link_visited(update, context, int(ad_id))

        elif data.startswith("channel_skip:"):
            _, ad_id = data.split(":")
            await link_skip(update, context, int(ad_id))

        elif data.startswith("channel_joined:"):
            _, ad_id = data.split(":")
            await channel_joined(update, context, int(ad_id))

        else:
            await query.answer("Unknown button action.")

    except Exception as e:
        print(f"Callback error: {e}")
        await query.answer("⚠️ An error occurred. Please try again.")


async def unified_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    text = update.message.text
    user_id = update.effective_user.id

    # Handle forwarded messages first (updated check)
    if hasattr(update.message, 'forward_origin') and update.message.forward_origin:
        if context.user_data.get("verify_state"):
            await handle_forwarded_message(update, context)  # Use new handler
            return
            
    if text == "💰 Balance":
        await balance_command(update, context)
    elif text == "🙌 Referrals":
        await referrals_command(update, context)
    elif text == "📜 History":
        await history_command(update, context)
    elif text == "🔁 Convert":
        await handle_convert(update, context)
    elif text == "⚙ Settings":
        await settings_command(update, context)
    elif text == "📊 My Ads":
        await my_ads(update, context)
    elif text == "👁 Watch Ads":
        await watch_ads(update, context)
    elif text == "🤖 Message Bots":
        await message_bot_ads(update, context)
    elif text == "🖥 Visit Sites":
        await message_link_ads(update, context)
    elif text == "📣 Join Chats":
        await channel_ads(update, context)
    elif text == "➕ New Ad ➕":
        await newad_start(update, context)
    elif text == "➕ Deposit":
        await start_deposit(update, context)
    elif text == "➖ Withdraw":
        await start_withdraw(update, context)
    elif text in ("🔙 Back", "🔙 Cancel"):
        await start(update, context)
        return ConversationHandler.END
    # DO NOT add a final else clause 


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
    # Clear any previous context
    context.user_data.clear()
    
    # Keyboard with "🔙Back" button
    reply_markup = ReplyKeyboardMarkup(
        [["🔙Back"]],
        resize_keyboard=True,
        one_time_keyboard=True  # Added to make the keyboard less intrusive
    )

    await update.message.reply_text(
        "💸 How much SOL would you like to deposit?\n\n"
        "• Minimum deposit: 0.1 SOL\n"
        "• Enter amount (e.g. `0.5` or `1.25`):",
        parse_mode="Markdown",
        reply_markup=reply_markup,
        disable_web_page_preview=True
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
        if amount < 0.0002:  # Minimum deposit check
            await update.message.reply_text(
                "❌ Minimum deposit is 0.002 SOL. Please enter a larger amount.",
                reply_markup=ReplyKeyboardMarkup([["🔙Back"]], resize_keyboard=True)
            )
            return ASK_DEPOSIT_AMOUNT
    except ValueError:
        await update.message.reply_text(
            "❌ Please enter a valid number (e.g. 0.5 or 1.25).",
            reply_markup=ReplyKeyboardMarkup([["🔙Back"]], resize_keyboard=True)
        )
        return ASK_DEPOSIT_AMOUNT

    result = create_payment(user_id, amount)

    if result.get("invoice_url"):
        # Save amount in context for potential retries
        context.user_data['deposit_amount'] = amount
        
        await update.message.reply_text(
            f"🔄 Please complete your deposit of *{amount:.6f} SOL*\n\n"
            f"1. Click: [Payment Link]({result['invoice_url']})\n"
            f"2. Choose your payment method\n"
            f"3. Complete the transaction\n\n"
            "💡 Payments in other cryptos will auto-convert to SOL",
            parse_mode="Markdown",
            disable_web_page_preview=True,
            reply_markup=ReplyKeyboardMarkup(REPLY_KEYBOARD, resize_keyboard=True)
        )
    else:
        await update.message.reply_text(
            "❌ Failed to generate payment link. Please try again later.",
            reply_markup=ReplyKeyboardMarkup(REPLY_KEYBOARD, resize_keyboard=True)
        )
    return ConversationHandler.END

async def cancel_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❌ Deposit process canceled.",
        reply_markup=ReplyKeyboardMarkup(REPLY_KEYBOARD, resize_keyboard=True)
    )
    return ConversationHandler.END


async def cancel_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Deposit process canceled.")
    # Go back to start menu
    await start(update, context)
    return ConversationHandler.END




ASK_WALLET, ASK_WITHDRAW_AMOUNT = range(2)

async def start_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    wallet_address = user.get("wallet_address")

    # Clear any previous context
    context.user_data.clear()
    
    # Consistent cancel keyboard throughout flow
    cancel_keyboard = ReplyKeyboardMarkup([["🔙 Cancel"]], 
                                        resize_keyboard=True,
                                        one_time_keyboard=True)

    if not wallet_address:
        keyboard = [
            [InlineKeyboardButton("➕ Set Wallet Address", callback_data="set_wallet")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_withdraw")]
        ]
        await update.message.reply_text(
            "⚠️ No withdrawal wallet set\n\n"
            "Please set your Solana wallet address to withdraw funds:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ASK_WALLET

    payout_balance = float(user["payout_balance"])
    if payout_balance < MIN_WITHDRAW:
        await update.message.reply_text(
            f"❌ Minimum withdrawal: {MIN_WITHDRAW} SOL\n"
            f"💰 Your balance: {payout_balance:.6f} SOL\n\n"
            "Complete more tasks to increase your balance!",
            reply_markup=ReplyKeyboardMarkup(REPLY_KEYBOARD, resize_keyboard=True)
        )
        return ConversationHandler.END  # Added missing return

    await update.message.reply_text(
        f"💳 Withdrawal Wallet:\n`{wallet_address}`\n\n"
        f"💰 Available: {payout_balance:.6f} SOL\n\n"
        "Enter amount to withdraw:",
        parse_mode="Markdown",
        reply_markup=cancel_keyboard
    )
    return ASK_WITHDRAW_AMOUNT

async def withdraw_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "set_wallet":
        await query.edit_message_text(
            "📩 Send your Solana wallet address:\n\n"
            "• Must be a valid SOL address\n"
            "• Double-check before submitting\n"
            "• Used for all future withdrawals",
            reply_markup=ReplyKeyboardMarkup([["🔙 Cancel"]], resize_keyboard=True)
        )
        return ASK_WALLET
    elif query.data == "cancel_withdraw":
        await cancel_withdraw(update, context)
        return ConversationHandler.END

async def process_wallet_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wallet_address = update.message.text.strip()
    
    # Basic validation
    if len(wallet_address) < 32:  # Adjust based on actual SOL address format
        await update.message.reply_text(
            "❌ Invalid Solana address format\n"
            "Please check and resend your wallet address:",
            reply_markup=ReplyKeyboardMarkup([["🔙 Cancel"]], resize_keyboard=True)
        )
        return ASK_WALLET

    user_id = update.effective_user.id
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                UPDATE clickbotusers 
                SET wallet_address = %s 
                WHERE id = %s
            """, (wallet_address, user_id))
            conn.commit()

    await update.message.reply_text(
        f"✅ Wallet saved!\n`{wallet_address}`\n\n"
        "Now enter withdrawal amount (SOL):",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup([["🔙 Cancel"]], resize_keyboard=True)
    )
    return ASK_WITHDRAW_AMOUNT

async def process_withdraw_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    try:
        amount = float(text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid amount\nPlease enter a positive number (e.g. 1.5):",
            reply_markup=ReplyKeyboardMarkup([["🔙 Cancel"]], resize_keyboard=True)
        )
        return ASK_WITHDRAW_AMOUNT

    user = get_user(user_id)
    payout_balance = float(user["payout_balance"])
    wallet_address = user["wallet_address"]

    if amount < MIN_WITHDRAW:
        await update.message.reply_text(
            f"❌ Minimum withdrawal: {MIN_WITHDRAW} SOL\n"
            f"You entered: {amount:.6f} SOL",
            reply_markup=ReplyKeyboardMarkup([["🔙 Cancel"]], resize_keyboard=True)
        )
        return ASK_WITHDRAW_AMOUNT

    if amount > payout_balance:
        await update.message.reply_text(
            f"❌ Insufficient balance\n"
            f"Available: {payout_balance:.6f} SOL\n"
            f"Requested: {amount:.6f} SOL",
            reply_markup=ReplyKeyboardMarkup([["🔙 Cancel"]], resize_keyboard=True)
        )
        return ASK_WITHDRAW_AMOUNT

    # Process withdrawal
    new_balance = payout_balance - amount
    update_balances(user_id, payout=new_balance)

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO withdrawals 
                (user_id, amount, address, status, created_at)
                VALUES (%s, %s, %s, %s, NOW())
            """, (user_id, amount, wallet_address, "pending"))
            conn.commit()

    # Format withdrawal details
    withdrawal_msg = (
        f"✅ Withdrawal Submitted\n\n"
        f"• Amount: {amount:.6f} SOL\n"
        f"• Wallet: `{wallet_address}`\n"
        f"• Status: Pending Approval\n\n"
        f"⏳ Processed within 24 hours\n"
        f"📩 Contact support for questions"
    )

    await update.message.reply_text(
        withdrawal_msg,
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(REPLY_KEYBOARD, resize_keyboard=True)
    )

    # Admin notification
    admin_msg = (
        f"⚠️ New Withdrawal Request\n\n"
        f"• User: {user_id}\n"
        f"• Amount: {amount} SOL\n"
        f"• Wallet: {wallet_address}\n"
        f"• Balance After: {new_balance:.6f} SOL"
    )
    await context.bot.send_message(
        chat_id=CREATOR_ID,
        text=admin_msg
    )

    return ConversationHandler.END

async def cancel_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Withdrawal canceled.")
    # Go back to start menu
    await start(update, context)
    return ConversationHandler.END


# HISTORY COMMAND
async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            # Get deposits (order by newest first)
            cursor.execute("""
                SELECT amount, created_at 
                FROM deposits 
                WHERE user_id = %s
                ORDER BY created_at DESC
                LIMIT 10
            """, (user_id,))
            deposits = cursor.fetchall()

            # Get withdrawals (order by newest first)
            cursor.execute("""
                SELECT amount, status, created_at 
                FROM withdrawals 
                WHERE user_id = %s
                ORDER BY created_at DESC
                LIMIT 10
            """, (user_id,))
            withdrawals = cursor.fetchall()

    # Format deposits
    deposit_lines = []
    if deposits:
        for amt, dt in deposits:
            deposit_lines.append(f"💰 +{amt:.6f} SOL — {dt.strftime('%Y-%m-%d %H:%M')}")
    else:
        deposit_lines.append("No deposits yet.")

    # Format withdrawals
    withdraw_lines = []
    if withdrawals:
        for amt, status, dt in withdrawals:
            withdraw_lines.append(f"📤 -{amt:.6f} SOL — {status.capitalize()} — {dt.strftime('%Y-%m-%d %H:%M')}")
    else:
        withdraw_lines.append("No withdrawals yet.")

    # Final message
    msg = (
        "📜 **Transaction History**\n\n"
        "=== Deposits ===\n" +
        "\n".join(deposit_lines) +
        "\n\n=== Withdrawals ===\n" +
        "\n".join(withdraw_lines) +
        "\n\n_Showing last 10 of each_"
    )

    await update.message.reply_text(msg, parse_mode="Markdown")



async def referrals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = getattr(update.effective_user, "id", update.effective_user)
    bot_username = (await context.bot.get_me()).username

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            # Ensure user exists
            cursor.execute("SELECT 1 FROM clickbotusers WHERE id = %s", (user_id,))
            if not cursor.fetchone():
                cursor.execute("INSERT INTO clickbotusers (id) VALUES (%s)", (user_id,))
                conn.commit()

            # Fetch payout balance
            cursor.execute("SELECT payout_balance FROM clickbotusers WHERE id = %s", (user_id,))
            result = cursor.fetchone()
            payout_balance = result[0] if result and result[0] is not None else 0

            # Fetch total referrals
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

    # URL encode both the link and the text
    encoded_referral_link = urllib.parse.quote(referral_link)
    encoded_share_text = urllib.parse.quote(share_text)

    # Telegram share URL with full preloaded message
    share_url = f"https://t.me/share/url?url={encoded_referral_link}&text={encoded_share_text}"

    # Inline button for sharing
    keyboard = [
        [InlineKeyboardButton("Share ➠", url=share_url)]
    ]

    text = (
        f"🔍 You have *{total_refs}* referrals, and earned *{payout_balance:.6f} SOL*.\n\n"
        f"To refer people to the bot, send them this link:\n\n"
        f"{referral_link}\n\n"
        "💰 You will earn 15% of your friends' earnings from tasks, "
        "and 2% if your friend deposits.\n\n"
        "_You can withdraw affiliate income or spend it on ADS!_"
    )

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard), disable_web_page_preview=True)


# SETTINGS COMMAND
async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT notify_tasks FROM clickbotusers WHERE id = %s", (user_id,))
            current_pref = cursor.fetchone()[0]

    notif_status = "✅ Enabled" if current_pref else "❌ Disabled"

    keyboard = [
        [InlineKeyboardButton("📢 Main Channel", url=MAIN_CHANNEL_LINK)],
        [InlineKeyboardButton(f"⚙ Task Notification: {notif_status}", callback_data="toggle_task_notification")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "⚙ **Settings**\n\n"
        "Manage your preferences and notifications below:",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )


# TOGGLE TASK NOTIFICATION
async def toggle_task_notification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT notify_tasks FROM clickbotusers WHERE id = %s", (user_id,))
            current_pref = cursor.fetchone()[0]

            new_pref = not current_pref
            cursor.execute("UPDATE clickbotusers SET notify_tasks = %s WHERE id = %s", (new_pref, user_id))
            conn.commit()

    status = "✅ Enabled" if new_pref else "❌ Disabled"

    keyboard = [
        [InlineKeyboardButton("📢 Main Channel", url=MAIN_CHANNEL_LINK)],
        [InlineKeyboardButton(f"⚙ Task Notification: {status}", callback_data="toggle_task_notification")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        text="⚙ **Settings**\n\nManage your preferences and notifications below:",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )


# DAILY TASK COUNT
async def send_daily_task_count(context: CallbackContext):
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT COUNT(*) 
                FROM ads 
                WHERE status = 'active' 
                  AND date(created_at) = CURRENT_DATE
            """)
            ads_count = cursor.fetchone()[0]

            if ads_count == 0:
                return

            cursor.execute("SELECT id FROM clickbotusers WHERE notify_tasks = TRUE")
            users = cursor.fetchall()

    for (uid,) in users:
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=(
                    "✅ *New task available*\n\n"
                    f"❓ We found *{ads_count}* new tasks available for you today!\n\n"
                    "_You can disable this notification in settings._"
                ),
                parse_mode="Markdown"
            )
        except Exception:
            continue




async def my_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # Get total ad count
    with get_db_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SELECT COUNT(*) AS cnt FROM ads WHERE user_id = %s", (user_id,))
            count = cursor.fetchone()["cnt"]

    # Send menu
    text = f"Here you can manage all your running/expired promotions. ({count} / {MAX_ADS_PER_USER})"
    keyboard = [["➕ New Ad ➕"], ["🔙 Back"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(text, reply_markup=reply_markup)

    # Fetch all ads from different ad type tables
    with get_db_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute("""
                SELECT a.id, a.user_id, a.ad_type, a.status,
                       d.cpc, d.budget, d.clicks, d.skipped,
                       NULL AS title, NULL AS description
                FROM ads a
                JOIN post_view_ads_details d ON a.id = d.ad_id
                WHERE a.user_id = %s

                UNION ALL

                SELECT a.id, a.user_id, a.ad_type, a.status,
                       d.cpc, d.budget, d.clicks, d.skipped,
                       d.title, d.description
                FROM ads a
                JOIN bot_ads_details d ON a.id = d.ad_id
                WHERE a.user_id = %s

                UNION ALL

                SELECT a.id, a.user_id, a.ad_type, a.status,
                       d.cpc, d.budget, d.clicks, d.skipped,
                       d.title, d.description
                FROM ads a
                JOIN link_ads_details d ON a.id = d.ad_id
                WHERE a.user_id = %s

                UNION ALL

                SELECT a.id, a.user_id, a.ad_type, a.status,
                       d.cpc, d.budget, d.clicks, d.skipped,
                       d.title, d.description
                FROM ads a
                JOIN channel_ads_details d ON a.id = d.ad_id
                WHERE a.user_id = %s

                ORDER BY id DESC
            """, (user_id, user_id, user_id, user_id))
            ads = cursor.fetchall()

    if not ads:
        await update.message.reply_text("❌ You have no ads running.")
        return

    for ad in ads:
        # Build base ad text
        ad_text = f"⚙️ <b>Campaign #{ad['id']}</b> - 📃 <b>{ad['ad_type']}</b>\n\n"

        # Show title & description if available
        if ad['title']:
            ad_text += f"📌 <b>{ad['title']}</b>\n"
        if ad['description']:
            ad_text += f"📝 {ad['description']}\n\n"

        # Show stats
        ad_text += (
            f"💰 <b>CPC:</b> {float(ad['cpc']):.6f} SOL\n"
            f"💵 <b>Budget:</b> {float(ad['budget']):.6f} SOL\n\n"
            f"ℹ️ <b>Status:</b> {ad['status']}\n"
            f"👉 <b>Total Clicks:</b> {ad['clicks']} clicks\n"
            f"⏭ <b>Skipped:</b> {ad['skipped']} times\n"
        )

        # Inline action buttons
        buttons = [
            [
                InlineKeyboardButton(
                    "⏸ Pause" if ad['status'] == 'Active' else "▶ Resume",
                    callback_data=f"toggle_ad:{ad['id']}"
                ),
                InlineKeyboardButton(
                    "❌ Delete", callback_data=f"delete_ad:{ad['id']}"
                )
            ],
            [
                InlineKeyboardButton(
                    "🔺 Increase CPC", callback_data=f"increase_cpc:{ad['id']}"
                ),
                InlineKeyboardButton(
                    "💵 Edit Daily Budget", callback_data=f"edit_budget:{ad['id']}"
                )
            ]
        ]

        await update.message.reply_text(
            ad_text,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="HTML"
        )


promo_type_keyboard = [
    ["📣 Channel or Group", "🤖 Bot"],
    ["📃 Post Views", "🔗 Link URL"],
    ["🔙 Back"]
]

promo_type_markup = ReplyKeyboardMarkup(promo_type_keyboard, resize_keyboard=True, one_time_keyboard=True)

async def newad_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "What would you like to promote?\n\nChoose an option below....👇🏻"
    await update.message.reply_text(text, reply_markup=promo_type_markup)


CHANNEL_USERNAME, CHANNEL_TITLE, CHANNEL_DESCRIPTION, CHANNEL_CPC, CHANNEL_BUDGET = range(5)

async def channel_ad_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["🔙 Back"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

    text = (
        "➡️ Enter the username (e.g., @channelusername) or invite link (e.g., https://t.me/+abc123) "
        "of the public or private channel/group you want to promote:\n\n"
        '⚠️ Please add this bot to the channel administrators first.\n'
        'The bot needs "Invite New Members" rights.\n\n'
        "The bot will start sending members to your channel."
    )
    await update.message.reply_text(text, reply_markup=reply_markup)
    return CHANNEL_USERNAME


async def channel_username_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text.lower() == "🔙 back":
        await update.message.reply_text("Cancelled channel ad creation.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    channel_input = text
    bot = context.bot
    channel_link = None
    chat = None

    try:
        if channel_input.startswith("https://t.me/+") or channel_input.startswith("https://t.me/joinchat/"):
            # Private invite link — can't verify via get_chat
            channel_link = channel_input
            context.user_data["channel_username"] = None
            context.user_data["channel_chat_id"] = None

        elif channel_input.startswith("https://t.me/"):
            # Public link
            username = channel_input.split("https://t.me/")[1].strip("/")
            if not username.startswith("@"):
                username = "@" + username
            chat = await bot.get_chat(username)
            channel_link = f"https://t.me/{username.lstrip('@')}"

        else:
            # Just a username
            if not channel_input.startswith("@"):
                channel_input = "@" + channel_input
            chat = await bot.get_chat(channel_input)
            channel_link = f"https://t.me/{channel_input.lstrip('@')}"

        # If we could get a chat object, verify bot admin rights
        if chat:
            bot_member = await bot.get_chat_member(chat.id, bot.id)
            if bot_member.status not in ["administrator", "creator"]:
                await update.message.reply_text(
                    "❌ Make the bot ADMIN of your channel/group with rights to add people!\nPlease try again.",
                    reply_markup=ReplyKeyboardMarkup([["🔙 Back"]], resize_keyboard=True, one_time_keyboard=True),
                )
                return CHANNEL_USERNAME
            context.user_data["channel_username"] = chat.username if chat.username else None
            context.user_data["channel_chat_id"] = chat.id

    except Exception as e:
        await update.message.reply_text(
            f"❌ Could not access the channel/group: {e}\n"
            "Make sure the link/username is correct and the bot is added as admin (except for private links).",
            reply_markup=ReplyKeyboardMarkup([["🔙 Back"]], resize_keyboard=True, one_time_keyboard=True),
        )
        return CHANNEL_USERNAME

    # Store link exactly as given for later button creation
    context.user_data["channel_link"] = channel_link if channel_link else channel_input

    await update.message.reply_text(
        "Enter a title for your ad:",
        reply_markup=ReplyKeyboardMarkup([["🔙 Back"]], resize_keyboard=True, one_time_keyboard=True)
    )
    return CHANNEL_TITLE


async def channel_title_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    if title.lower() == "🔙 back":
        await update.message.reply_text("Cancelled channel ad creation.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    if len(title) < 3:
        await update.message.reply_text("Title too short, please enter at least 3 characters.")
        return CHANNEL_TITLE

    context.user_data["channel_title"] = title
    await update.message.reply_text(
        "Enter a description for your ad:", reply_markup=ReplyKeyboardMarkup([["🔙 Back"]], resize_keyboard=True, one_time_keyboard=True)
    )
    return CHANNEL_DESCRIPTION


async def channel_description_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    desc = update.message.text.strip()
    if desc.lower() == "🔙 back":
        await update.message.reply_text("Cancelled channel ad creation.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    if len(desc) < 5:
        await update.message.reply_text("Description too short, please enter at least 5 characters.")
        return CHANNEL_DESCRIPTION

    context.user_data["channel_description"] = desc
    await update.message.reply_text(
        "What is the most you want to pay per click?\n\n"
        "Minimum Cost Per Click (CPC): 0.0001 SOL\n\n"
        "➡️ Enter a value in SOL:",
        reply_markup=ReplyKeyboardMarkup([["🔙 Back"]], resize_keyboard=True, one_time_keyboard=True),
    )
    return CHANNEL_CPC


async def channel_cpc_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cpc_text = update.message.text.strip()
    if cpc_text.lower() == "🔙 back":
        await update.message.reply_text("Cancelled channel ad creation.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    try:
        cpc = float(cpc_text)
    except ValueError:
        await update.message.reply_text("Invalid value. Please enter a numeric value for CPC in SOL.")
        return CHANNEL_CPC

    if cpc < 0.0001:
        await update.message.reply_text("Minimum CPC is 0.0001 SOL. Please enter a valid value.")
        return CHANNEL_CPC

    context.user_data["channel_cpc"] = cpc

    # Fetch user's general balance from DB
    user_id = update.effective_user.id
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT general_balance FROM clickbotusers WHERE id = %s", (user_id,))
            result = cursor.fetchone()
            balance = float(result[0]) if result else 0.0

    context.user_data["user_balance"] = balance

    await update.message.reply_text(
        f"How much do you want to spend on this campaign?\n\n"
        f"Available balance: {balance:.8f} SOL\n\n"
        "➡️ Enter a value in SOL:",
        reply_markup=ReplyKeyboardMarkup([["➕ Deposit", "🔙 Back"]], resize_keyboard=True, one_time_keyboard=True),
    )
    return CHANNEL_BUDGET


async def channel_budget_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    budget_text = update.message.text.strip()
    user_balance = context.user_data.get("user_balance", 0.0)

    if budget_text.lower() == "🔙 back":
        await update.message.reply_text(
            "Cancelled channel ad creation.",
            reply_markup=ReplyKeyboardMarkup(REPLY_KEYBOARD, resize_keyboard=True)
        )
        return ConversationHandler.END

    if budget_text == "➕ Deposit":
        await start_deposit(update, context)
        return ConversationHandler.END  # End current conversation


    try:
        budget = float(budget_text)
    except ValueError:
        await update.message.reply_text("Invalid value. Please enter a numeric value for the campaign budget in SOL.")
        return CHANNEL_BUDGET

    if budget > user_balance:
        await update.message.reply_text(
            f"❌ You do not own enough SOL for this!\nYou own: {user_balance:.8f} SOL",
            reply_markup=ReplyKeyboardMarkup([["➕ Deposit", "🔙 Back"]], resize_keyboard=True, one_time_keyboard=True),
        )
        return CHANNEL_BUDGET

    context.user_data["channel_budget"] = budget

    ad_data = {
        "channel_username": context.user_data["channel_username"],
        "channel_link": context.user_data["channel_link"],
        "chat_id": context.user_data["channel_chat_id"],  # store for private verification
    }

    user_id = update.effective_user.id

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO ads (user_id, ad_type, details, status, created_at, expires_at)
                VALUES (%s, %s, %s, %s, now(), now() + interval '30 days')
                RETURNING id
                """,
                (user_id, "channel_or_group", json.dumps(ad_data), "active"),
            )
            ad_id = cursor.fetchone()[0]

            cursor.execute(
                """
                INSERT INTO channel_ads_details (ad_id, title, description, cpc, budget, clicks, skipped)
                VALUES (%s, %s, %s, %s, %s, 0, 0)
                """,
                (
                    ad_id,
                    context.user_data["channel_title"],
                    context.user_data["channel_description"],
                    context.user_data["channel_cpc"],
                    budget,
                ),
            )

            # Deduct budget from user balance
            cursor.execute(
                """
                UPDATE clickbotusers
                SET general_balance = general_balance - %s
                WHERE id = %s
                """,
                (budget, user_id),
            )

            conn.commit()

    message = (
        f"⚙️ Campaign #{ad_id} - 📣 Channel / Group promotion\n\n"
        f"✏️ Title: {context.user_data['channel_title']}\n"
        f"🗨 Description: {context.user_data['channel_description']}\n\n"
        f"🎉 Channel: {context.user_data['channel_username']}\n"
        f"🔗 URL: https://t.me/{context.user_data['channel_username'].lstrip('@')}\n\n"
        f"Status: ▶️ Ongoing\n"
        f"CPC: {context.user_data['channel_cpc']:.8f} SOL\n"
        f"Budget: {context.user_data['channel_budget']:.8f} SOL\n"
        f"Total Clicks: 0 clicks\n"
        f"Skipped: 0 times\n\n"
        "___________________________"
    )

    # Send campaign info without link preview
    await update.message.reply_text(
        message,
        disable_web_page_preview=True
    )

    # Then return to main menu
    await start(update, context)
    return ConversationHandler.END


async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Send cancellation message
    await update.message.reply_text("Operation cancelled.")
    
    # Return to main menu
    await start(update, context)
    return ConversationHandler.END

def get_next_channel_ad(user_id, exclude_ad_id=None):
    with get_db_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute("""
                SELECT 
                    a.id,
                    a.ad_type,
                    a.details,
                    cad.title,
                    cad.description,
                    cad.clicks,
                    cad.budget,
                    cad.cpc
                FROM ads a
                JOIN channel_ads_details cad ON cad.ad_id = a.id
                WHERE a.status = 'active'
                  AND cad.clicks < FLOOR(cad.budget / cad.cpc)
                  AND a.id NOT IN (
                      SELECT ad_id FROM channel_ads_clicks WHERE user_id = %s
                  )
                  AND a.id NOT IN (
                      SELECT ad_id FROM user_skipped_ads WHERE user_id = %s
                  )
                  AND a.id <> COALESCE(%s, -1)
                ORDER BY a.created_at ASC 
                LIMIT 1
            """, (user_id, user_id, exclude_ad_id))

            ad = cursor.fetchone()
            if not ad:
                return None

            # Parse details JSON
            details = ad.get("details", {})
            if isinstance(details, str):
                try:
                    details = json.loads(details)
                except json.JSONDecodeError:
                    details = {}
            
            ad["channel_link"] = details.get("channel_link", "")
            ad["channel_username"] = details.get("channel_username", "")

            return ad

def build_channel_ad_text(ad):
    title = html.escape(ad.get("title", "New Channel/Group"))
    description = html.escape(ad.get("description", ""))
    text_parts = [
        f"📣 <b>{title}</b>\n",
        *([f"{description}\n\n"] if description else []),
        "<b>Mission:</b> Join the channel/group\n\n",
        "Press <b>JOINED</b> after you have joined."
    ]
    return "".join(text_parts), ad.get("channel_link", "")


def build_channel_keyboard(ad_id, channel_link):
    # Clean and normalize
    channel_link = channel_link.strip()

    # If it's an @username format
    if channel_link.startswith('@'):
        channel_link = f"https://t.me/{channel_link.lstrip('@')}"
    
    # If it’s only the username without @
    elif not channel_link.startswith(('http://', 'https://', 'tg://')):
        channel_link = f"https://t.me/{channel_link}"

    keyboard = [
        [InlineKeyboardButton("📣 Join the Channel", url=channel_link)],
        [
            InlineKeyboardButton("⏭ Skip", callback_data=f"channel_skip:{ad_id}"),
            InlineKeyboardButton("✅ Joined", callback_data=f"channel_joined:{ad_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


async def channel_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ad = get_next_channel_ad(user_id)

    if not ad:
        await update.message.reply_text("‼️ No Join Chat ads available right now.")
        return

    ad_id = ad["id"]
    html_text, channel_link = build_channel_ad_text(ad)

    await update.message.reply_text(
        html_text,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=build_channel_keyboard(ad_id, channel_link)
    )

async def channel_skip(update: Update, context: ContextTypes.DEFAULT_TYPE, ad_id=None):
    query = update.callback_query
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    message_id = query.message.message_id

    await query.answer("⏭ Ad skipped")

    if ad_id is None:
        ad_id = int(query.data.split(":", 1)[1])

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO user_skipped_ads (user_id, ad_id)
                VALUES (%s, %s)
                ON CONFLICT (user_id, ad_id) DO NOTHING
            """, (user_id, ad_id))
            conn.commit()

    # Delete old message
    try:
        await context.bot.delete_message(chat_id, message_id)
    except:
        pass

    next_ad = get_next_channel_ad(user_id, exclude_ad_id=ad_id)
    if not next_ad:
        await context.bot.send_message(chat_id, "‼️ No more ads available.")
        return

    next_ad_id = next_ad["id"]
    html_text, channel_link = build_channel_ad_text(next_ad)
    await context.bot.send_message(
        chat_id,
        html_text,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=build_channel_keyboard(next_ad_id, channel_link)
    )

async def channel_joined(update: Update, context: ContextTypes.DEFAULT_TYPE, ad_id=None):
    query = update.callback_query
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    message_id = query.message.message_id

    await query.answer()  # no alert popups

    if ad_id is None:
        ad_id = int(query.data.split(":", 1)[1])

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT details, cad.cpc
                FROM ads a
                JOIN channel_ads_details cad ON cad.ad_id = a.id
                WHERE a.id = %s
            """, (ad_id,))
            row = cursor.fetchone()

    if not row:
        await context.bot.edit_message_text(
            "❌ Ad not found",
            chat_id=chat_id,
            message_id=message_id
        )
        return

    details = json.loads(row[0]) if isinstance(row[0], str) else row[0]
    cpc = float(row[1])

    channel_username = details.get("channel_username", "").lstrip("@")
    private_chat_id = details.get("chat_id")

    # ✅ Prefer chat_id for verification, fallback to username
    target_id = private_chat_id if private_chat_id else (f"@{channel_username}" if channel_username else None)

    if not target_id:
        await context.bot.edit_message_text(
            "⚠️ Channel information is missing.",
            chat_id=chat_id,
            message_id=message_id
        )
        return

    try:
        member = await context.bot.get_chat_member(target_id, user_id)
        if member.status not in ["member", "administrator", "creator"]:
            await context.bot.edit_message_text(
                "❌ You must join the channel before claiming the reward.",
                chat_id=chat_id,
                message_id=message_id
            )
            return
    except Exception as e:
        await context.bot.edit_message_text(
            "⚠️ Could not verify your membership. Please try again later.",
            chat_id=chat_id,
            message_id=message_id
        )
        print(e)
        return

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT 1 FROM channel_ads_clicks
                WHERE ad_id = %s AND user_id = %s
            """, (ad_id, user_id))
            if cursor.fetchone():
                await context.bot.edit_message_text(
                    "⚠️ You have already been rewarded for this ad.",
                    chat_id=chat_id,
                    message_id=message_id
                )
                return

    reward = round(cpc * 0.8, 6)
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO channel_ads_clicks (ad_id, user_id)
                VALUES (%s, %s)
            """, (ad_id, user_id))
            cursor.execute("""
                UPDATE channel_ads_details
                SET clicks = clicks + 1
                WHERE ad_id = %s
            """, (ad_id,))
            cursor.execute("""
                UPDATE clickbotusers
                SET payout_balance = payout_balance + %s
                WHERE id = %s
            """, (Decimal(str(reward)), user_id))
            conn.commit()

    # Replace ad message with confirmation
    await context.bot.edit_message_text(
        f"✅ Verified! You earned <b>{reward:.6f} SOL</b>",
        chat_id=chat_id,
        message_id=message_id,
        parse_mode="HTML"
    )

    # Show next ad
    await start(update, context)



BOT_FORWARD_MSG, BOT_PROMO_LINK, BOT_TITLE, BOT_DESCRIPTION, BOT_CPC, BOT_BUDGET = range(6)

async def bot_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["🔙 Back"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

    text = (
        "🔎 Forward any message from that bot to this chat.\n"
        "-> Open the bot that you want to promote.\n"
        "-> Select any messages from the bot and forward it here."
    )
    await update.message.reply_text(text, reply_markup=reply_markup)
    return BOT_FORWARD_MSG


async def bot_forward_msg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message

    # Check if message is forwarded from a bot (using forward_origin instead of forward_from)
    if not message.forward_origin or not isinstance(message.forward_origin, MessageOriginUser):
        await update.message.reply_text(
            "‼️ This is not a forwarded message from a bot. Please forward a message from the bot you want to promote."
        )
        return BOT_FORWARD_MSG

    # Get the original sender (bot)
    origin_user = message.forward_origin.sender_user
    if not origin_user or not origin_user.is_bot:
        await update.message.reply_text(
            "‼️ This is not a forwarded message from a bot. Please forward a message from the bot you want to promote."
        )
        return BOT_FORWARD_MSG

    # Save forwarded bot username for later
    context.user_data["bot_username"] = origin_user.username

    await update.message.reply_text(
        "➕ Promotion Creation\n\n"
        "Send now this information: link\n\n"
        "🔎 Now send the link to the bot that you want to promote.\n"
        "(All the traffic will be sent to that link)",
        reply_markup=ReplyKeyboardMarkup([["🔙 Back"]], resize_keyboard=True, one_time_keyboard=True),
    )
    return BOT_PROMO_LINK


async def bot_promo_link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link = update.message.text.strip()
    if link.lower() == "🔙 back":
        await update.message.reply_text("Cancelled bot ad creation.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    # Basic validation for link format https://t.me/botusername?start=yourref
    if not link.startswith("https://t.me/") or "?start=" not in link:
        await update.message.reply_text(
            "❌ Your bot url has to start like this:\nhttps://t.me/botusername?start=yourref\nPlease enter a valid link."
        )
        return BOT_PROMO_LINK

    context.user_data["bot_promo_link"] = link

    await update.message.reply_text(
        "Enter a title for your ad:",
        reply_markup=ReplyKeyboardMarkup([["🔙 Back"]], resize_keyboard=True, one_time_keyboard=True),
    )
    return BOT_TITLE


async def bot_title_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    if title.lower() == "🔙 back":
        await update.message.reply_text("Cancelled bot ad creation.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    if len(title) < 3:
        await update.message.reply_text("Title too short, please enter at least 3 characters.")
        return BOT_TITLE

    context.user_data["bot_title"] = title
    await update.message.reply_text(
        "Enter a description for your ad:",
        reply_markup=ReplyKeyboardMarkup([["🔙 Back"]], resize_keyboard=True, one_time_keyboard=True),
    )
    return BOT_DESCRIPTION


async def bot_description_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    desc = update.message.text.strip()
    if desc.lower() == "🔙 back":
        await update.message.reply_text("Cancelled bot ad creation.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    if len(desc) < 5:
        await update.message.reply_text("Description too short, please enter at least 5 characters.")
        return BOT_DESCRIPTION

    context.user_data["bot_description"] = desc
    await update.message.reply_text(
        "What is the most you want to pay per click?\n\n"
        "Minimum Cost Per Click (CPC): 0.00006 SOL\n\n"
        "Recommended: 0.00008-0.0001 SOL\n\n"
        "➡️ Enter a value in SOL:",
        reply_markup=ReplyKeyboardMarkup([["🔙 Back"]], resize_keyboard=True, one_time_keyboard=True),
    )
    return BOT_CPC


async def bot_cpc_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cpc_text = update.message.text.strip()
    if cpc_text.lower() == "🔙 back":
        await update.message.reply_text("Cancelled bot ad creation.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    try:
        cpc = float(cpc_text)
    except ValueError:
        await update.message.reply_text("Invalid value. Please enter a numeric value for CPC in SOL.")
        return BOT_CPC

    if cpc < 0.00006:
        await update.message.reply_text("Minimum CPC is 0.00006 SOL. Please enter a valid value.")
        return BOT_CPC

    context.user_data["bot_cpc"] = cpc

    # Fetch user's general balance from DB
    user_id = update.effective_user.id
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT general_balance FROM clickbotusers WHERE id = %s", (user_id,))
            result = cursor.fetchone()
            balance = float(result[0]) if result else 0.0

    context.user_data["user_balance"] = balance

    await update.message.reply_text(
        f"How much do you want to spend on this campaign?\n\n"
        f"Available balance: {balance:.8f} SOL\n\n"
        "➡️ Enter a value in SOL:",
        reply_markup=ReplyKeyboardMarkup([["➕ Deposit", "🔙 Back"]], resize_keyboard=True, one_time_keyboard=True),
    )
    return BOT_BUDGET


async def bot_budget_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    budget_text = update.message.text.strip()
    user_balance = context.user_data.get("user_balance", 0.0)

    if budget_text.lower() == "🔙 back":
        await update.message.reply_text("Cancelled bot ad creation.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    if budget_text == "➕ Deposit":
        await start_deposit(update, context)
        return ConversationHandler.END  # End current conversation

    try:
        budget = float(budget_text)
    except ValueError:
        await update.message.reply_text("Invalid value. Please enter a numeric value for the campaign budget in SOL.")
        return BOT_BUDGET

    if budget > user_balance:
        await update.message.reply_text(
            f"❌ You do not own enough SOL for this!\nYou own: {user_balance:.8f} SOL",
            reply_markup=ReplyKeyboardMarkup([["➕ Deposit", "🔙 Back"]], resize_keyboard=True, one_time_keyboard=True),
        )
        return BOT_BUDGET

    context.user_data["bot_budget"] = budget

    # Save ad to DB
    user_id = update.effective_user.id
    ad_data = {
        "bot_username": context.user_data["bot_username"],
        "bot_link": context.user_data["bot_promo_link"]
    }

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO ads (user_id, ad_type, details, status, created_at, expires_at)
                VALUES (%s, %s, %s, %s, now(), now() + interval '30 days')
                RETURNING id
                """,
                (user_id, "bot_promotion", json.dumps(ad_data), "active"),
            )
            ad_id = cursor.fetchone()[0]

            cursor.execute(
                """
                INSERT INTO bot_ads_details (ad_id, title, description, cpc, budget, clicks, skipped)
                VALUES (%s, %s, %s, %s, %s, 0, 0)
                """,
                (
                    ad_id,
                    context.user_data["bot_title"],
                    context.user_data["bot_description"],
                    context.user_data["bot_cpc"],
                    budget,
                ),
            )

            cursor.execute(
                """
                UPDATE clickbotusers
                SET general_balance = general_balance - %s
                WHERE id = %s
                """,
                (budget, user_id),
            )

            conn.commit()

    message = (
        f"⚙️ Campaign #{ad_id} - 🤖 Bot promotion\n\n"
        f"✏️ Title: {context.user_data['bot_title']}\n"
        f"🗨 Description: {context.user_data['bot_description']}\n\n"
        f"🤖 Bot: @{context.user_data['bot_username']}\n"
        f"🔗 URL: {context.user_data['bot_promo_link']}\n\n"
        f"Status: ▶️ Ongoing\n"
        f"CPC: {context.user_data['bot_cpc']:.8f} SOL\n"
        f"Budget: {context.user_data['bot_budget']:.8f} SOL\n"
        f"Total Clicks: 0 clicks\n"
        f"Skipped: 0 times\n\n"
        "___________________________"
    )

    # Send campaign info without link preview
    await update.message.reply_text(
        message,
        disable_web_page_preview=True
    )

    # Then return to main menu
    await start(update, context)
    return ConversationHandler.END


async def bot_cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Send cancellation message
    await update.message.reply_text("Operation cancelled.")
    
    # Return to main menu
    await start(update, context)
    return ConversationHandler.END


def get_next_bot_ad(user_id, exclude_ad_id=None):
    with get_db_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute("""
                SELECT 
                    a.id,
                    a.ad_type,
                    a.details,
                    a.status,
                    bad.title,
                    bad.description,
                    bad.clicks,
                    bad.budget,
                    bad.cpc
                FROM ads a
                JOIN bot_ads_details bad ON bad.ad_id = a.id
                WHERE a.status = 'active'
                  AND bad.clicks < FLOOR(bad.budget / bad.cpc)
                  AND a.id NOT IN (
                      SELECT ad_id FROM bot_ads_clicks WHERE user_id = %s
                  )
                  AND a.id NOT IN (
                      SELECT ad_id FROM user_skipped_ads WHERE user_id = %s
                  )
                  AND a.id <> COALESCE(%s, -1)
                ORDER BY a.created_at ASC 
                LIMIT 1
            """, (user_id, user_id, exclude_ad_id))
            
            ad = cursor.fetchone()
            if not ad:
                return None

            # Handle both JSON string and already-parsed dict cases
            details = ad.get("details", {})
            if isinstance(details, str):
                try:
                    details = json.loads(details)
                except json.JSONDecodeError:
                    details = {}
            
            ad["bot_link"] = details.get("bot_link", "")
            ad["bot_username"] = details.get("bot_username", "")

            return ad
            
def build_bot_ad_text(ad):
    """Build bot ad text with proper HTML formatting and link preservation.
    
    Args:
        ad: Dictionary containing ad details with keys:
            - title (str)
            - description (str)
            - bot_link (str)
    
    Returns:
        tuple: (formatted_text, original_bot_link)
    """
    # Safely extract values with defaults
    title = html.escape(ad.get("title", "New Bot"))
    description = html.escape(ad.get("description", ""))
    bot_link = ad.get("bot_link", "")  # Preserve exactly as-is
    
    # Build message parts
    text_parts = [
        f"🤖 <b>{title}</b>\n",
        *([f"{description}\n\n"] if description else []),
        "<b>Mission:</b> Start and interact with the bot\n\n",
        "Press <b>STARTED</b> after you've interacted with the bot."
    ]
    
    return "".join(text_parts), bot_link
    

def build_bot_keyboard(ad_id, bot_link):
    # Preserve the exact original link including all parameters
    if not bot_link.startswith(('http://', 'https://', 'tg://')):
        # Only add https if completely missing scheme
        bot_link = f"https://{bot_link}"

    keyboard = [
        [InlineKeyboardButton("🤖 Open Bot", url=bot_link)],  # Original link used here
        [
            InlineKeyboardButton("⏭ Skip", callback_data=f"bot_skip:{ad_id}"),
            InlineKeyboardButton("✅ Started", callback_data=f"bot_started:{ad_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


# Command: show first available Message Bot ad
async def message_bot_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ad = get_next_bot_ad(user_id)

    if not ad:
        await update.message.reply_text("‼️ No Message Bot ads available right now.")
        return

    ad_id = ad["id"]
    html_text, bot_link = build_bot_ad_text(ad)

    await update.message.reply_text(
        html_text,
        parse_mode="HTML",
        reply_markup=build_bot_keyboard(ad_id, bot_link)
    )


# Skip Message Bot ad
async def bot_skip(update: Update, context: ContextTypes.DEFAULT_TYPE, ad_id=None):
    query = update.callback_query
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    message_id = query.message.message_id

    await query.answer("⏭ Ad skipped")

    if ad_id is None:
        ad_id = int(query.data.split(":", 1)[1])

    # Store skipped ad in DB
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_skipped_ads (
                    user_id BIGINT,
                    ad_id INTEGER,
                    skipped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, ad_id)
                )
            """)
            cursor.execute("""
                INSERT INTO user_skipped_ads (user_id, ad_id)
                VALUES (%s, %s)
                ON CONFLICT (user_id, ad_id) DO NOTHING
            """, (user_id, ad_id))
            conn.commit()

    # Get next ad
    next_ad = get_next_bot_ad(user_id, exclude_ad_id=ad_id)

    # Delete old ad message
    try:
        await context.bot.delete_message(chat_id, message_id)
    except Exception as e:
        print(f"Failed to delete message: {e}")

    if not next_ad:
        await context.bot.send_message(chat_id, "‼️ No more ads available.")
        return

    # Send next ad
    next_ad_id = next_ad["id"]
    html_text, bot_link = build_bot_ad_text(next_ad)
    await context.bot.send_message(
        chat_id,
        html_text,
        parse_mode="HTML",
        reply_markup=build_bot_keyboard(next_ad_id, bot_link)
    )



async def handle_bot_started(update: Update, context: ContextTypes.DEFAULT_TYPE, ad_id=None):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    message_id = query.message.message_id

    try:
        # If ad_id not provided, extract from callback data
        if ad_id is None:
            callback_data = query.data
            if ':' in callback_data:
                ad_id = int(callback_data.split(':', 1)[1])
            else:
                ad_id = int(callback_data)
        
        # Get ad details from database
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT 
                        a.details->>'bot_username' as bot_username,
                        a.details->>'bot_link' as bot_link,
                        bad.cpc
                    FROM ads a
                    JOIN bot_ads_details bad ON bad.ad_id = a.id
                    WHERE a.id = %s
                """, (ad_id,))
                ad_data = cursor.fetchone()
                
                if not ad_data or not ad_data[0]:  # Check bot_username exists
                    await query.edit_message_text("❌ Ad data incomplete")
                    return

                bot_username = ad_data[0].lower().lstrip('@')
                bot_link = ad_data[1]
                cpc = float(ad_data[2])

        # Delete the original message
        try:
            await context.bot.delete_message(chat_id, message_id)
        except Exception as e:
            print(f"Couldn't delete message: {e}")

        # Set verification state
        context.user_data["verify_state"] = {
            "ad_id": ad_id,
            "bot_username": bot_username,
            "expected_cpc": cpc,
            "expires": time.time() + 120  # 2 minute timeout
        }

        # Request forwarded message
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"📩 Please forward the welcome message from @{bot_username} to verify\n"
                 "You have 2 minutes to complete this.",
            reply_markup=ReplyKeyboardMarkup(
                [["🔙 Cancel"]], 
                resize_keyboard=True,
                one_time_keyboard=True
            )
        )

    except (IndexError, ValueError):
        await query.answer("⚠️ Invalid ad ID format")
    except Exception as e:
        print(f"Error in bot started: {e}")
        await query.answer("⚠️ Error processing request")

async def handle_forwarded_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not hasattr(update, 'message') or not hasattr(update.message, 'forward_origin'):
        await update.message.reply_text("❌ Please forward the message, don't type it")
        return

    verify_data = context.user_data.get("verify_state")
    if not verify_data:
        return  # Not in verification state

    # Check verification timeout
    if time.time() > verify_data["expires"]:
        await update.message.reply_text("⌛ Verification timed out")
        context.user_data.pop("verify_state", None)
        return

    origin = update.message.forward_origin
    if origin.type != "user":
        await update.message.reply_text("❌ Must forward directly from the bot")
        return

    forwarded_username = origin.sender_user.username.lower()
    if forwarded_username != verify_data["bot_username"]:
        await update.message.reply_text(f"❌ Must forward from @{verify_data['bot_username']}")
        return

    # Verification successful - process reward
    reward = round(verify_data["expected_cpc"] * 0.8, 6)
    user_id = update.effective_user.id

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            # Record completion
            cursor.execute("""
                INSERT INTO bot_ads_clicks (ad_id, user_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
            """, (verify_data["ad_id"], user_id))
            
            # Update click count
            cursor.execute("""
                UPDATE bot_ads_details 
                SET clicks = clicks + 1 
                WHERE ad_id = %s
            """, (verify_data["ad_id"],))
            
            # Award user
            cursor.execute("""
                UPDATE clickbotusers
                SET payout_balance = payout_balance + %s
                WHERE id = %s
            """, (Decimal(str(reward)), user_id))
            
            # Process referral (15%)
            cursor.execute("SELECT referral_id FROM clickbotusers WHERE id = %s", (user_id,))
            if referrer := cursor.fetchone():
                bonus = round(reward * 0.15, 6)
                cursor.execute("""
                    UPDATE clickbotusers
                    SET payout_balance = payout_balance + %s
                    WHERE id = %s
                """, (Decimal(str(bonus)), referrer[0]))
            
            conn.commit()

    # Clear state and notify user
    context.user_data.pop("verify_state", None)
    await update.message.reply_text(
        f"✅ Verified! Earned {reward:.6f} SOL",
        reply_markup=ReplyKeyboardRemove()
    )
    
    # Show next ad
    await start(update, context)
    
POST_MSG, POST_CPC, POST_BUDGET = range(3)

async def post_views_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Initialize post views promotion"""
    context.user_data.clear()  # Clear any previous data
    reply_markup = ReplyKeyboardMarkup([["🔙 Back"]], resize_keyboard=True)
    
    await update.message.reply_text(
        "📝 <b>Post Views Promotion</b>\n\n"
        "1. Forward a message from any channel/group\n"
        "2. We'll generate a direct link to the message\n"
        "3. Set your cost per view and budget\n\n"
        "<i>Note: The message must be from a public channel</i>",
        parse_mode="HTML",
        reply_markup=reply_markup
    )
    return POST_MSG

async def post_views_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the forwarded message and generate link"""
    text = update.message.text.strip() if update.message.text else ""
    reply_markup = ReplyKeyboardMarkup([["🔙 Back"]], resize_keyboard=True)

    # Handle back button
    if text.lower() == "🔙 back":
        await post_views_cancel_handler(update, context)
        return ConversationHandler.END

    # Check if message is forwarded
    if not update.message.forward_origin:
        await update.message.reply_text(
            "❌ Please <b>forward</b> a message from a channel/group, don't just type it.",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        return POST_MSG

    origin = update.message.forward_origin
    chat_id = None
    username = None

    # Determine source chat based on origin type
    if isinstance(origin, MessageOriginChannel):
        chat_id = origin.chat.id
        username = origin.chat.username
    elif isinstance(origin, MessageOriginChat):
        chat_id = origin.sender_chat.id
        username = origin.sender_chat.username
    elif isinstance(origin, MessageOriginUser):
        await update.message.reply_text(
            "❌ Please forward from a <b>channel/group</b>, not a user.",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        return POST_MSG

    if not username:
        await update.message.reply_text(
            "❌ The source channel/group needs a <b>public username</b> to generate a link.",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        return POST_MSG

    # Store message details and generate link
    message_id = update.message.forward_origin.message_id
    post_link = f"https://t.me/{username}/{message_id}"
    
    context.user_data.update({
        "post_link": post_link,
        "post_source": username,
        "post_message_id": message_id
    })

    # Show preview
    preview_text = (
        f"🔗 Generated Post Link:\n{post_link}\n\n"
        "This is the link users will see and visit."
    )
    
    await update.message.reply_text(
        preview_text,
        reply_markup=reply_markup
    )

    # Proceed to CPC setting
    await update.message.reply_text(
        "💰 <b>Set Cost Per View (CPV)</b>\n\n"
        "Enter the maximum amount you'll pay when someone views this post:\n\n"
        "<i>Minimum: 0.00006 SOL\n"
        "Recommended: 0.0001-0.001 SOL</i>",
        parse_mode="HTML",
        reply_markup=reply_markup
    )
    return POST_CPC

async def post_views_cpc_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle CPV input"""
    cpc_text = update.message.text.strip()
    reply_markup = ReplyKeyboardMarkup([["🔙 Back"]], resize_keyboard=True)

    if cpc_text.lower() == "🔙 back":
        await post_views_cancel_handler(update, context)
        return ConversationHandler.END

    try:
        cpc = float(cpc_text)
        if cpc < 0.00006:
            raise ValueError("Below minimum")
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid amount. Minimum is 0.00006 SOL.\n"
            "Please enter a valid CPV:",
            reply_markup=reply_markup
        )
        return POST_CPC

    context.user_data["post_cpc"] = cpc

    # Get user balance
    user_id = update.effective_user.id
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT general_balance FROM clickbotusers WHERE id = %s",
                (user_id,)
            )
            balance = float(cursor.fetchone()[0] or 0)

    context.user_data["user_balance"] = balance

    await update.message.reply_text(
        f"💳 <b>Set Campaign Budget</b>\n\n"
        f"Available Balance: {balance:.6f} SOL\n"
        f"CPV: {cpc:.6f} SOL\n\n"
        "Enter total amount to spend on this campaign:",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(
            [["➕ Deposit", "🔙 Back"]],
            resize_keyboard=True
        )
    )
    return POST_BUDGET

async def post_views_budget_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle budget input and create campaign"""
    budget_text = update.message.text.strip()
    user_balance = context.user_data.get("user_balance", 0.0)
    reply_markup = ReplyKeyboardMarkup(
        [["➕ Deposit", "🔙 Back"]],
        resize_keyboard=True
    )

    if budget_text.lower() == "🔙 back":
        await post_views_cancel_handler(update, context)
        return ConversationHandler.END

    if budget_text == "➕ Deposit":
        await start_deposit(update, context)
        return ConversationHandler.END

    try:
        budget = float(budget_text)
        if budget > user_balance:
            raise ValueError("Insufficient funds")
        if budget < 0.001:
            raise ValueError("Below minimum")
    except ValueError as e:
        error_msg = {
            "Insufficient funds": f"❌ Only {user_balance:.6f} SOL available",
            "Below minimum": "❌ Minimum budget is 0.001 SOL"
        }.get(str(e), "❌ Invalid amount. Please enter a number")

        await update.message.reply_text(
            f"{error_msg}\n\nPlease enter a valid amount:",
            reply_markup=reply_markup
        )
        return POST_BUDGET

    # Save campaign to database
    user_id = update.effective_user.id
    ad_data = {
        "link": context.user_data["post_link"],
        "source": context.user_data["post_source"],
        "message_id": context.user_data["post_message_id"]
    }

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # Create ad record
                cursor.execute("""
                    INSERT INTO ads 
                    (user_id, ad_type, details, status, created_at, expires_at)
                    VALUES (%s, %s, %s, %s, NOW(), NOW() + INTERVAL '30 days')
                    RETURNING id
                """, (
                    user_id,
                    "post_views",
                    json.dumps(ad_data),
                    "active"
                ))
                ad_id = cursor.fetchone()[0]

                # Create ad details
                cursor.execute("""
                    INSERT INTO post_view_ads_details
                    (ad_id, cpc, budget, clicks, skipped)
                    VALUES (%s, %s, %s, 0, 0)
                """, (
                    ad_id,
                    context.user_data["post_cpc"],
                    budget
                ))

                # Deduct balance
                cursor.execute("""
                    UPDATE clickbotusers
                    SET general_balance = general_balance - %s
                    WHERE id = %s
                """, (budget, user_id))

                conn.commit()

        # Build confirmation message
        message = (
            f"⚙️ Campaign #{ad_id} - 📃 Post views promotion\n\n"
            f"<b>Post Link:</b> {context.user_data['post_link']}\n"
            f"Status: ▶️ Ongoing\n"
            f"<b>CPC:</b> {context.user_data['post_cpc']:.6f} SOL\n"
            f"<b>Budget:</b> {budget:.6f} SOL\n\n"
            f"Total Clicks: 0 clicks\n"
            f"Skipped: 0 times\n\n"
            "___________________________"
        )

        await update.message.reply_text(
            message,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=ReplyKeyboardMarkup(REPLY_KEYBOARD, resize_keyboard=True)
        )

    except Exception as e:
        await update.message.reply_text(
            f"❌ Failed to create campaign: {str(e)}",
            reply_markup=reply_markup
        )
        return POST_BUDGET

    return ConversationHandler.END

async def post_views_cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operation cancelled.", reply_markup=ReplyKeyboardRemove())
    await start(update, context)
    return ConversationHandler.END


# Helper: get next available ad; optionally exclude the current ad so Skip moves forward
def get_next_ad(user_id, exclude_ad_id=None):
    with get_db_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            sql = """
                SELECT 
                    a.id,
                    a.ad_type,
                    a.details,
                    a.status,
                    pvd.clicks,
                    pvd.budget,
                    pvd.cpc
                FROM ads a
                JOIN post_view_ads_details pvd ON pvd.ad_id = a.id
                WHERE a.status = 'active'
                  AND pvd.clicks < FLOOR(pvd.budget / pvd.cpc)
                  AND a.id NOT IN (
                      SELECT ad_id FROM post_view_ads_clicks WHERE user_id = %s
                  )
                  AND a.id NOT IN (
                      SELECT ad_id FROM user_skipped_ads WHERE user_id = %s
                  )
            """
            params = [user_id, user_id]

            # exclude the ad we just showed (so Skip won't return the same ad)
            if exclude_ad_id is not None:
                sql += " AND a.id <> %s"
                params.append(exclude_ad_id)

            # show newest first (change to ASC if you want oldest first)
            sql += " ORDER BY a.created_at ASC LIMIT 1"

            cursor.execute(sql, tuple(params))
            return cursor.fetchone()  # returns dict_row or None


def build_ad_text_and_link(ad):
    """Return (html_text, post_link) for an ad dict_row"""
    details = ad.get("details") or {}
    # old format uses "link", newer may use "post_link"
    post_link = details.get("post_link") or details.get("link") or ""
    title = details.get("title")
    description = details.get("description")

    parts = ["<b>Mission:</b> Read this post / increase views count"]
    if title:
        parts.append(f"\n\n📌 <b>{html.escape(str(title))}</b>")
    if description:
        parts.append(f"\n{html.escape(str(description))}")

    parts.append("\n\nPress <b>WATCHED</b> to complete this task")

    return "".join(parts), post_link


def build_watch_keyboard(ad_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⏭ Skip", callback_data=f"watch_skip:{ad_id}"),
            InlineKeyboardButton("✅ Watched", callback_data=f"watch_watched:{ad_id}")
        ]
    ])


# Watch Ads command — show first available ad to the user
async def watch_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ad = get_next_ad(user_id)

    if not ad:
        await update.message.reply_text(
            "‼️ Aw snap! There are no more ads available.\n\nPress MY ADS to create a new task"
        )
        return

    ad_id = ad["id"]
    html_text, post_link = build_ad_text_and_link(ad)

    await update.message.reply_text(html_text, reply_markup=build_watch_keyboard(ad_id), parse_mode="HTML")
    if isinstance(post_link, str) and post_link.startswith("http"):
        await update.message.reply_text(post_link)


async def watch_skip(update: Update, context: ContextTypes.DEFAULT_TYPE, ad_id=None):
    query = update.callback_query
    user_id = query.from_user.id

    try:
        await query.answer("⏭ Ad skipped")
        
        # Get ad_id from callback if not provided
        if ad_id is None:
            ad_id = int(query.data.split(":", 1)[1])

        # Record skipped ad in database
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS user_skipped_ads (
                        user_id BIGINT,
                        ad_id INTEGER,
                        skipped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (user_id, ad_id)
                    )
                ''')
                cursor.execute(
                    "INSERT INTO user_skipped_ads (user_id, ad_id) VALUES (%s, %s) "
                    "ON CONFLICT (user_id, ad_id) DO NOTHING",
                    (user_id, ad_id)
                )
                conn.commit()

        # Delete the original ad message
        try:
            await context.bot.delete_message(
                chat_id=query.message.chat_id,
                message_id=query.message.message_id
            )
        except Exception as e:
            print(f"Couldn't delete message: {e}")

        # Get next available ad (automatically excludes skipped ads)
        next_ad = get_next_ad(user_id)
        if not next_ad:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="‼️ No more ads available right now"
            )
            return

        # Send fresh ad
        next_ad_id = next_ad["id"]
        html_text, post_link = build_ad_text_and_link(next_ad)
        
        # Send new message
        new_msg = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=html_text,
            reply_markup=build_watch_keyboard(next_ad_id),
            parse_mode="HTML"
        )

        # Store message ID for potential future deletion
        context.user_data["last_ad_message_id"] = new_msg.message_id

        if isinstance(post_link, str) and post_link.startswith("http"):
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=post_link
            )

    except Exception as e:
        print(f"Error in watch_skip: {e}")
        await query.answer("⚠️ Error skipping ad", show_alert=True)

async def handle_watched_ad(update: Update, context: ContextTypes.DEFAULT_TYPE, ad_id: int):
    query = update.callback_query
    user_id = query.from_user.id

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # Check for duplicates
                cursor.execute(
                    "SELECT 1 FROM post_view_ads_clicks WHERE ad_id=%s AND user_id=%s",
                    (ad_id, user_id)
                )
                if cursor.fetchone():
                    await query.edit_message_text("✅ Already completed!")
                    return

                # Get reward amount
                cursor.execute(
                    "SELECT cpc FROM post_view_ads_details WHERE ad_id=%s",
                    (ad_id,)
                )
                cpc = float(cursor.fetchone()[0])
                reward = round(cpc * 0.8, 6)

                # Update records
                cursor.execute(
                    "INSERT INTO post_view_ads_clicks (ad_id, user_id) VALUES (%s, %s)",
                    (ad_id, user_id)
                )
                cursor.execute(
                    "UPDATE post_view_ads_details SET clicks = clicks + 1 WHERE ad_id = %s",
                    (ad_id,)
                )
                cursor.execute(
                    "UPDATE clickbotusers SET payout_balance = payout_balance + %s WHERE id = %s",
                    (Decimal(str(reward)), user_id)
                )

                # Process referral (15%)
                cursor.execute(
                    "SELECT referral_id FROM clickbotusers WHERE id = %s",
                    (user_id,)
                )
                referrer = cursor.fetchone()
                if referrer and referrer[0]:
                    bonus = round(reward * 0.15, 6)
                    cursor.execute(
                        "UPDATE clickbotusers SET payout_balance = payout_balance + %s WHERE id = %s",
                        (Decimal(str(bonus)), referrer[0])
                    )

                conn.commit()

        # Success message
        await query.edit_message_text(f"🎉 Congratulations! You've earned \n{reward:.6f} SOL!")

    except Exception as e:
        print(f"Error in handle_watched_ad: {e}")
        await query.answer("⚠️ Processing failed", show_alert=True)

        
# Define conversation states
LINK_URL, LINK_TITLE, LINK_DESCRIPTION, LINK_CPC, LINK_BUDGET = range(5)

def is_valid_url(url: str) -> bool:
    """Enhanced URL validation with domain pattern checking"""
    try:
        result = urlparse(url)
        if not all([result.scheme in ("http", "https"), result.netloc]):
            return False
            
        # Basic domain pattern validation
        domain_pattern = re.compile(
            r'^([a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$'
        )
        return bool(domain_pattern.match(result.netloc))
    except Exception:
        return False

async def link_url_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start link promotion with clear instructions"""
    context.user_data.clear()  # Clear any previous data
    
    text = (
        "🌐 <b>Link Promotion Setup</b>\n\n"
        "🔗 Enter any valid URL to promote:\n"
        "• Websites\n• Social Media\n• Videos\n• Products\n\n"
        "<i>Example: https://example.com?ref=123</i>"
    )
    
    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup([["🔙 Back"]], resize_keyboard=True)
    )
    return LINK_URL

async def link_url_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Validate and store the promotion URL"""
    text = update.message.text.strip()
    
    if text.lower() == "🔙 back":
        await cancel_handler(update, context)
        return ConversationHandler.END

    if not is_valid_url(text):
        error_msg = (
            "❌ <b>Invalid URL Format</b>\n\n"
            "Please enter a valid URL including:\n"
            "• http:// or https:// prefix\n"
            "• Proper domain name\n\n"
            "<i>Example: https://example.com</i>"
        )
        await update.message.reply_text(
            error_msg,
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup([["🔙 Back"]], resize_keyboard=True)
        )
        return LINK_URL

    context.user_data["link_url"] = text
    await update.message.reply_text(
        "✏️ <b>Create an attractive title:</b>\n\n"
        "<i>Example: Amazing Product - 50% Off Today!</i>",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup([["🔙 Back"]], resize_keyboard=True)
    )
    return LINK_TITLE

async def link_title_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Validate and store ad title"""
    title = update.message.text.strip()
    
    if title.lower() == "🔙 back":
        await cancel_handler(update, context)
        return ConversationHandler.END

    if len(title) < 5:
        await update.message.reply_text(
            "❌ Title too short (min 5 characters)\n"
            "Please enter a compelling title:",
            reply_markup=ReplyKeyboardMarkup([["🔙 Back"]], resize_keyboard=True)
        )
        return LINK_TITLE

    context.user_data["link_title"] = title
    await update.message.reply_text(
        "📝 <b>Write a detailed description:</b>\n\n"
        "<i>Example: Get our premium product at half price today only! "
        "Limited time offer for first 100 customers.</i>",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup([["🔙 Back"]], resize_keyboard=True)
    )
    return LINK_DESCRIPTION

async def link_description_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Validate and store ad description"""
    desc = update.message.text.strip()
    
    if desc.lower() == "🔙 back":
        await cancel_handler(update, context)
        return ConversationHandler.END

    if len(desc) < 20:
        await update.message.reply_text(
            "❌ Description too short (min 20 characters)\n"
            "Please enter a detailed description:",
            reply_markup=ReplyKeyboardMarkup([["🔙 Back"]], resize_keyboard=True)
        )
        return LINK_DESCRIPTION

    context.user_data["link_description"] = desc
    
    # Show CPC explanation with example
    cpc_msg = (
        "💰 <b>Set Your Cost-Per-Click (CPC)</b>\n\n"
        "This is the max you'll pay when someone clicks your link\n\n"
        "• Minimum: 0.00006 SOL\n"
        "• Recommended: 0.0001-0.01 SOL\n\n"
        "<i>Example entry: 0.001</i>"
    )
    
    await update.message.reply_text(
        cpc_msg,
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup([["🔙 Back"]], resize_keyboard=True)
    )
    return LINK_CPC

async def link_cpc_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Validate and store CPC value"""
    cpc_text = update.message.text.strip()
    
    if cpc_text.lower() == "🔙 back":
        await cancel_handler(update, context)
        return ConversationHandler.END

    try:
        cpc = float(cpc_text)
        if cpc < 0.00006:
            raise ValueError("Below minimum")
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid CPC value\n"
            "Please enter a number ≥ 0.00006 SOL:",
            reply_markup=ReplyKeyboardMarkup([["🔙 Back"]], resize_keyboard=True)
        )
        return LINK_CPC

    context.user_data["link_cpc"] = cpc
    
    # Get user balance
    user_id = update.effective_user.id
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT general_balance FROM clickbotusers WHERE id = %s", 
                (user_id,)
            )
            balance = float(cursor.fetchone()[0] or 0)

    context.user_data["user_balance"] = balance
    
    budget_msg = (
        "💵 <b>Set Campaign Budget</b>\n\n"
        f"Available Balance: {balance:.6f} SOL\n\n"
        "Enter total amount to spend:\n"
        "<i>Example: 1.5 (for 1.5 SOL)</i>"
    )
    
    await update.message.reply_text(
        budget_msg,
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(
            [["➕ Deposit", "🔙 Back"]], 
            resize_keyboard=True
        )
    )
    return LINK_BUDGET

async def link_budget_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Finalize campaign creation"""
    text = update.message.text.strip()
    user_id = update.effective_user.id
    balance = context.user_data["user_balance"]

    if text.lower() == "🔙 back":
        await cancel_handler(update, context)
        return ConversationHandler.END

    if text == "➕ Deposit":
        await start_deposit(update, context)
        return ConversationHandler.END

    try:
        budget = float(text)
        if budget > balance:
            raise ValueError("Insufficient funds")
        if budget < 0.001:
            raise ValueError("Below minimum")
    except ValueError as e:
        error_msg = {
            "Insufficient funds": (
                f"❌ Only {balance:.6f} SOL available\n"
                f"You requested: {text} SOL\n\n"
                "Please deposit more or reduce budget"
            ),
            "Below minimum": "❌ Minimum budget is 0.001 SOL",
        }.get(str(e), "❌ Invalid amount. Please enter a number")

        await update.message.reply_text(
            error_msg,
            reply_markup=ReplyKeyboardMarkup(
                [["➕ Deposit", "🔙 Back"]],
                resize_keyboard=True
            )
        )
        return LINK_BUDGET

    # Save budget in context for message display
    context.user_data["link_budget"] = budget

    # Save campaign to database
    ad_data = {
        "url": context.user_data["link_url"],
        "title": context.user_data["link_title"],
        "description": context.user_data["link_description"],
        "cpc": context.user_data["link_cpc"],
        "budget": budget
    }

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            # Create ad record
            cursor.execute("""
                INSERT INTO ads 
                (user_id, ad_type, details, status, created_at, expires_at)
                VALUES (%s, %s, %s, %s, NOW(), NOW() + INTERVAL '30 days')
                RETURNING id
            """, (
                user_id,
                "link_url",
                json.dumps({"url": ad_data["url"]}),
                "active"
            ))
            ad_id = cursor.fetchone()[0]

            # Create ad details
            cursor.execute("""
                INSERT INTO link_ads_details
                (ad_id, title, description, cpc, budget, clicks, skipped)
                VALUES (%s, %s, %s, %s, %s, 0, 0)
            """, (
                ad_id,
                ad_data["title"],
                ad_data["description"],
                ad_data["cpc"],
                ad_data["budget"]
            ))

            # Deduct balance
            cursor.execute("""
                UPDATE clickbotusers
                SET general_balance = general_balance - %s
                WHERE id = %s
            """, (budget, user_id))

            conn.commit()

    # Build confirmation message
    message = (
        f"⚙️ Campaign #{ad_id} - 🔗 Link URL promotion\n\n"
        f"✏️ Title: {context.user_data['link_title']}\n"
        f"🗨 Description: {context.user_data['link_description']}\n\n"
        f"🔗 URL: {context.user_data['link_url']}\n\n"
        f"Status: ▶️ Ongoing\n"
        f"CPC: {context.user_data['link_cpc']:.8f} SOL\n"
        f"Budget: {context.user_data['link_budget']:.8f} SOL\n"
        f"Total Clicks: 0 clicks\n"
        f"Skipped: 0 times\n\n"
        "___________________________"
    )

    # Send campaign info without link preview
    await update.message.reply_text(
        message,
        disable_web_page_preview=True
    )

    # Then return to main menu
    await start(update, context)
    return ConversationHandler.END

async def link_cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Send cancellation message
    await update.message.reply_text("Operation cancelled.")
    
    # Return to main menu
    await start(update, context)
    return ConversationHandler.END


def get_next_link_ad(user_id, exclude_ad_id=None):
    """Fetch next available link ad with proper filtering"""
    with get_db_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute("""
                SELECT 
                    a.id,
                    a.ad_type,
                    a.details->>'url' as url,
                    l.title,
                    l.description,
                    l.clicks,
                    l.budget,
                    l.cpc
                FROM ads a
                JOIN link_ads_details l ON l.ad_id = a.id
                WHERE a.status = 'active'
                  AND l.clicks < FLOOR(l.budget / l.cpc)
                  AND a.id NOT IN (
                      SELECT ad_id FROM link_ads_clicks WHERE user_id = %s
                  )
                  AND a.id NOT IN (
                      SELECT ad_id FROM user_skipped_ads WHERE user_id = %s
                  )
                  AND a.id <> COALESCE(%s, -1)
                ORDER BY a.created_at ASC
                LIMIT 1
            """, (user_id, user_id, exclude_ad_id))
            return cursor.fetchone()

def build_link_ad_text(ad):
    """Generate HTML-formatted ad text"""
    title = html.escape(ad.get("title", "Visit Site"))
    description = html.escape(ad.get("description", ""))
    url = ad.get("url", "#")
    
    text_parts = [
        f"🌐 <b>{title}</b>\n",
        *([f"{description}\n\n"] if description else []),
        "<b>Mission:</b> Visit the site for at least 10 seconds\n\n",
        "Press <b>OPEN LINK</b> to proceed."
    ]
    
    return "".join(text_parts), url

def build_link_keyboard(ad_id, url):
    """Create inline keyboard with verification"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⏭ Skip", callback_data=f"link_skip:{ad_id}"),
            InlineKeyboardButton("🌐 Open Link", url=url)
        ],
        [InlineKeyboardButton("✅ Visited", callback_data=f"link_visited:{ad_id}")]
    ])

async def message_link_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Universal ad display that works everywhere"""
    try:
        # Determine message source
        message = update.message or update.callback_query.message
        chat_id = message.chat.id
        
        user_id = update.effective_user.id
        ad = get_next_link_ad(user_id)

        if not ad:
            await context.bot.send_message(
                chat_id=chat_id,
                text="‼️ No more ads available"
            )
            return

        ad_id = ad["id"]
        html_text, url = build_link_ad_text(ad)

        # Store ad data with timestamp
        context.user_data[f"link_ad_{ad_id}"] = {
            "url": url,
            "chat_id": chat_id
        }

        # Send new ad
        await context.bot.send_message(
            chat_id=chat_id,
            text=html_text,
            parse_mode="HTML",
            reply_markup=build_link_keyboard(ad_id, url),
            disable_web_page_preview=True
        )

    except Exception as e:
        print(f"Ad display error: {e}")
        if update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ Failed to load ad"
            )
            
async def link_skip(update: Update, context: ContextTypes.DEFAULT_TYPE, ad_id=None):
    """Handle ad skipping with guaranteed ad display"""
    query = update.callback_query
    try:
        await query.answer("⏭ Ad skipped")  # Immediate feedback
        
        user_id = query.from_user.id
        chat_id = query.message.chat_id
        message_id = query.message.message_id
        
        ad_id = ad_id or int(query.data.split(":", 1)[1])

        # 1. Record skip in database
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE link_ads_details 
                    SET skipped = skipped + 1 
                    WHERE ad_id = %s
                """, (ad_id,))
                
                cursor.execute("""
                    INSERT INTO user_skipped_ads (user_id, ad_id)
                    VALUES (%s, %s)
                    ON CONFLICT DO NOTHING
                """, (user_id, ad_id))
                conn.commit()

        # 2. Delete old message (skip if fails)
        try:
            await context.bot.delete_message(chat_id, message_id)
        except Exception as e:
            print(f"Message deletion failed: {e}")

        # 3. Clear any existing ad state
        context.user_data.pop(f"link_ad_{ad_id}", None)

        # 4. Show loading indicator
        loading_msg = await context.bot.send_message(
            chat_id=chat_id,
            text="🔄 Loading next ad..."
        )

        # 5. Display new ad
        await message_link_ads(update, context)

        # 6. Clean up loading message
        try:
            await loading_msg.delete()
        except:
            pass

    except Exception as e:
        print(f"Skip error: {e}")
        if update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ Error loading next ad"
            )
            
async def link_visited(update: Update, context: ContextTypes.DEFAULT_TYPE, ad_id: int):
    query = update.callback_query
    chat_id = query.message.chat_id
    message_id = query.message.message_id
    user_id = query.from_user.id

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                
                # 1️⃣ Check if user already clicked this ad
                cursor.execute("""
                    SELECT 1 FROM link_ads_clicks 
                    WHERE ad_id = %s AND user_id = %s
                """, (ad_id, user_id))
                if cursor.fetchone():
                    return  # Stop further processing

                # 2️⃣ Get CPC for this ad
                cursor.execute("SELECT cpc FROM link_ads_details WHERE ad_id = %s", (ad_id,))
                cpc = float(cursor.fetchone()[0])
                reward = round(cpc * 0.8, 6)

                # 3️⃣ Record the click
                cursor.execute("""
                    INSERT INTO link_ads_clicks (ad_id, user_id)
                    VALUES (%s, %s)
                """, (ad_id, user_id))

                # 4️⃣ Update ad click count
                cursor.execute("""
                    UPDATE link_ads_details 
                    SET clicks = clicks + 1 
                    WHERE ad_id = %s
                """, (ad_id,))

                # 5️⃣ Add reward to user balance
                cursor.execute("""
                    UPDATE clickbotusers
                    SET payout_balance = payout_balance + %s
                    WHERE id = %s
                """, (Decimal(str(reward)), user_id))

                # 6️⃣ Process referral bonus
                cursor.execute("SELECT referral_id FROM clickbotusers WHERE id = %s", (user_id,))
                referrer = cursor.fetchone()
                if referrer and referrer[0]:
                    bonus = round(reward * 0.15, 6)
                    cursor.execute("""
                        UPDATE clickbotusers
                        SET payout_balance = payout_balance + %s
                        WHERE id = %s
                    """, (Decimal(str(bonus)), referrer[0]))

                conn.commit()

        # ✅ Send reward message
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🎉 Congratulations! You've earned {reward:.6f} SOL",
            reply_to_message_id=message_id
        )

        await start(update, context)

    except Exception as e:
        print(f"Error in link_visited: {e}")
            
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
                CREATE TABLE IF NOT EXISTS broadcast_clickbot (
                    user_id BIGINT PRIMARY KEY
                )
            ''')
            conn.commit()

    # Fetch all user IDs
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute('SELECT user_id FROM broadcast_clickbot')
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

def main():
    application = ApplicationBuilder().token(TOKEN).build()
    application.add_error_handler(error_handler)
    application.job_queue.run_daily(
        send_daily_task_count,
        time=time(hour=9, minute=0),  # Use time() directly
        name="daily_task_notification"
    )

    # 1. Create conversation handlers FIRST
    withdraw_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➖ Withdraw$"), start_withdraw)],
        states={
            ASK_WALLET: [
                CallbackQueryHandler(withdraw_button_handler, pattern="^set_wallet$"),
                MessageHandler(filters.Regex("^🔙 Cancel$"), cancel_withdraw),
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_wallet_address),
            ],
            ASK_WITHDRAW_AMOUNT: [
                MessageHandler(filters.Regex("^🔙 Cancel$"), cancel_withdraw),
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_withdraw_amount),
            ],
        },
        fallbacks=[MessageHandler(filters.Regex("^🔙 Cancel$"), cancel_withdraw)],
    )


    bot_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🤖 Bot$"), bot_start)],
        states={
            BOT_FORWARD_MSG: [MessageHandler(filters.ALL & ~filters.COMMAND, bot_forward_msg_handler)],
            BOT_PROMO_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot_promo_link_handler)],
            BOT_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot_title_handler)],
            BOT_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot_description_handler)],
            BOT_CPC: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot_cpc_handler)],
            BOT_BUDGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot_budget_handler)],
        },
        fallbacks=[CommandHandler("cancel", bot_cancel_handler)],
    )

    channel_ad_conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^📣 Channel or Group$"), channel_ad_start)
        ],
        states={
            CHANNEL_USERNAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, channel_username_handler)
            ],
            CHANNEL_TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, channel_title_handler)
            ],
            CHANNEL_DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, channel_description_handler)
            ],
            CHANNEL_CPC: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, channel_cpc_handler)
            ],
            CHANNEL_BUDGET: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, channel_budget_handler)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_handler)]
    )

    post_views_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📃 Post Views$"), post_views_start)],
        states={
            POST_MSG: [MessageHandler(filters.ALL & ~filters.COMMAND, post_views_message_handler)],
            POST_CPC: [MessageHandler(filters.TEXT & ~filters.COMMAND, post_views_cpc_handler)],
            POST_BUDGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, post_views_budget_handler)],
        },
        fallbacks=[CommandHandler("cancel", post_views_cancel_handler)],
    )

    link_url_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🔗 Link URL$"), link_url_start)],
        states={
            LINK_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, link_url_handler)],
            LINK_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, link_title_handler)],
            LINK_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, link_description_handler)],
            LINK_CPC: [MessageHandler(filters.TEXT & ~filters.COMMAND, link_cpc_handler)],
            LINK_BUDGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, link_budget_handler)],
        },
        fallbacks=[CommandHandler("cancel", link_cancel_handler)],
    )


    deposit_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ Deposit$"), start_deposit)],
        states={
            ASK_DEPOSIT_AMOUNT: [
                MessageHandler(filters.Regex("^🔙Back$"), cancel_deposit),
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_deposit_amount),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_deposit)],
    )

    # 2. Add conversation handlers BEFORE other handlers
    application.add_handler(withdraw_conv_handler)
    application.add_handler(channel_ad_conv_handler)
    application.add_handler(bot_conv_handler)
    application.add_handler(post_views_conv_handler)
    application.add_handler(link_url_conv_handler)
    application.add_handler(deposit_conv_handler)
   
    # 3. Add command handlers
    handlers = [
        CommandHandler("start", start),
        CommandHandler("help", help_command),
        CommandHandler("balance", balance_command),
        CommandHandler("withdraw", start_withdraw),
        CommandHandler("deposit", start_deposit),
        CommandHandler("newad", newad_start),
    ]
    for handler in handlers:
        application.add_handler(handler)

    # 4. Add callback handler
    application.add_handler(CallbackQueryHandler(callback_query_handler))
    
    # 5. Add unified message handler LAST
    application.add_handler(MessageHandler((filters.TEXT | filters.FORWARDED) & ~filters.COMMAND, unified_message_handler))

    # Run the bot
    application.run_polling()

if __name__ == "__main__":
    main()



















































