import os
import discord
from discord.ext import commands, tasks
from discord import ui, Interaction
import aiohttp
from aiohttp import ClientTimeout
from datetime import datetime, timezone, timedelta

from utils.supabase import get_supabase
from commands.tickets import create_or_get_ticket_channel, CloseTicketView
from utils.luarmor import create_or_update_user, compute_expiry_timestamp, get_user_info, add_time_to_user

# -----------------------------
# CONFIG
# -----------------------------
SHOP_CHANNEL_ID = 1444450990970503188
LOG_CHANNEL_ID = 1449252986911068273
GUILD_ID = 1345153296360542271

ACCESS_ROLE_ID = 1444450052323147826  # Premium role

SHOP_URL = "https://scriptunion.mysellauth.com/"
BOT_LOGO_URL = "https://cdn.discordapp.com/attachments/1449252986911068273/1449511913317732485/ScriptUnionIcon.png"

EMBED_COLOR = 0x489BF3

SELLAUTH_API_KEY = (os.getenv("SELLAUTH_API_KEY") or "").strip()
SELLAUTH_SHOP_ID = (os.getenv("SELLAUTH_SHOP_ID") or "").strip()

WHITELIST_PRODUCTS = ["fix it up", "fix-it-up", "fixitup"]

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

    return None


