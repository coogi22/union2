import os
import asyncio
import aiohttp
from aiohttp import ClientTimeout
from typing import Optional, Dict, Any
from datetime import datetime, timezone

LUARMOR_API_KEY = (os.getenv("LUARMOR_API_KEY") or "").strip()
LUARMOR_PROJECT_ID = (os.getenv("LUARMOR_PROJECT_ID") or "").strip()

BASE_URL = "https://api.luarmor.net/v3"

MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds (exponential backoff)


def _headers() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": LUARMOR_API_KEY,
    }


async def _request_with_retry(
    method: str,
    url: str,
    session: aiohttp.ClientSession,
    json: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Make a request with retry logic for rate limits and server errors."""
    
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with session.request(
                method,
                url,
                headers=_headers(),
                json=json,
                params=params,
            ) as resp:
                text = await resp.text()
                print(f"[LUARMOR] {method} {url}")
                print(f"[LUARMOR] Attempt {attempt} | Status {resp.status}")
                print(f"[LUARMOR] Response: {text}")

                if resp.status == 200:
                    try:
                        return await resp.json()
                    except:
                        return {"raw": text}

                # Retryable errors
                if resp.status in (401, 403, 429, 500, 502, 503, 504):
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(RETRY_DELAY * attempt)
                        continue

                return None

        except aiohttp.ClientError as e:
            print(f"[LUARMOR] Network error: {e}")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY * attempt)
                continue
            return None

    return None


async def create_or_update_user(
    discord_id: int,
    plan_name: str,
    note: str = "",
) -> Optional[Dict[str, Any]]:
    """
    Creates a Luarmor user or updates expiry if they already exist.
    Returns dict: { user_key, expires_at } or None on failure.
    """
    print(f"[LUARMOR] create_or_update_user called for discord_id={discord_id}, plan={plan_name}")
    print(f"[LUARMOR] API_KEY present: {bool(LUARMOR_API_KEY)}, PROJECT_ID: {LUARMOR_PROJECT_ID}")

    if not LUARMOR_API_KEY or not LUARMOR_PROJECT_ID:
        print("[LUARMOR] ❌ API key or project ID not configured")
        return None

    auth_expire = compute_expiry_timestamp(plan_name, plan_name)
    
    payload = {
        "discord_id": str(discord_id),
        "note": note,
    }

    # Only add auth_expire if not lifetime (-1 means never expires in Luarmor)
    if auth_expire is not None and auth_expire != -1:
        payload["auth_expire"] = auth_expire

    url = f"{BASE_URL}/projects/{LUARMOR_PROJECT_ID}/users"

    timeout = ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        data = await _request_with_retry("POST", url, session, json=payload)

        if data and data.get("success"):
            print(f"[LUARMOR] ✅ New user created: {data.get('user_key')}")
            return {
                "user_key": data.get("user_key"),
                "expires_at": (
                    datetime.fromtimestamp(auth_expire, tz=timezone.utc)
                    if auth_expire and auth_expire != -1
                    else None
                ),
            }

        # User might already exist - try to fetch and update
        print("[LUARMOR] User may exist, attempting to fetch and update...")
        user = await get_user_by_discord(discord_id)
        if not user:
            print("[LUARMOR] ❌ Could not find existing user")
            return None

        print(f"[LUARMOR] Found existing user: {user.get('user_key')}")
        updated = await update_user_expiry(user["user_key"], auth_expire)
        if not updated:
            print("[LUARMOR] ❌ Failed to update existing user")
            return None

        print(f"[LUARMOR] ✅ Updated existing user: {user.get('user_key')}")
        return {
            "user_key": user["user_key"],
            "expires_at": (
                datetime.fromtimestamp(auth_expire, tz=timezone.utc)
                if auth_expire and auth_expire != -1
                else None
            ),
        }


async def get_user_by_discord(discord_id: int) -> Optional[Dict[str, Any]]:
    """Get a Luarmor user by their Discord ID."""
    if not LUARMOR_API_KEY or not LUARMOR_PROJECT_ID:
        return None

    url = f"{BASE_URL}/projects/{LUARMOR_PROJECT_ID}/users"
    params = {"discord_id": str(discord_id)}

    timeout = ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        data = await _request_with_retry("GET", url, session, params=params)
        if data and data.get("users"):
            return data["users"][0]
        return None


async def update_user_expiry(user_key: str, auth_expire: Optional[int]) -> bool:
    """Update an existing Luarmor user's expiry."""
    if not LUARMOR_API_KEY or not LUARMOR_PROJECT_ID:
        return False

    payload = {
        "user_key": user_key,
        "auth_expire": auth_expire if auth_expire is not None else -1,
    }

    url = f"{BASE_URL}/projects/{LUARMOR_PROJECT_ID}/users"

    timeout = ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        data = await _request_with_retry("PATCH", url, session, json=payload)
        return bool(data and data.get("success"))


async def delete_user(user_key: str) -> bool:
    """Delete a Luarmor key (removes user's whitelist access)."""
    if not LUARMOR_API_KEY or not LUARMOR_PROJECT_ID:
        return False

    url = f"{BASE_URL}/projects/{LUARMOR_PROJECT_ID}/users"
    params = {"user_key": user_key}

    timeout = ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        data = await _request_with_retry("DELETE", url, session, params=params)
        return bool(data and data.get("success"))


async def reset_hwid(user_key: str) -> bool:
    """Reset the HWID for a key."""
    if not LUARMOR_API_KEY or not LUARMOR_PROJECT_ID:
        return False

    url = f"{BASE_URL}/projects/{LUARMOR_PROJECT_ID}/users/resethwid"
    payload = {"user_key": user_key}

    timeout = ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        data = await _request_with_retry("POST", url, session, json=payload)
        return bool(data and data.get("success"))


def compute_expiry_timestamp(product_name: str | None, variant_name: str | None) -> int | None:
    """
    Convert product/variant name to Unix timestamp for Luarmor auth_expire.
    Returns -1 for lifetime (never expires in Luarmor).
    """
    text = f"{product_name or ''} {variant_name or ''}".lower()
    now = int(datetime.now(timezone.utc).timestamp())

    if "week" in text:
        return now + (7 * 86400)
    if "month" in text:
        return now + (30 * 86400)
    if "year" in text:
        return now + (365 * 86400)
    if "lifetime" in text or "life" in text:
        return -1  # Luarmor: -1 = never expires
    
    return -1  # Default to lifetime
