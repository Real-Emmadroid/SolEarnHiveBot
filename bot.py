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
import time
import pytz
from pytz import timezone as pytz_timezone  # to handle 'Africa/Lagos'
from flask import Flask
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
    get_db_connection, register_raider, save_reaction, update_premium_statuses, get_status_template, add_raid_template, is_premium_user, get_premium_status, redeem_password, add_premium_password
)

# Configuration
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

CREATOR_ID = 7112609512  # Replace with your actual Telegram user ID
FOLLOWER_PRICE = 0.02  # Per 1 follower (Dollar)
BANK_DETAILS = "SOL: BXPq18Cy9naEpk6A7ChDePS1R2bnWVNtxWcGsJP6apuY\nUSDT (Trc20): TL287Wo3sc8MM8uTvE6ksV3hMxMyHcnSdR"
TRENDING_CHANNEL_ID = -1002763078436
TRENDING_FORUM_ID = -1002763078436   # Replace with your group/forum ID
TRENDING_TOPIC_ID = 2            # Replace with your topic's message_thread_id
TRENDING11 = 11
TRENDING12 = 12
BOT_USERNAME = "sfttrendingbot"
UTC = pytz.utc


# Rate limiting storage
user_last_request = {}

@lru_cache(maxsize=1)
def get_leaderboard_hash(top_groups):
    return hash(tuple((g[0], g[2]) for g in top_groups))  # Hash group_id and vote_count

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


def setup_expiration_checks(job_queue: JobQueue):
    job_queue.run_daily(
        callback=check_expirations,
        time=time(hour=10, minute=0),  # 10:00 AM
        days=(0, 1, 2, 3, 4, 5, 6),  # All days
        name="daily_premium_checks"
    )
    


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


async def setlanguage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set user language with inline responses"""
    user = update.effective_user
    chat = update.effective_chat
    
    if not context.args:
        response = {
            "en": "Usage: /setlanguage <en|pg>",
            "pg": "How to use: /setlanguage <en|pg>"
        }
        lang = get_user_language(user.id)
        await update.message.reply_text(response.get(lang, response["en"]))
        return

    language = context.args[0].lower()
    if language not in ["en", "pg"]:
        response = {
            "en": "Unsupported language. Use: en, pg",
            "pg": "We no sabi this language. Use: en, pg"
        }
        lang = get_user_language(user.id)
        await update.message.reply_text(response.get(lang, response["en"]))
        return

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO user_languages (user_id, language_code)
                    VALUES (%s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET language_code = %s
                """, (user.id, language, language))
                conn.commit()
        
        response = {
            "en": "✅ Language set to English.",
            "pg": "✅ Language don change to Pidgin."
        }
        await update.message.reply_text(response.get(language, response["en"]))
    except Exception as e:
        logger.error(f"Set language error: {e}")
        await update.message.reply_text("⚠️ Error saving language preference")

async def setgrouplanguage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set group language with inline responses"""
    chat = update.effective_chat
    user = update.effective_user
    
    if chat.type == "private":
        response = {
            "en": "❗ This command can only be used in groups.",
            "pg": "❗ Na only for group you fit use this command."
        }
        lang = get_user_language(user.id)
        await update.message.reply_text(response.get(lang, response["en"]))
        return

    if not await is_admin(chat.id, user.id, context.bot):
        response = {
            "en": "❗ Only admins can change group language.",
            "pg": "❗ Na only admin fit change group language."
        }
        lang = get_group_language(chat.id)
        await update.message.reply_text(response.get(lang, response["en"]))
        return

    if not context.args:
        response = {
            "en": "Usage: /setgrouplanguage <en|pg>",
            "pg": "How to use: /setgrouplanguage <en|pg>"
        }
        lang = get_group_language(chat.id)
        await update.message.reply_text(response.get(lang, response["en"]))
        return

    language = context.args[0].lower()
    if language not in ["en", "pg"]:
        response = {
            "en": "Unsupported language. Use: en, pg",
            "pg": "We no sabi this language. Use: en, pg"
        }
        lang = get_group_language(chat.id)
        await update.message.reply_text(response.get(lang, response["en"]))
        return

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO group_languages (chat_id, language_code)
                    VALUES (%s, %s)
                    ON CONFLICT (chat_id) DO UPDATE SET language_code = %s
                """, (chat.id, language, language))
                conn.commit()
        
        response = {
            "en": "✅ Group language set to English.",
            "pg": "✅ Group language don change to Pidgin."
        }
        await update.message.reply_text(response.get(language, response["en"]))
    except Exception as e:
        logger.error(f"Set group language error: {e}")
        await update.message.reply_text("⚠️ Error saving group language")

async def hello_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Greeting with language support"""
    chat = update.effective_chat
    user = update.effective_user
    
    if chat.type == "private":
        lang = get_user_language(user.id)
    else:
        lang = get_group_language(chat.id)
    
    response = {
        "en": "Hello!",
        "pg": "Affa!"
    }
    await update.message.reply_text(response.get(lang, response["en"]))

def get_user_language(user_id):
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT language_code FROM user_languages WHERE user_id = %s", (user_id,))
            result = cursor.fetchone()
            return result[0] if result else 'en'

def get_group_language(chat_id):
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT language_code FROM group_languages WHERE chat_id = %s", (chat_id,))
            result = cursor.fetchone()
            return result[0] if result else 'en'


# Command Handlers
START_TEXT = """🔥 Welcome to @SolEarnHiveBot 🔥

⚠️ *WARNING:* If this bot is not listed on @SolEarnHiveUpdates, it's likely a scam.

This bot lets you earn TRX by completing simple tasks:
🖥️ Visit sites to earn
🤖 Message bots to earn
📣 Join chats to earn
👁️ Watch ads to earn

You can also create ads with /newad

Use /help and read FAQ for more info.
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
    
    # Force join check
    if not await is_user_joined_channel(context, user.id):
        await prompt_force_join(update, context)
        return
    
    await update.message.reply_text(
        text=START_TEXT,
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(REPLY_KEYBOARD, resize_keyboard=True)
    )

FORCE_JOIN_CHANNEL = "@SolEarnHiveUpdates"

async def is_user_joined_channel(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(FORCE_JOIN_CHANNEL, user_id)
        return member.status in ["member", "creator", "administrator"]
    except Exception as e:
        return False

async def prompt_force_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Join Channel", url=f"https://t.me/{FORCE_JOIN_CHANNEL.lstrip('@')}")],
        [InlineKeyboardButton("🔄 I've Joined", callback_data="check_joined")]
    ])
    await update.message.reply_text(
        "🚫 To use this bot, please join our channel first.",
        reply_markup=keyboard
    )

async def check_joined_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    if await is_user_joined_channel(context, user_id):
        await query.message.edit_text("✅ You're now verified. Send /start again.")
    else:
        await query.answer("❌ You haven't joined yet.", show_alert=True)



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

async def register_raider(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.message.reply_text("❗ Raider registration is only allowed in DM.")
        return

    user = update.effective_user

    if len(context.args) < 2:
        await update.message.reply_text("Please provide your Twitter link and nationality.\n\nFormat:\n`/register_raider <twitter_link> <nationality>`", parse_mode='Markdown')
        return

    twitter = context.args[0]
    nationality = " ".join(context.args[1:])

    # Acknowledge to user
    await update.message.reply_text("✅ Application sent. Review in progress...")

    # Notify creator
    text = (
        "📨 *New Raider Application*\n\n"
        f"👤 Username: @{user.username or 'N/A'}\n"
        f"🆔 ID: `{user.id}`\n"
        f"🌐 Twitter: {twitter}\n"
        f"🌍 Nationality: {nationality}\n\n"
        f"/manualapprove {user.id} {twitter} {nationality}\n"
        f"/manualreject {user.id}"
    )

    await context.bot.send_message(chat_id=CREATOR_ID, text=text, parse_mode='Markdown')

async def manualapprove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != CREATOR_ID:
        return
        
    if len(context.args) < 3:
        await update.message.reply_text("Usage:\n/manualapprove <user_id> <twitter_link> <nationality>")
        return

    user_id = int(context.args[0])
    twitter = context.args[1]
    nationality = " ".join(context.args[2:])
    
    # DM the user
    try:
        await context.bot.send_message(chat_id=user_id, text="🎉 Your raider application has been *approved*! Welcome aboard!", parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"Could not notify user: {e}")

    # Insert into DB
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO verified_raiders (user_id, twitter, nationality)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (user_id) DO NOTHING
                """, (user_id, twitter, nationality))
                conn.commit()
        await update.message.reply_text("✅ Raider approved and added to database.")
    except Exception as e:
        await update.message.reply_text(f"DB error: {e}")

async def manualreject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != CREATOR_ID:
        return
        
    if len(context.args) < 1:
        await update.message.reply_text("Usage:\n/manualreject <user_id>")
        return

    user_id = int(context.args[0])

    try:
        await context.bot.send_message(chat_id=user_id, text="❌ Your raider application has been *rejected*. You may try again later.", parse_mode='Markdown')
        await update.message.reply_text("❗ Raider rejected and notified.")
    except Exception as e:
        await update.message.reply_text(f"Error notifying user: {e}")


async def sendgroupid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat = update.effective_chat

    if chat.type not in ["group", "supergroup"]:
        await update.message.reply_text("This command can only be used in a group.")
        return

    group_id = chat.id
    group_title = chat.title or "Unnamed Group"

    message = f"📩 New team group submitted:\n\n🆔 Group ID: `{group_id}`\n🏷️ Group Name: *{group_title}*"

    await context.bot.send_message(
        chat_id=CREATOR_ID,
        text=message,
        parse_mode='Markdown'
    )

async def manualapproveteam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != CREATOR_ID:
        return  # Silent block for unauthorized users

    if len(context.args) < 1:
        await update.message.reply_text("Usage:\n/manualapproveteam <group_id>")
        return

    group_id = int(context.args[0])

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT group_name, custom_photo_url FROM group_votes WHERE group_id = %s", (group_id,))
                result = cursor.fetchone()

                if not result:
                    await update.message.reply_text("❌ Group not found in database.")
                    return

                group_name, photo_url = result

                # Update verification status
                cursor.execute("UPDATE group_votes SET verified = TRUE WHERE group_id = %s", (group_id,))
                conn.commit()

        # Button linking to verified leaderboard
        button = InlineKeyboardMarkup([
            [InlineKeyboardButton("✔️ Verified Shill Teams", url=f"https://t.me/stftrending/2/377")]
        ])

        caption = f"✅ *{group_name}* has been verified as an official Shill Team!\n\nWelcome aboard to the trending squad 🚀"

        # Send to the group
        await context.bot.send_photo(
            chat_id=group_id,
            photo=photo_url,
            caption=caption,
            parse_mode='Markdown',
            reply_markup=button
        )

        # Send to trending forum
        await context.bot.send_photo(
            chat_id=TRENDING_FORUM_ID,
            message_thread_id=TRENDING11,
            photo=photo_url,
            caption=caption,
            parse_mode='Markdown',
            reply_markup=button
        )

        await update.message.reply_text("✅ Shill team approved and notified.")

    except Exception as e:
        await update.message.reply_text(f"❌ Error approving team: {e}")


WEBAPP_LINK = "https://stftrending.github.io/tg/shill.html"

async def register_command(update: Update, context: CallbackContext):
    try:
        # Image URL (replace if you want a custom image)
        image_url = "https://t.me/myhostinger/34"  # Example banner

        # Message with image and choices
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=image_url,
            caption=(
                "📋 *RAIDER REGISTRATION*\n\n"
                "You can register faster through our *website*, or continue with the bot here.\n\n"
                "_Note that Shill Team registration is only available and accessible through the website_\n\n"
                "Choose your preferred method below 👇"
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Proceed on Website", url=WEBAPP_LINK)],
                [InlineKeyboardButton("🤖 Continue with Bot", url=f"https://t.me/{context.bot.username}?start=register_raider")]
            ])
        )
    except Exception as e:
        logging.error(f"Error in /register command: {e}")
        await update.message.reply_text("Something went wrong while displaying the registration options.")

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


# BUYING OF TWITTER FOLLOWERS FEATURES
ASK_COUNT, ASK_LINK, ASK_REGION, SHOW_PRICE, WAIT_FOR_PROOF = range(5)

async def buy_followers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        await update.message.reply_text("Make we no spam group, use command in my DM.")
        return ConversationHandler.END

    await update.message.reply_text("How many followers do you want? (e.g. 500)")
    return ASK_COUNT

async def ask_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    count = update.message.text.strip()
    if not count.isdigit():
        await update.message.reply_text("Please enter a valid number.")
        return ASK_COUNT

    context.user_data["followers_count"] = int(count)
    await update.message.reply_text("Send your Twitter account link:")
    return ASK_LINK

async def ask_region(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["twitter_link"] = update.message.text.strip()
    keyboard = [["Worldwide", "European"], ["Asian", "African"]]
    await update.message.reply_text(
        "Choose nationality of followers:",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
    )
    return ASK_REGION

async def show_payment_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    region = update.message.text.strip()
    context.user_data["region"] = region

    count = context.user_data["followers_count"]
    price = count * FOLLOWER_PRICE
    context.user_data["price"] = int(price)

    msg = (
        f"ORDER SUMMARY\n\n"
        f"Followers: {count}\n"
        f"Region: {region}\n"
        f"Twitter: {context.user_data['twitter_link']}\n"
        f"Total Amount: ${int(price)}"
    )

    button = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Proceed to Payment", callback_data="proceed_payment")]
    ])

    await update.message.reply_text(msg, reply_markup=button)
    return SHOW_PRICE

async def proceed_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    pay_info = (
        f"SUPPORTED NETWORKS\n\n"
        f"{BANK_DETAILS}\n\n"
        f"After payment, click the PAID button below and upload a screenshot of payment."
    )

    paid_btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ PAID", callback_data="paid_button")]
    ])

    await query.edit_message_text(text=pay_info, reply_markup=paid_btn)
    return WAIT_FOR_PROOF

async def paid_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Please upload a valid screenshot proof of your transaction now.")
    return WAIT_FOR_PROOF

async def handle_payment_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    proof = update.message.photo[-1].file_id  # Get highest quality image

    caption = (
        f"📥 NEW PAYMENT PROOF\n\n"
        f"User: @{user.username} ({user.id})\n"
        f"Followers: {context.user_data['followers_count']}\n"
        f"Link: {context.user_data['twitter_link']}\n"
        f"Region: {context.user_data['region']}\n"
        f"Amount Paid: ${context.user_data['price']}"
    )

    await context.bot.send_photo(chat_id=CREATOR_ID, photo=proof, caption=caption)
    await update.message.reply_text("✅ Order Submitted.\n\n After successful confirmation, expect followers in less than 48 hours.\n\n Drip mode enabled to avoid spam.")
    return ConversationHandler.END

async def set_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global FOLLOWER_PRICE
    if update.effective_user.id != CREATOR_ID:
        await update.message.reply_text("Only the creator can update pricing.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /setprice <naira_amount_per_100_followers>")
        return

    try:
        FOLLOWER_PRICE = int(context.args[0])
        await update.message.reply_text(f"New price set: ₦{FOLLOWER_PRICE} per followers.")
    except:
        await update.message.reply_text("Invalid price.")

async def mark_completed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != CREATOR_ID:
        await update.message.reply_text("Only the creator can run this.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /markcompleted <user_id>")
        return

    user_id = int(context.args[0])
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text="YOUR ORDER HAS BEEN COMPLETED..."
        )
        await update.message.reply_text("Order marked as completed.")
    except Exception as e:
        await update.message.reply_text(f"Failed to notify user: {e}")

async def mark_failed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != CREATOR_ID:
        await update.message.reply_text("Only the creator can run this.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /markfailed <user_id>")
        return

    user_id = int(context.args[0])
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text="⚠️ Sorry, We could'nt verify your payment..."
        )
        await update.message.reply_text("Order marked as failed.")
    except Exception as e:
        await update.message.reply_text(f"Failed to notify user: {e}")


async def get_group_name(group_id: int, context: ContextTypes.DEFAULT_TYPE) -> str:
    try:
        chat = await context.bot.get_chat(group_id)
        return chat.title
    except Exception as e:
        logger.warning(f"Couldn't fetch group name for {group_id}: {e}")
        return f"Group-{group_id}"

async def get_group_vote_count(group_id: int) -> int:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT vote_count FROM group_votes WHERE group_id = %s", (group_id,))
            result = cursor.fetchone()
            return result[0] if result else 0

async def get_group_rank(group_id: int) -> int:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT COUNT(*) FROM group_votes 
                WHERE vote_count > (SELECT vote_count FROM group_votes WHERE group_id = %s)
            """, (group_id,))
            return cursor.fetchone()[0] + 1

