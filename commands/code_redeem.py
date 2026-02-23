import discord
from discord.ext import commands, tasks
from discord import app_commands, ui, Interaction
import json
import os
from utils.supabase import get_supabase

supabase = get_supabase()

GUILD_ID = 1345153296360542271
REDEEM_CHANNEL_ID = 1448176697693175970

BUTTON_COLOR_MAP = {
    "grey": discord.ButtonStyle.secondary,
    "gray": discord.ButtonStyle.secondary,
    "green": discord.ButtonStyle.success,
    "red": discord.ButtonStyle.danger,
    "blurple": discord.ButtonStyle.primary
}

class DynamicRedeemButton(ui.Button):
    def __init__(self, label, style, product_path, required_role):
        super().__init__(label=label, style=style)
        self.product_path = product_path
        self.required_role = required_role

    async def callback(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)

        # Ensure the guild exists
        guild = interaction.client.get_guild(GUILD_ID)
        if not guild:
            return await interaction.followup.send("❌ Guild not found.", ephemeral=True)

        # Check if user has the required role
        if self.required_role not in [r.id for r in interaction.user.roles]:
            return await interaction.followup.send(
                "❌ You do not have the required role to redeem this product.",
                ephemeral=True
            )

        # Fetch redemption row for this role
        resp = supabase.table("role_redeem")\
            .select("*")\
            .eq("role_id", self.required_role)\
            .execute()

        if not resp.data:
            return await interaction.followup.send(
                "❌ No redemption entry exists for this product.",
                ephemeral=True
            )

        row = resp.data[0]

        # Check if already redeemed by this user
        if row.get("redeemed") and row.get("redeemed_by") == interaction.user.id:
            return await interaction.followup.send(
                "❌ You already redeemed this product.", ephemeral=True
            )

        # Check product file exists
        if not os.path.exists(self.product_path):
            return await interaction.followup.send(
                "❌ Product file missing on server.", ephemeral=True
            )

        # Send file via DM
        try:
            await interaction.user.send(
                f"📦 Here is your product file for {self.label}:",
                file=discord.File(self.product_path)
            )
        except discord.Forbidden:
            return await interaction.followup.send(
                "❌ You must enable DMs to receive your product.", ephemeral=True
            )

        # Mark as redeemed by this user
        supabase.table("role_redeem")\
            .update({
                "redeemed": True,
                "redeemed_by": interaction.user.id
            })\
            .eq("role_id", self.required_role)\
            .execute()

        await interaction.followup.send(
            "✅ Product redeemed and sent to your DMs!", ephemeral=True
        )


class RedeemView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

        # Load buttons from config
        try:
            with open("buttonconfig.json", "r") as f:
                data = json.load(f)
        except Exception as e:
            print(f"Failed to load buttonconfig.json: {e}")
            data = {"buttons": []}

        for entry in data.get("buttons", []):
            if not all(k in entry for k in ("ButtonName", "ButtonColor", "ButtonProductPath", "RedeemRole")):
                print(f"Invalid button entry in config: {entry}")
                continue

            self.add_item(DynamicRedeemButton(
                label=entry["ButtonName"],
                style=BUTTON_COLOR_MAP.get(entry["ButtonColor"].lower(), discord.ButtonStyle.secondary),
                product_path=entry["ButtonProductPath"],
                required_role=int(entry["RedeemRole"])
            ))


class CodeRedeem(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.dashboard_message = None
        self.refresh_dashboard.start()

    def cog_unload(self):
        self.refresh_dashboard.cancel()

    @tasks.loop(minutes=1)
    async def refresh_dashboard(self):
        await self.bot.wait_until_ready()
        channel = self.bot.get_channel(REDEEM_CHANNEL_ID)
        if not channel:
            print("Redeem channel not found.")
            return

        # Delete old bot messages
        try:
            async for msg in channel.history(limit=10):
                if msg.author == self.bot.user:
                    await msg.delete()
        except Exception as e:
            print(f"Failed to delete messages: {e}")

        embed = discord.Embed(
            title="🎁 Product Redeem Dashboard",
            description="Click a button below to redeem your purchased product.",
            color=discord.Color.blurple()
        )
        view = RedeemView()
        try:
            self.dashboard_message = await channel.send(embed=embed, view=view)
        except Exception as e:
            print(f"Failed to send dashboard: {e}")

    @app_commands.command(name="redeem-dashboard", description="Show your redeem dashboard.")
    async def user_dashboard(self, interaction: Interaction):
        embed = discord.Embed(
            title="🎁 Product Redeem Dashboard",
            description="Click a button below to redeem your purchased product.",
            color=discord.Color.blurple()
        )
        view = RedeemView()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(CodeRedeem(bot))
