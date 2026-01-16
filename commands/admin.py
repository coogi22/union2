import os
import discord
from discord.ext import commands, tasks
from discord import Interaction
from datetime import datetime, timezone, timedelta
import random
import string

from utils.supabase import get_supabase
from utils.luarmor import get_user_info, add_time_to_user, delete_user_by_discord, create_or_update_user
from utils.roblox import verify_gamepass_purchase, get_gamepass_info, get_all_gamepasses, GAMEPASSES

# -----------------------------
# CONFIG
# -----------------------------
GUILD_ID = 1345153296360542271
LOG_CHANNEL_ID = 1449252986911068273
PURCHASE_LOG_CHANNEL_ID = 1449252986911068273
ACCESS_ROLE_ID = 1444450052323147826

ADMIN_STAFF_ROLE_IDS = {
    1432015464036433970,  # Staff Role (full access - can whitelist, add time, etc.)
}

SUPPORT_ROLE_IDS = {
    1449491116822106263,  # Support Team (view only - can only check orders)
}

ALL_STAFF_ROLE_IDS = ADMIN_STAFF_ROLE_IDS | SUPPORT_ROLE_IDS

EMBED_COLOR = 0x489BF3
BOT_LOGO_URL = "https://cdn.discordapp.com/attachments/1449252986911068273/1449511913317732485/ScriptUnionIcon.png"

supabase = get_supabase()


def _is_admin_staff(member: discord.Member) -> bool:
    """Full staff - can whitelist, add time, apply referrals, etc."""
    return any(r.id in ADMIN_STAFF_ROLE_IDS for r in member.roles)


def _is_any_staff(member: discord.Member) -> bool:
    """Any staff or support - for view-only commands"""
    return any(r.id in ALL_STAFF_ROLE_IDS for r in member.roles)


def _generate_referral_code() -> str:
    chars = string.ascii_uppercase + string.digits
    code = ''.join(random.choices(chars, k=6))
    return f"REF-{code}"


