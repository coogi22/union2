import os
import aiohttp
from aiohttp import ClientTimeout
from datetime import datetime, timezone

LUARMOR_API_KEY = (os.getenv("LUARMOR_API_KEY") or "").strip()
LUARMOR_PROJECT_ID = (os.getenv("LUARMOR_PROJECT_ID") or "").strip()

BASE_URL = "https://api.luarmor.net/v3"

async def create_luarmor_key(
    discord_id: str,
    auth_expire: int | None = None,
    note: str | None = None
) -> dict | None:
    """
    Create a new Luarmor key for a user.
    - discord_id: User's Discord ID (auto-links to key)
    - auth_expire: Unix timestamp for expiry (None = never expires)
    - note: Optional note (e.g. invoice ID, product name)
    
    Returns: {"success": True, "user_key": "..."} or None on failure
    """
    if not LUARMOR_API_KEY or not LUARMOR_PROJECT_ID:
        print("❌ Luarmor API key or project ID not configured")
        return None

    url = f"{BASE_URL}/projects/{LUARMOR_PROJECT_ID}/users"
    headers = {
        "Authorization": LUARMOR_API_KEY,
        "Content-Type": "application/json"
    }
    
    body = {"discord_id": str(discord_id)}
    
    if auth_expire:
        body["auth_expire"] = auth_expire
    
    if note:
        body["note"] = note

    timeout = ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, headers=headers, json=body) as resp:
            data = await resp.json()
            if resp.status == 200 and data.get("success"):
                return data
            print(f"❌ Luarmor create key failed: {data}")
            return None


async def get_user_by_discord(discord_id: str) -> dict | None:
    """
    Get a Luarmor user by their Discord ID.
    Returns the user object or None if not found.
    """
    if not LUARMOR_API_KEY or not LUARMOR_PROJECT_ID:
        return None

    url = f"{BASE_URL}/projects/{LUARMOR_PROJECT_ID}/users"
    headers = {"Authorization": LUARMOR_API_KEY}
    params = {"discord_id": str(discord_id)}

    timeout = ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=headers, params=params) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            users = data.get("users", [])
            return users[0] if users else None


async def update_luarmor_key(
    user_key: str,
    auth_expire: int | None = None,
    identifier: str | None = None,
    note: str | None = None
) -> bool:
    """
    Update an existing Luarmor key.
    - user_key: The key to update
    - auth_expire: New expiry timestamp (-1 for never)
    - identifier: New HWID
    - note: New note
    """
    if not LUARMOR_API_KEY or not LUARMOR_PROJECT_ID:
        return False

    url = f"{BASE_URL}/projects/{LUARMOR_PROJECT_ID}/users"
    headers = {
        "Authorization": LUARMOR_API_KEY,
        "Content-Type": "application/json"
    }
    
    body = {"user_key": user_key}
    
    if auth_expire is not None:
        body["auth_expire"] = auth_expire
    if identifier:
        body["identifier"] = identifier
    if note:
        body["note"] = note

    timeout = ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.patch(url, headers=headers, json=body) as resp:
            data = await resp.json()
            return resp.status == 200 and data.get("success", False)


async def delete_luarmor_key(user_key: str) -> bool:
    """Delete a Luarmor key (removes user's whitelist access)."""
    if not LUARMOR_API_KEY or not LUARMOR_PROJECT_ID:
        return False

    url = f"{BASE_URL}/projects/{LUARMOR_PROJECT_ID}/users"
    headers = {"Authorization": LUARMOR_API_KEY}
    params = {"user_key": user_key}

    timeout = ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.delete(url, headers=headers, params=params) as resp:
            data = await resp.json()
            return resp.status == 200 and data.get("success", False)


async def reset_hwid(user_key: str) -> bool:
    """Reset the HWID for a key (allows user to re-link on next execution)."""
    if not LUARMOR_API_KEY or not LUARMOR_PROJECT_ID:
        return False

    url = f"{BASE_URL}/projects/{LUARMOR_PROJECT_ID}/users/resethwid"
    headers = {
        "Authorization": LUARMOR_API_KEY,
        "Content-Type": "application/json"
    }
    body = {"user_key": user_key}

    timeout = ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, headers=headers, json=body) as resp:
            data = await resp.json()
            return resp.status == 200 and data.get("success", False)


def compute_expiry_timestamp(product_name: str | None, variant_name: str | None) -> int | None:
    """
    Convert product/variant name to Unix timestamp for Luarmor auth_expire.
    Returns None for lifetime (Luarmor uses -1 or omit for never expire).
    """
    text = f"{product_name or ''} {variant_name or ''}".lower()
    now = datetime.now(timezone.utc)

    if "week" in text:
        return int((now.timestamp()) + (7 * 86400))
    if "month" in text:
        return int((now.timestamp()) + (30 * 86400))
    if "year" in text:
        return int((now.timestamp()) + (365 * 86400))
    if "lifetime" in text:
        return -1  # Luarmor: -1 = never expires
    
    return -1  # Default to lifetime
