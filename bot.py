import os
import json
import threading
from datetime import timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

import discord
from discord.ext import commands
from discord import app_commands


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get("PORT", 8000))

DATA_FILE = "data.json"


# =========================================================
# FAKE WEB SERVER FOR RENDER
# =========================================================

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Rec Buddy is alive!")

    def log_message(self, format, *args):
        # Don't spam Render logs with HTTP requests
        return


def run_web():
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()


threading.Thread(target=run_web, daemon=True).start()


# =========================================================
# INTENTS
# =========================================================

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True


bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# =========================================================
# DATA
# =========================================================

warnings_data = {}
log_channels = {}


def load_data():
    global warnings_data, log_channels

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        warnings_data = data.get("warnings", {})
        log_channels = data.get("log_channels", {})

    except FileNotFoundError:
        warnings_data = {}
        log_channels = {}

    except Exception as e:
        print(f"Failed to load data: {e}")


def save_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "warnings": warnings_data,
                "log_channels": log_channels
            },
            f,
            indent=2
        )


# =========================================================
# HELPERS
# =========================================================

def get_log_channel(guild: discord.Guild):
    channel_id = log_channels.get(str(guild.id))

    if not channel_id:
        return None

    return guild.get_channel(int(channel_id))


async def send_log(
    guild: discord.Guild,
    title: str,
    description: str,
    color: discord.Color = discord.Color.blurple()
):
    channel = get_log_channel(guild)

    if not channel:
        return

    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=discord.utils.utcnow()
    )

    try:
        await channel.send(embed=embed)
    except discord.Forbidden:
        pass
    except discord.HTTPException:
        pass


def can_moderate(
    moderator: discord.Member,
    target: discord.Member
):
    if target == moderator:
        return False, "❌ You can't moderate yourself."

    if target == moderator.guild.owner:
        return False, "❌ You can't moderate the server owner."

    if target.top_role >= moderator.top_role:
        return False, "❌ That member has an equal or higher role than you."

    if target.top_role >= moderator.guild.me.top_role:
        return False, "❌ My highest role must be above that member's highest role."

    return True, None


# =========================================================
# ERROR HANDLING
# =========================================================

async def handle_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):

    if isinstance(error, app_commands.MissingPermissions):
        message = (
            "❌ You don't have the required Discord permission "
            "to use this command."
        )

    elif isinstance(error, app_commands.BotMissingPermissions):
        message = (
            "❌ I don't have the required Discord permission "
            "to perform this action."
        )

    elif isinstance(error, app_commands.CheckFailure):
        message = "❌ You can't use this command."

    else:
        print(f"Command error: {repr(error)}")
        message = "❌ Something went wrong while executing the command."

    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(
            message,
            ephemeral=True
        )


# =========================================================
# /PURGE
# =========================================================

@bot.tree.command(
    name="purge",
    description="Delete recent messages from this channel."
)
@app_commands.describe(
    amount="Number of messages to delete (1-100)"
)
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.checks.bot_has_permissions(manage_messages=True)
async def purge(
    interaction: discord.Interaction,
    amount: app_commands.Range[int, 1, 100]
):
    if not isinstance(
        interaction.channel,
        discord.TextChannel
    ):
        return await interaction.response.send_message(
            "❌ This command can only be used in a text channel.",
            ephemeral=True
        )

    await interaction.response.defer(ephemeral=True)

    try:
        deleted = await interaction.channel.purge(
            limit=amount
        )

        await interaction.followup.send(
            f"🧹 Deleted **{len(deleted)}** messages.",
            ephemeral=True
        )

        await send_log(
            interaction.guild,
            "🧹 Messages Purged",
            (
                f"**Moderator:** {interaction.user.mention}\n"
                f"**Channel:** {interaction.channel.mention}\n"
                f"**Requested:** {amount}\n"
                f"**Deleted:** {len(deleted)}"
            ),
            discord.Color.orange()
        )

    except discord.Forbidden:
        await interaction.followup.send(
            "❌ I don't have permission to delete messages here.",
            ephemeral=True
        )


# =========================================================
# /LOCK
# =========================================================

