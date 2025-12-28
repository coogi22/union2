import os
import discord
from discord.ext import commands, tasks
from discord import ui, Interaction
import aiohttp
from aiohttp import ClientTimeout
from datetime import datetime, timezone, timedelta

from utils.supabase import get_supabase
from commands.tickets import create_or_get_ticket_channel, CloseTicketView
from utils.luarmor import create_or_update_user, compute_expiry_timestamp

# -----------------------------
# CONFIG
# -----------------------------
SHOP_CHANNEL_ID = 1444450990970503188
LOG_CHANNEL_ID = 1449252986911068273
GUILD_ID = 1345153296360542271

ACCESS_ROLE_ID = 1444450052323147826  # Premium role

STAFF_ROLE_IDS = {
    1432015464036433970,
    1449491116822106263,
}

SHOP_URL = "https://scriptunion.mysellauth.com/"
BOT_LOGO_URL = "https://cdn.discordapp.com/attachments/1449252986911068273/1449511913317732485/ScriptUnionIcon.png"

EMBED_COLOR = 0x489BF3

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
        product = item.get("product", {}) or {}
        variant = item.get("variant", {}) or {}

        product_name = (product.get("name") or "Unknown").strip()
        variant_name = (variant.get("name") or product_name).strip()
        return product_name, variant_name

    return "Unknown", "Standard"


def compute_expires_at_from_variant(variant_name: str) -> str | None:
    """Returns ISO string or None for lifetime."""
    v = (variant_name or "").lower()
    now = datetime.now(timezone.utc)

    if "week" in v:
        return (now + timedelta(days=7)).isoformat()
    if "month" in v:
        return (now + timedelta(days=30)).isoformat()
    if "year" in v:
        return (now + timedelta(days=365)).isoformat()

    # Lifetime / default
    return None


# -----------------------------
# MODAL
# -----------------------------
class RedeemOrderModal(ui.Modal, title="Redeem Order ID"):
    order_id = ui.TextInput(
        label="SellAuth Order / Invoice ID",
        placeholder="Paste your order/invoice ID here",
        required=True,
        max_length=128,
    )

    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: Interaction):
        invoice_id = self.order_id.value.strip()
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        if not guild:
            await interaction.followup.send("This must be used in the server.", ephemeral=True)
            return

        member = guild.get_member(interaction.user.id) or await guild.fetch_member(interaction.user.id)

        # Already redeemed?
        existing = (
            supabase.table("role_redeem")
            .select("id")
            .eq("invoice_id", invoice_id)
            .limit(1)
            .execute()
        )
        if existing.data:
            await interaction.followup.send("This order has already been redeemed.", ephemeral=True)
            return

        invoice = await fetch_invoice(invoice_id)
        if not invoice or not invoice_is_paid(invoice):
            await interaction.followup.send("Order is invalid or unpaid.", ephemeral=True)
            return

        product_name, variant_name = extract_product_and_variant(invoice)
        expires_at = compute_expires_at_from_variant(variant_name)

        # Give role
        role = guild.get_role(ACCESS_ROLE_ID)
        if not role:
            await interaction.followup.send("Premium role not found. Contact staff.", ephemeral=True)
            return

        try:
            if role not in member.roles:
                await member.add_roles(role, reason=f"SellAuth redeem {invoice_id}")
        except discord.Forbidden:
            await interaction.followup.send(
                "I can’t assign roles. Make sure my role is above the Premium role and I have Manage Roles.",
                ephemeral=True
            )
            return

        luarmor_key = None
        luarmor_expiry = None
        
        luarmor_result = await create_or_update_user(
            discord_id=member.id,
            plan_name=variant_name,
            note=f"{product_name} | {variant_name} | Invoice: {invoice_id}"
        )
        
        if luarmor_result:
            luarmor_key = luarmor_result.get("user_key")
            luarmor_expiry = luarmor_result.get("expires_at")

        # Save redemption
        supabase.table("role_redeem").insert({
            "role_id": int(ACCESS_ROLE_ID),
            "redeemed": True,
            "redeemed_by": int(member.id),
            "invoice_id": invoice_id,
            "product_name": product_name,
            "variant_name": variant_name,
            "discord_id": int(member.id),
            "expires_at": expires_at,
            "redeemed_at": datetime.now(timezone.utc).isoformat(),
            "luarmor_key": luarmor_key,
            "whitelisted": True if luarmor_key else False,
        }).execute()

        # Log embed
        log_channel = guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            embed = discord.Embed(title="Order Redeemed", color=discord.Color.green())
            embed.add_field(name="User", value=f"<@{member.id}>\n`{member.id}`", inline=False)
            embed.add_field(name="Product", value=product_name, inline=True)
            embed.add_field(name="Variant", value=variant_name, inline=True)
            embed.add_field(name="Invoice ID", value=f"`{invoice_id}`", inline=False)
            
            if luarmor_key:
                embed.add_field(name="Luarmor Key", value=f"||`{luarmor_key}`||", inline=False)
                embed.add_field(name="Whitelist Status", value="✅ Auto-whitelisted", inline=False)
            else:
                embed.add_field(name="Whitelist Status", value="⚠️ Failed - manual whitelist needed", inline=False)

            if expires_at:
                try:
                    ts = int(datetime.fromisoformat(expires_at.replace("Z", "+00:00")).timestamp())
                    embed.add_field(name="Expires", value=f"<t:{ts}:F>", inline=False)
                except Exception:
                    embed.add_field(name="Expires", value=f"`{expires_at}`", inline=False)
            else:
                embed.add_field(name="Expires", value="Lifetime", inline=False)

            await log_channel.send(embed=embed)

        if luarmor_key:
            await interaction.followup.send(
                "✅ **Order confirmed! You're all set.**\n\n"
                f"🔑 Your key: ||`{luarmor_key}`||\n"
                "📋 Add `script_key = \"YOUR_KEY\";` to the top of your script.\n"
                "🔒 Your HWID will auto-link on first execution.\n\n"
                "Need help? Open a ticket!",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "✅ Order confirmed. Premium role applied.\n"
                "⚠️ Auto-whitelist failed. Please open a ticket so staff can whitelist you manually.",
                ephemeral=True,
            )


