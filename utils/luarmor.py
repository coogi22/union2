import os
import asyncio
import aiohttp
from aiohttp import ClientTimeout
from typing import Optional, Dict, Any
from datetime import datetime, timezone
import re

LUARMOR_API_KEY = (os.getenv("LUARMOR_API_KEY") or "").strip()
LUARMOR_PROJECT_ID = (os.getenv("LUARMOR_PROJECT_ID") or "").strip()

BASE_URL = "https://api.luarmor.net/v3"


def _resolve_project_id(project_id: Optional[str] = None) -> str:
    """Return the given project_id, or fall back to the default Fix-It-Up project."""
    return (project_id or LUARMOR_PROJECT_ID or "").strip()


def project_id_for_product(product_name: str | None) -> str:
    """Resolve the Luarmor project for a product without cross-product cleanup."""
    text = (product_name or "").lower()
    if "corsa" in text:
        return (os.getenv("LUARMOR_PROJECT_CORSA") or "41aa3309f65c5f894bf7b5bdf46555bb").strip()
    return LUARMOR_PROJECT_ID

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
    project_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Creates a Luarmor user or updates expiry if they already exist.
    Returns dict: { user_key, expires_at } or None on failure.
    """
    project_id = _resolve_project_id(project_id)
    print(f"[LUARMOR] create_or_update_user called for discord_id={discord_id}, plan={plan_name}")
    print(f"[LUARMOR] API_KEY present: {bool(LUARMOR_API_KEY)}, PROJECT_ID: {project_id}")

    if not LUARMOR_API_KEY or not project_id:
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

    url = f"{BASE_URL}/projects/{project_id}/users"

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
        user = await get_user_by_discord(discord_id, project_id=project_id)
        if not user:
            print("[LUARMOR] ❌ Could not find existing user")
            return None

        print(f"[LUARMOR] Found existing user: {user.get('user_key')}")

        # Stack durations: add new time on top of remaining time
        current_expire = user.get("auth_expire")
        now = int(datetime.now(timezone.utc).timestamp())

        if auth_expire is not None and auth_expire != -1:
            # Calculate how many seconds the new purchase adds
            new_duration = auth_expire - now

            if current_expire is not None and current_expire != -1 and current_expire > now:
                # User still has active time - stack on top of remaining
                stacked_expire = current_expire + new_duration
                print(f"[LUARMOR] Stacking: current expires {current_expire}, adding {new_duration}s, new expire {stacked_expire}")
            else:
                # User expired or no expiry - start from now
                stacked_expire = auth_expire
                print(f"[LUARMOR] No active time, setting expire to {stacked_expire}")
        else:
            # Lifetime purchase
            stacked_expire = -1
            print("[LUARMOR] Lifetime purchase, setting to never expire")

        updated = await update_user_expiry(user["user_key"], stacked_expire, project_id=project_id)
        if not updated:
            print("[LUARMOR] ❌ Failed to update existing user")
            return None

        final_expire = stacked_expire if stacked_expire != -1 else None
        print(f"[LUARMOR] ✅ Updated existing user: {user.get('user_key')} with stacked expiry")
        return {
            "user_key": user["user_key"],
            "expires_at": (
                datetime.fromtimestamp(final_expire, tz=timezone.utc)
                if final_expire
                else None
            ),
        }


async def get_user_by_discord(discord_id: int, project_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Get a Luarmor user by their Discord ID."""
    project_id = _resolve_project_id(project_id)
    if not LUARMOR_API_KEY or not project_id:
        return None

    url = f"{BASE_URL}/projects/{project_id}/users"
    params = {"discord_id": str(discord_id)}

    timeout = ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        data = await _request_with_retry("GET", url, session, params=params)
        if data and data.get("users"):
            return data["users"][0]
        return None


async def update_user_expiry(user_key: str, auth_expire: Optional[int], project_id: Optional[str] = None) -> bool:
    """Update an existing Luarmor user's expiry."""
    project_id = _resolve_project_id(project_id)
    if not LUARMOR_API_KEY or not project_id:
        return False

    payload = {
        "user_key": user_key,
        "auth_expire": auth_expire if auth_expire is not None else -1,
    }

    url = f"{BASE_URL}/projects/{project_id}/users"

    timeout = ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        data = await _request_with_retry("PATCH", url, session, json=payload)
        return bool(data and data.get("success"))


