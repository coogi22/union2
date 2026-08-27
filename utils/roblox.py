import os
import aiohttp
from typing import Optional, Tuple

# Gamepass IDs and prices (in Robux, before fees).
# Fix-It-Up keeps its existing 7/30-day passes; the former lifetime pass is now 90 days.
# Corsa Legends IDs can be supplied through CORSA_GAMEPASS_* environment variables.
GAMEPASSES = {
    1740966992: {"name": "Fix-It-Up — 7 Days", "product": "fix it up", "price": 750, "days": 7, "url": "https://www.roblox.com/game-pass/1740966992/750"},
    1740773120: {"name": "Fix-It-Up — 30 Days", "product": "fix it up", "price": 1700, "days": 30, "url": "https://www.roblox.com/game-pass/1740773120/1700"},
    843404211: {"name": "Fix-It-Up — 90 Days", "product": "fix it up", "price": 3400, "days": 90, "url": "https://www.roblox.com/game-pass/843404211/3400"},
}


def _add_env_gamepass(env_name: str, name: str, product: str, price: int, days: int) -> None:
    raw_id = (os.getenv(env_name) or "").strip()
    if not raw_id.isdigit():
        return
    gamepass_id = int(raw_id)
    GAMEPASSES[gamepass_id] = {
        "name": f"{product} — {name}",
        "product": product.lower(),
        "price": price,
        "days": days,
        "url": f"https://www.roblox.com/game-pass/{gamepass_id}",
    }


# Junk Mechanics and Corsa Legends purchase passes.
GAMEPASSES.update({
    1962306481: {
        "name": "Junk Mechanics — 1 Day",
        "product": "junk mechanics",
        "price": 400,
        "days": 1,
        "url": "https://www.roblox.com/game-pass/1962306481/400",
    },
    1963224497: {
        "name": "Junk Mechanics — 7 Days",
        "product": "junk mechanics",
        "price": 1200,
        "days": 7,
        "url": "https://www.roblox.com/game-pass/1963224497/1200",
    },
    1961952488: {
        "name": "Junk Mechanics — 30 Days",
        "product": "junk mechanics",
        "price": 2000,
        "days": 30,
        "url": "https://www.roblox.com/game-pass/1961952488/2000",
    },
    1792027572: {
        "name": "Corsa Legends — 7 Days",
        "product": "corsa legends",
        "price": 1200,
        "days": 7,
        "url": "https://www.roblox.com/game-pass/1792027572/donate",
    },
    1791084207: {
        "name": "Corsa Legends — 30 Days",
        "product": "corsa legends",
        "price": 3400,
        "days": 30,
        "url": "https://www.roblox.com/game-pass/1791084207/donate",
    },
    1792009590: {
        "name": "Corsa Legends — 90 Days",
        "product": "corsa legends",
        "price": 6000,
        "days": 90,
        "url": "https://www.roblox.com/game-pass/1792009590/donate",
    },
})

# Optional environment overrides/additions remain supported.
_add_env_gamepass("CORSA_GAMEPASS_7_DAYS", "7 Days", "Corsa Legends", 1200, 7)
_add_env_gamepass("CORSA_GAMEPASS_30_DAYS", "30 Days", "Corsa Legends", 3400, 30)
_add_env_gamepass("CORSA_GAMEPASS_90_DAYS", "90 Days", "Corsa Legends", 6000, 90)

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