@bot.tree.command(
    name="lock",
    description="Lock a channel so regular members cannot send messages."
)
@app_commands.describe(
    channel="The channel to lock."
)
@app_commands.checks.has_permissions(manage_channels=True)
@app_commands.checks.bot_has_permissions(manage_channels=True)
async def lock(
    interaction: discord.Interaction,
    channel: discord.TextChannel | None = None
):
    channel = channel or interaction.channel

    if not isinstance(channel, discord.TextChannel):
        return await interaction.response.send_message(
            "❌ Invalid channel.",
            ephemeral=True
        )

    try:
        await channel.set_permissions(
            interaction.guild.default_role,
            send_messages=False
        )

        await interaction.response.send_message(
            f"🔒 Locked {channel.mention}.",
            ephemeral=True
        )

        await send_log(
            interaction.guild,
            "🔒 Channel Locked",
            (
                f"**Moderator:** {interaction.user.mention}\n"
                f"**Channel:** {channel.mention}"
            ),
            discord.Color.red()
        )

    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ I can't modify that channel's permissions.",
            ephemeral=True
        )


# =========================================================
# /UNLOCK
# =========================================================

@bot.tree.command(
    name="unlock",
    description="Unlock a channel so regular members can send messages again."
)
@app_commands.describe(
    channel="The channel to unlock."
)
@app_commands.checks.has_permissions(manage_channels=True)
@app_commands.checks.bot_has_permissions(manage_channels=True)
async def unlock(
    interaction: discord.Interaction,
    channel: discord.TextChannel | None = None
):
    channel = channel or interaction.channel

    if not isinstance(channel, discord.TextChannel):
        return await interaction.response.send_message(
            "❌ Invalid channel.",
            ephemeral=True
        )

    try:
        await channel.set_permissions(
            interaction.guild.default_role,
            send_messages=None
        )

        await interaction.response.send_message(
            f"🔓 Unlocked {channel.mention}.",
            ephemeral=True
        )

        await send_log(
            interaction.guild,
            "🔓 Channel Unlocked",
            (
                f"**Moderator:** {interaction.user.mention}\n"
                f"**Channel:** {channel.mention}"
            ),
            discord.Color.green()
        )

    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ I can't modify that channel's permissions.",
            ephemeral=True
        )


# =========================================================
# /SLOWMODE
# =========================================================

@bot.tree.command(
    name="slowmode",
    description="Set the slowmode delay for a channel."
)
@app_commands.describe(
    seconds="Slowmode delay in seconds (0-21600).",
    channel="The channel to configure."
)
@app_commands.checks.has_permissions(manage_channels=True)
@app_commands.checks.bot_has_permissions(manage_channels=True)
async def slowmode(
    interaction: discord.Interaction,
    seconds: app_commands.Range[int, 0, 21600],
    channel: discord.TextChannel | None = None
):
    channel = channel or interaction.channel

    if not isinstance(channel, discord.TextChannel):
        return await interaction.response.send_message(
            "❌ Invalid channel.",
            ephemeral=True
        )

    try:
        await channel.edit(
            slowmode_delay=seconds
        )

        if seconds == 0:
            text = "disabled slowmode"
        else:
            text = f"set slowmode to **{seconds} seconds**"

        await interaction.response.send_message(
            f"🐌 {text} in {channel.mention}.",
            ephemeral=True
        )

        await send_log(
            interaction.guild,
            "🐌 Slowmode Changed",
            (
                f"**Moderator:** {interaction.user.mention}\n"
                f"**Channel:** {channel.mention}\n"
                f"**Delay:** {seconds} seconds"
            ),
            discord.Color.blurple()
        )

    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ I can't modify that channel.",
            ephemeral=True
        )


# =========================================================
# /KICK
# =========================================================

@bot.tree.command(
    name="kick",
    description="Kick a member from the server."
)
@app_commands.describe(
    member="The member to kick.",
    reason="Reason for the kick."
)
@app_commands.checks.has_permissions(kick_members=True)
@app_commands.checks.bot_has_permissions(kick_members=True)
async def kick(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str | None = None
):
    allowed, error = can_moderate(
        interaction.user,
        member
    )

    if not allowed:
        return await interaction.response.send_message(
            error,
            ephemeral=True
        )

    reason = reason or "No reason provided."

    try:
        await member.kick(reason=reason)

        await interaction.response.send_message(
            f"👢 Kicked **{member}**.",
            ephemeral=True
        )

        await send_log(
            interaction.guild,
            "👢 Member Kicked",
            (
                f"**Moderator:** {interaction.user.mention}\n"
                f"**Member:** {member.mention}\n"
                f"**Reason:** {reason}"
            ),
            discord.Color.orange()
        )

    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ I can't kick that member.",
            ephemeral=True
        )


# =========================================================
# /BAN
# =========================================================

