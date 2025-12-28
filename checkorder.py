import os
import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
from aiohttp import ClientTimeout
from datetime import datetime, timezone
from typing import Optional

from utils.supabase import get_supabase

# -----------------------------
# CONFIG
# -----------------------------
GUILD_ID = 1345153296360542271
STAFF_ROLE_IDS = {1432015464036433970, 1449491116822106263}

SHOP_URL = os.getenv("SHOP_URL", "").strip()
SELLAUTH_API_KEY = os.getenv("SELLAUTH_API_KEY")
SELLAUTH_SHOP_ID = os.getenv("SELLAUTH_SHOP_ID")

supabase = get_supabase()

# -----------------------------
# SELLAUTH
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

def get_paid_refund_cancel(invoice: Optional[dict]) -> tuple[bool, bool, bool, str]:
    if not invoice:
        return False, False, False, "not_found"

    status = (invoice.get("status") or "unknown").lower()
    refunded = bool(invoice.get("refunded", False))
    cancelled = bool(invoice.get("cancelled", False))

    paid = status in {"paid", "completed", "complete"} and not refunded and not cancelled
    return paid, refunded, cancelled, status

def extract_product_and_variant(invoice: Optional[dict]) -> tuple[str, str]:
    """
    Matches your SellAuth payload:
      invoice['items'][0]['product']['name']
      invoice['items'][0]['variant']['name']
    """
    if not invoice:
        return "Unknown", "Standard"

    items = invoice.get("items")
    if isinstance(items, list) and items and isinstance(items[0], dict):
        first = items[0]
        product = first.get("product")
        variant = first.get("variant")

        product_name = None
        variant_name = None

        if isinstance(product, dict):
            product_name = product.get("name") or product.get("title")
        if isinstance(variant, dict):
            variant_name = variant.get("name") or variant.get("title")

        if product_name:
            return str(product_name).strip(), str(variant_name or "Standard").strip()

    return "Unknown", "Standard"

def try_parse_iso_to_unix(ts: Optional[str]) -> Optional[int]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except Exception:
        return None

# -----------------------------
# UI
# -----------------------------
class CopyOrderView(discord.ui.View):
    def __init__(self, invoice_id: str):
        super().__init__(timeout=120)
        self.invoice_id = invoice_id

        if SHOP_URL:
            self.add_item(discord.ui.Button(label="Open Shop", url=SHOP_URL, style=discord.ButtonStyle.link))

    @discord.ui.button(label="Copy Order ID", style=discord.ButtonStyle.primary)
    async def copy_order_id(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(f"```{self.invoice_id}```", ephemeral=True)

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
class CheckOrder(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="checkorder",
        description="Check SellAuth paid/refund status + whether an order has been redeemed"
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @staff_only()
    async def checkorder(self, interaction: discord.Interaction, order_id: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        invoice_id = (order_id or "").strip()

        # Supabase: redeemed?
        redeemed_row = None
        try:
            res = (
                supabase.table("role_redeem")
                .select("*")
                .eq("invoice_id", invoice_id)
                .limit(1)
                .execute()
            )
            if res.data:
                redeemed_row = res.data[0]
        except Exception:
            redeemed_row = None

        # SellAuth: invoice
        invoice = await fetch_invoice(invoice_id)
        paid, refunded, cancelled, status = get_paid_refund_cancel(invoice)

        # Product/variant (SellAuth first, then Supabase fallback)
        sa_product, sa_variant = extract_product_and_variant(invoice)
        db_product = (redeemed_row.get("product_name") if redeemed_row else None)
        db_variant = (redeemed_row.get("variant_name") if redeemed_row else None)

        product_name = sa_product if sa_product != "Unknown" else (db_product or "Unknown")
        variant_name = sa_variant if sa_variant != "Standard" else (db_variant or sa_variant or "Standard")

        is_redeemed = bool(redeemed_row)

        # Colors/headline
        if paid and is_redeemed:
            color = discord.Color.green()
            headline = "Paid and redeemed"
        elif paid and not is_redeemed:
            color = discord.Color.orange()
            headline = "Paid but not redeemed"
        else:
            color = discord.Color.red()
            if refunded:
                headline = "Refunded"
            elif cancelled:
                headline = "Cancelled"
            elif invoice is None:
                headline = "Order not found"
            else:
                headline = "Not paid / not completed"

        flags = []
        if refunded:
            flags.append("REFUNDED")
        if cancelled:
            flags.append("CANCELLED")
        flags_text = " • ".join(flags) if flags else "None"

        embed = discord.Embed(
            title="Order Check",
            description=f"**{headline}**",
            color=color
        )

        embed.add_field(name="Order ID", value=f"`{invoice_id}`", inline=False)
        embed.add_field(name="SellAuth Status", value=f"`{status}`", inline=True)
        embed.add_field(name="Flags", value=f"`{flags_text}`", inline=True)
        embed.add_field(name="Paid", value="✅ Yes" if paid else "❌ No", inline=True)
        embed.add_field(name="Redeemed", value="✅ Yes" if is_redeemed else "❌ No", inline=True)

        embed.add_field(
            name="Product",
            value=f"**{product_name}**\nVariant: `{variant_name}`",
            inline=False
        )

        if redeemed_row:
            redeemed_at = redeemed_row.get("redeemed_at")
            redeemed_ts = try_parse_iso_to_unix(redeemed_at)
            redeemed_at_display = f"<t:{redeemed_ts}:F>" if redeemed_ts else f"`{redeemed_at or 'N/A'}`"

            # NEW: use discord_id (fallback to old username if needed)
            discord_id = redeemed_row.get("discord_id")
            if discord_id:
                granted_to_display = f"<@{discord_id}>\n`{discord_id}`"
            else:
                granted_to_display = str(redeemed_row.get("discord_username", "Unknown"))

            embed.add_field(
                name="Granted To",
                value=granted_to_display,
                inline=True
            )
            embed.add_field(
                name="Redeemed By",
                value=f"`{redeemed_row.get('redeemed_by', 'N/A')}`",
                inline=True
            )
            embed.add_field(
                name="Redeemed At",
                value=redeemed_at_display,
                inline=False
            )

            # OPTIONAL: show expiry if present
            expires_at = redeemed_row.get("expires_at")
            if expires_at:
                exp_ts = try_parse_iso_to_unix(expires_at)
                exp_display = f"<t:{exp_ts}:F>" if exp_ts else f"`{expires_at}`"
                embed.add_field(name="Expires", value=exp_display, inline=False)
            else:
                # If you want lifetime shown only when redeemed, keep this:
                embed.add_field(name="Expires", value="Lifetime / None", inline=False)

        embed.set_footer(text="Script Union • Order Verification")

        view = CopyOrderView(invoice_id)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            try:
                await interaction.response.send_message(str(error), ephemeral=True)
            except Exception:
                await interaction.followup.send(str(error), ephemeral=True)
            return
        raise error

async def setup(bot: commands.Bot):
    await bot.add_cog(CheckOrder(bot))
    print("✅ Loaded cog: checkorder")
