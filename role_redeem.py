import discord
from discord.ext import commands
from discord import app_commands, Interaction
from utils.supabase import get_supabase

GUILD_ID = 1432550511495610472
EXTRA_ROLE_ID = 1438358929187934310

supabase = get_supabase()

class RoleRedeem(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="role-redeem", description="Redeem a code to receive a role.")
    @app_commands.describe(code="Enter the redemption code")
    async def role_redeem(self, interaction: Interaction, code: str):

        guild = self.bot.get_guild(GUILD_ID)
        if not guild:
            return await interaction.response.send_message("Guild not found.", ephemeral=True)

        response = supabase.table("role_redeem").select("*").eq("code", code).execute()

        if not response.data:
            return await interaction.response.send_message("❌ Invalid or already used code.", ephemeral=True)

        row = response.data[0]

        if row.get("discord_id"):
            return await interaction.response.send_message("❌ This code has already been redeemed.", ephemeral=True)

        role_id = row.get("role_id")
        if not role_id:
            return await interaction.response.send_message("❌ This code has no role linked to it.", ephemeral=True)

        role = guild.get_role(int(role_id))
        if not role:
            return await interaction.response.send_message("❌ The role linked to this code no longer exists.", ephemeral=True)

        extra_role = guild.get_role(EXTRA_ROLE_ID)

        try:
            roles_to_add = [role]
            if extra_role:
                roles_to_add.append(extra_role)

            await interaction.user.add_roles(*roles_to_add, reason="Redeemed role via /role-redeem")

        except discord.Forbidden:
            return await interaction.response.send_message("⚠️ I do not have permission to give one of the roles.", ephemeral=True)

        supabase.table("role_redeem").update({"discord_id": interaction.user.id}).eq("code", code).execute()

        await interaction.response.send_message(
            f"✅ Successfully redeemed! You received **{role.name}**"
            + (f" and **{extra_role.name}**." if extra_role else "."),
            ephemeral=True
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(RoleRedeem(bot))