# -----------------------------
# VIEW
# -----------------------------
class ShopView(ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

        self.add_item(ui.Button(label="🛒 Purchase", url=SHOP_URL, style=discord.ButtonStyle.link))

    @ui.button(label="✅ Redeem Order ID", style=discord.ButtonStyle.primary)
    async def redeem_order(self, interaction: Interaction, button: ui.Button):
        await interaction.response.send_modal(RedeemOrderModal(self.bot))

    @ui.button(label="🎫 Open Ticket", style=discord.ButtonStyle.secondary)
    async def open_ticket(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True)
        channel = await create_or_get_ticket_channel(interaction.guild, interaction.user)
        await interaction.followup.send(f"Ticket ready: {channel.mention}", ephemeral=True)


# -----------------------------
# COG
# -----------------------------
class Shop(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.refresh_shop.start()

    @tasks.loop(count=1)
    async def refresh_shop(self):
        await self.bot.wait_until_ready()
        channel = self.bot.get_channel(SHOP_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            return

        async for msg in channel.history(limit=10):
            if msg.author == self.bot.user:
                await msg.delete()

        embed = discord.Embed(
            title="Fix-It-Up Premium Script — Shop",
            description=(
                f"{SHOP_URL}\n\n"
                "**How it works:**\n"
                "1) 🛒 Purchase premium\n"
                "2) ✅ Redeem Order ID\n"
                "3) 💎 Receive Premium role\n"
                "4) 🎫 Open a ticket to get whitelisted"
            ),
            color=discord.Color(EMBED_COLOR),
        )

        embed.add_field(name="👑 Lifetime", value="**$25 USD**\n**4,000 Robux**", inline=True)
        embed.add_field(name="📅 Month", value="**$10 USD**\n**1,700 Robux**", inline=True)
        embed.add_field(name="📅 Week", value="**$5 USD**\n**750 Robux**", inline=True)

        embed.add_field(
            name="🎁 Roblox Gift Cards",
            value=(
                "Accepted via ticket only\n"
                "**Must be $5 higher than product price**\n"
                "Example: $25 product → $30 card"
            ),
            inline=False,
        )

        embed.set_author(name="Script Union Shop", icon_url=BOT_LOGO_URL)
        embed.set_thumbnail(url=BOT_LOGO_URL)
        embed.set_footer(text="Fix-It-Up Script • Premium Access")

        await channel.send(embed=embed, view=ShopView(self.bot))


async def setup(bot: commands.Bot):
    await bot.add_cog(Shop(bot))