@bot.tree.command(
    name="ban",
    description="Ban a member from the server."
)
@app_commands.describe(
    member="The member to ban.",
    reason="Reason for the ban."
)
@app_commands.checks.has_permissions(ban_members=True)
@app_commands.checks.bot_has_permissions(ban_members=True)
async def ban(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str | None = None
):
    allowed, error = can_moderate(
        interaction.user,
        member
    )

    if not allowed:
        return await interaction.response.send_message(
            error,
            ephemeral=True
        )

    reason = reason or "No reason provided."

    try:
        await member.ban(
            reason=reason,
            delete_message_seconds=0
        )

        await interaction.response.send_message(
            f"🔨 Banned **{member}**.",
            ephemeral=True
        )

        await send_log(
            interaction.guild,
            "🔨 Member Banned",
            (
                f"**Moderator:** {interaction.user.mention}\n"
                f"**Member:** {member.mention}\n"
                f"**Reason:** {reason}"
            ),
            discord.Color.red()
        )

    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ I can't ban that member.",
            ephemeral=True
        )


# =========================================================
# /UNBAN
# =========================================================

@bot.tree.command(
    name="unban",
    description="Unban a user using their Discord user ID."
)
@app_commands.describe(
    user_id="Discord user ID of the banned user."
)
@app_commands.checks.has_permissions(ban_members=True)
@app_commands.checks.bot_has_permissions(ban_members=True)
async def unban(
    interaction: discord.Interaction,
    user_id: str
):
    try:
        user = await bot.fetch_user(int(user_id))

    except (ValueError, discord.NotFound):
        return await interaction.response.send_message(
            "❌ Invalid Discord user ID.",
            ephemeral=True
        )

    try:
        await interaction.guild.unban(user)

        await interaction.response.send_message(
            f"🔓 Unbanned **{user}**.",
            ephemeral=True
        )

        await send_log(
            interaction.guild,
            "🔓 Member Unbanned",
            (
                f"**Moderator:** {interaction.user.mention}\n"
                f"**User:** {user.mention}\n"
                f"**ID:** `{user.id}`"
            ),
            discord.Color.green()
        )

    except discord.NotFound:
        await interaction.response.send_message(
            "❌ That user isn't banned.",
            ephemeral=True
        )

    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ I can't unban users.",
            ephemeral=True
        )


# =========================================================
# /TIMEOUT
# =========================================================

@bot.tree.command(
    name="timeout",
    description="Timeout a member."
)
@app_commands.describe(
    member="The member to timeout.",
    minutes="Timeout duration in minutes (1-40320).",
    reason="Reason for the timeout."
)
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.checks.bot_has_permissions(moderate_members=True)
async def timeout(
    interaction: discord.Interaction,
    member: discord.Member,
    minutes: app_commands.Range[int, 1, 40320],
    reason: str | None = None
):
    allowed, error = can_moderate(
        interaction.user,
        member
    )

    if not allowed:
        return await interaction.response.send_message(
            error,
            ephemeral=True
        )

    reason = reason or "No reason provided."

    try:
        await member.timeout(
            timedelta(minutes=minutes),
            reason=reason
        )

        await interaction.response.send_message(
            f"⏱️ Timed out **{member}** for **{minutes} minutes**.",
            ephemeral=True
        )

        await send_log(
            interaction.guild,
            "⏱️ Member Timed Out",
            (
                f"**Moderator:** {interaction.user.mention}\n"
                f"**Member:** {member.mention}\n"
                f"**Duration:** {minutes} minutes\n"
                f"**Reason:** {reason}"
            ),
            discord.Color.orange()
        )

    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ I can't timeout that member.",
            ephemeral=True
        )


# =========================================================
# /UNTIMEOUT
# =========================================================

@bot.tree.command(
    name="untimeout",
    description="Remove a member's timeout."
)
@app_commands.describe(
    member="The member to untimeout."
)
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.checks.bot_has_permissions(moderate_members=True)
async def untimeout(
    interaction: discord.Interaction,
    member: discord.Member
):
    allowed, error = can_moderate(
        interaction.user,
        member
    )

    if not allowed:
        return await interaction.response.send_message(
            error,
            ephemeral=True
        )

    try:
        await member.timeout(
            None,
            reason=f"Timeout removed by {interaction.user}"
        )

        await interaction.response.send_message(
            f"🔓 Removed timeout from **{member}**.",
            ephemeral=True
        )

        await send_log(
            interaction.guild,
            "🔓 Timeout Removed",
            (
                f"**Moderator:** {interaction.user.mention}\n"
                f"**Member:** {member.mention}"
            ),
            discord.Color.green()
        )

    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ I can't remove that member's timeout.",
            ephemeral=True
        )


# =========================================================
# /WARN
# =========================================================

