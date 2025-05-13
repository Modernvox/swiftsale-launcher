# test_db.py
import psycopg
from config import load_environment
env_vars = load_environment()
conn = psycopg.connect(env_vars['DATABASE_URL'])
print('Connected successfully')
conn.close()