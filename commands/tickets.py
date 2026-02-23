import discord
from discord.ext import commands, tasks
from discord import ui, Interaction
from datetime import datetime, timezone, timedelta
import io

from utils.supabase import get_supabase

# -----------------------------
# CONFIG
# -----------------------------
TICKET_CATEGORY_ID = 1448176697693175970
LOG_CHANNEL_ID = 1449252986911068273
TICKET_PANEL_CHANNEL_ID = 1459670755137818648  # Channel for ticket creation panel

STAFF_ROLE_IDS = {
    1432015464036433970,  # Staff Role
    1449491116822106263,  # Support Team
}

EMBED_COLOR = 0x489BF3

TICKET_AUTO_CLOSE_DAYS = 3

supabase = get_supabase()


def _has_staff_role(member: discord.Member) -> bool:
    return any(r.id in STAFF_ROLE_IDS for r in member.roles)


def _get_opener_id_from_topic(topic: str | None) -> int | None:
    # stored like: "ticket_opener=123"
    if not topic:
        return None
    for part in topic.split():
        if part.startswith("ticket_opener="):
            try:
                return int(part.split("=", 1)[1])
            except Exception:
                return None
    return None


def _get_ticket_id_from_topic(topic: str | None) -> int | None:
    # stored like: "ticket_id=12"
    if not topic:
        return None
    for part in topic.split():
        if part.startswith("ticket_id="):
            try:
                return int(part.split("=", 1)[1])
            except Exception:
                return None
    return None


class TicketReasonSelect(ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="Robux Payment",
                description="Purchase with Robux gamepass",
                emoji="💵",
                value="robux"
            ),
            discord.SelectOption(
                label="Support",
                description="Get help with an issue",
                emoji="🛠️",
                value="support"
            ),
            discord.SelectOption(
                label="Other",
                description="Something else",
                emoji="❓",
                value="other"
            ),
        ]
        super().__init__(
            placeholder="Select a reason...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="ticket_reason_select_v1"
        )

    async def callback(self, interaction: Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return

        reason = self.values[0]
        
        # Create ticket with reason
        channel = await create_or_get_ticket_channel(interaction.guild, interaction.user, reason)
        
        if channel:
            await interaction.response.send_message(
                f"Your ticket has been created: {channel.mention}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "Failed to create ticket. Please contact staff.",
                ephemeral=True
            )


class TicketReasonView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketReasonSelect())