def format_time(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"

async def check_vote_cooldown(user_id: int, is_premium: bool) -> int:
    cooldown_hours = 3 if is_premium else 6
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT EXTRACT(EPOCH FROM (voted_at + INTERVAL '%s hours' - NOW()))
                FROM user_votes
                WHERE user_id = %s
                ORDER BY voted_at DESC
                LIMIT 1
            """, (cooldown_hours, user_id))
            result = cursor.fetchone()
            return max(0, int(result[0])) if result else 0

### Updated Notification Functions ###
async def send_vote_notification(group_id: int, new_count: int, context: ContextTypes.DEFAULT_TYPE):
    # Skip notification if disabled for this group
    if not is_vote_notification_enabled(group_id):
        return

    try:
        # Fetch group details including display link
        group_name, display_link, *_ = await get_group_details(group_id, context)
        
        # Format group name with link if available
        if display_link:
            group_display = f"[{group_name}]({display_link})"
        else:
            group_display = f"*{group_name}*"
        
        position = await get_group_rank(group_id)
        votes_needed = max(0, 10 - new_count)
        leaderboard_link = f"https://t.me/stftrending/2/377"
        
        # Create the message
        await context.bot.send_photo(
            chat_id=group_id,
            photo="https://t.me/myhostinger/2",
            caption=(
                f"🗳 *NEW VOTE RECEIVED!* \n\n"
                f"🏆 *Group:* {group_display}\n"
                f"📊 *Current Votes:* {new_count}\n"
                f"📈 *Leaderboard Position:* #{position}\n"
                f"🗳 *Votes needed to trend:* {votes_needed}\n\n"
                f"{'🎉 *CONGRATULATIONS!* Your group is now trending on the [leaderboard](' + leaderboard_link + ')!' if new_count >= 10 else ''}"
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "🗳 VOTE ", 
                    url=f"https://t.me/{context.bot.username}?start=poll_{group_id}"
                )],
                [InlineKeyboardButton(
                    "🏆 VIEW LEADERBOARD", 
                    url=leaderboard_link
                )]
            ])
        )
        
    except Exception as e:
        logger.error(f"Vote notification error: {e}")
        # Fallback without image
        await context.bot.send_message(
            chat_id=group_id,
            text=f"🗳 New vote for {group_name} - Total: {new_count}",
            parse_mode="Markdown"
        )

async def get_group_details(group_id: int, context: ContextTypes.DEFAULT_TYPE) -> tuple:
    """Fetch group name, display link, and custom photo URL from database"""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT group_name, display_link, contactme_link, custom_photo_url
                    FROM group_votes 
                    WHERE group_id = %s
                """, (group_id,))
                result = cursor.fetchone()
                
                if result:
                    return result  # returns all 4: (group_name, display_link, contactme_link, custom_photo_url)
    except Exception as e:
        logger.error(f"Failed to fetch group details: {e}")
    
    # Fallback if not in database
    name = await get_group_name(group_id, context)
    return name, None, None

    
async def send_leaderboard_notification(group_id: int, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Get group details with display and photo link
        group_name, display_link, contactme_link, custom_photo_url = await get_group_details(group_id, context)

        # Format group name with display link if available
        if display_link:
            group_display = f"[{group_name}]({display_link})"
        else:
            group_display = f"*{group_name}*"

        # Prepare image
        photo = None

        if custom_photo_url:
            # Use Telegram-hosted photo URL
            photo = custom_photo_url

        else:
            # Try fetching group profile photo
            try:
                chat = await context.bot.get_chat(group_id)
                if chat.photo:
                    photo_file = await chat.photo.big_file.download_as_bytearray()
                    photo_bytes = BytesIO(photo_file)
                    photo_bytes.name = 'group_photo.jpg'
                    photo = photo_bytes
            except Exception:
                pass  # Will fallback to default image

        if not photo:
            photo = "https://t.me/myhostinger/3"  # Default image URL fallback

        # Message text
        leaderboard_link = "https://t.me/stftrending/2/377"
        message_text = (
            f"🚀 {group_display} HAS OFFICIALLY [ENTERED THE LEADERBOARD]({leaderboard_link})!\n\n"
            "🔵 [DEX](https://t.me/stfinfoportal/235) ┃ "
            "📖 [RULES](https://t.me/stfinfoportal/183) ┃ "
            "🔥 [BOOST](https://t.me/stftrendingbot?start=boosttrend)\n"
            "🟣 [TRENING SPOT](https://t.me/stftrendingbot?start=boostvote)\n\n"
            "Congratulations on reaching 10 votes! 🎉"
        )

        keyboard_buttons = []

        # Only add group redirect if display_link is available
        if contactme_link:
            keyboard_buttons.append(
                InlineKeyboardButton("💼 Hire Shill Team", url=contactme_link)
            )

        keyboard_buttons.append(
            InlineKeyboardButton("Verified Shill Teams", url="https://t.me/stftrending/11/411")
        )

        keyboard = InlineKeyboardMarkup([[btn] for btn in keyboard_buttons])


        # Send photo message
        await context.bot.send_photo(
            chat_id=TRENDING_CHANNEL_ID,
            message_thread_id=TRENDING_TOPIC_ID,
            photo=photo,
            caption=message_text,
            parse_mode="Markdown",
            reply_markup=keyboard
        )

    except Exception as e:
        logger.error(f"Leaderboard notification failed: {e}")

        # Fallback plain message if all fails
        await context.bot.send_message(
            chat_id=TRENDING_CHANNEL_ID,
            message_thread_id=TRENDING_TOPIC_ID,
            text=f"🚀 {group_name} HAS OFFICIALLY ENTERED THE LEADERBOARD!",
            parse_mode="Markdown"
        )
        
# Add this helper function to get group profile photo
async def get_group_profile_photo(group_id: int, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat = await context.bot.get_chat(group_id)
        if chat.photo:
            photo_file = await chat.photo.big_file.download_as_bytearray()
            return BytesIO(photo_file)
    except Exception as e:
        logger.warning(f"Couldn't get group photo: {e}")
    return None

# Define conversation states
BOOST_GET_GROUP, BOOST_SHOW_PLANS, BOOST_PAYMENT, BOOST_HASH = range(4)  # Use 4 distinct states

async def boost_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        await update.message.reply_text("Please use this command in my DM.")
        return ConversationHandler.END

    # Clear previous data
    context.user_data.clear()
    
    await update.message.reply_text(
        "📋 TO BOOST YOUR GROUP ON TRENDING\n\n"
        "1. Forward any message from your group to @userdatailsbot\n"
        "2. It will reply with your Group ID\n"
        "3. Send me that ID now\n\n"
        "Example: -123456789\n\n"
        "Couldn't find your group ID? Message @iam_emmadroid for assistance\n\n"
        "Or reload /boostvote"
    )
    return BOOST_GET_GROUP

async def get_group_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = update.message.text.strip()
    
    if not group_id.lstrip('-').isdigit():
        await update.message.reply_text("❌ Invalid Group ID. Please send only numbers.")
        return BOOST_GET_GROUP

    try:
        chat = await context.bot.get_chat(int(group_id))
        context.user_data['group_id'] = group_id
        context.user_data['group_name'] = chat.title
        
        # Show boost plans (same pattern as your followers flow)
        plans = [
            {"votes": 10, "price": 0.002, "sol": "BXPq18Cy9naEpk6A7ChDePS1R2bnWVNtxWcGsJP6apuY"},
            {"votes": 50, "price": 0.009, "sol": "BXPq18Cy9naEpk6A7ChDePS1R2bnWVNtxWcGsJP6apuY"},
            {"votes": 100, "price": 0.02, "sol": "BXPq18Cy9naEpk6A7ChDePS1R2bnWVNtxWcGsJP6apuY"}
        ]
        
        buttons = [
            [InlineKeyboardButton(
                f"{p['votes']} Votes - {p['price']} SOL",
                callback_data=f"plan_{p['votes']}"
            )] for p in plans
        ]
        
        await update.message.reply_text(
            f"🚀 Boost Plans for {chat.title}",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return BOOST_SHOW_PLANS

    except Exception:
        await update.message.reply_text("❌ Couldn't verify this group. Check the ID and try again.")
        return BOOST_GET_GROUP

async def handle_plan_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    _, votes = query.data.split('_')
    context.user_data['selected_plan'] = {
        'votes': int(votes),
        'price': 0.002 if votes == '10' else 0.009 if votes == '50' else 0.02
    }
    
    # Payment instructions (like your followers flow)
    await query.edit_message_text(
        f"💳 Send {context.user_data['selected_plan']['price']} SOL to:\n\n"
        f"SOL_ADDR_{votes[-1]}\n\n"
        "After payment, click below to submit your transaction hash:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm Payment", callback_data="submit_hash")]
        ])
    )
    return BOOST_PAYMENT

async def request_hash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🔗 Please reply with your Solana transaction hash:\n\n"
        "Example: https://solscan.io/tx/5428...\n\n"
        "Find this in your wallet's transaction history."
    )
    return BOOST_HASH

