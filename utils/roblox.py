import aiohttp
from typing import Optional, Tuple

# Gamepass IDs and prices (in Robux, before fees)
GAMEPASSES = {
    109857815: {"name": "Week", "price": 700, "days": 7},
    129890883: {"name": "Month", "price": 1700, "days": 30},
    125899946: {"name": "Lifetime", "price": 4000, "days": None}  # None = lifetime
}

async def get_user_id_from_username(username: str) -> Optional[int]:
    """Get Roblox user ID from username"""
    try:
        async with aiohttp.ClientSession() as session:
            # Try the new API first
            async with session.post(
                "https://users.roblox.com/v1/usernames/users",
                json={"usernames": [username], "excludeBannedUsers": False}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("data") and len(data["data"]) > 0:
                        return data["data"][0]["id"]
        return None
    except Exception as e:
        print(f"[ROBLOX ERROR] Failed to get user ID: {e}")
        return None

async def check_gamepass_ownership(user_id: int, gamepass_id: int) -> bool:
    """Check if a Roblox user owns a specific gamepass"""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://inventory.roblox.com/v1/users/{user_id}/items/GamePass/{gamepass_id}"
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # If data array is not empty, user owns the gamepass
                    return len(data.get("data", [])) > 0
        return False
    except Exception as e:
        print(f"[ROBLOX ERROR] Failed to check gamepass: {e}")
        return False

async def verify_gamepass_purchase(username: str, gamepass_id: int) -> Tuple[bool, Optional[int], str]:
    """
    Verify a gamepass purchase.
    Returns: (success, roblox_user_id, message)
    """
    # Get user ID
    user_id = await get_user_id_from_username(username)
    if not user_id:
        return False, None, f"Could not find Roblox user '{username}'"
    
    # Check if valid gamepass
    if gamepass_id not in GAMEPASSES:
        return False, user_id, f"Invalid gamepass ID: {gamepass_id}"
    
    # Check ownership
    owns_gamepass = await check_gamepass_ownership(user_id, gamepass_id)
    if not owns_gamepass:
        gamepass_name = GAMEPASSES[gamepass_id]["name"]
        return False, user_id, f"User '{username}' does not own the {gamepass_name} gamepass"
    
    return True, user_id, "Verified"

def get_gamepass_info(gamepass_id: int) -> Optional[dict]:
    """Get gamepass info by ID"""
    return GAMEPASSES.get(gamepass_id)

def get_all_gamepasses() -> dict:
    """Get all gamepass info"""
    return GAMEPASSES
