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
from flask import request
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
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_URL = "https://solearnhivebot.onrender.com/webhook"
BOT_LOOP = None

CREATOR_ID = 7112609512  # Replace with your actual Telegram user ID
BOT_USERNAME = "solearnhivebot"
MIN_WITHDRAW = 0.1  # Minimum allowed
UTC = pytz.utc
NOWPAYMENTS_API_KEY = "5RRXFWG-7ZY41Q9-P19J9DZ-Q3QSZJM"
app = Flask(__name__)

def get_db_connection():
    """Get a connection to the Supabase PostgreSQL database."""
    conn = psycopg.connect(os.getenv("DATABASE_URL"), sslmode="require")
    return conn

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


# Rate limiting storage
user_last_request = {}


# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize databases
init_databases()

# ----------------- FLASK root (uptime) -----------------
@app.route('/', methods=['GET'])
def home():
    return "Bot is running ✅", 200

# ----------------- Create Application (Telegram) -----------------
application = Application.builder().token(TOKEN).build()

# ----------------- Helper: get_user etc (adapted) -----------------
def get_user(user_id: int) -> dict:
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, general_balance, payout_balance, deposit_address, wallet_address, referral_id FROM clickbotusers WHERE id = %s", (user_id,))
            row = cur.fetchone()
            if not row:
                # create user
                cur.execute("INSERT INTO clickbotusers (id) VALUES (%s)", (user_id,))
                conn.commit()
                cur.execute("SELECT id, general_balance, payout_balance, deposit_address, wallet_address, referral_id FROM clickbotusers WHERE id = %s", (user_id,))
                row = cur.fetchone()
            # Map columns carefully (some may be None)
            return {
                "id": row[0],
                "general_balance": float(row[1] or 0),
                "payout_balance": float(row[2] or 0),
                "deposit_address": row[3],
                "wallet_address": row[4],
                "referral_id": row[5]
            }

def update_balances(user_id: int, general: float = None, payout: float = None):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            if general is not None:
                cur.execute("UPDATE clickbotusers SET general_balance = %s WHERE id = %s", (general, user_id))
            if payout is not None:
                cur.execute("UPDATE clickbotusers SET payout_balance = %s WHERE id = %s", (payout, user_id))
            conn.commit()

def set_deposit_address(user_id: int, address: str):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE clickbotusers SET deposit_address = %s WHERE id = %s", (address, user_id))
            conn.commit()

def get_deposit_address(user_id: int):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT deposit_address FROM clickbotusers WHERE id = %s", (user_id,))
            r = cur.fetchone()
            return r[0] if r else None

def convert_earnings_to_general(user_id: int):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT general_balance, payout_balance FROM clickbotusers WHERE id = %s", (user_id,))
            r = cur.fetchone()
            if not r:
                return False, 0.0
            general, payout = float(r[0] or 0), float(r[1] or 0)
            if payout <= 0:
                return False, 0.0
            new_general = general + payout
            cur.execute("UPDATE clickbotusers SET general_balance = %s, payout_balance = 0 WHERE id = %s", (new_general, user_id))
            conn.commit()
            return True, payout

def add_referral_deposit_bonus(user_id: int, deposit_amount: float):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT referral_id FROM clickbotusers WHERE id = %s", (user_id,))
            row = cur.fetchone()
            if not row or not row[0]:
                return
            referrer_id = row[0]
            bonus = deposit_amount * 0.02
            cur.execute("UPDATE clickbotusers SET payout_balance = COALESCE(payout_balance,0) + %s WHERE id = %s", (bonus, referrer_id))
            conn.commit()

def add_referral_task_bonus(user_id: int, earning_amount: float):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT referral_id FROM clickbotusers WHERE id = %s", (user_id,))
            row = cur.fetchone()
            if not row or not row[0]:
                return
            referrer_id = row[0]
            bonus = earning_amount * 0.15
            cur.execute("UPDATE clickbotusers SET payout_balance = COALESCE(payout_balance,0) + %s WHERE id = %s", (bonus, referrer_id))
            conn.commit()