@bot.tree.command(
    name="warn",
    description="Warn a member."
)
@app_commands.describe(
    member="The member to warn.",
    reason="Reason for the warning."
)
@app_commands.checks.has_permissions(moderate_members=True)
async def warn(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str | None = None
):
    allowed, error = can_moderate(
        interaction.user,
        member
    )

    if not allowed:
        return await interaction.response.send_message(
            error,
            ephemeral=True
        )

    reason = reason or "No reason provided."

    guild_id = str(interaction.guild.id)
    user_id = str(member.id)

    warnings_data.setdefault(guild_id, {})
    warnings_data[guild_id].setdefault(user_id, [])

    warnings_data[guild_id][user_id].append({
        "reason": reason,
        "moderator": interaction.user.id,
        "timestamp": discord.utils.utcnow().isoformat()
    })

    save_data()

    count = len(
        warnings_data[guild_id][user_id]
    )

    await interaction.response.send_message(
        f"⚠️ Warned **{member}**.\n"
        f"Warnings: **{count}**",
        ephemeral=True
    )

    await send_log(
        interaction.guild,
        "⚠️ Member Warned",
        (
            f"**Moderator:** {interaction.user.mention}\n"
            f"**Member:** {member.mention}\n"
            f"**Warnings:** {count}\n"
            f"**Reason:** {reason}"
        ),
        discord.Color.yellow()
    )


# =========================================================
# /WARNINGS
# =========================================================

@bot.tree.command(
    name="warnings",
    description="View a member's warnings."
)
@app_commands.describe(
    member="The member whose warnings you want to view."
)
@app_commands.checks.has_permissions(moderate_members=True)
async def warnings(
    interaction: discord.Interaction,
    member: discord.Member
):
    guild_id = str(interaction.guild.id)
    user_id = str(member.id)

    user_warnings = warnings_data.get(
        guild_id,
        {}
    ).get(
        user_id,
        []
    )

    if not user_warnings:
        return await interaction.response.send_message(
            f"✅ **{member}** has no warnings.",
            ephemeral=True
        )

    embed = discord.Embed(
        title=f"⚠️ Warnings — {member}",
        color=discord.Color.yellow()
    )

    for index, warning in enumerate(
        user_warnings,
        start=1
    ):
        moderator = interaction.guild.get_member(
            warning["moderator"]
        )

        moderator_text = (
            moderator.mention
            if moderator
            else f"`{warning['moderator']}`"
        )

        embed.add_field(
            name=f"Warning #{index}",
            value=(
                f"**Reason:** {warning['reason']}\n"
                f"**Moderator:** {moderator_text}"
            ),
            inline=False
        )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )


# =========================================================
# /CLEARWARNINGS
# =========================================================

@bot.tree.command(
    name="clearwarnings",
    description="Clear all warnings from a member."
)
@app_commands.describe(
    member="The member whose warnings should be cleared."
)
@app_commands.checks.has_permissions(moderate_members=True)
async def clearwarnings(
    interaction: discord.Interaction,
    member: discord.Member
):
    guild_id = str(interaction.guild.id)
    user_id = str(member.id)

    if guild_id in warnings_data:
        warnings_data[guild_id].pop(
            user_id,
            None
        )

    save_data()

    await interaction.response.send_message(
        f"🧹 Cleared warnings for **{member}**.",
        ephemeral=True
    )

    await send_log(
        interaction.guild,
        "🧹 Warnings Cleared",
        (
            f"**Moderator:** {interaction.user.mention}\n"
            f"**Member:** {member.mention}"
        ),
        discord.Color.green()
    )


# =========================================================
# /USERINFO
# =========================================================

@bot.tree.command(
    name="userinfo",
    description="Show information about a Discord member."
)
@app_commands.describe(
    member="The member to inspect."
)
async def userinfo(
    interaction: discord.Interaction,
    member: discord.Member | None = None
):
    member = member or interaction.user

    embed = discord.Embed(
        title=f"👤 {member}",
        color=member.color
        if member.color != discord.Color.default()
        else discord.Color.blurple()
    )

    embed.set_thumbnail(
        url=member.display_avatar.url
    )

    embed.add_field(
        name="User ID",
        value=f"`{member.id}`",
        inline=False
    )

    embed.add_field(
        name="Joined Discord",
        value=discord.utils.format_dt(
            member.created_at,
            style="F"
        ),
        inline=True
    )

    if member.joined_at:
        embed.add_field(
            name="Joined Server",
            value=discord.utils.format_dt(
                member.joined_at,
                style="F"
            ),
            inline=True
        )

    embed.add_field(
        name="Top Role",
        value=member.top_role.mention,
        inline=True
    )

    embed.add_field(
        name="Bot",
        value="Yes" if member.bot else "No",
        inline=True
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )


