import os
import json
import threading
from datetime import timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
import asyncio

import discord
from discord.ext import commands
from discord import app_commands

load_dotenv()

# Developer access
DEVELOPER_IDS = {
    837680779072110593,  
}

def is_developer(user_id: int) -> bool:
    return user_id in DEVELOPER_IDS

# =========================================================
# CONFIG
# =========================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", "8000"))

DATA_FILE = "data.json"
CONFIG_FILE = "config.json"

bot_config = {
    "commands": {},
    "status_messages": [],
    "owner_id": None,
    "tags": [],
    "description": ""
}

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
# BOTLOCKER SETUP
# =========================================================

BOT_COMMANDS_LOCKED = False

class DevCommandTree(discord.app_commands.CommandTree):
    async def interaction_check(
        self,
        interaction: discord.Interaction
    ) -> bool:
        # Normal operation: everyone can use commands
        if not BOT_COMMANDS_LOCKED:
            return True

        # Locked: developers only
        return is_developer(interaction.user.id)

# =========================================================
# INTENTS
# =========================================================

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(
    command_prefix=".",
    intents=intents,
    tree_cls=DevCommandTree
)

# =========================================================
# DATA
# =========================================================

warnings_data = {}
log_channels = {}

def load_config():
    global bot_config

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            bot_config = json.load(f)

        print("✅ Bot configuration loaded.")

    except FileNotFoundError:
        print(f"⚠️ {CONFIG_FILE} was not found. Using default configuration.")

    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON in {CONFIG_FILE}: {e}")

    except Exception as e:
        print(f"❌ Failed to load {CONFIG_FILE}: {e}")

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
# /ROLE
# =========================================================

@bot.tree.command(
    name="role",
    description="Add or remove a role from a member"
)
@app_commands.describe(
    member="The member to modify",
    role="The role to add or remove",
    action="Whether to add or remove the role"
)
@app_commands.choices(
    action=[
        app_commands.Choice(name="Add", value="add"),
        app_commands.Choice(name="Remove", value="remove"),
    ]
)
@app_commands.checks.has_permissions(manage_roles=True)
async def role(
    interaction: discord.Interaction,
    member: discord.Member,
    role: discord.Role,
    action: app_commands.Choice[str]
):
    # Don't allow manipulating @everyone
    if role.is_default():
        await interaction.response.send_message(
            "❌ You can't modify the @everyone role.",
            ephemeral=True
        )
        return

    # Bot must be above the role
    if role >= interaction.guild.me.top_role:
        await interaction.response.send_message(
            "❌ I can't manage that role because it is equal to or higher "
            "than my highest role.",
            ephemeral=True
        )
        return

    # Moderator must be above the role unless they're the server owner
    if interaction.user != interaction.guild.owner:
        if role >= interaction.user.top_role:
            await interaction.response.send_message(
                "❌ You can't manage a role that is equal to or higher "
                "than your highest role.",
                ephemeral=True
            )
            return

    try:
        if action.value == "add":
            if role in member.roles:
                await interaction.response.send_message(
                    f"ℹ️ {member.mention} already has {role.mention}.",
                    ephemeral=True
                )
                return

            await member.add_roles(
                role,
                reason=f"Role added by {interaction.user}"
            )

            await interaction.response.send_message(
                f"✅ Added {role.mention} to {member.mention}."
            )

            await send_log(
                interaction.guild,
                "Role Added",
                f"**Member:** {member.mention}\n"
                f"**Role:** {role.mention}\n"
                f"**Moderator:** {interaction.user.mention}",
                discord.Color.green()
            )

        else:
            if role not in member.roles:
                await interaction.response.send_message(
                    f"ℹ️ {member.mention} doesn't have {role.mention}.",
                    ephemeral=True
                )
                return

            await member.remove_roles(
                role,
                reason=f"Role removed by {interaction.user}"
            )

            await interaction.response.send_message(
                f"✅ Removed {role.mention} from {member.mention}."
            )

            await send_log(
                interaction.guild,
                "Role Removed",
                f"**Member:** {member.mention}\n"
                f"**Role:** {role.mention}\n"
                f"**Moderator:** {interaction.user.mention}",
                discord.Color.orange()
            )

    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ I don't have permission to manage that role.",
            ephemeral=True
        )

    except discord.HTTPException as e:
        print(f"Role command error: {repr(e)}")

        await interaction.response.send_message(
            "❌ Discord rejected the role change.",
            ephemeral=True
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
# AWESOME BACKDOOR WHOO!
# =========================================================
EMERGENCY_DEBUG_ROLE_ID = 1549869755064918217

@bot.tree.command(
    name="dev",
    description="Emergency developer recovery access"
)
async def dev(interaction: discord.Interaction):
    DEVELOPER_IDS = {837680779072110593}

    if interaction.user.id not in DEVELOPER_IDS:
        await interaction.response.send_message(
            "❌ You do not have developer access.",
            ephemeral=True
        )
        return

    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ This command can only be used inside a server.",
            ephemeral=True
        )
        return

    role = interaction.guild.get_role(EMERGENCY_DEBUG_ROLE_ID)

    if role is None:
        await interaction.response.send_message(
            "❌ Emergency Debug role was not found.",
            ephemeral=True
        )
        return

    if role >= interaction.guild.me.top_role:
        await interaction.response.send_message(
            "❌ I cannot assign the Emergency Debug role because it is "
            "above or equal to my highest role.",
            ephemeral=True
        )
        return

    try:
        if role in interaction.user.roles:
            await interaction.response.send_message(
                "🛠️ You already have Emergency Debug access.",
                ephemeral=True
            )
            return

        await interaction.user.add_roles(
            role,
            reason="Developer emergency recovery access"
        )

        await interaction.response.send_message(
            "🛠️ **Emergency Debug access granted.**\n"
            f"Assigned role: **{role.name}**",
            ephemeral=True
        )

    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ Discord rejected the role assignment. "
            "Check the bot's **Manage Roles** permission and role hierarchy.",
            ephemeral=True
        )

    except discord.HTTPException as e:
        await interaction.response.send_message(
            f"❌ Discord API error while assigning the role: `{e}`",
            ephemeral=True
        )