async def receive_hash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tx_hash = update.message.text.strip()
    
    # Send to creator (like your followers proof)
    await context.bot.send_message(
        chat_id=CREATOR_ID,
        text=f"🔄 Boost Payment\n\n"
             f"Group: {context.user_data['group_name']}\n"
             f"User: @{update.effective_user.username}\n"
             f"🆔 User ID: {update.effective_user.id}\n"
             f"Votes: {context.user_data['selected_plan']['votes']}\n"
             f"Amount: {context.user_data['selected_plan']['price']} SOL\n"
             f"Hash: {tx_hash}"
    )
    
    await update.message.reply_text(
        "✅ Order submitted! Votes will be added after confirmation.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END
    
async def manual_add_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Creator-only command to manually add votes to a group"""
    if update.effective_user.id != CREATOR_ID:
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /manualaddvote <group_id> <vote_count> [user_id]\n\n"
            "Example:\n"
            "/manualaddvote 123456789 50 987654321"
        )
        return

    try:
        group_id = int(context.args[0])
        vote_count = int(context.args[1])
        user_id = int(context.args[2]) if len(context.args) > 2 else None

        try:
            chat = await context.bot.get_chat(group_id)
            group_name = chat.title
            group_photo = chat.photo.big_file_id if chat.photo else None
        except:
            group_name = f"Group {group_id}"
            group_photo = None

        with get_db_connection() as conn:
            with conn.cursor() as cursor:

                # --- Check if group exists in group_votes
                cursor.execute("SELECT 1 FROM group_votes WHERE group_id = %s", (group_id,))
                in_group_votes = cursor.fetchone() is not None

                # --- Check if group exists in hyped_projects
                cursor.execute("SELECT 1 FROM hyped_projects WHERE group_id = %s", (group_id,))
                in_hyped_projects = cursor.fetchone() is not None

                new_count = 0
                target_table = None

                if in_group_votes:
                    target_table = "group_votes"
                elif in_hyped_projects:
                    target_table = "hyped_projects"

                expiry_time = datetime.now(timezone.utc) + timedelta(hours=24)

                # --- If group found in either table, update vote_count
                if target_table:
                    cursor.execute(
                        f"""
                        UPDATE {target_table}
                        SET vote_count = COALESCE(vote_count, 0) + %s,
                            vote_expiry = %s
                        WHERE group_id = %s
                        RETURNING vote_count
                        """, (vote_count, expiry_time, group_id)
                    )
                    new_count = cursor.fetchone()[0]

                # --- If not found anywhere, insert into group_votes by default
                else:
                    target_table = "group_votes"
                    cursor.execute(
                        """
                        INSERT INTO group_votes (group_id, group_name, vote_count, vote_expiry)
                        VALUES (%s, %s, %s ,%s)
                        RETURNING vote_count
                        """, (group_id, group_name, vote_count, expiry_time)
                    )
                    new_count = cursor.fetchone()[0]

                # --- Get display_link (optional)
                cursor.execute(
                    f"SELECT display_link FROM {target_table} WHERE group_id = %s", (group_id,)
                )
                display_link = cursor.fetchone()
                display_link = display_link[0] if display_link else None

                # --- Get rank (only from group_votes table)
                rank = "N/A"
                if target_table == "group_votes":
                    cursor.execute("""
                        SELECT position FROM (
                            SELECT group_id, ROW_NUMBER() OVER (ORDER BY vote_count DESC) AS position
                            FROM group_votes
                        ) ranked WHERE group_id = %s
                    """, (group_id,))
                    rank_result = cursor.fetchone()
                    rank = rank_result[0] if rank_result else "N/A"

                conn.commit()

        # --- Notify creator
        await update.message.reply_text(
            f"✅ Added {vote_count} votes to {group_name}\n"
            f"Total: {new_count} | Rank: #{rank if target_table == 'group_votes' else '—'}",
            parse_mode="Markdown"
        )

        # Notify user if specified
        if user_id:
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"🎉 Boost completed!\n\n• Group: {group_name}\n• Votes: +{vote_count}\n• Total: {new_count}\n• Rank: #{rank}",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"User notification failed: {e}")
                await update.message.reply_text(f"⚠️ Failed to notify user {user_id}")

        # Send notification to trending channel
        await send_boost_notification(context, group_id, group_name, display_link, new_count, rank, group_photo)

    except ValueError:
        await update.message.reply_text("❌ Group ID and vote count must be numbers.")
    except Exception as e:
        logger.error(f"Manual add vote failed: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Failed to add votes. Please check logs.")

async def send_boost_notification(context, group_id, group_name, display_link, vote_count, rank, photo_id=None):
    message = (
        "✨ *TRENDING BOOST CONFIRMED* ✨\n\n"
        f"🏆 Group: [{group_name}]({display_link})\n"
        f"📊 Total Votes: *{vote_count}*\n"
        f"🏅 Current Rank: *#{rank}*\n\n"
        "_Boost completed successfully!_"
    )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🚀 BOOST YOUR SPACE TRENDING", 
            url=f"https://t.me/{context.bot.username}?start=boostvote"
        )
    ]])

    try:
        await context.bot.send_photo(
            chat_id=TRENDING_FORUM_ID,
            message_thread_id=TRENDING_TOPIC_ID,
            photo="https://t.me/myhostinger/14",
            caption=message,
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Trending notification failed: {e}")
        await context.bot.send_message(
            chat_id=TRENDING_FORUM_ID,
            message_thread_id=TRENDING_TOPIC_ID,
            text=message,
            parse_mode="Markdown",
            reply_markup=keyboard
        )

# Define conversation states
BOOST_GET_GROUP1, BOOST_SHOW_PLANS2, BOOST_PAYMENT3, BOOST_HASH4 = range(4)

async def boost_trend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        await update.message.reply_text("❗Please use this command in my DM.")
        return ConversationHandler.END

    context.user_data.clear()

    await update.message.reply_text(
        "📈 TO PLACE YOUR GROUP ON TRENDING (LEADERBOARD)\n\n"
        "1. Forward any message from your group to @userdatailsbot\n"
        "2. It will reply with your Group ID\n"
        "3. Send me that ID now\n\n"
        "Example: -123456789\n\n"
        "Can't find it? Message @iam_emmadroid for help\n\n"
        "Or reload /boosttrend"
    )
    return BOOST_GET_GROUP1

async def get_group_id1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = update.message.text.strip()

    if not group_id.lstrip('-').isdigit():
        await update.message.reply_text("Invalid Group ID. Please send only numbers.")
        return BOOST_GET_GROUP1

    try:
        chat = await context.bot.get_chat(int(group_id))
        context.user_data['group_id'] = group_id
        context.user_data['group_name'] = chat.title

        plans = [
            {"hours": 4, "price": 0.01},
            {"hours": 12, "price": 0.03},
            {"hours": 24, "price": 0.06}
        ]

        buttons = [
            [InlineKeyboardButton(
                f"{p['hours']} hours on Leaderboard – {p['price']} SOL",
                callback_data=f"trend_{p['hours']}"
            )] for p in plans
        ]

        await update.message.reply_text(
            f"🚀 Boost Plans for *{chat.title}*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return BOOST_SHOW_PLANS2

    except Exception as e:
        await update.message.reply_text("❌ Couldn't verify this group. Check the ID and try again.")
        return BOOST_GET_GROUP1

async def handle_trend_plan_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, hours = query.data.split('_')
    duration = int(hours)
    price = {4: 0.01, 12: 0.03, 24: 0.06}.get(duration)

    if price is None:
        await query.edit_message_text("❌ Invalid plan selected.")
        return BOOST_SHOW_PLANS2

    context.user_data['selected_plan'] = {'hours': duration, 'price': price}

    await query.edit_message_text(
        f"💳 *Send {price} SOL to:*\n\n"
        "BXPq18Cy9naEpk6A7ChDePS1R2bnWVNtxWcGsJP6apuY\n\n"
        "After payment, click below to submit your transaction hash 👇",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm Payment", callback_data="submit_hash1")]
        ])
    )
    return BOOST_PAYMENT3

async def request_hash1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "🔗 Please reply with your Solana transaction hash:\n\n"
        "Example: https://solscan.io/tx/5428...\n\n"
        "Find this in your wallet's transaction history."
    )
    return BOOST_HASH4

async def receive_hash1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tx_hash = update.message.text.strip()

    await context.bot.send_message(
        chat_id=CREATOR_ID,
        text=(
            "🔥 Boost Trend Payment\n\n"
            f"👥 Group: {context.user_data['group_name']}\n"
            f"🆔 User ID: {update.effective_user.id}\n"
            f"👤 User: @{update.effective_user.username or update.effective_user.first_name}\n"
            f"⏱ Duration: {context.user_data['selected_plan']['hours']} hours\n"
            f"💰 Amount: {context.user_data['selected_plan']['price']} SOL\n"
            f"🔗 Hash: {tx_hash}"
        )
    )

    await update.message.reply_text(
        "✅ Order submitted!\n\nBoost will be added after confirmations.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END


async def manual_boost_trend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != CREATOR_ID:
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /manualboosttrend <group_id> <duration_in_hours> [user_id]"
        )
        return

    try:
        group_id = int(context.args[0])
        hours = int(context.args[1])
        user_id = int(context.args[2]) if len(context.args) > 2 else None

        if hours <= 0:
            await update.message.reply_text("Duration must be a positive number of hours.")
            return

        boost_until_local = datetime.now(timezone.utc) + timedelta(hours=hours)

        try:
            chat = await context.bot.get_chat(group_id)
            group_name = chat.title
        except:
            group_name = f"Group {group_id}"

        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # Detect target table
                cursor.execute("SELECT 1 FROM group_votes WHERE group_id = %s", (group_id,))
                in_group_votes = cursor.fetchone() is not None

                cursor.execute("SELECT 1 FROM hyped_projects WHERE group_id = %s", (group_id,))
                in_hyped_projects = cursor.fetchone() is not None

                table = "group_votes" if in_group_votes else (
                    "hyped_projects" if in_hyped_projects else "group_votes"
                )

                cursor.execute(
                    f"""
                    INSERT INTO {table} (group_id, group_name, trend_duration_until)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (group_id) DO UPDATE
                    SET trend_duration_until = EXCLUDED.trend_duration_until
                    """,
                    (group_id, group_name, boost_until_local)
                )
                conn.commit()

        # Notify creator (you)
        await update.message.reply_text(
            f"✅ {group_name} manually boosted for {hours} hour(s).\n"
            f"⏳ Boost active until: {boost_until_local.strftime('%Y-%m-%d %H:%M:%S')} WAT",
            parse_mode="Markdown"
        )

        # Notify user if specified
        if user_id:
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"🎉 Your group {group_name} has been boosted for {hours} hour(s)!\n"
                         f"⏳ Active until: {boost_until_local.strftime('%Y-%m-%d %H:%M:%S')} ",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.warning(f"Failed to notify user {user_id}: {e}")

    except ValueError:
        await update.message.reply_text("Group ID and duration must be numbers.")
    except Exception as e:
        logger.error(f"Manual boost trend failed: {e}", exc_info=True)
        await update.message.reply_text("Failed to manually boost.")

### Updated Vote Cleanup Function (6-hour expiration) ###
async def delete_expired_votes(context: ContextTypes.DEFAULT_TYPE):
    """Delete votes older than 6 hours"""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # 1. Get all votes older than 24 hours
                cursor.execute("""
                    SELECT user_id, group_id FROM user_votes
                    WHERE voted_at < NOW() - INTERVAL '24 hours'
                """)
                expired_votes = cursor.fetchall()
                
                if not expired_votes:
                    return
                
                # 2. Decrement vote counts for affected groups
                for user_id, group_id in expired_votes:
                    cursor.execute("""
                        UPDATE group_votes
                        SET vote_count = GREATEST(0, vote_count - 1)
                        WHERE group_id = %s
                        RETURNING vote_count
                    """, (group_id,))
                    
                    result = cursor.fetchone()
                    if result:
                        new_count = result[0]
                        if new_count == 10:
                            await send_vote_notification(group_id, new_count, context)

                # 3. Delete the expired votes
                cursor.execute("""
                    DELETE FROM user_votes
                    WHERE voted_at < NOW() - INTERVAL '24 hours'
                """)
                conn.commit()
                
                logger.info(f"Cleaned up {len(expired_votes)} votes older than 24 hours")
                
    except Exception as e:
        logger.error(f"Error deleting expired votes: {e}")


### VOTE COMMAND HANDLER ###
async def vote_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    
    if not chat or chat.type == "private":
        await update.message.reply_text("This command only works in groups!")
        return

    deep_link = f"https://t.me/{context.bot.username}?start=poll_{chat.id}"
    current_votes = await get_group_vote_count(chat.id)
    
    await context.bot.send_photo(
        chat_id=chat.id,
        photo="https://t.me/myhostinger/7",
        caption=f"🗳 *Cast Vote for {chat.title}?*\n\n"
               f"• Current Votes: {current_votes}\n"
               f"• Position: #{await get_group_rank(chat.id)}\n\n"
               f"Votes needed to enter Leaderboard:\n"
               f"{max(0, 10 - current_votes)}\n\n"
               f"_{datetime.now().strftime('%I:%M %p')}_",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✔ Vote for This Group", url=deep_link)]
        ]),
        parse_mode="Markdown"
    )

### VOTE PROCESSING HANDLER ###
async def handle_vote_cast(update: Update, context: ContextTypes.DEFAULT_TYPE, group_id: int):
    user = update.effective_user
    chat = update.effective_chat
    
    if chat.type != "private":
        await update.message.reply_text("Please complete your vote in our private chat!")
        return

    try:
        is_premium = is_premium_user(user.id)
        cooldown_hours = 3 if is_premium else 6
        
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # 1. First clean up expired votes (older than 24 hours) for this user-group pair
                cursor.execute("""
                    DELETE FROM user_votes
                    WHERE user_id = %s AND group_id = %s 
                    AND voted_at < NOW() - INTERVAL '24 hours'
                    RETURNING 1
                """, (user.id, group_id))
                
                if cursor.rowcount > 0:
                    cursor.execute("""
                        UPDATE group_votes
                        SET vote_count = GREATEST(0, vote_count - 1)
                        WHERE group_id = %s
                    """, (group_id,))
                
                # 2. Ensure group exists with minimum required fields
                try:
                    chat_info = await context.bot.get_chat(group_id)
                    group_name = chat_info.title
                except Exception:
                    group_name = f"Group {group_id}"

                cursor.execute("""
                    INSERT INTO group_votes 
                    (group_id, group_name, vote_count, last_voted)
                    VALUES (%s, %s, 0, NOW())
                    ON CONFLICT (group_id) DO UPDATE
                    SET group_name = COALESCE(group_votes.group_name, EXCLUDED.group_name)
                """, (group_id, group_name))
                
                # 1. Check if user has voted for THIS GROUP recently
                cursor.execute("""
                    SELECT EXTRACT(EPOCH FROM (
                        voted_at + INTERVAL '%s hours' - NOW()
                    )) 
                    FROM user_votes 
                    WHERE user_id = %s AND group_id = %s
                    ORDER BY voted_at DESC 
                    LIMIT 1
                """, (cooldown_hours, user.id, group_id))
                
                cooldown_result = cursor.fetchone()
                
                # 2. If cooldown active for this group, block voting
                if cooldown_result and cooldown_result[0] > 0:
                    remaining_seconds = float(cooldown_result[0])
                    hours = int(remaining_seconds // 3600)
                    minutes = int((remaining_seconds % 3600) // 60)
                    remaining = f"{hours}h {minutes}m" if hours else f"{minutes}m"
                    
                    await update.message.reply_text(
                        f"⏳ You can vote for this group again in {remaining}\n\n"
                        f"Premium members can vote every 3 hours!\n\n"
                        f"You can still vote for other groups!",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🌟 Leaderboard", url="https://t.me/stftrending/2/377")]
                        ])
                    )
                    return
                
                # 3. Record new vote (will overwrite previous vote for this group)
                cursor.execute("""
                    INSERT INTO user_votes (user_id, group_id, is_premium, voted_at)
                    VALUES (%s, %s, %s, NOW())
                    ON CONFLICT (user_id, group_id) DO UPDATE 
                    SET voted_at = NOW()
                """, (user.id, group_id, is_premium))

                # 4. Update group stats
                cursor.execute("""
                    UPDATE group_votes
                    SET vote_count = vote_count + 1,
                        last_voted = NOW()
                    WHERE group_id = %s
                    RETURNING vote_count
                """, (group_id,))
                
                new_count = cursor.fetchone()[0]
                conn.commit()

                # 5. Get group info
                group_name = await get_group_name(group_id, context)
                
                # 6. Send notifications
                await send_vote_notification(group_id, new_count, context)
                if new_count == 10:
                    await send_leaderboard_notification(group_id, context)
                
                # 7. Confirmation message
                await update.message.reply_text(
                    f"✅ *Vote Cast Successfully!*\n\n"
                    f"Thank you for voting for *{group_name}*!\n\n"
                    f"Current Votes: *{new_count}*\n"
                    f"Next vote for this group in {'3' if is_premium else '6'} hours\n\n"
                    f"You can vote for other groups immediately!",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🏆 View Leaderboard", url=f"https://t.me/stftrending/2/377")]
                    ])
                )

    except Exception as e:
        logger.error(f"Vote processing error for {user.id}: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Failed to process your vote. Please try again later.")
        
async def get_cooldown_remaining(user_id: int, cooldown_hours: int) -> str:
    """Calculate remaining cooldown time in human-readable format"""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT EXTRACT(EPOCH FROM (
                    voted_at + INTERVAL '%s hours' - NOW()
                )) 
                FROM user_votes 
                WHERE user_id = %s 
                ORDER BY voted_at DESC 
                LIMIT 1
            """, (cooldown_hours, user.id))
            
            result = cursor.fetchone()
            if not result or not result[0]:
                return "0 minutes"
            
            remaining_seconds = max(0, float(result[0]))
            hours = int(remaining_seconds // 3600)
            minutes = int((remaining_seconds % 3600) // 60)
            
            if hours > 0:
                return f"{hours} hours and {minutes} minutes"
            return f"{minutes} minutes"

TRENDING_FORUM_ID = -1002763078436   # Replace with your group/forum ID
TRENDING_TOPIC_ID = 2            # Replace with your topic's message_thread_id
LEADERBOARD_IMAGE1 = "https://t.me/myhostinger/7"

logger = logging.getLogger(__name__)

# PostgreSQL Functions
def get_last_message_id(topic_id: int):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT message_id FROM leaderboard_state
                    WHERE topic_id = %s
                    ORDER BY updated_at DESC LIMIT 1
                """, (topic_id,))
                result = cursor.fetchone()
                return result[0] if result else None
    except Exception as e:
        logger.error(f"Failed to get message ID for topic {topic_id}: {e}")
        return None


def save_last_message_id(topic_id: int, message_id: int):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO leaderboard_state (topic_id, message_id)
                    VALUES (%s, %s)
                """, (topic_id, message_id))
                conn.commit()
    except Exception as e:
        logger.error(f"Failed to save message ID for topic {topic_id}: {e}")


# Main Leaderboard Function
async def update_leaderboard(context: CallbackContext):
    try:
        EMOJI_NUMBERS = [
            "1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟",
            "1️⃣1️⃣","1️⃣2️⃣","1️⃣3️⃣","1️⃣4️⃣","1️⃣5️⃣","1️⃣6️⃣","1️⃣7️⃣","1️⃣8️⃣","1️⃣9️⃣","2️⃣0️⃣"
        ]

        now = datetime.now(timezone.utc)

        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # Get boosted groups (trend_duration_until in the future)
                cursor.execute("""
                    SELECT group_id, group_name, vote_count, display_link, verified
                    FROM group_votes
                    WHERE trend_duration_until > %s
                    ORDER BY trend_duration_until DESC
                    LIMIT 10
                """, (now,))
                boosted_groups = cursor.fetchall()

                # Get non-boosted groups
                cursor.execute("""
                    SELECT group_id, group_name, vote_count, display_link, verified
                    FROM group_votes
                    WHERE vote_count >= 10 AND (trend_duration_until IS NULL OR trend_duration_until <= %s)
                    ORDER BY vote_count DESC
                    LIMIT 20
                """, (now,))
                regular_groups = cursor.fetchall()

        # Build leaderboard header
        leaderboard_text = "*STF VOTE TRENDING LEADERBOARD*\n\n"

        # First: display boosted groups (no numbering)
        for group_id, name, votes, link, verified in boosted_groups:
            dot = "◉" if verified else ""
            entry = "🔥 "
            entry += f"[{name}]({link})  " if link else f"*{name}*  "
            entry += f"{dot}"
            leaderboard_text += entry + "\n\n"

        # Then: display ranked (non-boosted) groups with numbering
        for idx, (group_id, name, votes, link, verified) in enumerate(regular_groups, 1):
            badge = EMOJI_NUMBERS[idx - 1] if idx <= 20 else f"{idx}."
            dot = "◉" if verified else ""

            entry = f"{badge}"
            entry += f"[{name}]({link})  " if link else f"*{name}*  "
            entry += f"🗳{votes}  {dot}"

            if idx == 1:
                entry += "🥇"
            elif idx == 2:
                entry += "🥈"
            elif idx == 3:
                entry += "🥉"
            elif votes > 100:
                entry += "🔥"

            leaderboard_text += entry + "\n\n"

        leaderboard_text += (
            "\n➖➖➖➖➖➖➖➖➖➖\n"
            "[🔥 BOOST](https://t.me/stftrendingbot?start=boosttrend)     "   
            "[📜 RULES](https://t.me/stfinfoportal/9)\n"
            "➖➖➖➖➖➖➖➖➖➖\n\n"
            "[ℹ️ ABOUT STF TRENDING](https://t.me/stfinfoportal/9)\n"
            "_Note: Teams with ◉ tick have been verified_\n"
            "➖➖➖➖➖➖➖➖➖➖\n"
            f"⏱ Updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
            "➖➖➖➖➖➖➖➖➖➖"
        )

        # Send or edit leaderboard message
        last_message_id = get_last_message_id(TRENDING_TOPIC_ID)
        media = InputMediaPhoto(
            media=LEADERBOARD_IMAGE1,
            caption=leaderboard_text,
            parse_mode="Markdown"
        )

        if last_message_id:
            await context.bot.edit_message_media(
                chat_id=TRENDING_FORUM_ID,
                message_id=last_message_id,
                media=media
            )
        else:
            message = await context.bot.send_photo(
                chat_id=TRENDING_FORUM_ID,
                message_thread_id=TRENDING_TOPIC_ID,
                photo=LEADERBOARD_IMAGE1,
                caption=leaderboard_text,
                parse_mode="Markdown"
            )
            save_last_message_id(TRENDING_TOPIC_ID, message.message_id)

    except Exception as e:
        logger.error(f"Leaderboard update failed: {e}", exc_info=True)

# States
SEARCH_SHILL_TEAM, CONFIRM_SHILL_TEAM, SUBMIT_REVIEW = range(3)

# Trending constants
TRENDING_FORUM_ID = -1002763078436
TRENDING11 = 11

async def start_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        bot_username = (await context.bot.get_me()).username
        deep_link_url = f"https://t.me/{bot_username}?start=review"

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 Continue via DM", url=deep_link_url)]
        ])

        await update.message.reply_text(
            "⭐️ You are about to give a review.\n\nClick the button below to continue in DM.",
            reply_markup=keyboard
        )
        return ConversationHandler.END

    await update.message.reply_text("Enter the shill team name you want to give a review.\n\nOr Reload /review")
    return SEARCH_SHILL_TEAM