def should_whitelist_product(product_name: str, variant_name: str) -> bool:
    """Check if this product should be whitelisted on Luarmor."""
    combined = f"{product_name} {variant_name}".lower()
    return any(p in combined for p in WHITELIST_PRODUCTS)

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
    
    referral_code = ui.TextInput(
        label="Referral Code (Optional)",
        placeholder="Enter a referral code if you have one",
        required=False,
        max_length=32,
    )

    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: Interaction):
        invoice_id = self.order_id.value.strip()
        ref_code = self.referral_code.value.strip().upper() if self.referral_code.value else None
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            guild = interaction.guild
            if not guild:
                await interaction.followup.send("This must be used in the server.", ephemeral=True)
                return

            member = guild.get_member(interaction.user.id) or await guild.fetch_member(interaction.user.id)

            existing_invoice = (
                supabase.table("role_redeem")
                .select("id, redeemed_by")
                .eq("invoice_id", invoice_id)
                .limit(1)
                .execute()
            )
            if existing_invoice.data:
                redeemed_by = existing_invoice.data[0].get("redeemed_by")
                await interaction.followup.send(
                    f"This order has already been redeemed by <@{redeemed_by}>.", 
                    ephemeral=True
                )
                return

            invoice = await fetch_invoice(invoice_id)
            if not invoice:
                await interaction.followup.send(
                    "Order not found. Please check your invoice ID and try again.", 
                    ephemeral=True
                )
                return
            
            if not invoice_is_paid(invoice):
                status = invoice.get("status", "unknown")
                refunded = invoice.get("refunded", False)
                cancelled = invoice.get("cancelled", False)
                
                if refunded:
                    await interaction.followup.send("This order has been refunded.", ephemeral=True)
                elif cancelled:
                    await interaction.followup.send("This order has been cancelled.", ephemeral=True)
                else:
                    await interaction.followup.send(f"Order status: {status}. Payment not completed.", ephemeral=True)
                return

            created_at = invoice.get("created_at")
            if created_at:
                try:
                    order_date = datetime.fromtimestamp(int(created_at), tz=timezone.utc)
                    days_old = (datetime.now(timezone.utc) - order_date).days
                    
                    if days_old > 3:
                        await interaction.followup.send(
                            f"This order is {days_old} days old and cannot be auto-redeemed.\n\n"
                            "Please open a ticket for manual verification and a staff member will assist you.",
                            ephemeral=True
                        )
                        return
                except Exception as e:
                    print(f"[DEBUG] Could not parse order date: {e}")

            product_name, variant_name = extract_product_and_variant(invoice)
            expires_at = compute_expires_at_from_variant(variant_name)

            role = guild.get_role(ACCESS_ROLE_ID)
            if not role:
                await interaction.followup.send("Premium role not found. Contact staff.", ephemeral=True)
                return

            try:
                if role not in member.roles:
                    await member.add_roles(role, reason=f"SellAuth redeem {invoice_id}")
            except discord.Forbidden:
                await interaction.followup.send(
                    "I can't assign roles. Make sure my role is above the Premium role and I have Manage Roles.",
                    ephemeral=True
                )
                return

            luarmor_key = None
            luarmor_expiry = None
            
            if should_whitelist_product(product_name, variant_name):
                try:
                    luarmor_result = await create_or_update_user(
                        discord_id=member.id,
                        plan_name=variant_name,
                        note=f"{product_name} | {variant_name} | Invoice: {invoice_id}"
                    )
                    
                    if luarmor_result:
                        luarmor_key = luarmor_result.get("user_key")
                        luarmor_expiry = luarmor_result.get("expires_at")
                except Exception as e:
                    print(f"[LUARMOR ERROR] Failed to whitelist {member.id}: {e}")
            else:
                print(f"[SKIP WHITELIST] Product '{product_name}' is not a whitelistable product")

            referral_bonus_msg = ""
            if ref_code:
                try:
                    print(f"[REFERRAL] Processing code: {ref_code}")
                    referral_data = (
                        supabase.table("referrals")
                        .select("*")
                        .eq("referral_code", ref_code)
                        .limit(1)
                        .execute()
                    )
                    
                    if referral_data.data:
                        referral = referral_data.data[0]
                        referrer_id = referral["referrer_discord_id"]
                        bonus_days = referral.get("bonus_days_per_referral", 3)
                        print(f"[REFERRAL] Found code for referrer {referrer_id}, bonus days: {bonus_days}")
                        
                        if referrer_id != member.id:
                            already_used = (
                                supabase.table("referral_uses")
                                .select("id")
                                .eq("referred_discord_id", member.id)
                                .limit(1)
                                .execute()
                            )
                            
                            if not already_used.data:
                                print(f"[REFERRAL] Adding {bonus_days} days to referrer {referrer_id}")
                                
                                referrer_result = await add_time_to_user(referrer_id, bonus_days)
                                
                                bonus_applied = False
                                if referrer_result:
                                    if referrer_result.get("error") == "lifetime":
                                        print(f"[REFERRAL] Referrer has lifetime, no bonus needed")
                                        bonus_applied = True
                                    elif referrer_result.get("new_expire"):
                                        print(f"[REFERRAL] Added bonus days, new expire: {referrer_result.get('new_expire')}")
                                        bonus_applied = True
                                else:
                                    print(f"[REFERRAL] Referrer not in Luarmor, creating account with {bonus_days} days")
                                    try:
                                        new_user = await create_or_update_user(
                                            discord_id=referrer_id,
                                            plan_name=f"Referral Bonus ({bonus_days} days)",
                                            note=f"Referral bonus from {member.id} using code {ref_code}"
                                        )
                                        if new_user:
                                            print(f"[REFERRAL] Created Luarmor account for referrer {referrer_id}")
                                            bonus_applied = True
                                            
                                            # Give them the premium role too
                                            try:
                                                referrer_member = guild.get_member(referrer_id) or await guild.fetch_member(referrer_id)
                                                if referrer_member and role not in referrer_member.roles:
                                                    await referrer_member.add_roles(role, reason=f"Referral bonus from {member.id}")
                                                    print(f"[REFERRAL] Added premium role to referrer {referrer_id}")
                                            except Exception as role_err:
                                                print(f"[REFERRAL] Could not add role to referrer: {role_err}")
                                    except Exception as create_err:
                                        print(f"[REFERRAL] Failed to create account for referrer: {create_err}")
                                
                            if bonus_applied:
                                referral_bonus_msg = f"\n\nReferral code applied! <@{referrer_id}> received {bonus_days} bonus days."
                                
                                try:
                                    referrer = await guild.fetch_member(referrer_id)
                                    if referrer:
                                        await referrer.send(
                                            f"Someone used your referral code `{ref_code}`!\n"
                                            f"You received **{bonus_days} bonus days** added to your subscription."
                                        )
                                except Exception as dm_err:
                                    print(f"[REFERRAL] Could not DM referrer: {dm_err}")
                            else:
                                referral_bonus_msg = f"\n\nReferral code applied, but <@{referrer_id}> doesn't have an active subscription to add days to."
                        else:
                            print(f"[REFERRAL] User tried to use their own code")
                    else:
                        print(f"[REFERRAL] Code not found: {ref_code}")
                except Exception as e:
                    print(f"[REFERRAL ERROR] {e}")

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
                "referral_code": ref_code,
            }).execute()

            log_channel = guild.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                embed = discord.Embed(title="Order Redeemed", color=discord.Color.green())
                embed.add_field(name="User", value=f"<@{member.id}>\n`{member.id}`", inline=False)
                embed.add_field(name="Product", value=product_name, inline=True)
                embed.add_field(name="Variant", value=variant_name, inline=True)
                embed.add_field(name="Invoice ID", value=f"`{invoice_id}`", inline=False)
                
                if luarmor_key:
                    embed.add_field(name="Luarmor Key", value=f"||`{luarmor_key}`||", inline=False)
                    embed.add_field(name="Whitelist Status", value="Auto-whitelisted", inline=False)
                else:
                    embed.add_field(name="Whitelist Status", value="Failed - manual whitelist needed", inline=False)

                if expires_at:
                    try:
                        ts = int(datetime.fromisoformat(expires_at.replace("Z", "+00:00")).timestamp())
                        embed.add_field(name="Expires", value=f"<t:{ts}:F>", inline=False)
                    except Exception:
                        embed.add_field(name="Expires", value=f"`{expires_at}`", inline=False)
                else:
                    embed.add_field(name="Expires", value="Lifetime", inline=False)
                
                if ref_code:
                    embed.add_field(name="Referral Code Used", value=f"`{ref_code}`", inline=False)

                await log_channel.send(embed=embed)

            if should_whitelist_product(product_name, variant_name):
                if luarmor_key:
                    await interaction.followup.send(
                        "**Order Confirmed - You're all set!**\n\n"
                        f"Head to <#1444457969407496352> and press **Get Script** to get started.\n\n"
                        f"Need help? Open a ticket!{referral_bonus_msg}",
                        ephemeral=True,
                    )
                else:
                    await interaction.followup.send(
                        "**Order Confirmed** - Premium role applied.\n\n"
                        f"Auto-whitelist failed. Please open a ticket so staff can whitelist you manually.{referral_bonus_msg}",
                        ephemeral=True,
                    )
            else:
                # Non-whitelist product (like alts)
                await interaction.followup.send(
                    f"**Order Confirmed!**\n\n"
                    f"Your **{product_name}** order has been verified.\n"
                    f"Check your SellAuth email for delivery details.{referral_bonus_msg}",
                    ephemeral=True,
                )
        
        except Exception as e:
            print(f"[REDEEM ERROR] {e}")
            try:
                await interaction.followup.send(
                    "An error occurred while processing your order. Please try again or open a ticket.",
                    ephemeral=True,
                )
            except:
                pass


