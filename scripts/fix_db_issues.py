"""Fix database issues - add missing address column and fix invalid emails."""
import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text, create_engine
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Build database URL from environment without embedding fallback credentials.
DATABASE_URL = os.getenv("DATABASE_URL")
DB_NAME = os.getenv("DB_NAME", "seacom_experimental_db")

if not DATABASE_URL:
    DB_USER = os.getenv("DB_USER", "postgres")
    DB_PASSWORD = os.getenv("DB_PASSWORD")
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = os.getenv("DB_PORT", "5432")

    if not DB_PASSWORD:
        raise RuntimeError("Set DB_PASSWORD or DATABASE_URL before running this script.")

    DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

print(f"Connecting to database: {DB_NAME}")

engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    # 1. Add missing address column to sites table
    print("Adding address column to sites table...")
    conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS address VARCHAR(500)"))
    
    # 2. Add missing PostGIS location column
    print("Adding location column (PostGIS geometry) to sites table...")
    conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS location geometry(POINT, 4326)"))
    
    # 3. Add missing geofence_radius column
    print("Adding geofence_radius column to sites table...")
    conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS geofence_radius INTEGER DEFAULT 100"))
    
    # 4. Add missing columns to technicians table for PostGIS tracking
    print("Adding PostGIS columns to technicians table...")
    conn.execute(text("ALTER TABLE technicians ADD COLUMN IF NOT EXISTS current_location geometry(POINT, 4326)"))
    conn.execute(text("ALTER TABLE technicians ADD COLUMN IF NOT EXISTS last_location_update TIMESTAMPTZ"))
    conn.execute(text("ALTER TABLE technicians ADD COLUMN IF NOT EXISTS home_base geometry(POINT, 4326)"))
    conn.execute(text("ALTER TABLE technicians ADD COLUMN IF NOT EXISTS is_available BOOLEAN DEFAULT true"))
    
    # 5. Delete users with invalid email addresses (duplicates exist with valid emails)
    print("Removing users with invalid email addresses...")
    result = conn.execute(text("DELETE FROM users WHERE email LIKE '%@seacom.test' RETURNING email"))
    deleted = result.fetchall()
    if deleted:
        print(f"  Deleted {len(deleted)} user(s) with invalid emails: {[r[0] for r in deleted]}")
    else:
        print("  No invalid emails found")
    
    conn.commit()
    print("Migration complete!")