# --- Step 2: Search and show matching teams ---
async def search_shill_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.lower()
    context.user_data["review_user_id"] = update.effective_user.id

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT group_id, group_name FROM group_votes WHERE LOWER(group_name) LIKE %s AND verified = TRUE", (f"%{user_input}%",))
    results = cur.fetchall()
    cur.close()
    conn.close()

    if not results:
        await update.message.reply_text("SHILL TEAM NOT REGISTERED WITH US.")
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton(name, callback_data=f"select_{group_id}")]
        for group_id, name in results
    ]
    keyboard.append([InlineKeyboardButton("Cancel ❌", callback_data="cancel_review")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🔍 Select the correct shill team:", reply_markup=reply_markup)
    return CONFIRM_SHILL_TEAM

# --- Step 3: Confirm team and show rating stats ---
async def confirm_shill_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel_review":
        await query.edit_message_text("❌ Review cancelled.")
        return ConversationHandler.END

    group_id = int(query.data.split("_")[1])
    context.user_data["review_group_id"] = group_id

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT rating, COUNT(*) FROM group_reviews 
        WHERE group_id = %s 
        GROUP BY rating
    """, (group_id,))
    counts = cur.fetchall()

    cur.execute("SELECT group_name, contactme_link, custom_photo_url FROM group_votes WHERE group_id = %s", (group_id,))
    result = cur.fetchone()
    conn.close()

    group_name, contactme_link, custom_photo_url = result

    rating_summary = {i: 0 for i in range(1, 6)}
    for rating, count in counts:
        rating_summary[rating] = count

    text = (
        f"Current reviews for *{group_name}*\n\n"
        f"⭐️ 5 Stars reviews: {rating_summary[5]}\n"
        f"⭐️ 4 Stars reviews: {rating_summary[4]}\n"
        f"⭐️ 3 Stars reviews: {rating_summary[3]}\n"
        f"⭐️ 2 Stars reviews: {rating_summary[2]}\n"
        f"⭐️ 1 Star reviews: {rating_summary[1]}\n\n"
        f"Choose your rating:"
    )

    buttons = [
        [InlineKeyboardButton(f"{i} ⭐️", callback_data=f"rate_{i}")] for i in range(5, 0, -1)
    ]
    reply_markup = InlineKeyboardMarkup(buttons)
    await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode="Markdown")
    return SUBMIT_REVIEW

# --- Step 4: Save rating and notify ---
async def submit_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    rating = int(query.data.split("_")[1])
    group_id = context.user_data["review_group_id"]
    user_id = context.user_data["review_user_id"]

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO group_reviews (group_id, user_id, rating, created_at)
        VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (group_id, user_id) 
        DO UPDATE SET rating = EXCLUDED.rating, created_at = EXCLUDED.created_at
    """, (group_id, user_id, rating))

    cur.execute("SELECT group_name, contactme_link, custom_photo_url FROM group_votes WHERE group_id = %s", (group_id,))
    group_name, contactme_link, photo_url = cur.fetchone()

    conn.commit()
    cur.close()
    conn.close()

    await query.edit_message_text("👍 Review submitted successfully!")

    if not contactme_link:
        contactme_link = "https://t.me/stftrending/11/411"  # fallback

    leaderboard_button = InlineKeyboardMarkup([
        [InlineKeyboardButton("💼 Hire Shill Team", url=contactme_link)]
    ])

    # Send to team group
    try:
        await context.bot.send_photo(
            chat_id=group_id,
            photo=photo_url,
            caption=(
                f"🟢 Your Team *{group_name}* just received a new rating.\n\n"
                f"⭐️ Rating: {rating}/5\n\n"
                f"All deeds are rewarded, always make sure you get rated after jobs /review"
            ),
            parse_mode="Markdown",
            reply_markup=leaderboard_button
        )
    except:
        pass  # skip if group can't be messaged

    # Send to trending forum
    await context.bot.send_photo(
        chat_id=TRENDING_FORUM_ID,
        message_thread_id=TRENDING11,
        photo=photo_url,
        caption=f"🟢 *New Received Review Incoming*\n\n*Shill Team:* *{group_name}*\n\n*Rating:* {rating} ⭐️ Stars",
        parse_mode="Markdown",
        reply_markup=leaderboard_button
    )

    return ConversationHandler.END

# --- Cancel command ---
async def cancel_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Review cancelled.")
    return ConversationHandler.END

async def top_voters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show top 10 users who casted the most votes in this group"""
    if update.message.chat.type not in ["group", "supergroup"]:
        await update.message.reply_text("❌ This command only works in groups!")
        return

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # Get top 10 voters by vote count
                cursor.execute("""
                    SELECT user_id, COUNT(*) as vote_count
                    FROM user_votes
                    WHERE group_id = %s
                    GROUP BY user_id
                    ORDER BY vote_count DESC
                    LIMIT 10
                """, (update.effective_chat.id,))
                
                top_voters = cursor.fetchall()
                
                if not top_voters:
                    await update.message.reply_text("📊 No voting data yet!")
                    return
                
                # Format leaderboard
                leaderboard = "🏆 Top Voters (Most Votes Cast):\n\n"
                for i, (user_id, vote_count) in enumerate(top_voters, 1):
                    try:
                        user = await context.bot.get_chat_member(
                            update.effective_chat.id,
                            user_id
                        )
                        username = user.user.username or user.user.first_name
                        leaderboard += f"{i}. @{username} - {vote_count} votes\n"
                    except:
                        leaderboard += f"{i}. [Unknown User] - {vote_count} votes\n"
                
                await update.message.reply_text(
                    leaderboard,
                    parse_mode=None
                )
                
    except Exception as e:
        logger.error(f"Top voters error: {e}")
        await update.message.reply_text("⚠️ Couldn't fetch voting data. Try again later.")

async def set_trend_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Admin check
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context.bot):
        await update.message.reply_text("Only admins can set trend links.")
        return

    # Check if link was provided
    if not context.args:
        await update.message.reply_text(
            "Please provide a link after the command:\n"
            "Example: /settrendlink https://t.me/yourgroup\n"
            "Or: /settrendlink https://twitter.com/yourgroup"
        )
        return

    # Validate URL
    link = context.args[0].strip()
    if not link.startswith(('http://', 'https://')):
        await update.message.reply_text("❌ Please provide a valid HTTP/HTTPS URL")
        return

    # Get group name from chat
    group_name = update.effective_chat.title

    # Save to database with group name
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO group_votes (group_id, group_name, display_link)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (group_id) DO UPDATE SET
                        display_link = EXCLUDED.display_link,
                        group_name = EXCLUDED.group_name
                """, (update.effective_chat.id, group_name, link))
                conn.commit()
        
        await update.message.reply_text(
            f"✅ Success! Your group will now display with this link in the leaderboard:\n{link}",
            disable_web_page_preview=True
        )
    except Exception as e:
        logger.error(f"Failed to save trend link: {e}")
        await update.message.reply_text("⚠️ Failed to save link. Please try again.")

async def remove_trend_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Admin check
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context.bot):
        await update.message.reply_text("Only admins can remove trend links.")
        return

    # Remove link from database
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # Check if link exists
                cursor.execute("""
                    SELECT display_link FROM group_votes
                    WHERE group_id = %s
                """, (update.effective_chat.id,))
                result = cursor.fetchone()
                
                if not result or not result[0]:
                    await update.message.reply_text("ℹ️ No trend link is currently set for this group")
                    return
                
                # Remove the link but keep group name
                cursor.execute("""
                    UPDATE group_votes
                    SET display_link = NULL
                    WHERE group_id = %s
                """, (update.effective_chat.id,))
                conn.commit()
        
        await update.message.reply_text(
            "✅ Trend link removed! Your group will now display without a custom link in the leaderboard."
        )
    except Exception as e:
        logger.error(f"Failed to remove trend link: {e}")
        await update.message.reply_text("⚠️ Failed to remove link. Please try again.")

async def set_contactme_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Admin check
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context.bot):
        await update.message.reply_text("Only admins can set contactme links.")
        return

    # Check if link was provided
    if not context.args:
        await update.message.reply_text(
            "Please provide a link after the command:\n"
            "Example: /setcontactmelink https://t.me/yourgroup\n"
            "Or: /setcontactmelink https://twitter.com/yourgroup"
        )
        return

    # Validate URL
    link = context.args[0].strip()
    if not link.startswith(('http://', 'https://')):
        await update.message.reply_text("❌ Please provide a valid HTTP/HTTPS URL")
        return

    # Get group name from chat
    group_name = update.effective_chat.title

    # Save to database with group name
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO group_votes (group_id, group_name, contactme_link)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (group_id) DO UPDATE SET
                        contactme_link = EXCLUDED.contactme_link,
                        group_name = EXCLUDED.group_name
                """, (update.effective_chat.id, group_name, link))
                conn.commit()
        
        await update.message.reply_text(
            f"✅ Success! Your team can now be hired and connected via:\n{link}",
            disable_web_page_preview=True
        )
    except Exception as e:
        logger.error(f"Failed to save contact link: {e}")
        await update.message.reply_text("Failed to save link. Please try again.")

async def remove_contactme_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Admin check
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context.bot):
        await update.message.reply_text("Only admins can remove contactme links.")
        return

    # Remove link from database
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # Check if link exists
                cursor.execute("""
                    SELECT contactme_link FROM group_votes
                    WHERE group_id = %s
                """, (update.effective_chat.id,))
                result = cursor.fetchone()
                
                if not result or not result[0]:
                    await update.message.reply_text("ℹ️ No contactme link currently set for this group")
                    return
                
                # Remove the link but keep group name
                cursor.execute("""
                    UPDATE group_votes
                    SET contactme_link = NULL
                    WHERE group_id = %s
                """, (update.effective_chat.id,))
                conn.commit()
        
        await update.message.reply_text(
            "✅ contactme link removed! Your group currently can't be reached or hired."
        )
    except Exception as e:
        logger.error(f"Failed to remove contactme link: {e}")
        await update.message.reply_text("Failed to remove contactme link. Please try again.")

SET_IMAGE_WAIT = 1

# Step 1: Command Handler – initiate image upload
async def start_set_trend_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context.bot):
        await update.message.reply_text("Only admins can set a trend image.")
        return ConversationHandler.END

    await update.message.reply_text("📸 Now send the trend image you want to use.")
    return SET_IMAGE_WAIT

# Step 2: Receive photo and save file_id
async def receive_trend_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("❗That's not a photo. Please send an image.")
        return SET_IMAGE_WAIT

    photo = update.message.photo[-1]
    file_id = photo.file_id
    group_id = update.effective_chat.id
    group_name = update.effective_chat.title

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO group_votes (group_id, group_name, custom_photo_url)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (group_id) DO UPDATE SET
                        custom_photo_url = EXCLUDED.custom_photo_url,
                        group_name = EXCLUDED.group_name
                """, (group_id, group_name, file_id))
                conn.commit()

        await update.message.reply_text("✅ Trend image saved successfully!")
    except Exception as e:
        logger.error(f"Error saving trend image: {e}")
        await update.message.reply_text("❌ Failed to save image. Please try again.")

    return ConversationHandler.END

# Step 3: Cancel handler
async def cancel_set_trend_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Image setting cancelled.")
    return ConversationHandler.END


async def remove_trend_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Admin check
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context.bot):
        await update.message.reply_text("Only admins can remove the trend image.")
        return

    group_id = update.effective_chat.id

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # Check if an image URL is currently set
                cursor.execute("""
                    SELECT custom_photo_url FROM group_votes
                    WHERE group_id = %s
                """, (group_id,))
                result = cursor.fetchone()

                if not result or not result[0]:
                    await update.message.reply_text("ℹ️ No trend image is currently set for this group.")
                    return

                # Clear the image URL
                cursor.execute("""
                    UPDATE group_votes
                    SET custom_photo_url = NULL
                    WHERE group_id = %s
                """, (group_id,))
                conn.commit()

        await update.message.reply_text("✅ Trend image removed successfully!")
    except Exception as e:
        logger.error(f"Failed to remove trend image: {e}")
        await update.message.reply_text("❌ Failed to remove image. Try again later.")



# === Command: /linkproject ===
DELETE_DELAY = 5  # 5 seconds for message deletion
async def link_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enhanced /linkproject command with auto-cleanup and trending notifications"""
    try:
        # Admin check
        if not await is_admin(update.effective_chat.id, update.effective_user.id, context.bot):
            response = await update.message.reply_text("Only admins can link projects.")
            await delete_messages_after_delay(update.message, response, delay=DELETE_DELAY)
            return

        # Argument validation
        if len(context.args) < 2:
            response = await update.message.reply_text("Usage: /linkproject <projectname> <community_group_id>")
            await delete_messages_after_delay(update.message, response, delay=DELETE_DELAY)
            return

        project_name = context.args[0].strip()
        community_group_id = int(context.args[1])

        # Database operation
        with get_db_connection() as conn:
            cur = conn.cursor()
            # Get group name for notification
            cur.execute("SELECT title FROM groups WHERE id = %s", (community_group_id,))
            group_name = cur.fetchone()[0] if cur.rowcount else f"Group {community_group_id}"
            
            # Update project mapping
            cur.execute("""
                INSERT INTO project_mapping (project_name, raid_group_id, community_group_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (project_name) DO UPDATE SET
                    raid_group_id = EXCLUDED.raid_group_id,
                    community_group_id = EXCLUDED.community_group_id
            """, (project_name, update.effective_chat.id, community_group_id))
            conn.commit()

        # Send confirmation and auto-delete
        response = await update.message.reply_text(
            f"✅ {project_name} linked to {group_name} (ID: {community_group_id})"
        )
        await delete_messages_after_delay(update.message, response, delay=DELETE_DELAY)

    except Exception as e:
        logger.error(f"Link project error: {e}")
        response = await update.message.reply_text("Error processing request")
        await delete_messages_after_delay(update.message, response, delay=DELETE_DELAY)
        
# === Command: /setshilltarget ===
async def set_shill_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context.bot):
        await update.message.reply_text("Only admins can set shill target.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("Usage: /setshilltarget <project_name> <expected_links>")
        return

    project_name = context.args[0]
    try:
        expected = int(context.args[1])
    except ValueError:
        await update.message.reply_text("Target must be number.")
        return

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO shill_targets (project_name, expected_links)
            VALUES (%s, %s)
            ON CONFLICT (project_name) DO UPDATE SET expected_links = EXCLUDED.expected_links
        """, (project_name, expected))
        conn.commit()

    await update.message.reply_text(f"✅ Target for {project_name} set to {expected} links.")