# ----------------- BOT HANDLERS -----------------
START_TEXT = """🔥 Welcome to @SolEarnHiveBot 🔥

This bot lets you earn SOL by completing simple tasks:
🖥️ Visit sites to earn
🤖 Message bots to earn
📣 Join chats to earn
👁️ Watch ads to earn

You can also create your own ads with /newad

Use the /help command or visit updates channel for more info.
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
    args = context.args
    referral_id = None
    if args and args[0].isdigit():
        referral_id = int(args[0])
        if referral_id == user_id:
            referral_id = None
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM clickbotusers WHERE id = %s", (user_id,))
            if cur.fetchone():
                # user exists, just show menu
                await update.message.reply_text(text=START_TEXT, parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(REPLY_KEYBOARD, resize_keyboard=True))
                return
            cur.execute("INSERT INTO clickbotusers (id, general_balance, payout_balance, referral_id) VALUES (%s, 0, 0, %s)", (user_id, referral_id))
            conn.commit()
    await update.message.reply_text(text=START_TEXT, parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(REPLY_KEYBOARD, resize_keyboard=True))

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Use the menu or commands. /start to show menu.")

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    general = float(user.get("general_balance", 0))
    payout = float(user.get("payout_balance", 0))
    message = (
        f"💰 *Your Balance*\n\n"
        f"• 🪙 *General Balance:* {general:.6f} SOL\n"
        f"• 💸 *Available for Payout:* {payout:.6f} SOL\n\n"
        f"Use the options below to manage your wallet."
    )
    BALANCE_MENU_KEYBOARD = ReplyKeyboardMarkup([
        ["➕ Deposit", "➖ Withdraw"],
        ["📜 History", "🔁 Convert"],
        ["🔙 Back"]
    ], resize_keyboard=True)
    await update.message.reply_text(text=message, parse_mode="Markdown", reply_markup=BALANCE_MENU_KEYBOARD)

# unified_message_handler (menu)
async def unified_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    text = update.message.text or ""
    if text == "💰 Balance":
        await balance_command(update, context)
    elif text == "🙌 Referrals":
        await referrals_command(update, context)
    elif text == "📜 History":
        await update.message.reply_text("🛠 Transaction history will show here.")
    elif text == "🔁 Convert":
        await handle_convert(update, context)
    elif text == "🔙 Back":
        await start(update, context)
    else:
        await start(update, context)

# convert handler
async def handle_convert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    success, amount = convert_earnings_to_general(user_id)
    if success:
        await update.message.reply_text(f"🔁 Converted *{amount:.6f} SOL* from `Available for Payout` to `General Balance` ✅", parse_mode="Markdown")
    else:
        await update.message.reply_text("⚠️ Nothing to convert. Your payout balance is empty.", parse_mode="Markdown")

# ----------------- DEPOSIT CONVERSATION -----------------
ASK_DEPOSIT_AMOUNT = 1
async def start_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply_markup = ReplyKeyboardMarkup([["🔙Back"]], resize_keyboard=True)
    await update.message.reply_text("💸 How much SOL would you like to deposit?\n\nPlease enter the amount (e.g. `0.5`):", parse_mode="Markdown", reply_markup=reply_markup)
    return ASK_DEPOSIT_AMOUNT

async def process_deposit_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if text == "🔙Back":
        return await cancel_deposit(update, context)
    user_id = update.effective_user.id
    try:
        amount = float(text)
        if amount <= 0:
            raise ValueError
    except Exception:
        await update.message.reply_text("❌ Please enter a valid amount greater than 0.")
        return ASK_DEPOSIT_AMOUNT
    result = create_payment(user_id, amount)
    if result.get("invoice_url"):
        await update.message.reply_text(
            f"Click below to complete your deposit of *{amount:.6f} SOL*\nYou can pay in any crypto of your choice:\n\n{result['invoice_url']}\n\n💡 Payment in other cryptocurrencies will be automatically converted into SOL",
            parse_mode="Markdown", reply_markup=ReplyKeyboardRemove()
        )
    else:
        await update.message.reply_text("❌ Failed to generate deposit link. Try again later.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

async def cancel_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Deposit process canceled.", reply_markup=ReplyKeyboardRemove())
    await start(update, context)
    return ConversationHandler.END

# ----------------- WITHDRAW CONVERSATION -----------------
ASK_WALLET, ASK_WITHDRAW_AMOUNT = range(2)
async def start_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    wallet_address = user.get("wallet_address")
    # reply markup for cancel
    reply_markup = ReplyKeyboardMarkup([["🔙 Cancel"]], resize_keyboard=True)
    if not wallet_address:
        keyboard = [[InlineKeyboardButton("➕ Set / Change Wallet", callback_data="set_wallet")]]
        await update.message.reply_text("⚠️ You have not set a withdrawal wallet address.", reply_markup=InlineKeyboardMarkup(keyboard))
        return ASK_WALLET
    payout_balance = float(user.get("payout_balance", 0))
    if payout_balance < MIN_WITHDRAW:
        await update.message.reply_text(f"❌ You must have at least {MIN_WITHDRAW} SOL to withdraw.\n💰 Current balance: {payout_balance:.6f} SOL")
        return ConversationHandler.END
    await update.message.reply_text(f"💳 Your withdrawal wallet is:\n`{wallet_address}`\n\nEnter the amount of SOL you wish to withdraw:", parse_mode="Markdown", reply_markup=reply_markup)
    return ASK_WITHDRAW_AMOUNT

async def withdraw_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "set_wallet":
        await query.edit_message_text("📩 SEND ME YOUR SOLANA WALLET ADDRESS to use for future withdrawals.\n\n✅ Make sure it's correct — this will be saved in your account.")
        return ASK_WALLET

async def process_wallet_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    user_id = update.effective_user.id
    if text == "🔙 Cancel":
        return await cancel_withdraw(update, context)
    if len(text) < 20:
        await update.message.reply_text("❌ Invalid address. Please send a valid Solana address.")
        return ASK_WALLET
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE clickbotusers SET wallet_address = %s WHERE id = %s", (text, user_id))
            conn.commit()
    await update.message.reply_text(f"✅ Wallet address saved:\n`{text}`\n\nNow send me the amount of SOL you want to withdraw:", parse_mode="Markdown")
    return ASK_WITHDRAW_AMOUNT

async def process_withdraw_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if text == "🔙 Cancel":
        return await cancel_withdraw(update, context)
    user_id = update.effective_user.id
    try:
        amount = float(text)
        if amount <= 0:
            raise ValueError
    except Exception:
        await update.message.reply_text("❌ Please enter a valid withdrawal amount.")
        return ASK_WITHDRAW_AMOUNT
    user = get_user(user_id)
    payout_balance = float(user.get("payout_balance", 0))
    wallet_address = user.get("wallet_address")
    if amount < MIN_WITHDRAW:
        await update.message.reply_text(f"❌ Minimum withdrawal is {MIN_WITHDRAW} SOL")
        return ASK_WITHDRAW_AMOUNT
    if amount > payout_balance:
        await update.message.reply_text("❌ Insufficient payout balance.")
        return ASK_WITHDRAW_AMOUNT
    # deduct and record
    new_balance = payout_balance - amount
    update_balances(user_id, payout=new_balance)
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO withdrawals (user_id, amount, address, status) VALUES (%s, %s, %s, %s)", (user_id, amount, wallet_address, "pending"))
            conn.commit()
    await update.message.reply_text(f"✅ Withdrawal request submitted:\n💸 *{amount:.6f} SOL* to `{wallet_address}`\n\n⏳ Awaiting manual processing.", parse_mode="Markdown")
    await context.bot.send_message(chat_id=CREATOR_ID, text=f"🔔 New withdrawal request\nUser ID: {user_id}\nAmount: {amount} SOL\nAddress: {wallet_address}")
    return ConversationHandler.END

async def cancel_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Withdrawal process canceled.", reply_markup=ReplyKeyboardRemove())
    await start(update, context)
    return ConversationHandler.END

# ----------------- REFERRALS -----------------
async def referrals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_username = (await context.bot.get_me()).username
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT payout_balance FROM clickbotusers WHERE id = %s", (user_id,))
            row = cur.fetchone()
            payout_balance = float(row[0] or 0) if row else 0
            cur.execute("SELECT COUNT(*) FROM clickbotusers WHERE referral_id = %s", (user_id,))
            total_refs = cur.fetchone()[0]
    referral_link = f"https://t.me/{bot_username}?start={user_id}"
    share_text = ("🎙 Click2Earn With SOL EarnHive!\n\nEarn CRYPTO based on your social media activity — Viewing, liking, commenting, or joining TG channels.\n\nStart using SOL EarnHive today!\n\n" + referral_link)
    share_url = f"https://t.me/share/url?url={referral_link}&text={share_text.replace(' ', '+')}"
    keyboard = [[InlineKeyboardButton("📤 Share", url=share_url)]]
    text = (f"🔍 You have *{total_refs}* referrals, and earned *{payout_balance:.6f} SOL*.\n\n"
            f"To refer people to the bot, send them this link:\n`{referral_link}`\n\n"
            "💰 You will earn 15% of your friends' earnings from tasks, and 2% if your friend deposits.\n\n_You can withdraw affiliate income or spend it on ADS!_")
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

# ----------------- OTHER ADMIN / UTIL -----------------
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Update caused error: %s", context.error)
    if update and getattr(update, "message", None):
        try:
            await update.message.reply_text("⚠️ An error occurred.")
        except Exception:
            pass

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != CREATOR_ID:
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("❗Please reply to the message you want to broadcast.")
        return
    original = update.message.reply_to_message
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute('CREATE TABLE IF NOT EXISTS broadcast_chats (chat_id BIGINT PRIMARY KEY)')
            conn.commit()
            cur.execute('SELECT chat_id FROM broadcast_chats')
            chat_ids = [r[0] for r in cur.fetchall()]
    success = failed = 0
    for chat_id in chat_ids:
        try:
            if original.photo:
                await context.bot.send_photo(chat_id=chat_id, photo=original.photo[-1].file_id, caption=original.caption or "")
            elif original.video:
                await context.bot.send_video(chat_id=chat_id, video=original.video.file_id, caption=original.caption or "")
            elif original.text:
                await context.bot.send_message(chat_id=chat_id, text=original.text)
            else:
                await context.bot.forward_message(chat_id=chat_id, from_chat_id=original.chat.id, message_id=original.message_id)
            success += 1
        except Exception as e:
            logger.error("Broadcast failed to %s: %s", chat_id, e)
            failed += 1
    await update.message.reply_text(f"📢 Broadcast complete!\n\n✅ Sent: {success}\n❌ Failed: {failed}")

# ----------------- REGISTER HANDLERS -----------------
def register_handlers(app_obj):
    # deposit conv
    deposit_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ Deposit$"), start_deposit)],
        states={
            ASK_DEPOSIT_AMOUNT: [
                MessageHandler(filters.Regex("^🔙Back$"), cancel_deposit),
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_deposit_amount)
            ]
        },
        fallbacks=[MessageHandler(filters.Regex("^🔙Back$"), cancel_deposit), CommandHandler("cancel", cancel_deposit)],
        name="deposit_conv",
        persistent=False
    )
    app_obj.add_handler(deposit_conv)

    # withdraw conv
    withdraw_conv = ConversationHandler(
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
        name="withdraw_conv",
        persistent=False
    )
    app_obj.add_handler(withdraw_conv)
    application = Application.builder().token(TOKEN).build()

    # simple commands & handlers
    app_obj.add_handler(CommandHandler("start", start))
    app_obj.add_handler(CommandHandler("help", help_command))
    app_obj.add_handler(CommandHandler("broadcast", broadcast))
    app_obj.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unified_message_handler))
    app_obj.add_handler(CallbackQueryHandler(lambda u,c: None))  # placeholder for other callbacks you already have
    app_obj.add_error_handler(error_handler)

# register handlers to application
register_handlers(application)

# Initialize and start Application before Flask starts
async def init_bot():
    await application.initialize()
    await application.start()
    await application.bot.delete_webhook()
    await application.bot.set_webhook(WEBHOOK_URL)
    logger.info("Webhook set to %s", WEBHOOK_URL)

# ----------------- WEBHOOK route -----------------
@app.route("/webhook", methods=["POST"])
def webhook_entry():
    try:
        update = Update.de_json(request.get_json(force=True), application.bot)
        asyncio.run_coroutine_threadsafe(application.process_update(update), BOT_LOOP)
    except Exception as e:
        app.logger.error(f"Failed to process webhook update: {e}", exc_info=True)
    return "OK", 200




# ----------------- STARTUP: set webhook and run Flask -----------------
if __name__ == "__main__":
    BOT_LOOP = asyncio.get_event_loop()
    BOT_LOOP.run_until_complete(init_bot())
    app.run(host="0.0.0.0", port=8080)