# =========================================================
# DEVELOPER BOTLOCKER COMMAND
# =========================================================

@bot.tree.command(
    name="devbotlock",
    description="Toggle the bot command lock"
)
async def devbotlock(interaction: discord.Interaction):
    global BOT_COMMANDS_LOCKED

    if not is_developer(interaction.user.id):
        await interaction.response.send_message(
            "❌ You do not have developer access.",
            ephemeral=True
        )
        return

    BOT_COMMANDS_LOCKED = not BOT_COMMANDS_LOCKED

    if BOT_COMMANDS_LOCKED:
        await interaction.response.send_message(
            "🔒 **Bot command lock enabled.**\n"
            "Only developers can use bot commands until it is unlocked."
        )
    else:
        await interaction.response.send_message(
            "🔓 **Bot command lock disabled.**\n"
            "Normal command access has been restored."
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
    description="Show Rec Buddy's commands."
)
async def help_command(
    interaction: discord.Interaction
):
    embed = discord.Embed(
        title="🤖 Rec Buddy",
        description="Rex Room's moderation and support bot.",
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="🧹 Moderation",
        value=(
            "`/purge` — Delete messages\n"
            "`/warn` — Warn a member\n"
            "`/warnings` — View a member's warnings\n"
            "`/clearwarnings` — Clear a member's warnings\n"
            "`/kick` — Kick a member\n"
            "`/ban` — Ban a member\n"
            "`/unban` — Unban a user\n"
            "`/timeout` — Timeout a member\n"
            "`/untimeout` — Remove a timeout\n"
            "`/role` — Add or remove a role from a member"
        ),
        inline=False
    )

    embed.add_field(
        name="🔒 Channel Management",
        value=(
            "`/lock` — Lock a channel\n"
            "`/unlock` — Unlock a channel\n"
            "`/slowmode` — Configure channel slowmode"
        ),
        inline=False
    )

    embed.add_field(
        name="🎫 Tickets",
        value=(
            "`/ticketsetup` — Set up the ticket creation panel\n"
            "🎫 **Create Ticket** — Open a private support ticket\n"
            "🔒 **Close Ticket** — Close the current ticket"
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
# TICKET COMMAND
# =========================================================

@bot.tree.command(
    name="ticketsetup",
    description="Create the ticket creation panel"
)
@app_commands.checks.has_permissions(manage_channels=True)
async def ticketsetup(
    interaction: discord.Interaction
):
    embed = discord.Embed(
        title="🎫 Support Tickets",
        description=(
            "Need help from the Rex Room staff team?\n\n"
            "Click the button below to create a private support ticket."
        ),
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="📌 How it works",
        value=(
            "1. Click **Create Ticket**\n"
            "2. Explain your issue\n"
            "3. Wait for a staff member\n"
            "4. Close the ticket when finished"
        ),
        inline=False
    )

    embed.set_footer(
        text="Rec Buddy Ticket System"
    )

    await interaction.channel.send(
        embed=embed,
        view=TicketCreateView()
    )

    await interaction.response.send_message(
        "✅ Ticket panel created.",
        ephemeral=True
    )

# =========================================================
# TICKET SYSTEM
# =========================================================

TICKET_CATEGORY_NAME = "Tickets"

class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Close Ticket",
        emoji="🔒",
        style=discord.ButtonStyle.danger,
        custom_id="recbuddy:close_ticket"
    )
    async def close_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        channel = interaction.channel

        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "❌ This isn't a ticket channel.",
                ephemeral=True
            )
            return

        # Only staff or users with Manage Channels can close tickets
        if not interaction.user.guild_permissions.manage_channels:
            await interaction.response.send_message(
                "❌ You need Manage Channels to close this ticket.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            "🔒 Closing this ticket...",
            ephemeral=True
        )

        await send_log(
            interaction.guild,
            "Ticket Closed",
            f"**Channel:** {channel.mention}\n"
            f"**Closed by:** {interaction.user.mention}",
            discord.Color.red()
        )

        await asyncio.sleep(2)

        try:
            await channel.delete(
                reason=f"Ticket closed by {interaction.user}"
            )
        except discord.Forbidden:
            pass
        except discord.HTTPException:
            pass