async def delete_user(user_key: str, project_id: Optional[str] = None) -> bool:
    """Delete a Luarmor key from only the specified product project."""
    project_id = _resolve_project_id(project_id)
    if not LUARMOR_API_KEY or not project_id:
        return False

    url = f"{BASE_URL}/projects/{project_id}/users"
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
    
    day_match = re.search(r'(\d+)\s*days?', text)
    if day_match:
        days = int(day_match.group(1))
        return now + (days * 86400)
    
    return -1  # Default to lifetime


async def get_user_info(discord_id: int, project_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Get full Luarmor user info including expiry."""
    project_id = _resolve_project_id(project_id)
    if not LUARMOR_API_KEY or not project_id:
        return None

    url = f"{BASE_URL}/projects/{project_id}/users"
    params = {"discord_id": str(discord_id)}

    timeout = ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        data = await _request_with_retry("GET", url, session, params=params)
        if data and data.get("users"):
            return data["users"][0]
        return None


async def add_time_to_user(discord_id: int, days: int, project_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Add days to a user's expiry. Returns updated user info or None."""
    project_id = _resolve_project_id(project_id)
    user = await get_user_info(discord_id, project_id=project_id)
    if not user:
        return None
    
    user_key = user.get("user_key")
    current_expire = user.get("auth_expire")
    
    # If lifetime (-1 or None), can't add time
    if current_expire is None or current_expire == -1:
        return {"error": "lifetime", "user": user}
    
    # Calculate new expiry
    now = int(datetime.now(timezone.utc).timestamp())
    
    # If already expired, start from now
    if current_expire < now:
        new_expire = now + (days * 86400)
    else:
        new_expire = current_expire + (days * 86400)
    
    success = await update_user_expiry(user_key, new_expire, project_id=project_id)
    if success:
        return {
            "user_key": user_key,
            "old_expire": current_expire,
            "new_expire": new_expire,
        }
    return None


async def delete_user_by_discord(discord_id: int, project_id: Optional[str] = None) -> bool:
    """Delete a Luarmor user only within one product project."""
    user = await get_user_by_discord(discord_id, project_id=project_id)
    if not user:
        return False

    user_key = user.get("user_key")
    if not user_key:
        return False

    return await delete_user(user_key, project_id=project_id)


async def get_all_users() -> list:
    """Get all Luarmor users for the project."""
    if not LUARMOR_API_KEY or not LUARMOR_PROJECT_ID:
        return []

    url = f"{BASE_URL}/projects/{LUARMOR_PROJECT_ID}/users"

    timeout = ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        data = await _request_with_retry("GET", url, session)
        if data and data.get("users"):
            return data["users"]
        return []


async def compensate_all_users(hours: int) -> dict:
    """
    Add hours to ALL active (non-lifetime) users.
    Returns dict with success count, skipped count, and errors.
    """
    users = await get_all_users()
    
    if not users:
        return {"success": 0, "skipped": 0, "errors": 0, "total": 0}
    
    success = 0
    skipped = 0
    errors = 0
    seconds_to_add = hours * 3600
    now = int(datetime.now(timezone.utc).timestamp())
    
    for user in users:
        user_key = user.get("user_key")
        current_expire = user.get("auth_expire")
        
        if not user_key:
            errors += 1
            continue
        
        # Skip lifetime users (-1 or None)
        if current_expire is None or current_expire == -1:
            skipped += 1
            continue
        
        # Skip already expired users
        if current_expire < now:
            skipped += 1
            continue
        
        # Add hours to expiry
        new_expire = current_expire + seconds_to_add
        
        try:
            result = await update_user_expiry(user_key, new_expire)
            if result:
                success += 1
            else:
                errors += 1
        except Exception as e:
            print(f"[COMPENSATE] Error updating {user_key}: {e}")
            errors += 1
    
    return {
        "success": success,
        "skipped": skipped,
        "errors": errors,
        "total": len(users)
    }
