import aiohttp
from typing import Optional, Tuple

# Gamepass IDs and prices (in Robux, price buyer pays - before Roblox fees)
GAMEPASSES = {
    109857815: {"name": "Week", "price": 750, "days": 7},
    129890883: {"name": "Month", "price": 1700, "days": 30},
    125899946: {"name": "Lifetime", "price": 4000, "days": None}  # None = lifetime
}


async def get_user_id_from_username(username: str) -> Optional[int]:
    """Get Roblox user ID from username"""
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                "https://users.roblox.com/v1/usernames/users",
                json={"usernames": [username], "excludeBannedUsers": False}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("data") and len(data["data"]) > 0:
                        return data["data"][0]["id"]
                else:
                    print(f"[ROBLOX] Username lookup returned status {resp.status}")
        return None
    except Exception as e:
        print(f"[ROBLOX ERROR] Failed to get user ID: {e}")
        return None


async def check_gamepass_ownership(user_id: int, gamepass_id: int) -> bool:
    """Check if a Roblox user owns a specific gamepass using the economy API"""
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Use the economy transactions endpoint as primary check
            url = f"https://inventory.roblox.com/v1/users/{user_id}/items/GamePass/{gamepass_id}"
            headers = {"Accept": "application/json"}
            async with session.get(url, headers=headers) as resp:
                print(f"[ROBLOX] Inventory check status: {resp.status}")
                if resp.status == 200:
                    data = await resp.json()
                    return len(data.get("data", [])) > 0
                elif resp.status == 403:
                    # Inventory is private, try alternate endpoint
                    print("[ROBLOX] Inventory private, trying alternate endpoint...")
                    alt_url = f"https://games.roblox.com/v1/games/passes/{gamepass_id}/owners?limit=100&sortOrder=Desc"
                    async with session.get(alt_url, headers=headers) as alt_resp:
                        print(f"[ROBLOX] Owners check status: {alt_resp.status}")
                        if alt_resp.status == 200:
                            alt_data = await alt_resp.json()
                            for owner in alt_data.get("data", []):
                                if owner.get("id") == user_id:
                                    return True
                    return False
        return False
    except Exception as e:
        print(f"[ROBLOX ERROR] Failed to check gamepass: {e}")
        return False


async def verify_gamepass_purchase(username: str, gamepass_id: int) -> Tuple[bool, Optional[int], str]:
    """
    Verify a gamepass purchase.
    Returns: (success, roblox_user_id, message)
    """
    print(f"[ROBLOX] Starting verification for {username}, gamepass {gamepass_id}")

    # Get user ID
    user_id = await get_user_id_from_username(username)
    if not user_id:
        return False, None, f"Could not find Roblox user '{username}'"

    print(f"[ROBLOX] Found user ID: {user_id}")

    # Check if valid gamepass
    if gamepass_id not in GAMEPASSES:
        return False, user_id, f"Invalid gamepass ID: {gamepass_id}"

    # Check ownership
    owns_gamepass = await check_gamepass_ownership(user_id, gamepass_id)
    if not owns_gamepass:
        gamepass_name = GAMEPASSES[gamepass_id]["name"]
        return False, user_id, f"User '{username}' does not own the {gamepass_name} gamepass (inventory may be private)"

    return True, user_id, "Verified"


def get_gamepass_info(gamepass_id: int) -> Optional[dict]:
    """Get gamepass info by ID"""
    return GAMEPASSES.get(gamepass_id)


def get_all_gamepasses() -> dict:
    """Get all gamepass info"""
    return GAMEPASSES