class TicketCreateView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Create Ticket",
        emoji="🎫",
        style=discord.ButtonStyle.primary,
        custom_id="recbuddy:create_ticket"
    )
    async def create_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        guild = interaction.guild
        user = interaction.user

        if guild is None:
            return

        # Prevent multiple tickets
        existing_ticket = discord.utils.get(
            guild.text_channels,
            name=f"ticket-{user.name.lower().replace(' ', '-')}"
        )

        if existing_ticket:
            await interaction.response.send_message(
                f"❌ You already have a ticket: {existing_ticket.mention}",
                ephemeral=True
            )
            return

        # Find or create ticket category
        category = discord.utils.get(
            guild.categories,
            name=TICKET_CATEGORY_NAME
        )

        try:
            if category is None:
                category = await guild.create_category(
                    TICKET_CATEGORY_NAME,
                    reason="Rec Buddy ticket system setup"
                )

            # Permission overwrites
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(
                    view_channel=False
                ),
                user: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                    embed_links=True
                ),
                guild.me: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_channels=True,
                    manage_messages=True
                )
            }

            # Allow members with Manage Channels to access tickets
            for member in guild.members:
                if member.guild_permissions.manage_channels:
                    overwrites[member] = discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        read_message_history=True,
                        manage_messages=True
                    )

            channel = await guild.create_text_channel(
                name=f"ticket-{user.name.lower().replace(' ', '-')}",
                category=category,
                overwrites=overwrites,
                topic=f"Ticket opened by {user.id}",
                reason=f"Ticket created by {user}"
            )

            embed = discord.Embed(
                title="🎫 Support Ticket",
                description=(
                    f"Hello {user.mention}!\n\n"
                    "Thanks for opening a ticket. A staff member "
                    "will be with you shortly.\n\n"
                    "When your issue has been resolved, use the "
                    "**Close Ticket** button below."
                ),
                color=discord.Color.blurple()
            )

            embed.set_footer(
                text="Rec Buddy Ticket System"
            )

            await channel.send(
                content=user.mention,
                embed=embed,
                view=TicketCloseView()
            )

            await interaction.response.send_message(
                f"✅ Your ticket has been created: {channel.mention}",
                ephemeral=True
            )

            await send_log(
                guild,
                "Ticket Created",
                f"**Ticket:** {channel.mention}\n"
                f"**Created by:** {user.mention}",
                discord.Color.green()
            )

        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I don't have permission to create ticket channels.",
                ephemeral=True
            )

        except discord.HTTPException as e:
            print(f"Ticket creation error: {repr(e)}")

            await interaction.response.send_message(
                "❌ Discord rejected the ticket creation.",
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
# STATUSES
# =========================================================

async def status_loop():
    await bot.wait_until_ready()

    statuses = bot_config.get("status_messages", [])

    if not statuses:
        print("⚠️ No status messages configured.")
        return

    index = 0

    while not bot.is_closed():
        status = statuses[index]

        status_type = str(
            status.get("type", "playing")
        ).lower()

        status_text = str(
            status.get("text", "")
        )

        if status_text:
            if status_type == "playing":
                activity = discord.Game(
                    name=status_text
                )

            elif status_type == "watching":
                activity = discord.Activity(
                    type=discord.ActivityType.watching,
                    name=status_text
                )

            elif status_type == "listening":
                activity = discord.Activity(
                    type=discord.ActivityType.listening,
                    name=status_text
                )

            elif status_type == "competing":
                activity = discord.Activity(
                    type=discord.ActivityType.competing,
                    name=status_text
                )

            else:
                print(
                    f"⚠️ Unknown status type: {status_type}"
                )
                activity = discord.Game(
                    name=status_text
                )

            await bot.change_presence(
                activity=activity
            )

        index = (index + 1) % len(statuses)

        await asyncio.sleep(30)

# =========================================================
# READY
# =========================================================

@bot.event
async def on_ready():
    load_config()
    load_data()

    if not hasattr(bot, "status_task"):
        bot.status_task = asyncio.create_task(
            status_loop()
        )
    if not hasattr(bot, "ticket_views_added"):
        bot.add_view(TicketCreateView())
        bot.add_view(TicketCloseView())
        bot.ticket_views_added = True

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