# === Command: /shillstat ===
async def shill_stat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_premium_user(update.effective_user.id):
        await update.message.reply_text("This is a premium feature, use /gent to upgrade.")
        return

    if len(context.args) < 1:
        await update.message.reply_text("Usage: /shillstat <project_name>")
        return

    project = context.args[0]
    today = datetime.now().date()

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT lead_username, COUNT(*) FROM project_links
            WHERE project_name = %s AND DATE(timestamp) = %s
            GROUP BY lead_username
            ORDER BY COUNT(*) DESC
        """, (project, today))
        stats = cur.fetchall()

        cur.execute("SELECT expected_links FROM shill_targets WHERE project_name = %s", (project,))
        expected = cur.fetchone()
        expected_count = expected[0] if expected else "Not Set"

    if not stats:
        await update.message.reply_text("No shill activity found today for this project.")
        return

    report = f"📊 Shill Stats for {project} ({today})\n\n"
    for lead, count in stats:
        report += f"• {lead} dropped {count} links\n"
    report += f"\n🎯 Expected: {expected_count} links\n"

    await update.message.reply_text(report)

async def delete_messages_after_delay(*messages, delay=5):
    """Delete messages after specified delay (default 5 seconds)"""
    await asyncio.sleep(delay)
    for msg in messages:
        try:
            await msg.delete()
        except Exception as e:
            logger.warning(f"Couldn't delete message: {e}")

# This pattern matches Twitter/X links only
TWITTER_REGEX = r"(https?://(?:www\.)?(?:x\.com|twitter\.com)/[^\s]+)"

async def link_tracker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    message = update.message
    text = message.text or message.caption or ""
    twitter_links = set()

    # 1. Check plain text
    twitter_links.update(re.findall(TWITTER_REGEX, text))

    # 2. Check embedded entities (like [link](https://...))
    if message.entities:
        for entity in message.parse_entities(types=[MessageEntityType.URL, MessageEntityType.TEXT_LINK]).values():
            if re.match(TWITTER_REGEX, entity):
                twitter_links.add(entity)

    if not twitter_links:
        return

    user = update.effective_user
    chat_id = message.chat_id
    msg_id = message.message_id
    now = datetime.now()

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:

                # Get project mapping
                cur.execute("""
                    SELECT pm.project_name, pm.community_group_id, g.title 
                    FROM project_mapping pm
                    LEFT JOIN groups g ON pm.community_group_id = g.id
                    WHERE pm.raid_group_id = %s
                """, (chat_id,))
                row = cur.fetchone()
                
                if not row:
                    return  # No linked project

                project_name, community_id, community_name = row

                # Save the link
                for link in twitter_links:
                    cur.execute("""
                        INSERT INTO project_links (project_name, lead_username, chat_id, message_id, timestamp)
                        VALUES (%s, %s, %s, %s, %s)
                        RETURNING id
                    """, (
                        project_name,
                        f"@{user.username}" if user.username else user.full_name,
                        chat_id,
                        msg_id,
                        now
                    ))
                    link_id = cur.fetchone()[0]

                # Update stats
                cur.execute("""
                    INSERT INTO community_stats (community_id, project_name, daily_links, all_time_links)
                    VALUES (%s, %s, 1, 1)
                    ON CONFLICT (community_id, project_name) 
                    DO UPDATE SET 
                        daily_links = community_stats.daily_links + 1,
                        all_time_links = community_stats.all_time_links + 1,
                        last_updated = NOW()
                """, (community_id, project_name))

            conn.commit()

    except Exception as e:
        logger.error(f"Error tracking link: {e}")
MILESTONES = {10, 20, 30, 40, 50, 60, 70, 80, 90, 100}  # Milestone thresholds

async def check_milestones(context: CallbackContext):
    """Check and post milestone achievements"""
    try:
        logger.info("🔍 Checking for milestones...")
        
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # First ensure the column exists
                cursor.execute("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name='community_stats' 
                    AND column_name='last_posted_milestone'
                """)
                if not cursor.fetchone():
                    cursor.execute("""
                        ALTER TABLE community_stats 
                        ADD COLUMN last_posted_milestone INTEGER
                    """)
                    conn.commit()
                    logger.info("✅ Added last_posted_milestone column")

                # Get projects that hit milestones
                cursor.execute("""
                    SELECT 
                        cs.community_id,
                        pm.project_name,
                        cs.daily_links,
                        cs.all_time_links,
                        cs.last_posted_milestone
                    FROM community_stats cs
                    JOIN project_mapping pm ON cs.project_name = pm.project_name
                    WHERE cs.daily_links >= 10
                    AND DATE(cs.last_updated) = CURRENT_DATE
                    AND (cs.last_posted_milestone IS NULL 
                         OR cs.daily_links > cs.last_posted_milestone)
                """)
                
                milestones = cursor.fetchall()
                logger.info(f"📊 Found {len(milestones)} milestones to post")

        for group_id, project, daily_links, all_time_links, last_posted in milestones:
            group_name = await get_group_name(group_id, context)
            rank = await calculate_rank(group_id)
            
            # Determine which milestones to post (10, 20, 30 etc)
            for milestone in MILESTONES:
                if daily_links >= milestone and (last_posted is None or milestone > last_posted):
                    await post_milestone(
                        context,
                        project,
                        group_name,
                        milestone,
                        all_time_links,
                        rank
                    )
                    
                    # Update last posted milestone
                    with get_db_connection() as conn:
                        with conn.cursor() as cursor:
                            cursor.execute("""
                                UPDATE community_stats
                                SET last_posted_milestone = %s
                                WHERE community_id = %s 
                                AND project_name = %s
                            """, (milestone, group_id, project))
                            conn.commit()

    except Exception as e:
        logger.error(f"❌ Milestone check error: {e}", exc_info=True)

async def calculate_rank(group_id: int) -> int:
    """Calculate current rank for a group"""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT COUNT(*) FROM (
                    SELECT community_id, SUM(daily_links) as total
                    FROM community_stats
                    WHERE DATE(last_updated) = CURRENT_DATE
                    GROUP BY community_id
                ) t WHERE t.total > (
                    SELECT SUM(daily_links) 
                    FROM community_stats 
                    WHERE community_id = %s
                    AND DATE(last_updated) = CURRENT_DATE
                )
            """, (group_id,))
            return cursor.fetchone()[0] + 1  # Add 1 since rank starts at 1

FORUM_CHAT_ID = -1002763078436  # Replace with your actual forum chat ID
FORUM_TOPIC1 = 1

async def post_milestone(context: CallbackContext, project: str, group_name: str, 
                        milestone: int, all_time_links: int, rank: int):
    """Post a milestone achievement to channel"""
    try:
        logger.info(f"📨 Posting milestone: {group_name} - {project} - {milestone} links")
        
        # Determine message template
        if milestone >= 50:
            title = f"{group_name} has shilled {project} to the top chart! 🚀"
        elif milestone >= 20:
            title = f"{group_name} shilling the {project} project like no other! 🔥"
        else:
            title = f"{group_name} is in charge of the {project} project! 👑"

        message = (
            f"{title}\n\n"
            f"🔗 Milestone reached: {milestone} links today\n"
            f"🏆 All-time total: {all_time_links}\n"
            f"📈 Current rank: #{rank}\n"
        )

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Live Raid Leaderboard", url="https://t.me/stftrending/1")
        ]])

        await context.bot.send_photo(
            chat_id=FORUM_CHAT_ID,
            photo="https://t.me/myhostinger/19",
            caption=message,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        logger.info("✅ Milestone posted successfully")

    except Exception as e:
        logger.error(f"❌ Failed to post milestone: {e}", exc_info=True)
        
async def reset_dailycounts(context: ContextTypes.DEFAULT_TYPE):
    """Reset daily link counts at midnight UTC"""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE community_stats
                    SET daily_links = 0
                    WHERE DATE(last_updated) < CURRENT_DATE
                """)
                conn.commit()
    except Exception as e:
        logger.error(f"Reset error: {e}")



# === Scheduled Daily Report (11 PM) ===
async def auto_shill_report(context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now().date()

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT project_name FROM project_mapping")
        projects = cur.fetchall()

        for (project,) in projects:
            cur.execute("""
                SELECT lead_username, COUNT(*) FROM project_links
                WHERE project_name = %s AND DATE(timestamp) = %s
                GROUP BY lead_username ORDER BY COUNT(*) DESC
            """, (project, today))
            stats = cur.fetchall()

            cur.execute("SELECT community_group_id FROM project_mapping WHERE project_name = %s", (project,))
            group = cur.fetchone()
            group_id = group[0] if group else None

            cur.execute("SELECT expected_links FROM shill_targets WHERE project_name = %s", (project,))
            expected = cur.fetchone()
            expected_count = expected[0] if expected else "Not Set"

            if not stats or not group_id:
                continue

            msg = f"📊 Shill Summary for {project} ({today})\n\n"
            for lead, count in stats:
                msg += f"• {lead} dropped {count} links\n"
            msg += f"\n🎯 Expected: {expected_count} links"

            try:
                await context.bot.send_message(group_id, msg)
            except:
                pass

TWITTER_REGEX = r"(https?://(?:www\.)?(?:x\.com|twitter\.com)/[^\s]+)"

async def unified_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return

    # Handle new members (welcome messages)
    if message.new_chat_members:
        await on_new_member(update, context)
        return  # Skip further processing for join messages

    if not message.text:
        return

    # 🧠 Track users for /wakeall feature
    try:
        user = update.effective_user
        chat = update.effective_chat
        if chat.type in ["group", "supergroup"] and not user.is_bot:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        INSERT INTO group_users (chat_id, user_id, username)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (chat_id, user_id) DO UPDATE SET username = EXCLUDED.username
                    """, (chat.id, user.id, user.username or user.full_name))
                    conn.commit()
    except Exception as e:
        logger.error(f"Tracking user failed: {e}")

    text = message.text.strip().lower()

    # 1. Password handler
    if context.user_data.get("expecting_password"):
        await handle_password(update, context)
        return

    # 3. Twitter link check
    if update.message:
        text = update.message.text or update.message.caption or ""
        entities = update.message.entities or []

        # Check plain Twitter links OR embedded link entities
        if re.search(TWITTER_REGEX, text) or any(
            ent.type in [MessageEntityType.URL, MessageEntityType.TEXT_LINK] for ent in entities
        ):
            await link_tracker(update, context)
    

TRENDING_FORUM_ID = -1002763078436   # Replace with your group/forum ID
TRENDING11 = 11

def get_last_uv_leaderboard_message_id():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT message_id
                    FROM leaderboard_state
                    WHERE topic_id = %s
                """, (TRENDING11,))
                result = cursor.fetchone()
                return result[0] if result else None
    except Exception as e:
        logger.error(f"Failed to fetch message ID for topic {TRENDING11}: {e}")
        return None



def save_uv_leaderboard_message_id(message_id: int):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO leaderboard_state (topic_id, message_id)
                    VALUES (%s, %s)
                    ON CONFLICT (topic_id) DO UPDATE
                    SET message_id = EXCLUDED.message_id,
                        updated_at = CURRENT_TIMESTAMP
                """, (TRENDING11, message_id))
                conn.commit()
    except Exception as e:
        logger.error(f"Failed to save message ID for topic {TRENDING11}: {e}")


async def update_verified_leaderboard(context: ContextTypes.DEFAULT_TYPE):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT 
                        gv.group_name, 
                        gv.display_link,
                        ROUND(AVG(gr.rating)::numeric, 1) AS avg_rating,
                        COUNT(gr.rating) AS review_count
                    FROM group_votes gv
                    LEFT JOIN group_reviews gr ON gv.group_id = gr.group_id
                    WHERE gv.verified = TRUE
                    GROUP BY gv.group_name, gv.display_link
                    ORDER BY avg_rating DESC NULLS LAST
                """)
                verified_groups = cursor.fetchall()

        leaderboard_text = "✅ *VERIFIED SHILL TEAMS* \n\n"

        for name, link, avg_rating, review_count in verified_groups:
            rating_display = f"{avg_rating:.1f}" if avg_rating is not None else "N/A"
            reviews_display = f"({review_count} review{'s' if review_count != 1 else ''})" if review_count else "(0)"
            leaderboard_text += f"🔹 [{name}]({link}) ⭐ {rating_display} {reviews_display}\n\n"

        leaderboard_text += f"\n⏱ Updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"

        last_message_id = get_last_uv_leaderboard_message_id()

        media = InputMediaPhoto(
            media="https://t.me/myhostinger/34",
            caption=leaderboard_text,
            parse_mode="Markdown"
        )

        if last_message_id:
            await context.bot.edit_message_media(
                chat_id=TRENDING_FORUM_ID,
                message_id=last_message_id,
                media=media
            )
        else:
            message = await context.bot.send_photo(
                chat_id=TRENDING_FORUM_ID,
                message_thread_id=TRENDING11,
                photo="https://t.me/myhostinger/30",
                caption=leaderboard_text,
                parse_mode="Markdown"
            )
            save_uv_leaderboard_message_id(message.message_id)

    except Exception as e:
        logger.error(f"Verified leaderboard update failed: {e}", exc_info=True)


def setup_verified_leaderboard_job(application):
    """Schedule the verified leaderboard to send every 5 minutes, starting after 60 seconds"""
    
    first_run = timedelta(seconds=60)

    application.job_queue.run_repeating(
        callback=update_verified_leaderboard,
        interval=300,  # Every 5 minutes
        first=first_run,  # First run after 60 seconds
        name="verified_leaderboard"
    )

    logger.info("Verified leaderboard scheduled - first run in 60 seconds, then every 5 minutes.")



FORUM_CHAT_ID = -1002763078436  # Replace with your actual forum chat ID
FORUM_TOPIC_ID12 = 12
LEADERBOARD_IMAGE = "https://t.me/myhostinger/26"

def get_last_xp_leaderboard_message_id():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT message_id FROM leaderboard_state
                    WHERE topic_id = %s
                    ORDER BY updated_at DESC LIMIT 1
                """, (FORUM_TOPIC_ID12,))
                result = cursor.fetchone()
                return result[0] if result else None
    except Exception as e:
        logger.error(f"Failed to get message ID for topic {FORUM_TOPIC_ID12}: {e}")
        return None


def save_xp_leaderboard_message_id(message_id: int):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO leaderboard_state (topic_id, message_id)
                    VALUES (%s, %s)
                    ON CONFLICT (topic_id) DO UPDATE
                    SET message_id = EXCLUDED.message_id,
                        updated_at = CURRENT_TIMESTAMP
                """, (FORUM_TOPIC_ID12, message_id))
                conn.commit()
    except Exception as e:
        logger.error(f"Failed to save message ID for topic {FORUM_TOPIC_ID12}: {e}")

        

async def send_xp_leaderboard(context: CallbackContext):
    try:
        EMOJI_NUMBERS = [
            "1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟",
            "1️⃣1️⃣","1️⃣2️⃣","1️⃣3️⃣","1️⃣4️⃣","1️⃣5️⃣","1️⃣6️⃣","1️⃣7️⃣","1️⃣8️⃣","1️⃣9️⃣","2️⃣0️⃣"
        ]

        now = datetime.now(timezone.utc)

        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # Get boosted groups (trend_duration_until in the future)
                cursor.execute("""
                    SELECT group_id, group_name, vote_count, display_link, marketcap, token_name
                    FROM hyped_projects
                    WHERE trend_duration_until > %s
                    ORDER BY trend_duration_until DESC
                    LIMIT 10
                """, (now,))
                boosted_groups = cursor.fetchall()

                # Get non-boosted groups
                cursor.execute("""
                    SELECT group_id, group_name, vote_count, display_link, marketcap, token_name
                    FROM hyped_projects
                    WHERE vote_count >= 10 AND (trend_duration_until IS NULL OR trend_duration_until <= %s)
                    ORDER BY vote_count DESC
                    LIMIT 20
                """, (now,))
                regular_groups = cursor.fetchall()

        # Build leaderboard header
        leaderboard_text = "*ECOSYSTEM TRENDING LEADERBOARD*\n\n"

        # First: display boosted groups (no numbering)
        for group_id, name, votes, link, marketcap, token_name in boosted_groups:
            entry = "🔥 "
            entry += f"[{token_name}]({link})  \n" if link else f"*{token_name}*  "
            try:
                marketcap = f"${int(float(marketcap)):,}"
            except (TypeError, ValueError):
                marketcap = f"${marketcap}"

            entry += f"💰 MC: {marketcap}\n\n"
            leaderboard_text += entry

        # Then: display ranked (non-boosted) groups with numbering
        for idx, (group_id, name, votes, link, marketcap, token_name) in enumerate(regular_groups, 1):
            badge = EMOJI_NUMBERS[idx - 1] if idx <= 20 else f"{idx}."

            entry = f"{badge}"
            entry += f"[{token_name}]({link})  " if link else f"*{token_name}*  "
            entry += f"🗳{votes} "

            if idx == 1:
                entry += "🥇"
            elif idx == 2:
                entry += "🥈"
            elif idx == 3:
                entry += "🥉"
            elif votes > 100:
                entry += "🔥"

            entry += "\n"

            try:
                marketcap = f"${int(float(marketcap)):,}"
            except (TypeError, ValueError):
                marketcap = f"${marketcap}"

            entry += f"💰 MC: {marketcap}\n\n"
            leaderboard_text += entry

        leaderboard_text += (
            "\n➖➖➖➖➖➖➖➖➖➖\n"
            "[🔥 BOOST](https://t.me/stftrendingbot?start=boosttrend) ┃ " 
            "[🚀 LIST YOUR PROJECT](https://t.me/stftrendingbot?start=add)\n"
            "        [👥 HIRE SHILL TEAMS](https://t.me/stftrending/11/411)\n"
            "➖➖➖➖➖➖➖➖➖➖\n\n"
            "[ℹ️ ABOUT STF TRENDING](https://t.me/stfinfoportal/9)\n"
            "[⚠️ RULES](https://t.me/stfinfoportal/183)\n"
            "_Note: This is a Multi-chain leaderboard, Projects are listed from different networks for exposure_\n"
            "➖➖➖➖➖➖➖➖➖➖\n"
            f"⏱ Updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
            "➖➖➖➖➖➖➖➖➖➖"
        )
        # Update or send
        last_msg_id = get_last_xp_leaderboard_message_id()
        media = InputMediaPhoto(
            media=LEADERBOARD_IMAGE,
            caption=leaderboard_text,
            parse_mode="Markdown"
        )

        if last_msg_id:
            await context.bot.edit_message_media(
                chat_id=FORUM_CHAT_ID,
                message_id=last_msg_id,
                media=media
            )
        else:
            message = await context.bot.send_photo(
                chat_id=FORUM_CHAT_ID,
                message_thread_id=FORUM_TOPIC_ID12,
                photo=LEADERBOARD_IMAGE,
                caption=leaderboard_text,
                parse_mode="Markdown",
                disable_notification=True
            )
            save_xp_leaderboard_message_id(message.message_id)

    except Exception as e:
        logger.error(f"XP Forum Leaderboard update failed: {e}", exc_info=True)
        
