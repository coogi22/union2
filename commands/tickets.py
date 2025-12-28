import discord
from discord.ext import commands
from discord import ui, Interaction
from datetime import datetime, timezone
import io

from utils.supabase import get_supabase

# -----------------------------
# CONFIG
# -----------------------------
TICKET_CATEGORY_ID = 1448176697693175970
LOG_CHANNEL_ID = 1449252986911068273

STAFF_ROLE_IDS = {
    1432015464036433970,
    1449491116822106263,
}

EMBED_COLOR = 0x489BF3

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


async def create_or_get_ticket_channel(guild: discord.Guild, member: discord.Member) -> discord.TextChannel | None:
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
            .eq("opener_id", int(member.id))
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
            .insert({"opener_id": int(member.id), "status": "open"})
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

    topic = f"ticket_opener={member.id} ticket_id={ticket_id}"

    ch = await guild.create_text_channel(
        name=channel_name,
        category=category,
        overwrites=overwrites,
        topic=topic,
        reason=f"Ticket opened by {member} ({member.id})"
    )

    # Save channel_id back to DB (best-effort)
    try:
        supabase.table("tickets").update({"channel_id": int(ch.id)}).eq("id", int(ticket_id)).execute()
    except Exception:
        pass

    # ✅ NEW: Log open (cache-safe)
    log_ch = guild.get_channel(LOG_CHANNEL_ID)
    if log_ch is None:
        try:
            log_ch = await guild.fetch_channel(LOG_CHANNEL_ID)
        except Exception:
            log_ch = None

    if log_ch:
        opener_text = f"{member.mention} • **{member}** (`{member.id}`)"
        embed_open = discord.Embed(title="🎫 Ticket Opened", color=discord.Color(EMBED_COLOR))
        embed_open.add_field(name="Ticket #", value=f"`{ticket_id}`", inline=True)
        embed_open.add_field(name="Channel", value=ch.mention, inline=True)
        embed_open.add_field(name="Opened By", value=opener_text, inline=False)
        await log_ch.send(embed=embed_open)

    # Initial message
    embed = discord.Embed(
        title="🎫 Support Ticket",
        description=(
            "Thanks for opening a ticket.\n\n"
            "**To get whitelisted:**\n"
            "• Order ID from SellAuth\n"
            "• What you purchased / duration\n"
            "• Roblox username if purchasing with robux\n"
            "• Any extra info staff asks for\n"
        ),
        color=discord.Color(EMBED_COLOR),
    )

    staff_mentions = " ".join(f"<@&{rid}>" for rid in STAFF_ROLE_IDS)
    await ch.send(content=f"{staff_mentions}\n<@{member.id}>", embed=embed, view=CloseTicketView())

    return ch


class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Register persistent view so old buttons keep working after restart
        self.bot.add_view(CloseTicketView())


async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))
    print("✅ Loaded cog: tickets")