class CloseTicketView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(
        label="Close Ticket",
        style=discord.ButtonStyle.danger,
        custom_id="ticket_close_button_v2"
    )
    async def close_ticket(self, interaction: Interaction, button: ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("This can only be used in a ticket channel.", ephemeral=True)
            return

        opener_id = _get_opener_id_from_topic(channel.topic)
        ticket_id = _get_ticket_id_from_topic(channel.topic)

        is_staff = _has_staff_role(interaction.user)
        is_opener = (opener_id is not None and interaction.user.id == opener_id)

        if not (is_staff or is_opener):
            await interaction.response.send_message("You don’t have permission to close this ticket.", ephemeral=True)
            return

        await interaction.response.send_message("Generating transcript and closing ticket...", ephemeral=True)

        transcript_lines = []
        transcript_lines.append(f"{'='*60}")
        transcript_lines.append(f"TICKET TRANSCRIPT: {channel.name}")
        transcript_lines.append(f"Ticket ID: {ticket_id}")
        transcript_lines.append(f"Closed At: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        transcript_lines.append(f"{'='*60}\n")

        # Fetch all messages (oldest first)
        messages = []
        async for msg in channel.history(limit=None, oldest_first=True):
            messages.append(msg)

        for msg in messages:
            timestamp = msg.created_at.strftime('%Y-%m-%d %H:%M:%S')
            author = f"{msg.author} ({msg.author.id})"
            content = msg.content or "[No text content]"
            
            transcript_lines.append(f"[{timestamp}] {author}")
            transcript_lines.append(f"{content}")
            
            # Include attachment URLs
            if msg.attachments:
                for att in msg.attachments:
                    transcript_lines.append(f"  [Attachment: {att.filename} - {att.url}]")
            
            # Include embed titles
            if msg.embeds:
                for embed in msg.embeds:
                    if embed.title:
                        transcript_lines.append(f"  [Embed: {embed.title}]")
            
            transcript_lines.append("")  # Empty line between messages

        transcript_text = "\n".join(transcript_lines)
        transcript_file = io.BytesIO(transcript_text.encode('utf-8'))
        transcript_file.name = f"transcript-{channel.name}.txt"

        # Update DB (best-effort)
        if ticket_id:
            try:
                supabase.table("tickets").update({
                    "status": "closed",
                    "closed_at": datetime.now(timezone.utc).isoformat(),
                }).eq("id", ticket_id).execute()
            except Exception:
                pass

        # Log close (cache-safe)
        log_ch = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_ch is None:
            try:
                log_ch = await interaction.guild.fetch_channel(LOG_CHANNEL_ID)
            except Exception:
                log_ch = None

        # Resolve opener name from topic (if possible)
        opener_member = None
        if opener_id:
            opener_member = interaction.guild.get_member(opener_id)
            if opener_member is None:
                try:
                    opener_member = await interaction.guild.fetch_member(opener_id)
                except Exception:
                    opener_member = None

        opener_text = (
            f"{opener_member.mention} • **{opener_member}** (`{opener_id}`)"
            if opener_member else f"`{opener_id or 'Unknown'}`"
        )
        closer_text = f"{interaction.user.mention} • **{interaction.user}** (`{interaction.user.id}`)"

        if log_ch:
            embed = discord.Embed(title="Ticket Closed", color=discord.Color(EMBED_COLOR))
            embed.add_field(name="Ticket", value=f"`{channel.name}`", inline=True)
            if ticket_id:
                embed.add_field(name="Ticket #", value=f"`{ticket_id}`", inline=True)
            embed.add_field(name="Messages", value=f"`{len(messages)}`", inline=True)
            embed.add_field(name="Opened By", value=opener_text, inline=False)
            embed.add_field(name="Closed By", value=closer_text, inline=False)
            
            # Reset file position and send with transcript
            transcript_file.seek(0)
            await log_ch.send(
                embed=embed, 
                file=discord.File(transcript_file, filename=f"transcript-{channel.name}.txt")
            )

        try:
            await channel.delete(reason=f"Ticket closed by {interaction.user} ({interaction.user.id})")
        except Exception:
            pass


async def create_or_get_ticket_channel(guild: discord.Guild, member: discord.Member, reason: str = "other") -> discord.TextChannel | None:
    # Fetch category
    category = guild.get_channel(TICKET_CATEGORY_ID)
    if category is None:
        try:
            category = await guild.fetch_channel(TICKET_CATEGORY_ID)
        except Exception:
            category = None

    if not isinstance(category, discord.CategoryChannel):
        return None

    # If the member already has an OPEN ticket in DB, return that channel if it exists
    try:
        existing = (
            supabase.table("tickets")
            .select("id, channel_id")
            .eq("user_id", int(member.id))
            .eq("status", "open")
            .order("id", desc=True)
            .limit(1)
            .execute()
        )
        if existing.data:
            ch_id = existing.data[0].get("channel_id")
            if ch_id:
                ch = guild.get_channel(int(ch_id))
                if isinstance(ch, discord.TextChannel):
                    return ch
    except Exception:
        pass

    # Create a DB ticket row FIRST (this gives us the numeric ticket id)
    ticket_id = None
    try:
        ins = (
            supabase.table("tickets")
            .insert({"user_id": int(member.id), "status": "open"})
            .execute()
        )
        if ins.data and isinstance(ins.data, list):
            ticket_id = ins.data[0].get("id")
    except Exception:
        ticket_id = None

    # Fallback if DB insert failed
    if not ticket_id:
        ticket_id = int(datetime.now(timezone.utc).timestamp())

    # Zero-pad for alphabetical sorting
    channel_name = f"ticket-{int(ticket_id):04d}"

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, manage_messages=True),
    }

    for rid in STAFF_ROLE_IDS:
        role = guild.get_role(rid)
        if role:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

    topic = f"ticket_opener={member.id} ticket_id={ticket_id} reason={reason}"

    ch = await guild.create_text_channel(
        name=channel_name,
        category=category,
        overwrites=overwrites,
        topic=topic,
        reason=f"Ticket opened by {member} ({member.id}) - Reason: {reason}"
    )

    # Save channel_id back to DB (best-effort)
    try:
        supabase.table("tickets").update({"channel_id": int(ch.id)}).eq("id", int(ticket_id)).execute()
    except Exception:
        pass

    # Log open
    log_ch = guild.get_channel(LOG_CHANNEL_ID)
    if log_ch is None:
        try:
            log_ch = await guild.fetch_channel(LOG_CHANNEL_ID)
        except Exception:
            log_ch = None

    reason_display = {
        "robux": "Robux Payment",
        "support": "Support",
        "other": "Other"
    }.get(reason, "Other")

    if log_ch:
        opener_text = f"{member.mention} • **{member}** (`{member.id}`)"
        embed_open = discord.Embed(title="Ticket Opened", color=discord.Color(EMBED_COLOR))
        embed_open.add_field(name="Ticket #", value=f"`{ticket_id}`", inline=True)
        embed_open.add_field(name="Channel", value=ch.mention, inline=True)
        embed_open.add_field(name="Reason", value=f"`{reason_display}`", inline=True)
        embed_open.add_field(name="Opened By", value=opener_text, inline=False)
        await log_ch.send(embed=embed_open)

    if reason == "robux":
        embed = discord.Embed(
            title="Robux Payment",
            description="Thanks for purchasing with Robux!",
            color=discord.Color(EMBED_COLOR),
        )
        embed.add_field(
            name="Gamepasses",
            value=(
                "• [Week - 750 Robux](https://www.roblox.com/game-pass/109857815)\n"
                "• [Month - 1,700 Robux](https://www.roblox.com/game-pass/129890883)\n"
                "• [Lifetime - 4,000 Robux](https://www.roblox.com/game-pass/125899946)"
            ),
            inline=False
        )
        embed.add_field(
            name="Please Provide",
            value=(
                "1. Screenshot proof of your purchase\n"
                "2. Your Roblox username\n"
                "3. Which gamepass you purchased (Week/Month/Lifetime)"
            ),
            inline=False
        )
    elif reason == "support":
        embed = discord.Embed(
            title="Support Request",
            description="We're here to help! Please provide the following information:",
            color=discord.Color(EMBED_COLOR),
        )
        embed.add_field(
            name="Please Provide",
            value=(
                "1. Detailed explanation of your issue\n"
                "2. What executor you are using\n"
                "3. Screenshot of your console/error logs"
            ),
            inline=False
        )
        embed.set_footer(text="The more details you provide, the faster we can help!")
    else:
        embed = discord.Embed(
            title="Support Ticket",
            description="Thanks for opening a ticket! Please describe what you need help with.",
            color=discord.Color(EMBED_COLOR),
        )

    staff_mentions = " ".join(f"<@&{rid}>" for rid in STAFF_ROLE_IDS)
    await ch.send(content=f"{staff_mentions}\n<@{member.id}>", embed=embed, view=CloseTicketView())

    return ch


