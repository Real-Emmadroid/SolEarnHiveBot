import os
import psycopg
from datetime import datetime, timedelta
import time
from functools import wraps
from contextlib import contextmanager

# ========================
# Supabase Database Setup
# ========================
# Get the Supabase connection string from environment variables
DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    """Get a connection to the Supabase PostgreSQL database."""
    conn = psycopg.connect(DATABASE_URL, sslmode="require")
    return conn

# ========================
# Database Initialization
# ========================
def init_databases():
    """Initialize all database tables with proper schema."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS clickbotusers (
                    id BIGINT PRIMARY KEY,
                    general_balance NUMERIC DEFAULT 0,
                    payout_balance NUMERIC DEFAULT 0,
                    wallet_address TEXT,
                    referral_id BIGINT,
                    deposit_address TEXT
                );
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS withdrawals (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    amount NUMERIC,
                    address TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS channel_ads_details (
                    id SERIAL PRIMARY KEY,
                    ad_id BIGINT PRIMARY KEY REFERENCES ads(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    cpc NUMERIC(18,8) NOT NULL CHECK (cpc >= 0),
                    budget NUMERIC(18,8) NOT NULL CHECK (budget >= 0),
                    clicks BIGINT DEFAULT 0 CHECK (clicks >= 0),
                    skipped BIGINT DEFAULT 0 CHECK (skipped >= 0),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                );
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS bot_ads_details (
                    id SERIAL PRIMARY KEY,
                    ad_id BIGINT PRIMARY KEY REFERENCES ads(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    cpc NUMERIC(18,8) NOT NULL CHECK (cpc >= 0),
                    budget NUMERIC(18,8) NOT NULL CHECK (budget >= 0),
                    clicks BIGINT DEFAULT 0 CHECK (clicks >= 0),
                    skipped BIGINT DEFAULT 0 CHECK (skipped >= 0),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                );
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS link_ads_details (
                    id SERIAL PRIMARY KEY,
                    ad_id BIGINT PRIMARY KEY REFERENCES ads(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    cpc NUMERIC(18,8) NOT NULL CHECK (cpc >= 0),
                    budget NUMERIC(18,8) NOT NULL CHECK (budget >= 0),
                    clicks BIGINT DEFAULT 0 CHECK (clicks >= 0),
                    skipped BIGINT DEFAULT 0 CHECK (skipped >= 0),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                );
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS post_view_ads_details (
                    id SERIAL PRIMARY KEY,
                    ad_id BIGINT PRIMARY KEY REFERENCES ads(id) ON DELETE CASCADE,
                    cpc NUMERIC(18,8) NOT NULL CHECK (cpc >= 0),
                    budget NUMERIC(18,8) NOT NULL CHECK (budget >= 0),
                    clicks BIGINT DEFAULT 0 CHECK (clicks >= 0),
                    skipped BIGINT DEFAULT 0 CHECK (skipped >= 0),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                );
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS ads (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    ad_type VARCHAR(50) NOT NULL, -- 'channel_join', 'bot_messaging', 'post_view', 'external_link'
                    details JSONB NOT NULL,        -- e.g., {"channel_link": "..."} or {"url": "..."}
                    status VARCHAR(20) NOT NULL,   -- 'running', 'expired', 'paused', etc.
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                    expires_at TIMESTAMP WITH TIME ZONE
                );
            ''')


            cursor.execute('''
                ALTER TABLE premium_users
                ADD COLUMN IF NOT EXISTS granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ADD COLUMN IF NOT EXISTS last_notified_at TIMESTAMP DEFAULT NULL;
            ''')
            

        conn.commit()

# ========================
# Database Operations
# ========================

def get_user(user_id: int) -> dict:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM clickbotusers WHERE id = %s", (user_id,))
            row = cursor.fetchone()
            if not row:
                cursor.execute("INSERT INTO clickbotusers (id) VALUES (%s)", (user_id,))
                conn.commit()
                cursor.execute("SELECT * FROM clickbotusers WHERE id = %s", (user_id,))
                row = cursor.fetchone()
            return {
                'id': row[0],
                'general_balance': row[1],
                'payout_balance': row[2],
                'deposit_address': row[3],
                'wallet_address': row[4]
            }

def update_balances(user_id: int, general: float = None, payout: float = None):
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            if general is not None:
                cursor.execute("UPDATE clickbotusers SET general_balance = %s WHERE id = %s", (general, user_id))
            if payout is not None:
                cursor.execute("UPDATE clickbotusers SET payout_balance = %s WHERE id = %s", (payout, user_id))
            conn.commit()

def set_deposit_address(user_id: int, address: str):
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE clickbotusers SET deposit_address = %s WHERE id = %s", (address, user_id))
            conn.commit()

def get_deposit_address(user_id: int) -> str:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT deposit_address FROM clickbotusers WHERE id = %s", (user_id,))
            result = cursor.fetchone()
            return result[0] if result else None

def convert_earnings_to_general(user_id: int) -> tuple[bool, float]:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            # Fetch current balances
            cursor.execute("SELECT general_balance, payout_balance FROM clickbotusers WHERE id = %s", (user_id,))
            row = cursor.fetchone()

            if not row:
                return False, 0.0

            general, payout = float(row[0]), float(row[1])

            if payout <= 0:
                return False, 0.0

            # Perform conversion
            new_general = general + payout
            cursor.execute("""
                UPDATE clickbotusers 
                SET general_balance = %s, payout_balance = 0 
                WHERE id = %s
            """, (new_general, user_id))
            conn.commit()
            return True, payout


def add_referral_deposit_bonus(user_id, deposit_amount):
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT referral_id FROM clickbotusers WHERE user_id = %s", (user_id,))
            referrer = cursor.fetchone()
            if not referrer or not referrer[0]:
                return
            referrer_id = referrer[0]

            bonus = deposit_amount * 0.02  # 2% bonus
            cursor.execute("""
                UPDATE users
                SET payout_balance = payout_balance + %s
                WHERE user_id = %s
            """, (bonus, referrer_id))
            conn.commit()


def add_referral_task_bonus(user_id, earning_amount):
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT referral_id FROM clickbotusers WHERE user_id = %s", (user_id,))
            referrer = cursor.fetchone()
            if not referrer or not referrer[0]:
                return
            referrer_id = referrer[0]

            bonus = earning_amount * 0.15  # 15% bonus
            cursor.execute("""
                UPDATE clickbotusers
                SET payout_balance = payout_balance + %s
                WHERE user_id = %s
            """, (bonus, referrer_id))
            conn.commit()


            
def with_retry(max_attempts=3, delay=0.5):
    """Decorator for retrying database operations."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except psycopg2.OperationalError as e:
                    if "locked" in str(e) and attempt < max_attempts - 1:
                        time.sleep(delay * (attempt + 1))
                        continue
                    raise
            return None
        return wrapper
    return decorator



# ========================
# Initialization
# ========================
if __name__ == "__main__":
    init_databases()
    print("Databases initialized in Supabase PostgreSQL database.")











