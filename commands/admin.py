import os
import discord
from discord.ext import commands, tasks
from discord import Interaction
from datetime import datetime, timezone, timedelta
import random
import string

from utils.supabase import get_supabase
from utils.luarmor import get_user_info, add_time_to_user, delete_user_by_discord

# -----------------------------
# CONFIG
# -----------------------------
GUILD_ID = 1345153296360542271
LOG_CHANNEL_ID = 1449252986911068273
PURCHASE_LOG_CHANNEL_ID = 1449252986911068273  # Can be same or different channel
ACCESS_ROLE_ID = 1444450052323147826  # Premium role

STAFF_ROLE_IDS = {
    1432015464036433970,
    1449491116822106263,
}

EMBED_COLOR = 0x489BF3
BOT_LOGO_URL = "https://cdn.discordapp.com/attachments/1449252986911068273/1449511913317732485/ScriptUnionIcon.png"

supabase = get_supabase()


def _is_staff(member: discord.Member) -> bool:
    return any(r.id in STAFF_ROLE_IDS for r in member.roles)


def _generate_referral_code() -> str:
    """Generate a unique referral code like REF-ABC123"""
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

            # Get expired entries from Supabase
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

                    # Remove role if they have it
                    if member and role and role in member.roles:
                        await member.remove_roles(role, reason="Subscription expired")

                    # Mark as not whitelisted in DB
                    supabase.table("role_redeem").update({
                        "whitelisted": False
                    }).eq("id", entry["id"]).execute()

                    # Try to delete from Luarmor
                    try:
                        await delete_user_by_discord(discord_id)
                    except:
                        pass

                    # Log expiry
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

                    # DM user about expiry
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

            # Get entries expiring in ~3 days that haven't been reminded
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
                            "• Premium role\n"
                            "• Script whitelist\n\n"
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
    # COMMANDS
    # -----------------------------

    @discord.app_commands.command(name="userlookup", description="View a user's full purchase history and status")
    @discord.app_commands.describe(user="The user to look up")
    async def userlookup(self, interaction: Interaction, user: discord.Member):
        if not _is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # Get all redemptions for this user
        redemptions = supabase.table("role_redeem").select("*").eq(
            "discord_id", int(user.id)
        ).order("redeemed_at", desc=True).execute()

        # Get referral info
        referral = supabase.table("referrals").select("*").eq(
            "referrer_discord_id", int(user.id)
        ).limit(1).execute()

        # Get Luarmor info
        luarmor_info = await get_user_info(user.id)

        embed = discord.Embed(
            title=f"User Lookup: {user}",
            color=discord.Color(EMBED_COLOR)
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="User ID", value=f"`{user.id}`", inline=True)
        embed.add_field(name="Account Created", value=f"<t:{int(user.created_at.timestamp())}:R>", inline=True)
        embed.add_field(name="Joined Server", value=f"<t:{int(user.joined_at.timestamp())}:R>" if user.joined_at else "Unknown", inline=True)

        # Luarmor status
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

        # Purchase history
        if redemptions.data:
            history = []
            for i, r in enumerate(redemptions.data[:5]):  # Last 5
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

        # Referral info
        if referral.data:
            ref = referral.data[0]
            embed.add_field(
                name="Referral Code",
                value=f"`{ref.get('referral_code')}` ({ref.get('uses', 0)} uses)",
                inline=False
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.app_commands.command(name="stats", description="View shop statistics")
    async def stats(self, interaction: Interaction):
        if not _is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        now = datetime.now(timezone.utc)
        month_ago = (now - timedelta(days=30)).isoformat()
        week_ago = (now - timedelta(days=7)).isoformat()

        # Total redemptions
        total = supabase.table("role_redeem").select("id", count="exact").execute()
        total_count = total.count or 0

        # This month
        monthly = supabase.table("role_redeem").select("id", count="exact").gte(
            "redeemed_at", month_ago
        ).execute()
        monthly_count = monthly.count or 0

        # This week
        weekly = supabase.table("role_redeem").select("id", count="exact").gte(
            "redeemed_at", week_ago
        ).execute()
        weekly_count = weekly.count or 0

        # Active subscriptions
        active = supabase.table("role_redeem").select("id", count="exact").eq(
            "whitelisted", True
        ).execute()
        active_count = active.count or 0

        # By variant
        all_redemptions = supabase.table("role_redeem").select("variant_name").execute()
        variant_counts = {}
        for r in (all_redemptions.data or []):
            v = r.get("variant_name", "Unknown")
            variant_counts[v] = variant_counts.get(v, 0) + 1

        # Open tickets
        open_tickets = supabase.table("tickets").select("id", count="exact").eq(
            "status", "open"
        ).execute()
        ticket_count = open_tickets.count or 0

        embed = discord.Embed(
            title="Shop Statistics",
            color=discord.Color(EMBED_COLOR)
        )
        embed.set_thumbnail(url=BOT_LOGO_URL)

        embed.add_field(name="Total Redemptions", value=f"**{total_count}**", inline=True)
        embed.add_field(name="This Month", value=f"**{monthly_count}**", inline=True)
        embed.add_field(name="This Week", value=f"**{weekly_count}**", inline=True)

        embed.add_field(name="Active Subscriptions", value=f"**{active_count}**", inline=True)
        embed.add_field(name="Open Tickets", value=f"**{ticket_count}**", inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)

        # Variant breakdown
        if variant_counts:
            breakdown = "\n".join([f"• {k}: **{v}**" for k, v in sorted(variant_counts.items(), key=lambda x: -x[1])])
            embed.add_field(name="Sales by Variant", value=breakdown, inline=False)

        embed.set_footer(text=f"Stats as of {now.strftime('%Y-%m-%d %H:%M UTC')}")

        await interaction.followup.send(embed=embed, ephemeral=True)

    # -----------------------------
    # REFERRAL COMMANDS
    # -----------------------------

    @discord.app_commands.command(name="mycode", description="Get your referral code")
    async def mycode(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)

        # Check if user has a referral code
        existing = supabase.table("referrals").select("*").eq(
            "referrer_discord_id", int(interaction.user.id)
        ).limit(1).execute()

        if existing.data:
            ref = existing.data[0]
            code = ref.get("referral_code")
            uses = ref.get("uses", 0)
            bonus_days = ref.get("bonus_days_per_referral", 3)
        else:
            # Create new code
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
            description=(
                f"**Code:** `{code}`\n\n"
                f"Share this code with friends! When they purchase and mention your code in a ticket, "
                f"you'll get **{bonus_days} bonus days** added to your subscription!"
            ),
            color=discord.Color(EMBED_COLOR)
        )
        embed.add_field(name="Total Referrals", value=f"**{uses}**", inline=True)
        embed.add_field(name="Bonus Days Earned", value=f"**{uses * bonus_days}**", inline=True)
        embed.set_footer(text="Referral codes are applied by staff when processing orders")

        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.app_commands.command(name="referrals", description="View referral statistics")
    @discord.app_commands.describe(user="User to check (staff only, leave empty for yourself)")
    async def referrals(self, interaction: Interaction, user: discord.Member = None):
        await interaction.response.defer(ephemeral=True)

        target = user or interaction.user
        is_staff = _is_staff(interaction.user)

        # Non-staff can only check themselves
        if user and user.id != interaction.user.id and not is_staff:
            await interaction.followup.send("You can only check your own referrals.", ephemeral=True)
            return

        # Get referral info
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

        # Get referral history
        history = supabase.table("referral_uses").select("*").eq(
            "referrer_discord_id", int(target.id)
        ).order("created_at", desc=True).limit(10).execute()

        embed = discord.Embed(
            title=f"Referral Stats: {target}",
            color=discord.Color(EMBED_COLOR)
        )
        embed.add_field(name="Code", value=f"`{code}`", inline=True)
        embed.add_field(name="Total Referrals", value=f"**{uses}**", inline=True)
        embed.add_field(name="Bonus Days Earned", value=f"**{uses * bonus_days}**", inline=True)

        if history.data:
            recent = []
            for h in history.data[:5]:
                referred_id = h.get("referred_discord_id")
                created_at = h.get("created_at")
                if created_at:
                    try:
                        ts = int(datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp())
                        date_str = f"<t:{ts}:R>"
                    except:
                        date_str = created_at[:10]
                else:
                    date_str = "Unknown"
                recent.append(f"• <@{referred_id}> - {date_str}")
            
            embed.add_field(name="Recent Referrals", value="\n".join(recent), inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.app_commands.command(name="applyref", description="Apply a referral code for a purchase (staff only)")
    @discord.app_commands.describe(
        code="The referral code",
        buyer="The user who made the purchase"
    )
    async def applyref(self, interaction: Interaction, code: str, buyer: discord.Member):
        if not _is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # Find the referral code
        referral = supabase.table("referrals").select("*").eq(
            "referral_code", code.upper()
        ).limit(1).execute()

        if not referral.data:
            await interaction.followup.send(f"Referral code `{code}` not found.", ephemeral=True)
            return

        ref = referral.data[0]
        referrer_id = ref.get("referrer_discord_id")
        bonus_days = ref.get("bonus_days_per_referral", 3)

        # Can't refer yourself
        if referrer_id == buyer.id:
            await interaction.followup.send("Users can't use their own referral code.", ephemeral=True)
            return

        # Check if already referred by this person
        existing = supabase.table("referral_uses").select("id").eq(
            "referred_discord_id", int(buyer.id)
        ).limit(1).execute()

        if existing.data:
            await interaction.followup.send(f"{buyer.mention} has already used a referral code.", ephemeral=True)
            return

        # Add bonus days to referrer
        result = await add_time_to_user(referrer_id, bonus_days)

        # Record the referral
        supabase.table("referral_uses").insert({
            "referral_code": code.upper(),
            "referrer_discord_id": referrer_id,
            "referred_discord_id": int(buyer.id),
            "bonus_days_awarded": bonus_days
        }).execute()

        # Update uses count
        supabase.table("referrals").update({
            "uses": ref.get("uses", 0) + 1
        }).eq("id", ref["id"]).execute()

        embed = discord.Embed(
            title="Referral Applied",
            color=discord.Color.green()
        )
        embed.add_field(name="Referrer", value=f"<@{referrer_id}>", inline=True)
        embed.add_field(name="New Customer", value=f"{buyer.mention}", inline=True)
        embed.add_field(name="Bonus Days Added", value=f"**{bonus_days}** days", inline=True)

        if result and result.get("new_expire"):
            embed.add_field(name="Referrer's New Expiry", value=f"<t:{result['new_expire']}:F>", inline=False)
        elif result and result.get("error") == "lifetime":
            embed.add_field(name="Note", value="Referrer has lifetime - bonus days saved for future", inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

        # Log it
        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            log_embed = discord.Embed(
                title="Referral Code Applied",
                color=discord.Color.blue()
            )
            log_embed.add_field(name="Referrer", value=f"<@{referrer_id}> (`{referrer_id}`)", inline=True)
            log_embed.add_field(name="New Customer", value=f"{buyer.mention} (`{buyer.id}`)", inline=True)
            log_embed.add_field(name="Code", value=f"`{code.upper()}`", inline=True)
            log_embed.add_field(name="Bonus Days", value=f"{bonus_days}", inline=True)
            log_embed.add_field(name="Applied By", value=f"{interaction.user.mention}", inline=True)
            await log_channel.send(embed=log_embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))
    print("✅ Loaded cog: admin")