def setup_xp_leaderboard_job(application):
    """Schedule XP leaderboard to send every 5 minutes"""
    now = datetime.now(timezone.utc)

    # Round to the next multiple of 5 minutes
    minutes_until_next = 5 - (now.minute % 5)
    next_time = now + timedelta(minutes=minutes_until_next)
    next_time = next_time.replace(second=0, microsecond=0)

    application.job_queue.run_repeating(
        callback=send_xp_leaderboard,
        interval=300,  # 5 minutes
        first=next_time,
        name="xp_leaderboard"
    )

    logger.info(f"🕒 XP leaderboard job scheduled - first run at {next_time.strftime('%Y-%m-%d %H:%M:%S %Z')} UTC (then every 5 minutes)")
    
async def set_profile_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set a clickable profile link for a user"""
    user_id = update.effective_user.id
    
    if not context.args:
        await update.message.reply_text(
            "Please provide a link after the command:\n"
            "Example: /setprofile https://t.me/username\n"
            "Or: /setprofile https://twitter.com/username"
        )
        return
    
    link = context.args[0].strip()
    if not link.startswith(('http://', 'https://')):
        await update.message.reply_text("❌ Please provide a valid HTTP/HTTPS URL")
        return
    
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO user_xp (user_id, profile_link)
                    VALUES (%s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET
                        profile_link = EXCLUDED.profile_link
                """, (user_id, link))
                conn.commit()
        
        await update.message.reply_text(
            f"✅ Profile link set! Your name will now appear clickable in the XP leaderboard:\n{link}",
            disable_web_page_preview=True
        )
    except Exception as e:
        logger.error(f"Failed to set profile link: {e}")
        await update.message.reply_text("⚠️ Failed to save link. Please try again.")

async def remove_profile_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a user's profile link"""
    user_id = update.effective_user.id
    
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE user_xp
                    SET profile_link = NULL
                    WHERE user_id = %s
                    RETURNING profile_link
                """, (user_id,))
                result = cursor.fetchone()
                
                if not result or not result[0]:
                    await update.message.reply_text("ℹ️ No profile link is currently set")
                    return
                
                conn.commit()
        
        await update.message.reply_text("✅ Profile link removed!")
    except Exception as e:
        logger.error(f"Failed to remove profile link: {e}")
        await update.message.reply_text("⚠️ Failed to remove link. Please try again.")

TRENDING_FORUM_ID = -1002763078436
THREAD_MAP = {
    'ETH': 7,
    'SOL': 6,
    'BASE': 8,
    'BSC': 9
}

CHAIN_ID_MAP = {
    "ETH": "1",
    "BSC": "56",
    "BASE": "8453"
}

ETHERSCAN_API_KEY = "7AGJA9EBP9EMHWTT3UTINZFHCAGDKNA69B"

(
    HYPE_CHAIN, HYPE_CA, HYPE_TOKEN_NAME,
    HYPE_LINK, HYPE_IMAGE, HYPE_CONFIRM
) = range(6)

async def hypeme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user

    # 🚫 Restrict command to groups only
    if chat.type == "private":
        await update.message.reply_text(
            "This command can only be used in the project community.\n\n"
            "👉 Add me to your group and use the /add command there."
        )
        return ConversationHandler.END

    member = await context.bot.get_chat_member(chat.id, user.id)
    if member.status not in ("administrator", "creator"):
        await update.message.reply_text("Sorry, you have no permission to setup")
        return ConversationHandler.END

    group_id = chat.id
    user_id = user.id
    now = datetime.now(timezone.utc)

    if user_id != CREATOR_ID:

        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT last_used FROM hype_cooldowns
                    WHERE user_id = %s AND group_id = %s
                """, (user_id, group_id))
                result = cursor.fetchone()

                if result:
                    last_used = result[0].replace(tzinfo=timezone.utc)
                    if now - last_used < timedelta(minutes=1):
                        remaining = timedelta(minutes=1) - (now - last_used)
                        mins, secs = divmod(int(remaining.total_seconds()), 60)
                        await update.message.reply_text(
                            f"⏳ Please wait {mins}m {secs}s before using this again."
                        )
                        return ConversationHandler.END


                    cursor.execute("""
                        UPDATE hype_cooldowns SET last_used = %s
                        WHERE user_id = %s AND group_id = %s
                    """, (now, user_id, group_id))
                else:
                    cursor.execute("""
                        INSERT INTO hype_cooldowns (user_id, group_id, last_used)
                        VALUES (%s, %s, %s)
                    """, (user_id, group_id, now))

            conn.commit()
            
    # ✅ Proceed with blockchain selection
    context.user_data["group_id"] = group_id
    context.user_data["group_name"] = chat.title

    keyboard = [
        [InlineKeyboardButton("ETH", callback_data="ETH")],
         [InlineKeyboardButton("SOL", callback_data="SOL")],
        [InlineKeyboardButton("BSC", callback_data="BSC")],
         [InlineKeyboardButton("BASE", callback_data="BASE")]
    ]

    await update.message.reply_text("Welcome to STF TRENDING!\nLet's setup the bot and let the trending begin!\n\n📍 CHOOSE YOUR PROJECT BLOCKCHAIN:", reply_markup=InlineKeyboardMarkup(keyboard))
    return HYPE_CHAIN


async def handle_chain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chain = query.data
    context.user_data['chain'] = chain

    await query.message.reply_text("🧾 Enter your token contract address (CA):")
    return HYPE_CA

async def handle_ca(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ca = update.message.text.strip()
    chain = context.user_data['chain']
    marketcap = "-"
    valid = False

    try:
        if chain == "SOL":
            # 1. DexScreener Search
            res = requests.get(f"https://api.dexscreener.com/latest/dex/search?q={ca}")
            if res.ok:
                data = res.json()
                pairs = data.get("pairs", [])
                if pairs:
                    token = pairs[0]
                    marketcap = token.get("fdv") or "-"
                    if isinstance(marketcap, (int, float)):
                        marketcap = f"{int(marketcap):,}"
                    valid = True

            # 2. DexScreener Direct Token Lookup
            if not valid:
                res2 = requests.get(
                    f"https://api.dexscreener.com/tokens/v1/solana/{ca}",
                    headers={"Accept": "*/*"}
                )
                if res2.ok:
                    token = res2.json()
                    marketcap = token.get("fdv") or "-"
                    if isinstance(marketcap, (int, float)):
                        marketcap = f"{int(marketcap):,}"
                    valid = True

                    
        else:
            # First verify contract exists via Etherscan
            chain_id = CHAIN_ID_MAP[chain]
            res = requests.get("https://api.etherscan.io/v2/api", params={
                "chainid": chain_id,
                "module": "contract",
                "action": "getsourcecode",
                "address": ca,
                "apikey": ETHERSCAN_API_KEY
            })
            if res.ok:
                data = res.json()
                result = data.get('result', [])
                if result and result[0].get("ContractName"):
                    valid = True

                    # Try fetching marketcap from DexScreener
                    res2 = requests.get(f"https://api.dexscreener.com/latest/dex/search?q={ca}")
                    if res2.ok:
                        dex_data = res2.json()
                        pairs = dex_data.get("pairs", [])
                        if pairs:
                            token = pairs[0]
                            marketcap = token.get("fdv") or "-"
    
                            # Format marketcap with commas
                            if isinstance(marketcap, (int, float)):
                                marketcap = f"{int(marketcap):,}"


    except Exception as e:
        print("Error verifying contract:", e)

    if not valid:
        await update.message.reply_text("🔍 Searching...")
        await update.message.reply_text("⚠️ Token not live yet or has no volume in the last 12hrs.")
        await update.message.reply_text("Try again? /add")
        return ConversationHandler.END

    context.user_data['ca'] = ca
    context.user_data['marketcap'] = marketcap

    await update.message.reply_text("🔍 Searching info....")
    await update.message.reply_text("Token verified successfully.")
    await update.message.reply_text("✅ CA set Successfully!\nNOW ENTER YOUR TOKEN NAME:")
    return HYPE_TOKEN_NAME
    
async def handle_token_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['token_name'] = update.message.text.strip()
    await update.message.reply_text("✅ CA set Successfully!\n✅ Token Name set Successfully!\n🔗 NOW SEND ME YOUR PROJECT TELEGRAM LINK:")
    return HYPE_LINK

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link = update.message.text.strip()

    # Updated regex to support both public and private Telegram links
    if not re.match(r"^(https?://)?(t\.me|telegram\.me)/(joinchat/\w+|\+[\w-]+|[a-zA-Z0-9_]{5,})$", link):
        await update.message.reply_text("⏳ Validating telegram link ....")
        await update.message.reply_text("⚠️ Wrong Format. Try again\nFormat (e.g., https://t.me/yourgroup or https://t.me/+abcDEF12345).")
        return HYPE_LINK  # Ask for correct input again

    context.user_data['display_link'] = link
    await update.message.reply_text("⏳ Validating telegram link ....")
    await update.message.reply_text("📸 SEND ME A LOGO OR IMAGE THAT REPRESENTS YOUR PROJECT TOKEN:")
    return HYPE_IMAGE

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("Please send an image.")
        return HYPE_IMAGE

    photo = update.message.photo[-1]
    context.user_data['image_file_id'] = photo.file_id

    data = context.user_data
    caption = (
        f"🚀 <b>{data['token_name']}</b>\n"
        f"<b>Blockchain:</b> {data['chain']}\n"
        f"<b>Contract:</b> <code>{data['ca']}</code>\n"
        f"<b>Marketcap:</b> ${data['marketcap']}\n"
        f"<b>Link:</b> {data['display_link']}"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm", callback_data="CONFIRM_HYPE"),
         InlineKeyboardButton("❌ Cancel", callback_data="CANCEL_HYPE")]
    ])
    await update.message.reply_photo(
        photo=photo.file_id,
        caption=caption,
        reply_markup=keyboard,
        parse_mode='HTML'
    )
    return HYPE_CONFIRM

async def confirm_hype(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = context.user_data
    thread_id = THREAD_MAP.get(data['chain'], None)

    if not thread_id:
        await query.message.edit_caption("Error sending hype post.")
        return ConversationHandler.END

    # Construct caption
    caption = (
        f"<b>{data['token_name']}</b> ENTERED THE STF TRENDING\n\n"
        f"🔸<b>Blockchain:</b> {data['chain']}\n"
        f"🔸<b>Contract:</b> <code>{data['ca']}</code>\n"
        f"🔸<b>Link:</b> {data['display_link']}\n"
        f"🔸<b>Marketcap:</b> ${data['marketcap']}\n\n"
        f"🔵 <a href='https://t.me/stftrending/267'>INDEX</a> "
        f"🔘 <a href='https://t.me/stftrendingbot?start=boostvote'>BOOST</a> "
        f"🟣 <a href='https://t.me/{context.bot.username}?start=boosttrend'>TREND</a>"
    )

    # Determine DEX and BUY URLs
    ca = data['ca']
    chain = data['chain'].upper()

    if chain == "ETH":
        dex_url = f"https://dexscreener.com/ethereum/{ca}"
        buy_url = f"https://app.uniswap.org/swap?&chain=mainnet&use=v2&outputCurrency={ca}"

    elif chain == "SOL":
        dex_url = f"https://dexscreener.com/solana/{ca}"
        buy_url = f"https://dexscreener.com/solana/{ca}"

    elif chain == "BASE":
        dex_url = f"https://dexscreener.com/base/{ca}"
        buy_url = f"https://app.uniswap.org/swap?&chain=base&use=v2&outputCurrency={ca}"

    elif chain == "BSC":
        dex_url = f"https://dexscreener.com/bsc/{ca}"
        buy_url = f"https://pancakeswap.finance/?outputCurrency={ca}"

    else:
        dex_url = data.get('display_link', '#')
        buy_url = data.get('display_link', '#')

    # Inline keyboard with DEX and BUY buttons
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 DEX", url=dex_url),
            InlineKeyboardButton("BUY", url=buy_url)
        ]
    ])

    # Send hype post to trending forum
    await context.bot.send_photo(
        chat_id=TRENDING_FORUM_ID,
        message_thread_id=thread_id,
        photo=data['image_file_id'],
        caption=caption,
        reply_markup=keyboard,
        parse_mode='HTML'
    )


    # Save to DB
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO hyped_projects (
                    group_id, group_name, blockchain, contract_address,
                    token_name, display_link, marketcap, image_file_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (group_id) DO UPDATE SET
                    group_name = EXCLUDED.group_name,
                    blockchain = EXCLUDED.blockchain,
                    contract_address = EXCLUDED.contract_address,
                    token_name = EXCLUDED.token_name,
                    display_link = EXCLUDED.display_link,
                    marketcap = EXCLUDED.marketcap,
                    image_file_id = EXCLUDED.image_file_id,
                    timestamp = NOW()
            """, (
                data['group_id'], data['group_name'], data['chain'], data['ca'],
                data['token_name'], data['display_link'], data['marketcap'], data['image_file_id']
            ))

    await query.message.edit_caption("✅ Hype submitted successfully!\n\nNow use the /vote to start ranking on leaderboard", parse_mode='HTML')
    return ConversationHandler.END

async def cancel_hype(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.edit_caption("❌ Hype canceled.")
    return ConversationHandler.END

async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Ensure command is used as a reply
    if not update.message.reply_to_message:
        await update.message.reply_text("⚠️ Please reply to the suspicious message and use /report <reason>")
        return

    # Get reason for report
    if not context.args:
        await update.message.reply_text("⚠️ Please provide a reason. Example: /report spammer")
        return
    reason = " ".join(context.args)

    reporter = update.effective_user
    reported_message = update.message.reply_to_message
    reported_user = reported_message.from_user

    report_text = (
        f"🚨 *New Report Received!*\n\n"
        f"*Group:* {update.effective_chat.title}\n"
        f"*Reported by:* *@{reporter.username or reporter.id}*\n"
        f"*Reported user:* *@{reported_user.username or reported_user.id}*\n"
        f"*Reason:* {reason}\n"
        f"*Message Content:* {reported_message.text_html or '[Non-text message]'}"
    )

    try:
        # Get list of group admins
        chat_admins = await context.bot.get_chat_administrators(update.effective_chat.id)

        for admin in chat_admins:
            try:
                await context.bot.send_message(
                    chat_id=admin.user.id,
                    text=report_text,
                    parse_mode='Markdown'
                )
            except:
                # Admin might not have started the bot privately
                pass

        await update.message.reply_text("✅ Report delivered to admins.")
    except Exception as e:
        await update.message.reply_text(f"Failed to send report: {str(e)}")


# Admin command to generate passwords
async def generate_passwords(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to generate new passwords"""
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context.bot):
        await update.message.reply_text("❌ Only admins can generate passwords!")
        return
    
    try:
        count = int(context.args[0]) if context.args else 1
        passwords = []
        
        for _ in range(count):
            # Generate random 8-character password
            password = ''.join(random.choices(
                string.ascii_uppercase + string.digits, 
                k=8
            ))
            if add_premium_password(password):
                passwords.append(password)
        
        await update.message.reply_text(
            "🔑 Generated Premium Passwords:\n\n" +
            "\n".join(passwords) +
            "\n\nSend these to buyers via secure channel."
        )
    except ValueError:
        await update.message.reply_text("Usage: /genpass [count]")

