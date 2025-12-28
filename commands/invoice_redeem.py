import os
import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
from aiohttp import ClientTimeout
from datetime import datetime, timezone, timedelta
import traceback

from utils.supabase import get_supabase
from utils.luarmor import create_luarmor_key, get_user_by_discord, compute_expiry_timestamp

# -----------------------------
# CONFIG
# -----------------------------
GUILD_ID = 1345153296360542271
ACCESS_ROLE_ID = 1444450052323147826
LOG_CHANNEL_ID = 1449252986911068273

STAFF_ROLE_IDS = {1432015464036433970, 1449491116822106263}

SELLAUTH_API_KEY = (os.getenv("SELLAUTH_API_KEY") or "").strip()
SELLAUTH_SHOP_ID = (os.getenv("SELLAUTH_SHOP_ID") or "").strip()

supabase = get_supabase()

# -----------------------------
# SELLAUTH HELPERS
# -----------------------------
async def fetch_invoice(invoice_id: str) -> dict | None:
    if not SELLAUTH_API_KEY or not SELLAUTH_SHOP_ID:
        return None

    url = f"https://api.sellauth.com/v1/shops/{SELLAUTH_SHOP_ID}/invoices/{invoice_id}"
    headers = {"Authorization": f"Bearer {SELLAUTH_API_KEY}"}

    timeout = ClientTimeout(total=8)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                return None
            return await resp.json()

def invoice_is_paid(invoice: dict) -> bool:
    status = (invoice.get("status") or "").lower()
    refunded = bool(invoice.get("refunded", False))
    cancelled = bool(invoice.get("cancelled", False))
    return status in {"completed", "paid"} and not refunded and not cancelled

def extract_product_and_variant(invoice: dict) -> tuple[str, str]:
    items = invoice.get("items")
    if isinstance(items, list) and items:
        item = items[0]
        product = item.get("product", {})
        variant = item.get("variant", {})

        product_name = product.get("name") or "Unknown"
        variant_name = variant.get("name") or "Default"

        return product_name.strip(), variant_name.strip()

    return "Unknown", "Default"

# 🔥 FIX: expiry based on PRODUCT + VARIANT text
def compute_expires_at(product_name: str | None, variant_name: str | None) -> str | None:
    text = f"{product_name or ''} {variant_name or ''}".lower()
    now = datetime.now(timezone.utc)

    if "week" in text:
        return (now + timedelta(days=7)).isoformat()
    if "month" in text:
        return (now + timedelta(days=30)).isoformat()
    if "lifetime" in text:
        return (now + timedelta(days=999)).isoformat()

    return None  # Lifetime

def staff_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            raise app_commands.CheckFailure("Must be used in a server.")
        if any(r.id in STAFF_ROLE_IDS for r in interaction.user.roles):
            return True
        raise app_commands.CheckFailure("You do not have permission to use this command.")
    return app_commands.check(predicate)

# -----------------------------
# COG
# -----------------------------
class InvoiceRedeem(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="redeem",
        description="Verify a SellAuth invoice ID and grant access"
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @staff_only()
    async def redeem(self, interaction: discord.Interaction, order_id: str, user: discord.Member):
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            invoice_id = order_id.strip()

            # Already redeemed?
            existing = (
                supabase.table("role_redeem")
                .select("id")
                .eq("invoice_id", invoice_id)
                .limit(1)
                .execute()
            )
            if existing.data:
                await interaction.followup.send("❌ This invoice was already redeemed.", ephemeral=True)
                return

            invoice = await fetch_invoice(invoice_id)
            if not invoice or not invoice_is_paid(invoice):
                await interaction.followup.send("❌ Order is unpaid, cancelled, or refunded.", ephemeral=True)
                return

            product_name, variant_name = extract_product_and_variant(invoice)
            expires_at = compute_expires_at(product_name, variant_name)

            role = interaction.guild.get_role(ACCESS_ROLE_ID)
            if role and role not in user.roles:
                await user.add_roles(role, reason=f"SellAuth redeem {invoice_id}")

            luarmor_key = None
            luarmor_expiry = compute_expiry_timestamp(product_name, variant_name)
            
            # Check if user already has a Luarmor key
            existing_luarmor = await get_user_by_discord(str(user.id))
            
            if existing_luarmor:
                # User already has a key, store it
                luarmor_key = existing_luarmor.get("user_key")
            else:
                # Create new Luarmor key with Discord ID and expiry
                result = await create_luarmor_key(
                    discord_id=str(user.id),
                    auth_expire=luarmor_expiry,
                    note=f"{product_name} | {variant_name} | Invoice: {invoice_id}"
                )
                if result:
                    luarmor_key = result.get("user_key")

            # Save redemption
            supabase.table("role_redeem").insert({
                "invoice_id": invoice_id,
                "role_id": ACCESS_ROLE_ID,
                "redeemed": True,
                "redeemed_by": interaction.user.id,
                "discord_id": user.id,
                "product_name": product_name,
                "variant_name": variant_name,
                "expires_at": expires_at,
                "redeemed_at": datetime.now(timezone.utc).isoformat(),
                "luarmor_key": luarmor_key,  # Store Luarmor key
                "whitelisted": True if luarmor_key else False,
            }).execute()

            # Log embed
            log = interaction.guild.get_channel(LOG_CHANNEL_ID)
            if log:
                embed = discord.Embed(title="Order Redeemed (Dashboard)", color=discord.Color.orange())
                embed.add_field(name="User", value=f"<@{user.id}>\n`{user.id}`", inline=False)
                embed.add_field(name="Product", value=product_name, inline=True)
                embed.add_field(name="Variant", value=variant_name, inline=True)
                embed.add_field(name="Invoice ID", value=f"`{invoice_id}`", inline=False)
                
                if luarmor_key:
                    embed.add_field(name="Luarmor Key", value=f"||`{luarmor_key}`||", inline=False)
                else:
                    embed.add_field(name="Luarmor", value="⚠️ Key creation failed", inline=False)

                if expires_at:
                    ts = int(datetime.fromisoformat(expires_at.replace("Z", "+00:00")).timestamp())
                    embed.add_field(name="Expires", value=f"<t:{ts}:F>", inline=False)
                else:
                    embed.add_field(name="Expires", value="Lifetime", inline=False)

                await log.send(embed=embed)

            if luarmor_key:
                await interaction.followup.send(
                    f"✅ Order verified and access granted to {user.mention}.\n"
                    f"🔑 Luarmor key created - HWID will auto-link on first script execution.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    f"✅ Order verified and access granted to {user.mention}.\n"
                    f"⚠️ Luarmor key creation failed - check API config.",
                    ephemeral=True
                )

        except Exception as e:
            traceback.print_exc()
            await interaction.followup.send("❌ Internal error. Check bot logs.", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(InvoiceRedeem(bot))
    print("✅ Loaded cog: invoice_redeem")
