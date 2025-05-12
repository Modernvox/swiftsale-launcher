# C:\Users\lovei\SCD_SALES\setup_database.py

import sqlite3
from decouple import config
import os

# Define the database path
DATABASE_PATH = os.path.join(os.getcwd(), 'subscriptions.db')

def initialize_database():
    """
    Initialize the subscriptions database with a secure table structure.
    """
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    # Create subscriptions table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            tier TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()
    conn.close()
    print(f"Database initialized at {DATABASE_PATH}")

if __name__ == "__main__":
    initialize_database()
