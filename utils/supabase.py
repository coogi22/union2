import os
import importlib
from dotenv import load_dotenv

# Use importlib to load the pip "supabase" package directly,
# so Python doesn't confuse it with this file (utils/supabase.py)
_supabase_pkg = importlib.import_module("supabase")
create_client = _supabase_pkg.create_client
Client = _supabase_pkg.Client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Missing Supabase credentials in .env (SUPABASE_URL or SUPABASE_KEY)")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

print("Supabase client initialized")

def get_supabase() -> Client:
    """Return the active Supabase client."""
    return supabase