class Admin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.expiry_check.start()
        self.renewal_reminder.start()

    def cog_unload(self):
        self.expiry_check.cancel()
        self.renewal_reminder.cancel()

    # -----------------------------
    # BACKGROUND TASKS
    # -----------------------------
    
    @tasks.loop(minutes=10)
    async def expiry_check(self):
        """Check for expired keys and remove Premium role"""
        try:
            guild = self.bot.get_guild(GUILD_ID)
            if not guild:
                return

            now = datetime.now(timezone.utc).isoformat()
            expired = supabase.table("role_redeem").select(
                "id, discord_id, product_name, variant_name, expires_at"
            ).lt("expires_at", now).eq("whitelisted", True).execute()

            if not expired.data:
                return

            log_channel = guild.get_channel(LOG_CHANNEL_ID)
            role = guild.get_role(ACCESS_ROLE_ID)

            for entry in expired.data:
                discord_id = entry.get("discord_id")
                if not discord_id:
                    continue

                try:
                    member = guild.get_member(int(discord_id))
                    if member is None:
                        try:
                            member = await guild.fetch_member(int(discord_id))
                        except:
                            member = None

                    if member and role and role in member.roles:
                        await member.remove_roles(role, reason="Subscription expired")

                    supabase.table("role_redeem").update({
                        "whitelisted": False
                    }).eq("id", entry["id"]).execute()

                    try:
                        await delete_user_by_discord(discord_id)
                    except:
                        pass

                    if log_channel:
                        embed = discord.Embed(
                            title="Subscription Expired",
                            color=discord.Color.red()
                        )
                        embed.add_field(name="User", value=f"<@{discord_id}> (`{discord_id}`)", inline=False)
                        embed.add_field(name="Product", value=entry.get("product_name", "Unknown"), inline=True)
                        embed.add_field(name="Variant", value=entry.get("variant_name", "Unknown"), inline=True)
                        embed.set_footer(text="Role and whitelist access removed")
                        await log_channel.send(embed=embed)

                    if member:
                        try:
                            dm_embed = discord.Embed(
                                title="Your Subscription Has Expired",
                                description=(
                                    "Your Fix-It-Up Premium subscription has expired.\n\n"
                                    "Your Premium role and whitelist access have been removed.\n\n"
                                    "**Want to renew?**\n"
                                    "Visit our shop to purchase a new subscription!"
                                ),
                                color=discord.Color.red()
                            )
                            dm_embed.set_thumbnail(url=BOT_LOGO_URL)
                            await member.send(embed=dm_embed)
                        except:
                            pass

                except Exception as e:
                    print(f"[EXPIRY] Error processing {discord_id}: {e}")

        except Exception as e:
            print(f"[EXPIRY TASK ERROR] {e}")

    @expiry_check.before_loop
    async def before_expiry_check(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=1)
    async def renewal_reminder(self):
        """DM users 3 days before their subscription expires"""
        try:
            guild = self.bot.get_guild(GUILD_ID)
            if not guild:
                return

            now = datetime.now(timezone.utc)
            three_days = now + timedelta(days=3)
            three_days_plus_hour = three_days + timedelta(hours=1)

            expiring = supabase.table("role_redeem").select(
                "id, discord_id, product_name, variant_name, expires_at"
            ).gte("expires_at", three_days.isoformat()
            ).lt("expires_at", three_days_plus_hour.isoformat()
            ).eq("whitelisted", True).execute()

            if not expiring.data:
                return

            for entry in expiring.data:
                discord_id = entry.get("discord_id")
                if not discord_id:
                    continue

                try:
                    member = guild.get_member(int(discord_id))
                    if member is None:
                        try:
                            member = await guild.fetch_member(int(discord_id))
                        except:
                            continue

                    expires_at = entry.get("expires_at")
                    ts = int(datetime.fromisoformat(expires_at.replace("Z", "+00:00")).timestamp())

                    dm_embed = discord.Embed(
                        title="Subscription Expiring Soon!",
                        description=(
                            f"Your Fix-It-Up Premium subscription expires <t:{ts}:R>!\n\n"
                            "**Renew now to keep your access:**\n"
                            "- Premium role\n"
                            "- Script whitelist\n\n"
                            "Visit our shop to renew before it expires!"
                        ),
                        color=discord.Color.orange()
                    )
                    dm_embed.set_thumbnail(url=BOT_LOGO_URL)
                    await member.send(embed=dm_embed)

                except Exception as e:
                    print(f"[REMINDER] Error for {discord_id}: {e}")

        except Exception as e:
            print(f"[REMINDER TASK ERROR] {e}")

    @renewal_reminder.before_loop
    async def before_renewal_reminder(self):
        await self.bot.wait_until_ready()

    # -----------------------------
    # ADMIN STAFF ONLY COMMANDS (whitelist, add time, etc.)
    # -----------------------------

    @discord.app_commands.command(name="addtime", description="Add days to a user's whitelist")
    @discord.app_commands.describe(user="The user to add time to", days="Number of days to add")
    async def addtime(self, interaction: Interaction, user: discord.Member, days: int):
        if not _is_admin_staff(interaction.user):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)

        result = await add_time_to_user(user.id, days)

        if not result:
            await interaction.followup.send(f"{user.mention} doesn't have a whitelist key.", ephemeral=True)
            return

        if result.get("error") == "lifetime":
            await interaction.followup.send(f"{user.mention} has a lifetime key - no expiry to extend.", ephemeral=True)
            return

        embed = discord.Embed(title="Time Added", color=discord.Color.green())
        embed.add_field(name="User", value=f"{user.mention}", inline=True)
        embed.add_field(name="Days Added", value=f"**{days}** days", inline=True)
        
        if result.get("new_expire"):
            embed.add_field(name="New Expiry", value=f"<t:{result['new_expire']}:F>", inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            log_embed = discord.Embed(title="Whitelist Time Added", color=discord.Color.blue())
            log_embed.add_field(name="User", value=f"{user.mention} (`{user.id}`)", inline=True)
            log_embed.add_field(name="Days Added", value=f"{days}", inline=True)
            log_embed.add_field(name="Staff", value=f"{interaction.user.mention}", inline=True)
            await log_channel.send(embed=log_embed)

    @discord.app_commands.command(name="applyref", description="Apply a referral code for a user")
    @discord.app_commands.describe(code="The referral code", buyer="The user who made the purchase")
    async def applyref(self, interaction: Interaction, code: str, buyer: discord.Member):
        if not _is_admin_staff(interaction.user):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        referral = supabase.table("referrals").select("*").eq(
            "referral_code", code.upper()
        ).limit(1).execute()

        if not referral.data:
            await interaction.followup.send(f"Referral code `{code}` not found.", ephemeral=True)
            return

        ref = referral.data[0]
        referrer_id = ref.get("referrer_discord_id")
        bonus_days = ref.get("bonus_days_per_referral", 3)

        if referrer_id == buyer.id:
            await interaction.followup.send("Users can't use their own referral code.", ephemeral=True)
            return

        existing = supabase.table("referral_uses").select("id").eq(
            "referred_discord_id", int(buyer.id)
        ).limit(1).execute()

        if existing.data:
            await interaction.followup.send(f"{buyer.mention} has already used a referral code.", ephemeral=True)
            return

        result = await add_time_to_user(referrer_id, bonus_days)

        supabase.table("referral_uses").insert({
            "referral_code": code.upper(),
            "referrer_discord_id": referrer_id,
            "referred_discord_id": int(buyer.id),
            "bonus_days_awarded": bonus_days
        }).execute()

        supabase.table("referrals").update({
            "uses": ref.get("uses", 0) + 1
        }).eq("id", ref["id"]).execute()

        embed = discord.Embed(title="Referral Applied", color=discord.Color.green())
        embed.add_field(name="Referrer", value=f"<@{referrer_id}>", inline=True)
        embed.add_field(name="New Customer", value=f"{buyer.mention}", inline=True)
        embed.add_field(name="Bonus Days Added", value=f"**{bonus_days}** days", inline=True)

        if result and result.get("new_expire"):
            embed.add_field(name="Referrer's New Expiry", value=f"<t:{result['new_expire']}:F>", inline=False)
        elif result and result.get("error") == "lifetime":
            embed.add_field(name="Note", value="Referrer has lifetime - bonus days not needed", inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            log_embed = discord.Embed(title="Referral Code Applied", color=discord.Color.blue())
            log_embed.add_field(name="Referrer", value=f"<@{referrer_id}>", inline=True)
            log_embed.add_field(name="New Customer", value=f"{buyer.mention}", inline=True)
            log_embed.add_field(name="Code", value=f"`{code.upper()}`", inline=True)
            log_embed.add_field(name="Staff", value=f"{interaction.user.mention}", inline=True)
            await log_channel.send(embed=log_embed)

    @discord.app_commands.command(name="verifygamepass", description="Verify a Roblox gamepass purchase and whitelist user")
    @discord.app_commands.describe(
        user="The Discord user who purchased",
        roblox_username="Their Roblox username",
        gamepass="The gamepass they purchased"
    )
    @discord.app_commands.choices(gamepass=[
        discord.app_commands.Choice(name="Week (700 Robux)", value=109857815),
        discord.app_commands.Choice(name="Month (1700 Robux)", value=129890883),
        discord.app_commands.Choice(name="Lifetime (4000 Robux)", value=125899946),
    ])
    async def verifygamepass(self, interaction: Interaction, user: discord.Member, roblox_username: str, gamepass: int):
        if not _is_admin_staff(interaction.user):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # Verify gamepass ownership via Roblox API
        success, roblox_user_id, message = await verify_gamepass_purchase(roblox_username, gamepass)
        
        if not success:
            embed = discord.Embed(
                title="Verification Failed",
                description=message,
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Check if this roblox user already redeemed this gamepass
        gamepass_info = get_gamepass_info(gamepass)
        existing = supabase.table("gamepass_redemptions").select("*").eq(
            "roblox_user_id", roblox_user_id
        ).eq("gamepass_id", gamepass).execute()

        if existing.data:
            prev = existing.data[0]
            prev_discord = prev.get("discord_id")
            prev_date = prev.get("redeemed_at", "Unknown")[:10]
            embed = discord.Embed(
                title="Already Redeemed",
                description=(
                    f"This Roblox account already redeemed the **{gamepass_info['name']}** gamepass.\n\n"
                    f"**Previous redemption:**\n"
                    f"Discord: <@{prev_discord}>\n"
                    f"Date: {prev_date}\n\n"
                    "If the user deleted and re-purchased the gamepass, use `/addtime` to extend manually."
                ),
                color=discord.Color.orange()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Whitelist on Luarmor
        product_name = f"Script Union - Fix it up ({gamepass_info['name']})"
        luarmor_result = await create_or_update_user(user.id, product_name)

        if not luarmor_result or luarmor_result.get("error"):
            embed = discord.Embed(
                title="Whitelist Failed",
                description=f"Failed to create Luarmor key: {luarmor_result.get('error') if luarmor_result else 'Unknown error'}",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Record the redemption
        supabase.table("gamepass_redemptions").insert({
            "discord_id": int(user.id),
            "roblox_username": roblox_username,
            "roblox_user_id": roblox_user_id,
            "gamepass_id": gamepass,
            "product_type": gamepass_info["name"],
            "verified_by": int(interaction.user.id)
        }).execute()

        # Give role
        role = interaction.guild.get_role(ACCESS_ROLE_ID)
        if role and role not in user.roles:
            try:
                await user.add_roles(role, reason=f"Gamepass verified by {interaction.user}")
            except:
                pass

        # Calculate expiry for display
        if gamepass_info["days"]:
            expiry_ts = int((datetime.now(timezone.utc) + timedelta(days=gamepass_info["days"])).timestamp())
            expiry_text = f"<t:{expiry_ts}:F>"
        else:
            expiry_text = "Lifetime"

        embed = discord.Embed(
            title="Gamepass Verified & Whitelisted",
            color=discord.Color.green()
        )
        embed.add_field(name="Discord User", value=f"{user.mention}", inline=True)
        embed.add_field(name="Roblox User", value=f"`{roblox_username}`", inline=True)
        embed.add_field(name="Product", value=f"**{gamepass_info['name']}**", inline=True)
        embed.add_field(name="Expires", value=expiry_text, inline=True)
        embed.add_field(name="Verified By", value=f"{interaction.user.mention}", inline=True)

        await interaction.followup.send(embed=embed, ephemeral=True)

        # Log
        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            log_embed = discord.Embed(
                title="Gamepass Purchase Verified",
                color=discord.Color.green()
            )
            log_embed.add_field(name="User", value=f"{user.mention} (`{user.id}`)", inline=True)
            log_embed.add_field(name="Roblox", value=f"`{roblox_username}` (`{roblox_user_id}`)", inline=True)
            log_embed.add_field(name="Product", value=gamepass_info["name"], inline=True)
            log_embed.add_field(name="Verified By", value=f"{interaction.user.mention}", inline=True)
            await log_channel.send(embed=log_embed)

        # DM user
        try:
            dm_embed = discord.Embed(
                title="You've Been Whitelisted!",
                description=(
                    f"Your **{gamepass_info['name']}** gamepass purchase has been verified.\n\n"
                    f"Go to <#1444457969407492352> and press **Get Script** to get started!"
                ),
                color=discord.Color.green()
            )
            dm_embed.set_thumbnail(url=BOT_LOGO_URL)
            await user.send(embed=dm_embed)
        except:
            pass

    @discord.app_commands.command(name="revenue", description="View revenue statistics")
    async def revenue(self, interaction: Interaction):
        if not _is_admin_staff(interaction.user):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        now = datetime.now(timezone.utc)
        week_ago = (now - timedelta(days=7)).isoformat()
        month_ago = (now - timedelta(days=30)).isoformat()
        year_ago = (now - timedelta(days=365)).isoformat()

        # SellAuth prices (USD)
        SELLAUTH_PRICES = {
            "Week": 5.00,
            "Month": 10.00,
            "Lifetime": 25.00
        }

        # Robux prices (converted to USD at rough rate, no fees)
        ROBUX_PRICES = {
            "Week": 700,
            "Month": 1700,
            "Lifetime": 4000
        }

        def calculate_sellauth_revenue(data):
            total = 0.0
            for r in data:
                variant = r.get("variant_name", "")
                for key, price in SELLAUTH_PRICES.items():
                    if key.lower() in variant.lower():
                        total += price
                        break
            return total

        def calculate_robux_revenue(data):
            total = 0
            for r in data:
                product_type = r.get("product_type", "")
                for key, price in ROBUX_PRICES.items():
                    if key.lower() in product_type.lower():
                        total += price
                        break
            return total

        # Get SellAuth sales (role_redeem table - only Fix it up products)
        weekly_sellauth = supabase.table("role_redeem").select("variant_name").gte(
            "redeemed_at", week_ago
        ).like("product_name", "%Fix it up%").execute()
        
        monthly_sellauth = supabase.table("role_redeem").select("variant_name").gte(
            "redeemed_at", month_ago
        ).like("product_name", "%Fix it up%").execute()
        
        yearly_sellauth = supabase.table("role_redeem").select("variant_name").gte(
            "redeemed_at", year_ago
        ).like("product_name", "%Fix it up%").execute()

        # Get Robux sales (gamepass_redemptions table)
        weekly_robux = supabase.table("gamepass_redemptions").select("product_type").gte(
            "redeemed_at", week_ago
        ).execute()
        
        monthly_robux = supabase.table("gamepass_redemptions").select("product_type").gte(
            "redeemed_at", month_ago
        ).execute()
        
        yearly_robux = supabase.table("gamepass_redemptions").select("product_type").gte(
            "redeemed_at", year_ago
        ).execute()

        # Calculate totals
        week_usd = calculate_sellauth_revenue(weekly_sellauth.data or [])
        month_usd = calculate_sellauth_revenue(monthly_sellauth.data or [])
        year_usd = calculate_sellauth_revenue(yearly_sellauth.data or [])

        week_robux = calculate_robux_revenue(weekly_robux.data or [])
        month_robux = calculate_robux_revenue(monthly_robux.data or [])
        year_robux = calculate_robux_revenue(yearly_robux.data or [])

        embed = discord.Embed(
            title="Revenue Statistics",
            description="Revenue before any fees (SellAuth/Stripe/Roblox)",
            color=discord.Color(EMBED_COLOR)
        )
        embed.set_thumbnail(url=BOT_LOGO_URL)

        # Weekly
        embed.add_field(
            name="This Week (7 days)",
            value=(
                f"**USD:** ${week_usd:.2f}\n"
                f"**Robux:** R${week_robux:,}\n"
                f"Sales: {len(weekly_sellauth.data or [])} card/crypto, {len(weekly_robux.data or [])} robux"
            ),
            inline=False
        )

        # Monthly
        embed.add_field(
            name="This Month (30 days)",
            value=(
                f"**USD:** ${month_usd:.2f}\n"
                f"**Robux:** R${month_robux:,}\n"
                f"Sales: {len(monthly_sellauth.data or [])} card/crypto, {len(monthly_robux.data or [])} robux"
            ),
            inline=False
        )

        # Yearly
        embed.add_field(
            name="This Year (365 days)",
            value=(
                f"**USD:** ${year_usd:.2f}\n"
                f"**Robux:** R${year_robux:,}\n"
                f"Sales: {len(yearly_sellauth.data or [])} card/crypto, {len(yearly_robux.data or [])} robux"
            ),
            inline=False
        )

        embed.set_footer(text=f"Stats as of {now.strftime('%Y-%m-%d %H:%M UTC')}")

        await interaction.followup.send(embed=embed, ephemeral=True)

    # -----------------------------
    # VIEW-ONLY COMMANDS (support team can use)
    # -----------------------------

    @discord.app_commands.command(name="userlookup", description="Look up a user's purchase history and whitelist status")
    @discord.app_commands.describe(user="The user to look up")
    async def userlookup(self, interaction: Interaction, user: discord.Member):
        if not _is_any_staff(interaction.user):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        redemptions = supabase.table("role_redeem").select("*").eq(
            "discord_id", int(user.id)
        ).order("redeemed_at", desc=True).execute()

        referral = supabase.table("referrals").select("*").eq(
            "referrer_discord_id", int(user.id)
        ).limit(1).execute()

        luarmor_info = await get_user_info(user.id)

        embed = discord.Embed(
            title=f"User Lookup: {user}",
            color=discord.Color(EMBED_COLOR)
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="User ID", value=f"`{user.id}`", inline=True)
        embed.add_field(name="Account Created", value=f"<t:{int(user.created_at.timestamp())}:R>", inline=True)
        embed.add_field(name="Joined Server", value=f"<t:{int(user.joined_at.timestamp())}:R>" if user.joined_at else "Unknown", inline=True)

        if luarmor_info:
            key = luarmor_info.get("user_key", "Unknown")
            auth_expire = luarmor_info.get("auth_expire")
            hwid = luarmor_info.get("identifier", "Not set")
            
            if auth_expire is None or auth_expire == -1:
                expiry_text = "Lifetime"
            else:
                expiry_text = f"<t:{auth_expire}:F>"
            
            embed.add_field(name="Luarmor Key", value=f"||`{key}`||", inline=False)
            embed.add_field(name="Key Expires", value=expiry_text, inline=True)
            embed.add_field(name="HWID", value=f"`{hwid[:20]}...`" if len(str(hwid)) > 20 else f"`{hwid}`", inline=True)
        else:
            embed.add_field(name="Luarmor Status", value="No active whitelist", inline=False)

        if redemptions.data:
            history = []
            for i, r in enumerate(redemptions.data[:5]):
                product = r.get("product_name", "Unknown")
                variant = r.get("variant_name", "Unknown")
                invoice = r.get("invoice_id", "N/A")
                redeemed_at = r.get("redeemed_at")
                
                if redeemed_at:
                    try:
                        ts = int(datetime.fromisoformat(redeemed_at.replace("Z", "+00:00")).timestamp())
                        date_str = f"<t:{ts}:d>"
                    except:
                        date_str = redeemed_at[:10]
                else:
                    date_str = "Unknown"
                
                history.append(f"**{i+1}.** {variant} - {date_str}\n   Invoice: `{invoice[:15]}...`")
            
            embed.add_field(
                name=f"Purchase History ({len(redemptions.data)} total)",
                value="\n".join(history) or "None",
                inline=False
            )
        else:
            embed.add_field(name="Purchase History", value="No purchases found", inline=False)

        if referral.data:
            ref = referral.data[0]
            embed.add_field(
                name="Referral Code",
                value=f"`{ref.get('referral_code')}` ({ref.get('uses', 0)} uses)",
                inline=False
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.app_commands.command(name="stats", description="View bot and sales statistics")
    async def stats(self, interaction: Interaction):
        if not _is_any_staff(interaction.user):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        now = datetime.now(timezone.utc)
        month_ago = (now - timedelta(days=30)).isoformat()
        week_ago = (now - timedelta(days=7)).isoformat()

        total = supabase.table("role_redeem").select("id", count="exact").execute()
        total_count = total.count or 0

        monthly = supabase.table("role_redeem").select("id", count="exact").gte(
            "redeemed_at", month_ago
        ).execute()
        monthly_count = monthly.count or 0

        weekly = supabase.table("role_redeem").select("id", count="exact").gte(
            "redeemed_at", week_ago
        ).execute()
        weekly_count = weekly.count or 0

        active = supabase.table("role_redeem").select("id", count="exact").eq(
            "whitelisted", True
        ).execute()
        active_count = active.count or 0

        all_redemptions = supabase.table("role_redeem").select("variant_name").execute()
        variant_counts = {}
        for r in (all_redemptions.data or []):
            v = r.get("variant_name", "Unknown")
            variant_counts[v] = variant_counts.get(v, 0) + 1

        open_tickets = supabase.table("tickets").select("id", count="exact").eq(
            "status", "open"
        ).execute()
        ticket_count = open_tickets.count or 0

        embed = discord.Embed(title="Shop Statistics", color=discord.Color(EMBED_COLOR))
        embed.set_thumbnail(url=BOT_LOGO_URL)

        embed.add_field(name="Total Redemptions", value=f"**{total_count}**", inline=True)
        embed.add_field(name="This Month", value=f"**{monthly_count}**", inline=True)
        embed.add_field(name="This Week", value=f"**{weekly_count}**", inline=True)

        embed.add_field(name="Active Subscriptions", value=f"**{active_count}**", inline=True)
        embed.add_field(name="Open Tickets", value=f"**{ticket_count}**", inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)

        if variant_counts:
            breakdown = "\n".join([f"- {k}: **{v}**" for k, v in sorted(variant_counts.items(), key=lambda x: -x[1])])
            embed.add_field(name="Sales by Variant", value=breakdown, inline=False)

        embed.set_footer(text=f"Stats as of {now.strftime('%Y-%m-%d %H:%M UTC')}")

        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.app_commands.command(name="keytime", description="Check remaining time on a user's whitelist")
    @discord.app_commands.describe(user="The user to check (leave empty for yourself)")
    async def keytime(self, interaction: Interaction, user: discord.Member = None):
        target = user or interaction.user
        
        if user and user.id != interaction.user.id:
            if not _is_any_staff(interaction.user):
                await interaction.response.send_message("You can only check your own key time.", ephemeral=True)
                return

        await interaction.response.defer(ephemeral=True)

        luarmor_info = await get_user_info(target.id)

        embed = discord.Embed(
            title=f"Whitelist Key Time: {target}",
            color=discord.Color(EMBED_COLOR)
        )
        embed.set_thumbnail(url=target.display_avatar.url)

        if luarmor_info:
            key = luarmor_info.get("user_key", "Unknown")
            auth_expire = luarmor_info.get("auth_expire")
            hwid = luarmor_info.get("identifier", "Not set")
            
            if auth_expire is None or auth_expire == -1:
                expiry_text = "Lifetime"
                time_remaining = "Never expires"
            else:
                expiry_text = f"<t:{auth_expire}:F>"
                now = int(datetime.now(timezone.utc).timestamp())
                remaining = auth_expire - now
                if remaining > 0:
                    days = remaining // 86400
                    hours = (remaining % 86400) // 3600
                    time_remaining = f"**{days}** days, **{hours}** hours"
                else:
                    time_remaining = "**EXPIRED**"
            
            # Only show key to staff
            if _is_any_staff(interaction.user):
                embed.add_field(name="Luarmor Key", value=f"||`{key}`||", inline=False)
            
            embed.add_field(name="Expires", value=expiry_text, inline=True)
            embed.add_field(name="Time Remaining", value=time_remaining, inline=True)
            
            if _is_any_staff(interaction.user):
                embed.add_field(name="HWID", value=f"`{hwid[:20]}...`" if len(str(hwid)) > 20 else f"`{hwid}`", inline=False)
        else:
            embed.add_field(name="Status", value="No active whitelist", inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    # -----------------------------
    # USER COMMANDS (anyone can use)
    # -----------------------------

    @discord.app_commands.command(name="mycode", description="Get your referral code")
    async def mycode(self, interaction: Interaction):
        print(f"[MYCODE] Command called by {interaction.user.id}")
        
        try:
            await interaction.response.defer(ephemeral=True)
            
            existing = supabase.table("referrals").select("*").eq(
                "referrer_discord_id", int(interaction.user.id)
            ).limit(1).execute()

            if existing.data:
                ref = existing.data[0]
                code = ref.get("referral_code")
                uses = ref.get("uses", 0)
                bonus_days = ref.get("bonus_days_per_referral", 3)
            else:
                code = _generate_referral_code()
                
                while True:
                    check = supabase.table("referrals").select("id").eq(
                        "referral_code", code
                    ).limit(1).execute()
                    if not check.data:
                        break
                    code = _generate_referral_code()
                
                supabase.table("referrals").insert({
                    "referrer_discord_id": int(interaction.user.id),
                    "referral_code": code,
                    "uses": 0,
                    "bonus_days_per_referral": 3
                }).execute()
                
                uses = 0
                bonus_days = 3

            embed = discord.Embed(
                title="Your Referral Code",
                description=f"**`{code}`**",
                color=discord.Color(EMBED_COLOR)
            )
            embed.add_field(name="Total Referrals", value=f"**{uses}**", inline=True)
            embed.add_field(name="Bonus Per Referral", value=f"**{bonus_days}** days", inline=True)
            embed.add_field(
                name="How it works",
                value=(
                    "Share your code with friends!\n"
                    "When they purchase and enter your code during redemption, "
                    f"you'll receive **{bonus_days} bonus days** added to your subscription."
                ),
                inline=False
            )
            embed.set_footer(text="Referral bonuses are added automatically")

            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            print(f"[MYCODE ERROR] {e}")
            try:
                await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)
            except:
                pass

    @discord.app_commands.command(name="referrals", description="View your referral stats")
    @discord.app_commands.describe(user="The user to check (staff only)")
    async def referrals(self, interaction: Interaction, user: discord.Member = None):
        target = user or interaction.user
        
        if user and user.id != interaction.user.id:
            if not _is_any_staff(interaction.user):
                await interaction.response.send_message("You can only check your own referral stats.", ephemeral=True)
                return

        await interaction.response.defer(ephemeral=True)

        referral = supabase.table("referrals").select("*").eq(
            "referrer_discord_id", int(target.id)
        ).limit(1).execute()

        if not referral.data:
            await interaction.followup.send(
                f"{'You don' if target == interaction.user else f'{target.mention} doesn'}'t have a referral code yet. Use `/mycode` to create one!",
                ephemeral=True
            )
            return

        ref = referral.data[0]
        code = ref.get("referral_code")
        uses = ref.get("uses", 0)
        bonus_days = ref.get("bonus_days_per_referral", 3)

        referral_uses = supabase.table("referral_uses").select("*").eq(
            "referrer_discord_id", int(target.id)
        ).order("created_at", desc=True).limit(10).execute()

        embed = discord.Embed(
            title=f"Referral Stats: {target}",
            color=discord.Color(EMBED_COLOR)
        )
        embed.add_field(name="Referral Code", value=f"**`{code}`**", inline=True)
        embed.add_field(name="Total Referrals", value=f"**{uses}**", inline=True)
        embed.add_field(name="Total Bonus Days Earned", value=f"**{uses * bonus_days}** days", inline=True)

        if referral_uses.data:
            recent = []
            for r in referral_uses.data[:5]:
                referred_id = r.get("referred_discord_id")
                bonus = r.get("bonus_days_awarded", 0)
                created_at = r.get("created_at")
                
                if created_at:
                    try:
                        ts = int(datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp())
                        date_str = f"<t:{ts}:R>"
                    except:
                        date_str = "Unknown"
                else:
                    date_str = "Unknown"
                
                recent.append(f"<@{referred_id}> - +{bonus} days - {date_str}")
            
            embed.add_field(
                name="Recent Referrals",
                value="\n".join(recent) or "None",
                inline=False
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # -----------------------------
    # NEW COMMANDS
    # -----------------------------

    @discord.app_commands.command(name="whitelist", description="Manually whitelist a user")
    @discord.app_commands.describe(user="The user to whitelist", days="Number of days (0 for lifetime)")
    async def whitelist(self, interaction: Interaction, user: discord.Member, days: int = 0):
        if not _is_admin_staff(interaction.user):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # Check if user is blacklisted
        blacklisted = supabase.table("blacklist").select("*").eq(
            "discord_id", int(user.id)
        ).limit(1).execute()

        if blacklisted.data:
            await interaction.followup.send(f"{user.mention} is blacklisted and cannot be whitelisted.", ephemeral=True)
            return

        # Determine product name based on days
        if days == 0:
            product_name = "Script Union - Fix it up (Lifetime)"
            expiry_text = "Lifetime"
        elif days <= 7:
            product_name = "Script Union - Fix it up (Week)"
            expiry_text = f"{days} days"
        elif days <= 30:
            product_name = "Script Union - Fix it up (Month)"
            expiry_text = f"{days} days"
        else:
            product_name = f"Manual Whitelist ({days} days)"
            expiry_text = f"{days} days"

        # Create Luarmor key
        luarmor_result = await create_or_update_user(user.id, product_name)

        if not luarmor_result or luarmor_result.get("error"):
            error_msg = luarmor_result.get("error") if luarmor_result else "Unknown error"
            await interaction.followup.send(f"Failed to whitelist: {error_msg}", ephemeral=True)
            return

        # Give role
        role = interaction.guild.get_role(ACCESS_ROLE_ID)
        if role and role not in user.roles:
            try:
                await user.add_roles(role, reason=f"Manually whitelisted by {interaction.user}")
            except:
                pass

        # Calculate expiry for display
        if days == 0:
            expiry_display = "Lifetime"
        else:
            expiry_ts = int((datetime.now(timezone.utc) + timedelta(days=days)).timestamp())
            expiry_display = f"<t:{expiry_ts}:F>"

        embed = discord.Embed(title="User Whitelisted", color=discord.Color.green())
        embed.add_field(name="User", value=f"{user.mention}", inline=True)
        embed.add_field(name="Duration", value=expiry_text, inline=True)
        embed.add_field(name="Expires", value=expiry_display, inline=True)
        embed.add_field(name="Whitelisted By", value=f"{interaction.user.mention}", inline=True)

        await interaction.followup.send(embed=embed, ephemeral=True)

        # Log
        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            log_embed = discord.Embed(title="Manual Whitelist", color=discord.Color.green())
            log_embed.add_field(name="User", value=f"{user.mention} (`{user.id}`)", inline=True)
            log_embed.add_field(name="Duration", value=expiry_text, inline=True)
            log_embed.add_field(name="Staff", value=f"{interaction.user.mention}", inline=True)
            await log_channel.send(embed=log_embed)

        # DM user
        try:
            dm_embed = discord.Embed(
                title="You've Been Whitelisted!",
                description=(
                    f"You have been manually whitelisted for **{expiry_text}**.\n\n"
                    f"Go to <#1444457969407496352> and press **Get Script** to get started!"
                ),
                color=discord.Color.green()
            )
            dm_embed.set_thumbnail(url=BOT_LOGO_URL)
            await user.send(embed=dm_embed)
        except:
            pass

    @discord.app_commands.command(name="blacklist", description="Blacklist a user from redeeming")
    @discord.app_commands.describe(user="The user to blacklist", reason="Reason for blacklist")
    async def blacklist(self, interaction: Interaction, user: discord.Member, reason: str = "No reason provided"):
        if not _is_admin_staff(interaction.user):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # Check if already blacklisted
        existing = supabase.table("blacklist").select("*").eq(
            "discord_id", int(user.id)
        ).limit(1).execute()

        if existing.data:
            await interaction.followup.send(f"{user.mention} is already blacklisted.", ephemeral=True)
            return

        # Remove from Luarmor
        try:
            await delete_user_by_discord(user.id)
        except:
            pass

        # Remove Premium role
        role = interaction.guild.get_role(ACCESS_ROLE_ID)
        if role and role in user.roles:
            try:
                await user.remove_roles(role, reason=f"Blacklisted by {interaction.user}")
            except:
                pass

        # Add to blacklist table
        supabase.table("blacklist").insert({
            "discord_id": int(user.id),
            "reason": reason,
            "blacklisted_by": int(interaction.user.id)
        }).execute()

        embed = discord.Embed(title="User Blacklisted", color=discord.Color.red())
        embed.add_field(name="User", value=f"{user.mention} (`{user.id}`)", inline=True)
        embed.add_field(name="Reason", value=reason, inline=True)
        embed.add_field(name="Blacklisted By", value=f"{interaction.user.mention}", inline=True)

        await interaction.followup.send(embed=embed, ephemeral=True)

        # Log
        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            log_embed = discord.Embed(title="User Blacklisted", color=discord.Color.red())
            log_embed.add_field(name="User", value=f"{user.mention} (`{user.id}`)", inline=True)
            log_embed.add_field(name="Reason", value=reason, inline=False)
            log_embed.add_field(name="Staff", value=f"{interaction.user.mention}", inline=True)
            await log_channel.send(embed=log_embed)

    @discord.app_commands.command(name="unblacklist", description="Remove a user from the blacklist")
    @discord.app_commands.describe(user="The user to unblacklist")
    async def unblacklist(self, interaction: Interaction, user: discord.Member):
        if not _is_admin_staff(interaction.user):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # Check if blacklisted
        existing = supabase.table("blacklist").select("*").eq(
            "discord_id", int(user.id)
        ).limit(1).execute()

        if not existing.data:
            await interaction.followup.send(f"{user.mention} is not blacklisted.", ephemeral=True)
            return

        # Remove from blacklist
        supabase.table("blacklist").delete().eq("discord_id", int(user.id)).execute()

        embed = discord.Embed(title="User Unblacklisted", color=discord.Color.green())
        embed.add_field(name="User", value=f"{user.mention} (`{user.id}`)", inline=True)
        embed.add_field(name="Removed By", value=f"{interaction.user.mention}", inline=True)

        await interaction.followup.send(embed=embed, ephemeral=True)

        # Log
        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            log_embed = discord.Embed(title="User Unblacklisted", color=discord.Color.green())
            log_embed.add_field(name="User", value=f"{user.mention} (`{user.id}`)", inline=True)
            log_embed.add_field(name="Staff", value=f"{interaction.user.mention}", inline=True)
            await log_channel.send(log_embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))
    print("✅ Loaded cog: admin")