# Generate Text Command
async def start_genT(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /genT command initiation"""
    user_id = update.effective_user.id
    
    if is_premium_user(user_id):
        # User already has premium access
        await update.message.reply_text(
            "🔑 You already have premium access!\n\n"
            "Enter your crypto project details:\n\n"
            "Format: /generate [CoinName] [launched/prelaunch]\n"
            "Example: /generate Bitcoin launched"
        )
        return
    
    # Request password from user
    keyboard = [
        [InlineKeyboardButton("🛒 Buy Password", url="https://t.me/iam_emmadroid")],
        [InlineKeyboardButton("🔑 Enter Password", callback_data="enter_password")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🔒Ready for your Premium Access?\n\n"
        "Unlock all features in the bot for personal or group use\n"
        "For groups with multiple admins, price covers all admins!\n\n"
        "To unlock, you need a password:\n"
        "1. Purchase password from @iam_emmadroid\n"
        "2. Click 'Enter Password' below\n"
        "3. Enjoy unlimited access\n\n"
        "💰 Personal use: ₦2999 (Monthly payment)\n"
        "💰 Group use: Negotiable (Monthly payment)",
        reply_markup=reply_markup
    )

async def password_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle password button click with confirmation"""
    query = update.callback_query
    await query.answer()
    
    # Set password expectation in both user_data and chat_data
    context.user_data['expecting_password'] = True
    context.chat_data['expecting_password'] = True
    
    # Send a fresh message (don't edit existing one)
    await context.bot.send_message(
        chat_id=query.message.chat.id,
        text=(
            "🔑 Please send your premium password NOW:\n\n"
            "1. Type your password and send it here\n"
            "2. The bot will verify it immediately\n\n"
            "⚠️ You have 2 minutes before this expires"
        ),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_password")]
        ])
    )
    
    # Set a timeout to clear the state
    context.job_queue.run_once(
        clear_password_state,
        when=120,  # 2 minutes
        data={'user_id': query.from_user.id, 'chat_id': query.message.chat.id},
        name=f"pw_timeout_{query.from_user.id}"
    )

async def clear_password_state(context: ContextTypes.DEFAULT_TYPE):
    """Clear password expectation state after timeout"""
    job = context.job
    user_id = job.data['user_id']
    chat_id = job.data['chat_id']
    
    # Only clear if the state is still set
    if context.user_data.get('expecting_password'):
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text="⌛ Password entry timed out. Please start again.",
                reply_to_message_id=context.user_data.get('password_message_id')
            )
        except Exception as e:
            print(f"Timeout message error: {e}")
    
    context.user_data.pop('expecting_password', None)
    context.chat_data.pop('expecting_password', None)

async def handle_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle password input with full state management"""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    # Debug logging
    print(f"Password handler triggered by {user_id}")
    print(f"User data: {context.user_data}")
    print(f"Chat data: {context.chat_data}")

    # Check both states for password expectation
    expecting_password = (
        context.user_data.get('expecting_password') or 
        context.chat_data.get('expecting_password')
    )
    
    if not expecting_password:
        print("Not expecting password - ignoring")
        return

    password = update.message.text.strip()

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # 1. Verify and redeem password
                cursor.execute('''
                    UPDATE premium_passwords
                    SET 
                        used = TRUE,
                        used_by = %s,
                        used_at = NOW()
                    WHERE 
                        password = %s 
                        AND used = FALSE
                    RETURNING duration_days
                ''', (user_id, password))

                result = cursor.fetchone()
                if not result:
                    await update.message.reply_text("❌ Invalid or used password")
                    # Maintain both data states
                    context.user_data['expecting_password'] = True
                    context.chat_data['expecting_password'] = True
                    return

                duration_days = result[0] or 30  # Default 30 days
                expires_at = datetime.now() + timedelta(days=duration_days)

                # 2. Update premium status
                cursor.execute('''
                    INSERT INTO premium_users 
                    (user_id, expires_at)
                    VALUES (%s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET
                        expires_at = GREATEST(
                            premium_users.expires_at, 
                            EXCLUDED.expires_at
                        )
                    RETURNING expires_at
                ''', (user_id, expires_at))

                final_expiry = cursor.fetchone()[0]
                conn.commit()

                # 3. Success response
                await update.message.reply_text(
                    f"🎉 Premium activated until {final_expiry.strftime('%Y-%m-%d')}\n"
                    f"Duration: {duration_days} days\n"
                    "Use /help to see unlocked capabilities"
                )

    except Exception as e:
        logging.error(f"Password error: {str(e)}")
        await update.message.reply_text("⚠️ System error. Please try again.")
        
        # Maintain states on error
        context.user_data['expecting_password'] = True
        context.chat_data['expecting_password'] = True
        return

    finally:
        # Always clean up both states
        context.user_data.pop('expecting_password', None)
        context.chat_data.pop('expecting_password', None)
        
        # Cancel timeout job if exists
        job_name = f"pw_timeout_{user_id}_{chat_id}"
        for job in context.job_queue.get_jobs_by_name(job_name):
            job.schedule_removal()

        print("Password flow completed - states cleared")

async def cancel_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle password cancellation"""
    query = update.callback_query
    await query.answer()
    
    context.user_data.pop('expecting_password', None)
    context.chat_data.pop('expecting_password', None)
    
    await query.edit_message_text("❌ Password entry cancelled")

async def premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = get_premium_status(update.effective_user.id)
    
    if status['is_active']:
        await update.message.reply_text(
            f"🌟 PREMIUM ACTIVE\n"
            f"⌛ Expires: {status['expires_at'].strftime('%Y-%m-%d %H:%M')}\n"
            f"📅 {status['remaining_days']} days remaining"
        )
    else:
        await update.message.reply_text(
            "🔒 PREMIUM INACTIVE\n"
            "Renew access with /gent"
        )
async def check_expirations(context: ContextTypes.DEFAULT_TYPE):
    """Check and notify expiring premiums (run daily)"""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            # Get users with expiring premium
            cursor.execute('''
                SELECT 
                    user_id,
                    expires_at,
                    last_notified_at
                FROM premium_users
                WHERE 
                    expires_at BETWEEN 
                        CURRENT_TIMESTAMP AND 
                        (CURRENT_TIMESTAMP + INTERVAL '7 days')
                    AND (
                        last_notified_at IS NULL OR 
                        last_notified_at < expires_at - INTERVAL '1 day'
                    )
            ''')
            
            for user_id, expires_at, last_notified in cursor:
                remaining_days = (expires_at - datetime.now()).days
                
                # Customize messages based on urgency
                if remaining_days == 0:
                    message = "⚠️ Your premium expires TODAY! Renew to keep benefits."
                elif remaining_days <= 5:
                    message = f"🔔 Premium expires in {remaining_days} days! Renew now."
                else:
                    message = f"ℹ️ Friendly reminder: Premium expires in {remaining_days} days."
                
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=message,
                        parse_mode='HTML'
                    )
                    
                    # Update notification timestamp
                    cursor.execute('''
                        UPDATE premium_users
                        SET last_notified_at = CURRENT_TIMESTAMP
                        WHERE user_id = %s
                    ''', (user_id,))
                    conn.commit()
                    
                except Exception as e:
                    print(f"Failed to notify {user_id}: {e}")


async def get_target_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Helper to get target user from reply or arguments"""
    try:
        if update.message.reply_to_message:
            return update.message.reply_to_message.from_user

        if context.args:
            user_ref = context.args[0].lstrip('@')
            
            # Try by ID first
            try:
                user_id = int(user_ref)
                member = await context.bot.get_chat_member(
                    chat_id=update.effective_chat.id,
                    user_id=user_id
                )
                return member.user
            except ValueError:
                pass
            
            # Search by username
            async for member in context.bot.get_chat_members(update.effective_chat.id):
                if member.user.username and member.user.username.lower() == user_ref.lower():
                    return member.user

        return None
    except Exception as e:
        logger.error(f"Error getting target user: {str(e)}")
        return None


async def error_handler(update: Update, context: CallbackContext):
    error = context.error
    if update and update.message:
        await update.message.reply_text(f"⚠️ Error: {str(error)}")
    else:
        print(f"Unhandled error: {error}")


async def get_group_projvote_count(group_id: int) -> int:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT vote_count FROM hyped_projects WHERE group_id = %s", (group_id,))
            result = cursor.fetchone()
            return result[0] if result else 0

async def get_group_projrank(group_id: int) -> int:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT COUNT(*) FROM hyped_projects 
                WHERE vote_count > (SELECT vote_count FROM hyped_projects WHERE group_id = %s)
            """, (group_id,))
            return cursor.fetchone()[0] + 1

def format_time(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"

async def check_vote_cooldown(user_id: int, is_premium: bool) -> int:
    cooldown_hours = 3 if is_premium else 6
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT EXTRACT(EPOCH FROM (voted_at + INTERVAL '%s hours' - NOW()))
                FROM user_votes
                WHERE user_id = %s
                ORDER BY voted_at DESC
                LIMIT 1
            """, (cooldown_hours, user_id))
            result = cursor.fetchone()
            return max(0, int(result[0])) if result else 0

def is_vote_notification_enabled(group_id: int) -> bool:
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # Try group_votes first
                cursor.execute(
                    "SELECT vote_notifications_enabled FROM group_votes WHERE group_id = %s",
                    (group_id,)
                )
                result = cursor.fetchone()
                if result is not None:
                    return result[0]

                # If not found, check hyped_projects
                cursor.execute(
                    "SELECT vote_notifications_enabled FROM hyped_projects WHERE group_id = %s",
                    (group_id,)
                )
                result = cursor.fetchone()
                if result is not None:
                    return result[0]

                # Default fallback if group not in either table
                return True
    except Exception as e:
        logger.error(f"Error checking notification toggle: {e}")
        return True

# /postad <text> <url> <duration_hours>
async def post_ad_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != CREATOR_ID:
        return

    try:
        args_text = update.message.text.replace('/postad', '', 1).strip()
        args = shlex.split(args_text)  # Smart splitting including quotes

        if len(args) < 3:
            raise ValueError("Insufficient arguments")

        ad_text = args[0]  # Can be "Buy Now"
        ad_url = args[1]
        hours = int(args[2])
        expires_at = datetime.now(timezone.utc) + timedelta(hours=hours)

        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO button_ads (ad_text, ad_url, expires_at)
                    VALUES (%s, %s, %s)
                """, (ad_text, ad_url, expires_at))
                conn.commit()

        await update.message.reply_text(f"✅ Ad button saved! Expires in {hours} hours.")
    except Exception as e:
        logger.error(f"Failed to post ad: {e}")
        await update.message.reply_text("❌ Failed to save ad. Format:\n`/postad Buy Now https://t.me/someurl 4`", parse_mode="Markdown")

def get_active_ads():
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT ad_text, ad_url FROM button_ads
                WHERE expires_at > %s
            """, (datetime.now(timezone.utc),))
            return cursor.fetchall()

### Updated Notification Functions ###
async def send_projvote_notification(group_id: int, new_count: int, context: ContextTypes.DEFAULT_TYPE):
    # Skip notification if disabled for this group
    if not is_vote_notification_enabled(group_id):
        return

    try:
        # Fetch group details including display link
        group_name, display_link, *_ = await get_group_projdetails(group_id, context)
        
        # Format group name with link if available
        if display_link:
            group_display = f"[{group_name}]({display_link})"
        else:
            group_display = f"*{group_name}*"
        
        position = await get_group_projrank(group_id)
        votes_needed = max(0, 10 - new_count)
        leaderboard_link = f"https://t.me/stftrending/12/391"
        
        # Create the message
        ad_buttons = []
        for ad_text, ad_url in get_active_ads():
            ad_buttons.append([InlineKeyboardButton(f"📰 {ad_text} 🄰🄳🅂 📰", url=ad_url)])

        standard_buttons = [
            [InlineKeyboardButton("🗳 VOTE", url=f"https://t.me/{context.bot.username}?start=vote_{group_id}")],
            [InlineKeyboardButton("🏆 VIEW LEADERBOARD", url=leaderboard_link)]
        ]

        await context.bot.send_photo(
            chat_id=group_id,
            photo="https://t.me/myhostinger/36",
            caption=(
                f"🗳 *NEW VOTE RECEIVED!* \n\n"
                f"🏆 *Group:* {group_display}\n"
                f"📊 *Current Votes:* {new_count}\n"
                f"📈 *Leaderboard Position:* #{position}\n"
                f"🗳 *Votes needed to trend:* {votes_needed}\n\n"
                f"{'🎉 *CONGRATULATIONS!* Your group is now trending on the [leaderboard](' + leaderboard_link + ')!' if new_count >= 10 else ''}"
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(standard_buttons + ad_buttons)
        )

        
    except Exception as e:
        logger.error(f"Vote notification error: {e}")
        # Fallback without image
        await context.bot.send_message(
            chat_id=group_id,
            text=f"🗳 New vote for {group_name} - Total: {new_count}",
            parse_mode="Markdown"
        )

async def popups_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context.bot):
        await update.message.reply_text("You have no rights to do this.")
        return
    chat_id = update.effective_chat.id

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ YES", callback_data=f"popup_yes_{chat_id}"),
            InlineKeyboardButton("❌ NO", callback_data=f"popup_no_{chat_id}")
        ]
    ])

    await update.message.reply_text(
        "⚙️ You can Enable / Disable the popups notifications if someone vote for your community.\n\nDo you want to receive vote pop-up notifications?",
        reply_markup=keyboard
    )


def update_vote_popup_preference(group_id: int, enabled: bool):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("UPDATE group_votes SET vote_notifications_enabled = %s WHERE group_id = %s", (enabled, group_id))
                logger.info(f"group_votes update rowcount: {cursor.rowcount}")

                if cursor.rowcount == 0:
                    cursor.execute("UPDATE hyped_projects SET vote_notifications_enabled = %s WHERE group_id = %s", (enabled, group_id))
                    logger.info(f"hyped_projects update rowcount: {cursor.rowcount}")
                else:
                    logger.info(f"Updated group_votes for {group_id} to {enabled}")

                conn.commit()
    except Exception as e:
        logger.error(f"Error updating vote popup preference: {e}")


async def popup_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = int(data.split("_")[-1])

    if "yes" in data:
        update_vote_popup_preference(chat_id, True)
        await query.edit_message_text("✅ You will now receive vote notifications.")
    else:
        update_vote_popup_preference(chat_id, False)
        await query.edit_message_text("❌ You will not receive vote notifications.")


async def get_group_projdetails(group_id: int, context: ContextTypes.DEFAULT_TYPE) -> tuple:
    """Fetch group name, display link, and custom photo URL from database"""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT group_name, display_link, contract_address, token_name, marketcap, image_file_id, blockchain
                    FROM hyped_projects
                    WHERE group_id = %s
                """, (group_id,))
                result = cursor.fetchone()
                
                if result:
                    return result  # returns all 7: (group_name, display_link, contract_address, token_name, marketcap, image_file_id, blockchain)
    except Exception as e:
        logger.error(f"Failed to fetch group details: {e}")
    
    # Fallback if not in database
    name = await get_group_name(group_id, context)
    return name, None, None

def get_dex_and_buy_urls(chain: str, ca: str) -> tuple[str, str]:
    chain = chain.upper()
    if chain == "ETH":
        dex_url = f"https://dexscreener.com/ethereum/{ca}"
        buy_url = f"https://app.uniswap.org/swap?&chain=mainnet&use=v2&outputCurrency={ca}"
    elif chain == "SOL":
        dex_url = f"https://dexscreener.com/solana/{ca}"
        buy_url = f"https://dexscreener.com/solana/{ca}"
    elif chain == "BASE":
        dex_url = f"https://dexscreener.com/base/{ca}"
        buy_url = f"https://app.uniswap.org/swap?&chain=base&use=v2&outputCurrency={ca}"
    elif chain == "BSC":
        dex_url = f"https://dexscreener.com/bsc/{ca}"
        buy_url = f"https://pancakeswap.finance/?outputCurrency={ca}"
    else:
        dex_url = "#"
        buy_url = "#"
    return dex_url, buy_url


    