class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Register persistent views so buttons keep working after restart
        self.bot.add_view(CloseTicketView())
        self.bot.add_view(TicketReasonView())  # Register reason view
        self.auto_close_tickets.start()

    def cog_unload(self):
        self.auto_close_tickets.cancel()

    @discord.app_commands.command(name="ticketpanel", description="Send the ticket creation panel (Admin only)")
    @discord.app_commands.default_permissions(administrator=True)
    async def ticket_panel(self, interaction: Interaction):
        """Send the ticket creation panel to the designated channel"""
        if not interaction.guild:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return

        panel_channel = interaction.guild.get_channel(TICKET_PANEL_CHANNEL_ID)
        if not panel_channel:
            try:
                panel_channel = await interaction.guild.fetch_channel(TICKET_PANEL_CHANNEL_ID)
            except:
                await interaction.response.send_message(
                    f"Could not find ticket panel channel.", ephemeral=True
                )
                return

        embed = discord.Embed(
            title="Create a Ticket",
            description=(
                "Need help or want to purchase with Robux?\n\n"
                "Select a reason below to open a support ticket."
            ),
            color=discord.Color(EMBED_COLOR)
        )
        embed.add_field(
            name="💵 Robux Payment",
            value="Purchase a subscription using Roblox gamepasses",
            inline=False
        )
        embed.add_field(
            name="🛠️ Support",
            value="Get help with an issue or bug",
            inline=False
        )
        embed.add_field(
            name="❓ Other",
            value="Any other questions or inquiries",
            inline=False
        )
        embed.set_footer(text="Select a reason from the dropdown below")

        await panel_channel.send(embed=embed, view=TicketReasonView())
        await interaction.response.send_message(
            f"Ticket panel sent to {panel_channel.mention}!", ephemeral=True
        )

    @tasks.loop(hours=1)
    async def auto_close_tickets(self):
        """Automatically close tickets inactive for X days"""
        try:
            guild = self.bot.get_guild(1345153296360542271)
            if not guild:
                return

            cutoff = datetime.now(timezone.utc) - timedelta(days=TICKET_AUTO_CLOSE_DAYS)

            # Get open tickets
            open_tickets = supabase.table("tickets").select(
                "id, channel_id, user_id, last_activity"
            ).eq("status", "open").execute()

            if not open_tickets.data:
                return

            for ticket in open_tickets.data:
                channel_id = ticket.get("channel_id")
                if not channel_id:
                    continue

                channel = guild.get_channel(int(channel_id))
                if not isinstance(channel, discord.TextChannel):
                    # Channel deleted, mark ticket as closed
                    supabase.table("tickets").update({
                        "status": "closed",
                        "closed_at": datetime.now(timezone.utc).isoformat()
                    }).eq("id", ticket["id"]).execute()
                    continue

                # Check last message time
                last_activity = None
                async for msg in channel.history(limit=1):
                    last_activity = msg.created_at

                if last_activity and last_activity.replace(tzinfo=timezone.utc) < cutoff:
                    # Send warning then close
                    try:
                        await channel.send(
                            f"This ticket has been inactive for {TICKET_AUTO_CLOSE_DAYS} days and will be closed automatically."
                        )
                    except:
                        pass

                    # Generate transcript
                    transcript_lines = []
                    transcript_lines.append(f"{'='*60}")
                    transcript_lines.append(f"TICKET TRANSCRIPT: {channel.name} (AUTO-CLOSED)")
                    transcript_lines.append(f"Closed At: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
                    transcript_lines.append(f"Reason: Inactive for {TICKET_AUTO_CLOSE_DAYS} days")
                    transcript_lines.append(f"{'='*60}\n")

                    messages = []
                    async for msg in channel.history(limit=None, oldest_first=True):
                        messages.append(msg)

                    for msg in messages:
                        timestamp = msg.created_at.strftime('%Y-%m-%d %H:%M:%S')
                        author = f"{msg.author} ({msg.author.id})"
                        content = msg.content or "[No text content]"
                        transcript_lines.append(f"[{timestamp}] {author}")
                        transcript_lines.append(f"{content}")
                        if msg.attachments:
                            for att in msg.attachments:
                                transcript_lines.append(f"  [Attachment: {att.filename}]")
                        transcript_lines.append("")

                    transcript_text = "\n".join(transcript_lines)
                    transcript_file = io.BytesIO(transcript_text.encode('utf-8'))

                    # Update DB
                    supabase.table("tickets").update({
                        "status": "closed",
                        "closed_at": datetime.now(timezone.utc).isoformat()
                    }).eq("id", ticket["id"]).execute()

                    # Log
                    log_ch = guild.get_channel(LOG_CHANNEL_ID)
                    if log_ch:
                        user_id = ticket.get("user_id")
                        embed = discord.Embed(
                            title="Ticket Auto-Closed",
                            description=f"Inactive for {TICKET_AUTO_CLOSE_DAYS} days",
                            color=discord.Color.orange()
                        )
                        embed.add_field(name="Ticket", value=f"`{channel.name}`", inline=True)
                        embed.add_field(name="Messages", value=f"`{len(messages)}`", inline=True)
                        embed.add_field(name="Opened By", value=f"<@{user_id}>", inline=False)

                        transcript_file.seek(0)
                        await log_ch.send(
                            embed=embed,
                            file=discord.File(transcript_file, filename=f"transcript-{channel.name}.txt")
                        )

                    # Delete channel
                    try:
                        await channel.delete(reason="Auto-closed due to inactivity")
                    except:
                        pass

        except Exception as e:
            print(f"[AUTO-CLOSE ERROR] {e}")

    @auto_close_tickets.before_loop
    async def before_auto_close(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))
    print("✅ Loaded cog: tickets")
