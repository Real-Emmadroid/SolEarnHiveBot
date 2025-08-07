import os
import psycopg
from datetime import datetime, timedelta
import time
from functools import wraps

# ========================
# Supabase Database Setup
# ========================
# Get the Supabase connection string from environment variables
DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    """Get a connection to the Supabase PostgreSQL database."""
    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    return conn

# ========================
# Database Initialization
# ========================
def init_databases():
    """Initialize all database tables with proper schema."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            # Teams Database
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS teams (
                    id SERIAL PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL,
                    leader_id BIGINT NOT NULL,
                    verified BIGINT DEFAULT 0
                );
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS team_members (
                    id SERIAL PRIMARY KEY,
                    team_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    username TEXT NOT NULL,
                    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE
                );
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS pending_raiders (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT UNIQUE NOT NULL,
                    username TEXT NOT NULL,
                    twitter_handle TEXT,
                    team_id BIGINT NOT NULL,
                    requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(team_id) REFERENCES teams(id) ON DELETE CASCADE
                );
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS raiders (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT UNIQUE NOT NULL,
                    username TEXT,
                    team_id BIGINT,
                    twitter_handle TEXT,
                    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(team_id) REFERENCES teams(id) ON DELETE SET NULL
                );
            ''')

            # Bot Database
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS projects (
                    id SERIAL PRIMARY KEY,
                    chat_id BIGINT NOT NULL,
                    project_name TEXT NOT NULL,
                    leads TEXT NOT NULL,
                    raiders TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(chat_id, project_name)
                );
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS project_balances (
                    user_id BIGINT,
                    username TEXT,
                    project_name TEXT,
                    balance BIGINT DEFAULT 0,
                    week BIGINT DEFAULT 0,
                    PRIMARY KEY (user_id, project_name)
                );
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS warnings (
                    user_id BIGINT,
                    chat_id BIGINT,
                    count BIGINT DEFAULT 0,
                    last_warned TIMESTAMP,
                    PRIMARY KEY (user_id, chat_id)
                );
            ''')


            cursor.execute('''
                CREATE TABLE IF NOT EXISTS word_triggers (
                    chat_id BIGINT,
                    word TEXT,
                    response TEXT,
                    PRIMARY KEY (chat_id, word)
                );
            ''')
            
            
            # Filters Database
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS word_filters (
                    chat_id BIGINT,
                    word TEXT,
                    PRIMARY KEY (chat_id, word)
                );
            ''')

            #Roast Database
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS roasts (
                    id SERIAL PRIMARY KEY,
                    text TEXT NOT NULL,
                    added_by BIGINT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            ''')
            
            # Broadcast Database
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS broadcast_chats (
                    chat_id BIGINT PRIMARY KEY
                );
            ''')

            # Raids Database
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS raids (
                    id SERIAL PRIMARY KEY,
                    chat_id BIGINT NOT NULL,
                    message_id BIGINT NOT NULL,
                    tweet_id TEXT NOT NULL,
                    goals TEXT NOT NULL,
                    progress TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            ''')

            # Table for subscribed chats
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS bible_subscriptions (
                    chat_id BIGINT PRIMARY KEY
               ); 
           ''')

            # Table for bible verses
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS bible_verses (
                    id SERIAL PRIMARY KEY,
                    text TEXT NOT NULL
               );
           ''')

            # Raid templates table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS raid_templates (
                    id SERIAL PRIMARY KEY,
                    template_text TEXT NOT NULL,
                    coin_status VARCHAR(20) CHECK (coin_status IN ('launched', 'prelaunch')),
                    category VARCHAR(50),
                    used_count INT DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS premium_passwords (
                    password TEXT PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    used BOOLEAN DEFAULT FALSE,
                    used_by BIGINT DEFAULT NULL,
                    used_at TIMESTAMP DEFAULT NULL
                );
            ''')

            cursor.execute('''
                CREATE INDEX IF NOT EXISTS premium_users_active_idx 
                ON premium_users (is_active, expires_at)
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS premium_users (
                    user_id BIGINT PRIMARY KEY,
                    activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP,
                    is_active BOOLEAN DEFAULT TRUE
                );
            ''')


            cursor.execute('''
                ALTER TABLE premium_passwords 
                ADD COLUMN IF NOT EXISTS id SERIAL,
                ADD COLUMN IF NOT EXISTS duration_days BIGINT DEFAULT 30;
            ''')

            cursor.execute('''
                ALTER TABLE user_votes ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP 
                GENERATED ALWAYS AS (voted_at + INTERVAL '24 hours') STORED;
            ''')


            cursor.execute('''
                ALTER TABLE premium_users
                ADD COLUMN IF NOT EXISTS granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ADD COLUMN IF NOT EXISTS last_notified_at TIMESTAMP DEFAULT NULL;
            ''')
            
            # User usage tracking
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_raid_usage (
                    user_id BIGINT PRIMARY KEY,
                    daily_count INT DEFAULT 0,
                    last_used_date DATE,
                    is_premium BOOLEAN DEFAULT FALSE,
                    payment_tx_hash TEXT DEFAULT NULL,
                    last_request_time TIMESTAMP
                );
            ''')

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS group_timetables (
                    chat_id BIGINT NOT NULL,
                    project_name VARCHAR(50) NOT NULL,
                    content TEXT,
                    file_id TEXT,
                    PRIMARY KEY (chat_id, project_name)
                )
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS project_links (
                    id SERIAL PRIMARY KEY,
                    project_name TEXT NOT NULL,
                    lead_username TEXT NOT NULL,
                    message_id BIGINT,
                    chat_id BIGINT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS project_mapping (
                    project_name TEXT PRIMARY KEY,
                    raid_group_id BIGINT NOT NULL,
                    community_group_id BIGINT NOT NULL
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS groups (
                    id BIGINT PRIMARY KEY,
                    title TEXT NOT NULL,
                    display_link TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS shill_targets (
                    project_name TEXT PRIMARY KEY,
                    expected_links INTEGER NOT NULL
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS creator_triggers (
                    id SERIAL PRIMARY KEY,
                    trigger TEXT UNIQUE NOT NULL,
                    response TEXT NOT NULL
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS group_settings (
                    chat_id BIGINT PRIMARY KEY,
                    chat_mode BOOLEAN DEFAULT FALSE
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS community_triggers (
                    id SERIAL PRIMARY KEY,
                    trigger_word TEXT NOT NULL,
                    response TEXT,
                    sticker_id TEXT
                )
            """)

            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS community_triggers_unique_idx 
                ON community_triggers (
                    trigger_word, 
                    COALESCE(response, ''), 
                    COALESCE(sticker_id, '')
                )
            """)
            


            cursor.execute("""
                CREATE TABLE IF NOT EXISTS welcome_messages (
                    chat_id BIGINT PRIMARY KEY,
                    message TEXT NOT NULL
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS group_users (
                    chat_id BIGINT,
                    user_id BIGINT,
                    username TEXT,
                    PRIMARY KEY (chat_id, user_id)
                )
            """)


            cursor.execute("""
                CREATE TABLE IF NOT EXISTS raid_alarms (
                    id SERIAL PRIMARY KEY,
                    chat_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    project_name VARCHAR(100) NOT NULL,
                    alert_time TIMESTAMP NOT NULL,
                    interval_minutes INT DEFAULT NULL, -- For recurring alarms
                    notified BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_xp (
                    user_id BIGINT PRIMARY KEY,
                    username VARCHAR(100),
                    first_name VARCHAR(100),
                    xp INTEGER DEFAULT 0,
                    messages INTEGER DEFAULT 0,
                    profile_link TEXT,
                    last_active TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS group_votes (
                    group_id BIGINT PRIMARY KEY,
                    group_name TEXT NOT NULL,
                    vote_count INTEGER DEFAULT 0,
                    last_voted TIMESTAMP,
                    leaderboard_position INTEGER,
                    display_link TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS system_settings (
                    id INT PRIMARY KEY DEFAULT 1,
                    last_reset TIMESTAMP NOT NULL DEFAULT NOW()
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS community_stats (
                    community_id BIGINT NOT NULL,
                    project_name TEXT NOT NULL,
                    daily_links INTEGER DEFAULT 0,
                    all_time_links INTEGER DEFAULT 0,
                    last_updated TIMESTAMP DEFAULT NOW(),
                    PRIMARY KEY (community_id, project_name)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_votes (
                    user_id BIGINT,
                    group_id BIGINT,
                    voted_at TIMESTAMP DEFAULT NOW(),
                    is_premium BOOLEAN DEFAULT FALSE,
                    PRIMARY KEY (user_id, group_id)
                )
            """)

            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_user_votes_timestamp ON user_votes (user_id, voted_at);
            ''')

            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_group_votes_count ON group_votes (vote_count DESC);
            ''')
            

            # Reactions Database
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS reactions (
                    message_id BIGINT,
                    user_id BIGINT,
                    username TEXT,
                    timestamp TEXT,
                    PRIMARY KEY (message_id, user_id)
                );
            ''')

        conn.commit()

# ========================
# Database Operations
# ========================
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
# Maintenance Functions
# ========================
@with_retry()
def remove_inactive() -> int:
    """Remove inactive members."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            two_weeks_ago = datetime.now() - timedelta(weeks=2)
            cursor.execute(
                "DELETE FROM raiders WHERE last_active < %s",
                (two_weeks_ago,)
            )
            removed_count = cursor.rowcount
            conn.commit()
            return removed_count

# ========================
# Generate Text Functions
# ========================
@with_retry()
def get_status_template(coin_name: str, status: str) -> str:
    """Get professional template based on coin status"""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute('''
                SELECT template_text 
                FROM raid_templates 
                WHERE coin_status = %s 
                ORDER BY RANDOM() 
                LIMIT 1
            ''', (status,))
            result = cursor.fetchone()
            
            if not result:
                return None
                
            template = result[0]
            return template.replace("{coin}", coin_name)

@with_retry()
def add_raid_template(template_text: str, coin_status: str, category: str) -> bool:
    """Insert a new raid template into the database"""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            try:
                cursor.execute('''
                    INSERT INTO raid_templates 
                    (template_text, coin_status, category)
                    VALUES (%s, %s, %s)
                ''', (template_text, coin_status, category))
                conn.commit()
                return True
            except psycopg2.Error as e:
                print(f"Error inserting template: {e}")
                conn.rollback()
                return False

def add_premium_password(password: str) -> bool:
    """Add a new premium password to the database"""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            try:
                cursor.execute('''
                    INSERT INTO premium_passwords (password)
                    VALUES (%s)
                ''', (password,))
                conn.commit()
                return True
            except psycopg2.IntegrityError:
                conn.rollback()
                return False

def redeem_password(password: str, user_id: int, duration_days: int = 30) -> bool:
    """Redeem password with expiration, compatible with existing schema"""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            # 1. Verify and mark password as used
            cursor.execute('''
                UPDATE premium_passwords
                SET 
                    used = TRUE,
                    used_by = %s,
                    used_at = CURRENT_TIMESTAMP
                WHERE 
                    password = %s 
                    AND used = FALSE
                RETURNING created_at
            ''', (user_id, password))
            
            if not cursor.fetchone():
                return False
            
            # 2. Calculate expiration (now + duration)
            expires_at = datetime.now() + timedelta(days=duration_days)
            
            # 3. Upsert premium status
            cursor.execute('''
                INSERT INTO premium_users 
                (user_id, activated_at, expires_at, granted_at)
                VALUES (%s, CURRENT_TIMESTAMP, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (user_id) DO UPDATE SET
                    expires_at = GREATEST(
                        premium_users.expires_at, 
                        EXCLUDED.expires_at
                    ),
                    granted_at = EXCLUDED.granted_at
            ''', (user_id, expires_at))
            
            conn.commit()
            return True

def get_premium_status(user_id: int) -> dict:
    """Check premium status with proper expiration"""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            # Get current status from database
            cursor.execute('''
                SELECT 
                    expires_at,
                    expires_at > CURRENT_TIMESTAMP as is_active,
                    (expires_at - CURRENT_TIMESTAMP) as remaining
                FROM premium_users 
                WHERE user_id = %s
            ''', (user_id,))
            
            result = cursor.fetchone()
            
            if result:
                expires_at, is_active, remaining = result
                return {
                    'is_active': bool(is_active),
                    'expires_at': expires_at,
                    'remaining_days': remaining.days if remaining else 0
                }
            
            return {
                'is_active': False,
                'expires_at': None,
                'remaining_days': 0
            }

def update_premium_statuses():
    """Update all is_active flags (run daily)"""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute('''
                UPDATE premium_users
                SET is_active = (expires_at > CURRENT_TIMESTAMP)
            ''')
            conn.commit()

def is_premium_user(user_id: int) -> bool:
    """Check if user has active premium"""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute('''
                SELECT EXISTS(
                    SELECT 1 FROM premium_users 
                    WHERE user_id = %s 
                    AND expires_at > NOW()
                )
            ''', (user_id,))
            return cursor.fetchone()[0]
# ========================
# Query Functions
# ========================
@with_retry()
def get_team_members(team_name: str) -> list:
    """Get members of a team."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute('''
                SELECT username FROM raiders 
                WHERE team_id = (SELECT id FROM teams WHERE name = %s)
            ''', (team_name,))
            return cursor.fetchall()


# ========================
# Team Management
# ========================
@with_retry()
def create_team(name: str, leader_id: int) -> str:
    """Create a new team."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            try:
                cursor.execute(
                    "INSERT INTO teams (name, leader_id) VALUES (%s, %s)",
                    (name, leader_id)
                )
                conn.commit()
                return f"Team '{name}' created successfully."
            except psycopg2.IntegrityError:
                return f"Team '{name}' already exists."

@with_retry()
def register_raider(user_id: int, username: str, twitter_handle: str, team_name: str) -> str:
    """Register a new raider."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id FROM teams WHERE name = %s", (team_name,))
            team = cursor.fetchone()
            if not team:
                return f"❌ Team {team_name} not found"
            
            twitter_handle = twitter_handle.lstrip('@')
            
            try:
                cursor.execute('''
                    INSERT INTO raiders (user_id, username, twitter_handle, team_id)
                    VALUES (%s, %s, %s, %s)
                ''', (user_id, username, f"@{twitter_handle}", team[0]))
                conn.commit()
                return f"🎉 Registered in {team_name}!"
            except psycopg2.IntegrityError:
                return "✅ Already registered!"

@with_retry()
def list_teams() -> str:
    """List all teams."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT name FROM teams")
            teams = cursor.fetchall()
            return "Teams:\n" + "\n".join(team[0] for team in teams) if teams else "No teams available."

@with_retry()
def view_team(team_name: str) -> str:
    """View members of a team."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT username FROM raiders 
                WHERE team_id = (SELECT id FROM teams WHERE name = %s)
            """, (team_name,))
            members = cursor.fetchall()
            return "\n".join(member[0] for member in members) if members else f"No members in {team_name}"

@with_retry()
def remove_team(team_name: str, leader_id: int) -> str:
    """Remove a team."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM teams WHERE name = %s AND leader_id = %s", (team_name, leader_id))
            conn.commit()
            return f"Team '{team_name}' removed successfully."

@with_retry()
def leave_team(user_id: int) -> str:
    """Leave a team."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE raiders SET team_id = NULL WHERE user_id = %s", (user_id,))
            conn.commit()
            return "You have left your team."

@with_retry()
def verify_team(team_name: str) -> str:
    """Verify a team."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id FROM teams WHERE name = %s", (team_name,))
            team = cursor.fetchone()
            if not team:
                return f"Team '{team_name}' does not exist."
            
            team_id = team[0]
            cursor.execute("SELECT COUNT(*) FROM raiders WHERE team_id = %s", (team_id,))
            member_count = cursor.fetchone()[0]
            
            if member_count >= 80:
                cursor.execute("UPDATE teams SET verified = 1 WHERE id = %s", (team_id,))
                conn.commit()
                return f"Team '{team_name}' has been verified!"
            else:
                return f"Team '{team_name}' does not have enough members for verification."

# ========================
# Project Management
# ========================
@with_retry()
def save_project(chat_id: int, project_name: str, leads: list, raiders: list) -> None:
    """Save or update a project."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            leads_str = '\n'.join(leads) if leads else ''
            raiders_str = '\n'.join(raiders) if raiders else ''
            
            cursor.execute('''
                INSERT INTO projects (chat_id, project_name, leads, raiders)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT(chat_id, project_name) DO UPDATE SET
                    leads = EXCLUDED.leads,
                    raiders = EXCLUDED.raiders
            ''', (chat_id, project_name, leads_str, raiders_str))
            conn.commit()

@with_retry()
def create_project(team_name: str, project_name: str, leader_id: int) -> str:
    """Create a new project."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id FROM teams WHERE name = %s", (team_name,))
            team = cursor.fetchone()
            
            if not team:
                return f"Team '{team_name}' does not exist."
            
            team_id = team[0]
            cursor.execute("INSERT INTO projects (name, team_id, leader_id) VALUES (%s, %s, %s)", 
                           (project_name, team_id, leader_id))
            conn.commit()
            return f"Project '{project_name}' created under team '{team_name}' successfully!"

@with_retry()
def list_projects(team_name: str) -> str:
    """List all projects for a team."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id FROM teams WHERE name = %s", (team_name,))
            team = cursor.fetchone()
            
            if not team:
                return f"Team '{team_name}' does not exist."
            
            team_id = team[0]
            cursor.execute("SELECT name FROM projects WHERE team_id = %s", (team_id,))
            projects = cursor.fetchall()
            return f"Projects under '{team_name}':\n" + "\n".join(p[0] for p in projects) if projects else f"No projects found for team '{team_name}'."

@with_retry()
def delete_project(chat_id: int, project_name: str) -> str:
    """Delete a project."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM projects WHERE chat_id = %s AND project_name = %s", (chat_id, project_name))
            conn.commit()
            return f"Project '{project_name}' deleted successfully!"

# ========================
# Reaction Management
# ========================
@with_retry()
def save_reaction(message_id: int, username: str) -> bool:
    """Save a reaction to the database."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            timestamp = datetime.now().timestamp()
            cursor.execute('''
                INSERT INTO reactions (message_id, username, timestamp)
                VALUES (%s, %s, %s)
            ''', (message_id, username, timestamp))
            conn.commit()
            return True

# ========================
# Initialization
# ========================
if __name__ == "__main__":
    init_databases()

    print("Databases initialized in Supabase PostgreSQL database.")