# =========================================================
# /SERVERINFO
# =========================================================

@bot.tree.command(
    name="serverinfo",
    description="Show information about this Discord server."
)
async def serverinfo(
    interaction: discord.Interaction
):
    guild = interaction.guild

    embed = discord.Embed(
        title=f"📊 {guild.name}",
        color=discord.Color.blurple()
    )

    if guild.icon:
        embed.set_thumbnail(
            url=guild.icon.url
        )

    embed.add_field(
        name="Server ID",
        value=f"`{guild.id}`",
        inline=False
    )

    embed.add_field(
        name="Members",
        value=str(guild.member_count),
        inline=True
    )

    embed.add_field(
        name="Channels",
        value=str(len(guild.channels)),
        inline=True
    )

    embed.add_field(
        name="Roles",
        value=str(len(guild.roles)),
        inline=True
    )

    embed.add_field(
        name="Owner",
        value=guild.owner.mention
        if guild.owner
        else "Unknown",
        inline=True
    )

    embed.add_field(
        name="Created",
        value=discord.utils.format_dt(
            guild.created_at,
            style="F"
        ),
        inline=False
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )


# =========================================================
# /SETLOGCHANNEL
# =========================================================

@bot.tree.command(
    name="setlogchannel",
    description="Set the channel where Rec Buddy sends moderation logs."
)
@app_commands.describe(
    channel="The moderation log channel."
)
@app_commands.checks.has_permissions(manage_guild=True)
async def setlogchannel(
    interaction: discord.Interaction,
    channel: discord.TextChannel
):
    log_channels[str(interaction.guild.id)] = channel.id

    save_data()

    await interaction.response.send_message(
        f"📋 Moderation logs will now be sent to {channel.mention}.",
        ephemeral=True
    )


# =========================================================
# /LOGCHANNEL
# =========================================================

@bot.tree.command(
    name="logchannel",
    description="Show the currently configured moderation log channel."
)
async def logchannel(
    interaction: discord.Interaction
):
    channel = get_log_channel(
        interaction.guild
    )

    if not channel:
        return await interaction.response.send_message(
            "📋 No moderation log channel is configured.",
            ephemeral=True
        )

    await interaction.response.send_message(
        f"📋 Current log channel: {channel.mention}",
        ephemeral=True
    )


# =========================================================
# /HELP
# =========================================================

@bot.tree.command(
    name="help",
    description="Show Rec Buddy's moderation commands."
)
async def help_command(
    interaction: discord.Interaction
):
    embed = discord.Embed(
        title="🤖 Rec Buddy",
        description="Rex Room's moderation bot.",
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="🧹 Moderation",
        value=(
            "`/purge` — Delete messages\n"
            "`/warn` — Warn a member\n"
            "`/warnings` — View warnings\n"
            "`/clearwarnings` — Clear warnings\n"
            "`/kick` — Kick a member\n"
            "`/ban` — Ban a member\n"
            "`/unban` — Unban a user\n"
            "`/timeout` — Timeout a member\n"
            "`/untimeout` — Remove a timeout"
        ),
        inline=False
    )

    embed.add_field(
        name="🔒 Channel Management",
        value=(
            "`/lock` — Lock a channel\n"
            "`/unlock` — Unlock a channel\n"
            "`/slowmode` — Configure slowmode"
        ),
        inline=False
    )

    embed.add_field(
        name="📊 Information",
        value=(
            "`/userinfo` — View member information\n"
            "`/serverinfo` — View server information"
        ),
        inline=False
    )

    embed.add_field(
        name="📋 Logging",
        value=(
            "`/setlogchannel` — Configure moderation logs\n"
            "`/logchannel` — View the current log channel"
        ),
        inline=False
    )

    embed.set_footer(
        text="Rec Buddy • Rex Room"
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )


# =========================================================
# GLOBAL ERROR HANDLER
# =========================================================

@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):
    await handle_app_command_error(
        interaction,
        error
    )


# =========================================================
# READY
# =========================================================

@bot.event
async def on_ready():
    load_data()

    try:
        synced = await bot.tree.sync()

        print(
            f"✅ Rec Buddy is online as {bot.user}"
        )

        print(
            f"🔧 Synced {len(synced)} slash commands"
        )

        print(
            f"🌐 Web server running on port {PORT}"
        )

    except Exception as e:
        print(
            f"❌ Failed to sync commands: {repr(e)}"
        )


# =========================================================
# START
# =========================================================

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN environment variable is missing!"
    )

bot.run(BOT_TOKEN)