# -----------------------------
# VIEW
# -----------------------------
class ShopView(ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

        self.add_item(ui.Button(label="Purchase", url=SHOP_URL, style=discord.ButtonStyle.link))

    @ui.button(label="Redeem Order ID", style=discord.ButtonStyle.primary)
    async def redeem_order(self, interaction: Interaction, button: ui.Button):
        await interaction.response.send_modal(RedeemOrderModal(self.bot))

    @ui.button(label="Open Ticket", style=discord.ButtonStyle.secondary)
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
                "1) Purchase premium via card/crypto\n"
                "2) Click **Redeem Order ID** and enter your invoice ID\n"
                "3) Automatically receive your key, Premium role & whitelist!\n\n"
                "*Card & crypto payments are instant and automatic!*\n\n"
                "**Paying with Robux?**\n"
                "Open a ticket and a staff member will assist you."
            ),
            color=discord.Color(EMBED_COLOR),
        )

        embed.add_field(name="Lifetime", value="**$25 USD**\n4,000 R$", inline=True)
        embed.add_field(name="Month", value="**$10 USD**\n1,700 R$", inline=True)
        embed.add_field(name="Week", value="**$5 USD**\n700 R$", inline=True)

        embed.set_author(name="Script Union Shop", icon_url=BOT_LOGO_URL)
        embed.set_thumbnail(url=BOT_LOGO_URL)
        embed.set_footer(text="Fix-It-Up Script • Premium Access")

        await channel.send(embed=embed, view=ShopView(self.bot))


async def setup(bot: commands.Bot):
    await bot.add_cog(Shop(bot))