async def send_projleaderboard_notification(group_id: int, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Get group details with display and photo link
        group_name, contract_address, token_name, marketcap, display_link, image_file_id, blockchain = await get_group_projdetails(group_id, context)
        dex_url, buy_url = get_dex_and_buy_urls(blockchain, contract_address)

        # Format group name with display link if available
        if display_link:
            group_display = f'<a href="{display_link}">{token_name}</a>'
        else:
            group_display = f'<b>{token_name}</b>'


        # Prepare image
        photo = None

        if image_file_id:
            # Use Telegram-hosted photo URL
            photo = image_file_id

        else:
            # Try fetching group profile photo
            try:
                chat = await context.bot.get_chat(group_id)
                if chat.photo:
                    photo_file = await chat.photo.big_file.download_as_bytearray()
                    photo_bytes = BytesIO(photo_file)
                    photo_bytes.name = 'group_photo.jpg'
                    photo = photo_bytes
            except Exception:
                pass  # Will fallback to default image

        if not photo:
            photo = "https://t.me/myhostinger/26"  # Default image URL fallback

        # Message text
        leaderboard_link = "https://t.me/stftrending/12/391"
        message_text = (
            f"{group_display} HAS OFFICIALLY <a href='{leaderboard_link}'>ENTERED THE STF LEADERBOARD</a>!\n\n"
            f"🔸<b>CA:</b> <code>{contract_address}</code>\n"
            f"🔸<b>Group:</b> {display_link}\n"
            f"🔸<b>Marketcap:</b> ${marketcap}\n\n"
            "🔵 <a href='https://t.me/stfinfoportal/235'>DEX</a> ┃ "
            "🔘 <a href='https://t.me/stftrendingbot?start=boosttrend'>TREND</a> ┃ "
            "🟣 <a href='https://t.me/stftrendingbot?start=boostvote'>BOOST</a>"
        )


        # Inline keyboard
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📊 DEX", url=dex_url),
                InlineKeyboardButton("🛒 BUY", url=buy_url)
            ],
            [
                InlineKeyboardButton("📣 Hype Project", url=f"https://t.me/{context.bot.username}?start=add")
            ]
        ])


        # Send photo message
        await context.bot.send_photo(
            chat_id=TRENDING_CHANNEL_ID,
            message_thread_id=TRENDING12,
            photo=photo,
            caption=message_text,
            parse_mode='HTML',
            reply_markup=keyboard
        )

    except Exception as e:
        logger.error(f"Leaderboard notification failed: {e}")

        # Fallback plain message if all fails
        await context.bot.send_message(
            chat_id=TRENDING_CHANNEL_ID,
            message_thread_id=TRENDING12,
            text=f"🚀 {group_name} HAS OFFICIALLY ENTERED THE LEADERBOARD!",
            parse_mode="Markdown"
        )
        

### Updated Vote Cleanup Function (6-hour expiration) ###
async def delete_expired_projvotes(context: ContextTypes.DEFAULT_TYPE):
    """Delete user votes older than 24 hours and reset expired project vote counts"""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:

                # ✅ 1. Clean user_votes older than 24 hours
                cursor.execute("""
                    SELECT user_id, group_id FROM user_votes
                    WHERE voted_at < NOW() - INTERVAL '24 hours'
                """)
                expired_votes = cursor.fetchall()

                if expired_votes:
                    for user_id, group_id in expired_votes:
                        cursor.execute("""
                            UPDATE hyped_projects
                            SET vote_count = GREATEST(0, vote_count - 1)
                            WHERE group_id = %s
                            RETURNING vote_count
                        """, (group_id,))

                        result = cursor.fetchone()
                        if result:
                            new_count = result[0]
                            if new_count == 10:  # Optional threshold
                                await send_projvote_notification(group_id, new_count, context)

                    cursor.execute("""
                        DELETE FROM user_votes
                        WHERE voted_at < NOW() - INTERVAL '24 hours'
                    """)
                    logger.info(f"Deleted {len(expired_votes)} user_votes older than 24 hours")

                # ✅ 2. Reset expired project vote counts
                now = datetime.now(timezone.utc)
                for table in ['group_votes', 'hyped_projects']:
                    cursor.execute(f"""
                        UPDATE {table}
                        SET vote_count = 0, vote_expiry = NULL
                        WHERE vote_expiry IS NOT NULL AND vote_expiry <= %s
                    """, (now,))
                    logger.info(f"✅ Cleared expired vote counts in {table}")

            conn.commit()

    except Exception as e:
        logger.error(f"Error in vote expiry cleanup: {e}")


### VOTE COMMAND HANDLER ###
async def projvote_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    
    if not chat or chat.type == "private":
        await update.message.reply_text("This command only works in groups!")
        return

    deep_link = f"https://t.me/{context.bot.username}?start=vote_{chat.id}"
    current_votes = await get_group_projvote_count(chat.id)
    
    await context.bot.send_photo(
        chat_id=chat.id,
        photo="https://t.me/myhostinger/38",
        caption=f"🗳 *Cast Vote for {chat.title}?*\n\n"
               f"• Current Votes: {current_votes}\n"
               f"• Position: #{await get_group_projrank(chat.id)}\n\n"
               f"Votes needed to enter Leaderboard:\n"
               f"{max(0, 10 - current_votes)}\n\n"
               f"_{datetime.now().strftime('%I:%M %p')}_",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✔ Vote for This Group", url=deep_link)]
        ]),
        parse_mode="Markdown"
    )

### VOTE PROCESSING HANDLER ###
async def handle_projvote_cast(update: Update, context: ContextTypes.DEFAULT_TYPE, group_id: int):
    user = update.effective_user
    chat = update.effective_chat
    
    if chat.type != "private":
        await update.message.reply_text("Please complete your vote in our private chat!")
        return

    try:
        is_premium = is_premium_user(user.id)
        cooldown_hours = 3 if is_premium else 6
        
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # 1. First clean up expired votes (older than 24 hours) for this user-group pair
                cursor.execute("""
                    DELETE FROM user_votes
                    WHERE user_id = %s AND group_id = %s 
                    AND voted_at < NOW() - INTERVAL '24 hours'
                    RETURNING 1
                """, (user.id, group_id))
                
                if cursor.rowcount > 0:
                    cursor.execute("""
                        UPDATE hyped_projects
                        SET vote_count = GREATEST(0, vote_count - 1)
                        WHERE group_id = %s
                    """, (group_id,))
                
                # 2. Ensure group exists with minimum required fields
                try:
                    chat_info = await context.bot.get_chat(group_id)
                    group_name = chat_info.title
                except Exception:
                    group_name = f"Group {group_id}"

                cursor.execute("""
                    INSERT INTO hyped_projects
                    (group_id, group_name, vote_count, last_voted)
                    VALUES (%s, %s, 0, NOW())
                    ON CONFLICT (group_id) DO UPDATE
                    SET group_name = COALESCE(hyped_projects.group_name, EXCLUDED.group_name)
                """, (group_id, group_name))
                
                # 1. Check if user has voted for THIS GROUP recently
                cursor.execute("""
                    SELECT EXTRACT(EPOCH FROM (
                        voted_at + INTERVAL '%s hours' - NOW()
                    )) 
                    FROM user_votes 
                    WHERE user_id = %s AND group_id = %s
                    ORDER BY voted_at DESC 
                    LIMIT 1
                """, (cooldown_hours, user.id, group_id))
                
                cooldown_result = cursor.fetchone()
                
                # 2. If cooldown active for this group, block voting
                if cooldown_result and cooldown_result[0] > 0:
                    remaining_seconds = float(cooldown_result[0])
                    hours = int(remaining_seconds // 3600)
                    minutes = int((remaining_seconds % 3600) // 60)
                    remaining = f"{hours}h {minutes}m" if hours else f"{minutes}m"
                    
                    await update.message.reply_text(
                        f"⏳ You can vote for this group again in {remaining}\n\n"
                        f"Premium members can vote every 3 hours!\n\n"
                        f"You can still vote for other groups!",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🌟 Leaderboard", url="https://t.me/stftrending/12/391")]
                        ])
                    )
                    return
                
                # 3. Record new vote (will overwrite previous vote for this group)
                cursor.execute("""
                    INSERT INTO user_votes (user_id, group_id, is_premium, voted_at)
                    VALUES (%s, %s, %s, NOW())
                    ON CONFLICT (user_id, group_id) DO UPDATE 
                    SET voted_at = NOW()
                """, (user.id, group_id, is_premium))

                # 4. Update group stats
                cursor.execute("""
                    UPDATE hyped_projects
                    SET vote_count = vote_count + 1,
                        last_voted = NOW()
                    WHERE group_id = %s
                    RETURNING vote_count
                """, (group_id,))
                
                new_count = cursor.fetchone()[0]
                conn.commit()

                # 5. Get group info
                group_name = await get_group_name(group_id, context)
                
                # 6. Send notifications
                await send_projvote_notification(group_id, new_count, context)
                if new_count == 10:
                    await send_projleaderboard_notification(group_id, context)
                
                # 7. Confirmation message
                await update.message.reply_text(
                    f"✅ *Vote Cast Successfully!*\n\n"
                    f"Thank you for voting for *{group_name}*!\n\n"
                    f"Current Votes: *{new_count}*\n"
                    f"Next vote for this group in {'3' if is_premium else '6'} hours\n\n"
                    f"You can vote for other groups immediately!",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🏆 View Leaderboard", url=f"https://t.me/stftrending/12/391")]
                    ])
                )

    except Exception as e:
        logger.error(f"Vote processing error for {user.id}: {e}", exc_info=True)
        await update.message.reply_text("Failed to process your vote. Please try again later.")
        

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
    setup_verified_leaderboard_job(application)
    setup_xp_leaderboard_job(application)
    application.add_error_handler(error_handler)
    setup_expiration_checks(application.job_queue)
    application.job_queue.run_daily(
        callback=lambda ctx: update_premium_statuses(),
        time=time(hour=0, minute=0),  # Midnight UTC
        name="daily_premium_status_update"
    )
    application.job_queue.run_daily(
       auto_shill_report,
       time=time(hour=23, minute=0),
       name="auto_shill_report"
    )
    # 6. Daily Count Reset (Midnight UTC)
    application.job_queue.run_daily(
        reset_dailycounts,
        time=time(hour=0, minute=0, tzinfo=UTC),
        name="dailycount_reset"
    )
    
    # 7. Milestone Checks (every 5 minutes)
    application.job_queue.run_repeating(
        check_milestones,
        interval=300,
        first=30,
        name="milestone_check"
    )

    # 8. Vote Cleanup (every 1 hour)
    application.job_queue.run_repeating(
        delete_expired_votes,
        interval=3600,  # 1 hour
        first=60,       # Start 1 minute after launch
        name="vote_cleanup"
    )

    # 8. Vote Cleanup (every 1 hour)
    application.job_queue.run_repeating(
        delete_expired_projvotes,
        interval=3600,  # 1 hour
        first=60,       # Start 1 minute after launch
        name="projvote_cleanup"
    )

    # New leaderboard update job (every 5 minutes)
    application.job_queue.run_repeating(
        lambda ctx: update_leaderboard(ctx),
        interval=300,  # 5 minutes in seconds
        first=10,      # Start after 10 seconds
        name="leaderboard_update"
    )
    
    application.job_queue.run_daily(
        check_expirations,
        time=time(hour=10, minute=0),
        name="daily_expiration_checks"
    )

    # Add conversation handler for the boost flow
    followers_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("buyfollowers", buy_followers)],
        states={
            ASK_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_link)],
            ASK_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_region)],
            ASK_REGION: [MessageHandler(filters.TEXT & ~filters.COMMAND, show_payment_details)],
            SHOW_PRICE: [CallbackQueryHandler(proceed_payment_callback, pattern="^proceed_payment$")],
            WAIT_FOR_PROOF: [
                CallbackQueryHandler(paid_button_callback, pattern="^paid_button$"),
                MessageHandler(filters.PHOTO, handle_payment_proof)
            ]
        },
        fallbacks=[]
    )

    review_handler = ConversationHandler(
        entry_points=[
            CommandHandler("review", start_review),
            CommandHandler("start", start_review, filters=filters.Regex(r'^review$')),  # <-- handles /start review
        ],
        states={
            SEARCH_SHILL_TEAM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, search_shill_team)
            ],
            CONFIRM_SHILL_TEAM: [
                CallbackQueryHandler(confirm_shill_team, pattern=r"^select_-?\d+$"),
                CallbackQueryHandler(confirm_shill_team, pattern="^cancel_review$")
            ],
            SUBMIT_REVIEW: [
                CallbackQueryHandler(submit_review, pattern=r"^rate_\d$")
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel_review)],
    )

    boost_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("boostvote", boost_vote)],
        states={
            BOOST_GET_GROUP: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_group_id)],
            BOOST_SHOW_PLANS: [CallbackQueryHandler(handle_plan_selection, pattern="^plan_")],
            BOOST_PAYMENT: [CallbackQueryHandler(request_hash, pattern="^submit_hash$")],
            BOOST_HASH: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_hash)]
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)],
        allow_reentry=True
    )

    hype_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("add", hypeme)],
        states={
            HYPE_CHAIN: [CallbackQueryHandler(handle_chain)],
            HYPE_CA: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ca)],
            HYPE_TOKEN_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_token_name)],
            HYPE_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link)],
            HYPE_IMAGE: [MessageHandler(filters.PHOTO, handle_image)],
            HYPE_CONFIRM: [CallbackQueryHandler(confirm_hype, pattern="CONFIRM_HYPE"),
                       CallbackQueryHandler(cancel_hype, pattern="CANCEL_HYPE")],
        },
        fallbacks=[]
    )


    # Define the ConversationHandler for /boosttrend
    boost_trend_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("boosttrend", boost_trend)
        ],
        states={
            BOOST_GET_GROUP1: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_group_id1)
            ],
            BOOST_SHOW_PLANS2: [
                CallbackQueryHandler(handle_trend_plan_selection, pattern="^trend_")
            ],
            BOOST_PAYMENT3: [
                CallbackQueryHandler(request_hash1, pattern="^submit_hash1$")
            ],
            BOOST_HASH4: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_hash1)
            ]
        },
        fallbacks=[
            CommandHandler("cancel", lambda update, context: ConversationHandler.END)
        ],
        allow_reentry=True
    )

    set_trend_image_handler = ConversationHandler(
        entry_points=[CommandHandler("settrendimage", start_set_trend_image)],
        states={
            SET_IMAGE_WAIT: [MessageHandler(filters.PHOTO, receive_trend_image)],
        },
        fallbacks=[CommandHandler("cancel", cancel_set_trend_image)],
        allow_reentry=True
    )

    application.add_handler(followers_conv_handler)
    application.add_handler(boost_conv_handler)
    application.add_handler(boost_trend_conv_handler)
    application.add_handler(review_handler)
    application.add_handler(hype_conv_handler)
    application.add_handler(set_trend_image_handler)

    # Add command handlers
    handlers = [
        ("start", start),
        ("help", help_command),
        ("register_raider", register_raider),
        ("register", register_command),
        ("broadcast", broadcast),
        ("promo", broadcast_command),
        ("gent", start_genT),
        ("premium", premium_command),
        ("genpassq1", generate_passwords),
        ("report", report_command),
        ("buyfollowers", buy_followers),
        ("markcompleted", mark_completed),
        ("markfailed", mark_failed),
        ("setprice", set_price),
        ("linkproject", link_project),
        ("setshilltarget", set_shill_target),
        ("shillstat", shill_stat),
        ("review", start_review),
        ("cancel", cancel_review),
        ("add", hypeme),
        ("poll", vote_command),
        ("vote", projvote_command),
        ("postad", post_ad_command),
        ("boostvote", boost_vote),
        ("manualaddvote", manual_add_vote),
        ("boosttrend", boost_trend),
        ("manualboosttrend", manual_boost_trend),
        ("settrendlink", set_trend_link),
        ("removetrendlink", remove_trend_link),
        ("setcontactmelink", set_contactme_link),
        ("removecontactmelink", remove_contactme_link),
        ("settrendimage", start_set_trend_image),
        ("removetrendimage", remove_trend_image),
        ("setprofile", set_profile_link),
        ("delprofile", remove_profile_link),
        ("topvoters", top_voters),
        ("requestverification", sendgroupid),
        ("manualapprove", manualapprove),
        ("manualapproveteam", manualapproveteam),
        ("manualreject", manualreject),
        ("ultstat", ultstat),
        ("popups", popups_command),
        ("setlanguage", setlanguage),
        ("setgrouplanguage", setgrouplanguage),
        ("helloy", hello_handler),
    ]
    for command, handler in handlers:
        application.add_handler(CommandHandler(command, handler))

    # Add message handlers
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unified_message_handler))
    application.add_handler(MessageHandler(filters.ALL, track_chats))
    
    # Add callback handlers
    application.add_handler(CallbackQueryHandler(check_joined_callback, pattern="check_joined"))
    application.add_handler(CallbackQueryHandler(callback_query_handler))
    application.add_handler(CallbackQueryHandler(password_button_callback, pattern='^enter_password$'))
    application.add_handler(CallbackQueryHandler(cancel_password, pattern="^cancel_password$"))
    application.add_handler(CallbackQueryHandler(lambda u, c: u.callback_query.answer("❌ Button expired or invalid.")))
    



    
    # Scheduled Jobs
    application.job_queue.run_daily(
        lambda ctx: update_premium_statuses(),
        time=time(hour=0, minute=0),
        name="daily_status_refresh"
    )

    # Run the bot
    application.run_polling()

if __name__ == "__main__":
    main()








