import asyncio
import logging
from typing import Dict, Union, Optional, List
import functools
import datetime

import hikari
from hikari.snowflakes import Snowflake, Snowflakeish
import lightbulb
import miru
from models import PunishFailed
from models.bot import SnedBot
import models
from models.db_user import User
from models.errors import RoleHierarchyError
from models.timer import Timer
from utils import helpers

logger = logging.getLogger(__name__)

mod = lightbulb.Plugin("Moderation", include_datastore=True)
mod.d.actions = lightbulb.utils.DataStore()
max_timeout_seconds = 2246400  # Duration of segments to break timeouts up to


default_mod_settings = {
    "dm_users_on_punish": True,
    "clean_up_mod_commands": False,
}


async def get_settings(guild_id: int) -> Dict[str, bool]:
    records = await mod.app.db_cache.get(table="mod_config", guild_id=guild_id)
    if records:
        mod_settings = {
            "dm_users_on_punish": records[0]["dm_users_on_punish"],
            "clean_up_mod_commands": records[0]["clean_up_mod_commands"],
        }
    else:
        mod_settings = default_mod_settings

    return mod_settings


mod.d.actions.get_settings = get_settings


def mod_punish(func):
    """
    Decorates commands that are supposed to punish a user.
    """

    @functools.wraps(func)
    async def inner(*args, **kwargs):
        ctx: lightbulb.SlashContext = args[0]
        user: Union[hikari.User, hikari.Member] = ctx.options.user if hasattr(ctx.options, "user") else None
        reason = ctx.options.reason if hasattr(ctx.options, "reason") else None
        helpers.format_reason(reason, ctx.member, max_length=1500)

        if ctx.member.id == user.id:
            embed = hikari.Embed(
                title="❌ You cannot {pwn} yourself".format(pwn=ctx.command.name),
                description="You cannot {pwn} your own account.".format(pwn=ctx.command.name),
                color=ctx.app.error_color,
            )
            await ctx.respond(embed=embed)
            return

        if user.id == 163979124820541440:
            embed = hikari.Embed(
                title="❌ Stop hurting him!!",
                description="I swear he did nothing wrong!",
                color=ctx.app.error_color,
            )
            await ctx.respond(embed=embed)
            return

        if user.is_bot:
            embed = hikari.Embed(
                title="❌ Cannot execute on bots",
                description="This command cannot be executed on bots.",
                color=ctx.app.error_color,
            )
            await ctx.respond(embed=embed)
            return

        settings = await get_settings(ctx.guild_id)
        types_conj = {
            "warn": "warned in",
            "timeout": "timed out in",
            "kick": "kicked from",
            "ban": "banned from",
            "softban": "soft-banned from",
            "tempban": "temp-banned from",
        }

        if settings["dm_users_on_punish"] == True and isinstance(user, hikari.Member):
            guild = ctx.get_guild()
            guild_name = guild.name if guild else "Unknown server"
            embed = hikari.Embed(
                title=f"❗ You have been {types_conj[ctx.command.name]} **{guild_name}**",
                description=f"You have been {types_conj[ctx.command.name]} **{guild_name}**.\n**Reason:** ```{reason}```",
                color=ctx.app.error_color,
            )
            try:
                await user.send(embed=embed)
            except (hikari.ForbiddenError, hikari.HTTPError):
                pass

        try:
            await func(*args, **kwargs)
        except PunishFailed:
            return
        else:
            pass  # After punish actions

    return inner


async def get_notes(user_id: Snowflakeish, guild_id: Snowflakeish) -> List[str]:
    """Returns a list of strings corresponding to a user's journal."""
    db_user = await mod.app.global_config.get_user(user_id, guild_id)
    return db_user.notes


async def add_note(user_id: Snowflakeish, guild_id: Snowflakeish, note: str) -> None:
    """Add a new journal entry to this user."""
    note = helpers.format_reason(note, max_length=256)

    db_user = await mod.app.global_config.get_user(user_id, guild_id)

    notes = db_user.notes if db_user.notes else []
    notes.append(f"{helpers.format_dt(helpers.utcnow(), style='d')}: {note}")
    db_user.notes = notes

    await mod.app.global_config.update_user(db_user)


async def clear_notes(user_id: Snowflakeish, guild_id: Snowflakeish) -> None:
    """Clear all notes a user has."""

    db_user = await mod.app.global_config.get_user(user_id, guild_id)
    db_user.notes = []
    await mod.app.global_config.update_user(db_user)


async def warn(member: hikari.Member, moderator: hikari.Member, reason: Optional[str] = None) -> hikari.Embed:

    db_user = await mod.app.global_config.get_user(member.id, member.guild_id)
    db_user.warns += 1
    await mod.app.global_config.update_user(db_user)
    reason = helpers.format_reason(reason, max_length=1000)

    embed = hikari.Embed(
        title="⚠️ Warning issued",
        description=f"**{member}** has been warned by **{moderator}**.\n**Reason:** ```{reason}```",
        color=mod.app.warn_color,
    )
    log_embed = hikari.Embed(
        title="⚠️ Warning issued",
        description=f"**{member}** has been warned by **{moderator}**.\n**Warns:** {db_user.warns}\n**Reason:** ```{reason}```",
        color=mod.app.warn_color,
    )

    await mod.app.get_plugin("Logging").d.actions.log("warn", log_embed, member.guild_id)
    return embed

    # TODO: Add note


@mod.listener(models.TimerCompleteEvent)
async def timeout_extend(event: models.TimerCompleteEvent) -> None:
    """
    Extend timeouts longer than 28 days
    """

    timer: Timer = event.timer

    if timer.event != "timeout_extend":
        return

    member = event.app.cache.get_member(timer.guild_id, timer.user_id)
    expiry = int(timer.notes)

    if member:
        me = mod.app.cache.get_member(timer.guild_id, mod.app.user_id)
        if not helpers.can_harm(me, member, hikari.Permissions.MODERATE_MEMBERS):
            return

        if expiry - helpers.utcnow().timestamp() > max_timeout_seconds:

            await event.app.scheduler.create_timer(
                helpers.utcnow() + datetime.timedelta(seconds=max_timeout_seconds),
                "timeout_extend",
                timer.guild_id,
                member.id,
                notes=timer.notes,
            )
            await member.edit(
                communication_disabled_until=helpers.utcnow() + datetime.timedelta(seconds=max_timeout_seconds),
                reason="Automatic timeout extension applied.",
            )

        else:
            timeout_for = helpers.utcnow() + datetime.timedelta(seconds=expiry - round(helpers.utcnow().timestamp()))
            await member.edit(communication_disabled_until=timeout_for, reason="Automatic timeout extension applied.")

    else:
        db_user = await event.app.global_config.get_user(timer.user_id, timer.guild_id)
        if "timeout_on_join" not in db_user.flags.keys():
            db_user.flags["timeout_on_join"] = expiry
            await event.app.global_config.update_user(db_user)


@mod.listener(hikari.MemberCreateEvent)
async def member_create(event: hikari.MemberCreateEvent):
    """
    Reapply timeout if member left between two cycles
    """

    me = mod.app.cache.get_member(event.guild_id, mod.app.user_id)
    if not helpers.can_harm(me, event.member, hikari.Permissions.MODERATE_MEMBERS):
        return

    db_user: User = await event.app.global_config.get_user(event.member.id, event.guild_id)

    if not db_user.flags or "timeout_on_join" not in db_user.flags.keys():
        return

    expiry = db_user.flags["timeout_on_join"]

    if expiry - helpers.utcnow().timestamp() < 0:
        # If this is in the past already
        return

    if expiry - helpers.utcnow().timestamp() > max_timeout_seconds:
        await event.app.scheduler.create_timer(
            helpers.utcnow() + datetime.timedelta(seconds=max_timeout_seconds),
            "timeout_extend",
            event.member.guild_id,
            event.member.id,
            notes=str(expiry),
        )
        await event.member.edit(
            communication_disabled_until=helpers.utcnow() + datetime.timedelta(seconds=max_timeout_seconds),
            reason="Automatic timeout extension applied.",
        )

    else:
        await event.member.edit(
            communication_disabled_until=expiry,
            reason="Automatic timeout extension applied.",
        )


@mod.listener(hikari.MemberUpdateEvent)
async def member_update(event: hikari.MemberUpdateEvent):
    """
    Remove all extensions if a user's timeout was removed
    """

    if not event.old_member:
        return

    if event.old_member.communication_disabled_until() != event.member.communication_disabled_until():
        if event.member.communication_disabled_until() is None:
            records = await event.app.pool.fetch(
                """SELECT * FROM timers WHERE guild_id = $1 AND user_id = $2 AND event = $3""",
                event.guild_id,
                event.member.id,
                "timeout_extend",
            )

            if not records:
                return

            for record in records:
                await event.app.scheduler.cancel_timer(record.get("id"), event.guild_id)


async def timeout(
    member: hikari.Member, moderator: hikari.Member, duration: datetime.datetime, reason: Optional[str] = None
) -> datetime.datetime:
    """
    Times out a member for the specified duration, converts duration from string.
    Returns the mute duration as datetime.
    """

    reason = helpers.format_reason(reason, moderator, max_length=512)

    me = mod.app.cache.get_member(member.guild_id, mod.app.user_id)
    # Raise error if cannot harm user
    helpers.can_harm(me, member, hikari.Permissions.MODERATE_MEMBERS, raise_error=True)

    if duration > helpers.utcnow() + datetime.timedelta(seconds=max_timeout_seconds):
        await mod.app.scheduler.create_timer(
            helpers.utcnow() + datetime.timedelta(seconds=max_timeout_seconds),
            "timeout_extend",
            member.guild_id,
            member.id,
            notes=str(round(duration.timestamp())),
        )
        await member.edit(
            communication_disabled_until=helpers.utcnow() + datetime.timedelta(seconds=max_timeout_seconds),
            reason=reason,
        )

    else:
        await member.edit(communication_disabled_until=duration, reason=reason)

    return duration


async def remove_timeout(member: hikari.Member, moderator: hikari.Member, reason: Optional[str] = None) -> None:
    """
    Removes a timeout from a user with the specified reason.
    """

    reason = helpers.format_reason(reason, moderator)

    await member.edit(communication_disabled_until=None, reason=reason)


async def ban(
    user: Union[hikari.User, hikari.Member],
    guild_id: Snowflake,
    moderator: hikari.Member,
    duration: Optional[str] = None,
    *,
    soft: bool = False,
    days_to_delete: int = 1,
    reason: Optional[str] = hikari.UNDEFINED,
) -> hikari.Embed:
    """Ban a user from a guild.

    Parameters
    ----------
    user : Union[hikari.User, hikari.Member]
        The user that needs to be banned.
    guild_id : Snowflake
        The guild this ban is taking place.
    moderator : hikari.Member
        The moderator to log the ban under.
    duration : Optional[str], optional
        If specified, the duration of the ban, by default None
    soft : bool, optional
        If True, the ban is a softban, by default False
    days_to_delete : int, optional
        The days of message history to delete, by default 1
    reason : Optional[str], optional
        The reason for the ban, by default hikari.UNDEFINED

    Returns
    -------
    hikari.Embed
        The response embed to display to the user. May include any
        potential input errors.

    Raises
    ------
    RuntimeError
        Both soft & tempban were specified.
    """

    reason = reason or "No reason provided."

    if duration and soft:
        raise RuntimeError("Ban type cannot be soft when a duration is specified.")

    me = mod.app.cache.get_member(guild_id, mod.app.user_id)

    perms = lightbulb.utils.permissions_for(me)

    if not (perms & hikari.Permissions.BAN_MEMBERS):
        raise lightbulb.BotMissingRequiredPermission(perms=hikari.Permissions.BAN_MEMBERS)

    if isinstance(user, hikari.Member) and not helpers.is_above(me, user):
        raise RoleHierarchyError

    if duration:
        try:
            duration = await mod.app.scheduler.convert_time(duration)
            reason = f"[TEMPBAN] Banned until: {duration} (UTC)  |  {reason}"
        except ValueError:
            embed = hikari.Embed(
                title="❌ Invalid data entered",
                description="Your entered timeformat is invalid. Type `/help tempban` for more information.",
                color=mod.app.error_color,
            )
            return embed

    elif soft:
        reason = f"[SOFTBAN] {reason}"

    raw_reason = reason
    reason = helpers.format_reason(reason, moderator, max_length=512)

    try:
        await mod.app.rest.ban_user(guild_id, user.id, delete_message_days=days_to_delete, reason=reason)
        embed = hikari.Embed(
            title="🔨 User banned",
            description=f"**{user}** has been banned.\n**Reason:** ```{raw_reason}```",
            color=mod.app.error_color,
        )

        if soft:
            await mod.app.rest.unban_user(guild_id, user.id, reason="Automatic unban by softban.")

        elif duration:
            await mod.app.scheduler.create_timer(expires=duration, event="tempban", guild_id=guild_id, user_id=user.id)

        return embed

    except (hikari.ForbiddenError, hikari.HTTPError):
        embed = hikari.Embed(
            title="❌ Ban failed",
            description="This could be due to a configuration or network error. Please try again later.",
            color=mod.app.error_color,
        )
        return embed


async def kick(
    member: hikari.Member,
    moderator: hikari.Member,
    *,
    reason: Optional[str] = None,
) -> hikari.Embed:
    """[summary]

    Parameters
    ----------
    member : hikari.Member
        The member that needs to be kicked.
    moderator : hikari.Member
        The moderator to log the kick under.
    reason : Optional[str], optional
        The reason for the kick, by default None

    Returns
    -------
    hikari.Embed
        The response embed to display to the user. May include any
        potential input errors.
    """

    raw_reason = reason or "No reason provided."
    reason = helpers.format_reason(reason, moderator, max_length=512)

    me = mod.app.cache.get_member(member.guild_id, mod.app.user_id)
    # Raise error if cannot harm user
    helpers.can_harm(me, member, hikari.Permissions.MODERATE_MEMBERS, raise_error=True)

    try:
        await mod.app.rest.kick_user(member.guild_id, member, reason=reason)
        embed = hikari.Embed(
            title="🚪👈 User kicked",
            description=f"**{member}** has been kicked.\n**Reason:** ```{raw_reason}```",
            color=mod.app.error_color,
        )
        return embed

    except (hikari.ForbiddenError, hikari.HTTPError):
        embed = hikari.Embed(
            title="❌ Kick failed",
            description="This could be due to a configuration or network error. Please try again later.",
            color=mod.app.error_color,
        )
        return embed


@mod.command()
@lightbulb.command("journal", "Access and manage the moderation journal.")
@lightbulb.implements(lightbulb.SlashCommandGroup)
async def journal(ctx: lightbulb.SlashContext) -> None:
    pass


@journal.child()
@lightbulb.option("user", "The user to retrieve the journal for.", type=hikari.User)
@lightbulb.command("get", "Retrieve the journal for the specified user.")
@lightbulb.implements(lightbulb.SlashSubCommand)
async def journal_get(ctx: lightbulb.SlashContext) -> None:

    notes = await get_notes(ctx.options.user.id, ctx.guild_id)
    paginator = lightbulb.utils.StringPaginator(max_chars=1500)

    if notes:
        notes_fmt = []
        for i, note in enumerate(notes):
            notes_fmt.append(f"`#{i}` {note}")

        for note in notes_fmt:
            paginator.add_line(note)

        embeds = []
        for page in paginator.build_pages():
            embed = hikari.Embed(
                title="📒 " + "Journal entries for this user:",
                description=page,
                color=ctx.app.embed_blue,
            )
            embeds.append(embed)

        navigator = models.AuthorOnlyNavigator(ctx, pages=embeds)

        await navigator.send(ctx.interaction)

    else:
        embed = hikari.Embed(
            title="📒 Journal entries for this user:",
            description=f"There are no journal entries for this user yet. Any moderation-actions will leave an entry here, or you can set one manually with `/journal add {ctx.options.user}` ",
            color=ctx.app.embed_blue,
        )
        await ctx.respond(embed=embed)


@journal.child()
@lightbulb.option("note", "The journal note to add.")
@lightbulb.option("user", "The user to add a journal entry for.", type=hikari.User)
@lightbulb.command("add", "Add a new journal entry for the specified user.")
@lightbulb.implements(lightbulb.SlashSubCommand)
async def journal_add(ctx: lightbulb.SlashContext) -> None:

    await add_note(ctx.options.user.id, ctx.guild_id, f"💬 **Note by {ctx.author}:** {ctx.options.note}")
    embed = hikari.Embed(
        title="✅ Journal entry added!",
        description=f"Added a new journal entry to user **{ctx.options.user}**. You can view this user's journal via the command `/journal get {ctx.options.user}`.",
        color=ctx.app.embed_green,
    )
    await ctx.respond(embed=embed)


@mod.command()
@lightbulb.option("reason", "The reason for this warn", required=False)
@lightbulb.option("user", "The user to be warned.", type=hikari.Member)
@lightbulb.command("warn", "Warn a user. This gets added to their journal and their warn counter is incremented.")
@lightbulb.implements(lightbulb.SlashCommand)
async def warn_cmd(ctx: lightbulb.SlashContext) -> None:

    embed = await warn(ctx.options.user, ctx.member, reason=ctx.options.reason)
    await ctx.respond(embed=embed)


@mod.command()
@lightbulb.command("warns", "Manage warnings.")
@lightbulb.implements(lightbulb.SlashCommandGroup)
async def warns(ctx: lightbulb.SlashContext) -> None:
    pass


@warns.child()
@lightbulb.option("user", "The user to show the warning count for.", type=hikari.Member)
@lightbulb.command("list", "List the current warning count for a user.")
@lightbulb.implements(lightbulb.SlashSubCommand)
async def warns_list(ctx: lightbulb.SlashContext) -> None:
    db_user: User = await ctx.app.global_config.get_user(ctx.options.user.id, ctx.guild_id)
    warns = db_user.warns
    embed = hikari.Embed(
        title=f"{ctx.options.user}'s warnings",
        description=f"**Warnings:** `{warns}`",
        color=ctx.app.warn_color,
    )
    embed.set_thumbnail(ctx.options.user.display_avatar_url)
    await ctx.respond(embed=embed)


@warns.child()
@lightbulb.option("reason", "The reason for clearing this user's warns.", required=False)
@lightbulb.option("user", "The user to show the warning count for.", type=hikari.Member)
@lightbulb.command("clear", "Clear warnings for the specified user.")
@lightbulb.implements(lightbulb.SlashSubCommand)
async def warns_clear(ctx: lightbulb.SlashContext) -> None:
    db_user: User = await ctx.app.global_config.get_user(ctx.options.user.id, ctx.guild_id)
    db_user.warns = 0
    await ctx.app.global_config.update_user(db_user)

    reason = helpers.format_reason(ctx.options.reason)

    embed = hikari.Embed(
        title="✅ Warnings cleared",
        description=f"**{ctx.options.user}**'s warnings have been cleared.\n**Reason:** ```{reason}```",
        color=ctx.app.embed_green,
    )
    log_embed = hikari.Embed(
        title="⚠️ Warnings cleared.",
        description=f"{ctx.options.user.mention}'s warnings have been cleared by {ctx.author.mention}.\n**Reason:** ```{reason}```",
        color=ctx.app.embed_green,
    )

    await add_note(ctx.options.user.id, ctx.guild_id, f"⚠️ **Warnings cleared by {ctx.author}:** {reason}")
    await mod.app.get_plugin("Logging").d.actions.log("warn", log_embed, ctx.guild_id)
    await ctx.respond(embed=embed)


def load(bot: SnedBot) -> None:
    bot.add_plugin(mod)


def unload(bot: SnedBot) -> None:
    bot.remove_plugin(mod)


@mod.command()
@lightbulb.option("reason", "The reason for timing out this user.", required=False)
@lightbulb.option("duration", "The duration to time the user out for.")
@lightbulb.option("user", "The user to time out.", type=hikari.Member)
@lightbulb.command("timeout", "Timeout a user, supports durations longer than 28 days.")
@lightbulb.implements(lightbulb.SlashCommand)
async def timeout_cmd(ctx: lightbulb.SlashContext) -> None:
    member: hikari.Member = ctx.options.user
    reason: str = helpers.format_reason(ctx.options.reason, max_length=1024)

    if member.communication_disabled_until() is not None:
        embed = hikari.Embed(
            title="❌ User already timed out",
            description="User is already timed out. Use `/timeouts remove` to remove it.",
            color=ctx.app.error_color,
        )
        return await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)

    try:
        duration: datetime.datetime = await ctx.app.scheduler.convert_time(ctx.options.duration)
    except ValueError:
        embed = hikari.Embed(
            title="❌ Invalid data entered",
            description="Your entered timeformat is invalid.",
            color=ctx.app.error_color,
        )
        return await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)

    await ctx.respond(hikari.ResponseType.DEFERRED_MESSAGE_CREATE)

    await timeout(member, ctx.member, duration, reason)

    embed = hikari.Embed(
        title="🔇 " + "User timed out",
        description=f"**{member}** has been timed out until {helpers.format_dt(duration)}.\n**Reason:** ```{reason}```",
        color=ctx.app.embed_green,
    )
    await ctx.respond(embed=embed)


@mod.command()
@lightbulb.command("timeouts", "Manage timeouts.")
@lightbulb.implements(lightbulb.SlashCommandGroup)
async def timeouts(ctx: lightbulb.SlashContext) -> None:
    pass


@timeouts.child()
@lightbulb.option("reason", "The reason for timing out this user.", required=False)
@lightbulb.option("user", "The user to time out.", type=hikari.Member)
@lightbulb.command("remove", "Remove timeout from a user.")
@lightbulb.implements(lightbulb.SlashSubCommand)
async def timeouts_remove_cmd(ctx: lightbulb.SlashContext) -> None:
    member: hikari.Member = ctx.options.user
    reason: str = helpers.format_reason(ctx.options.reason, max_length=1024)

    if member.communication_disabled_until() is None:
        embed = hikari.Embed(
            title="❌ User not timed out",
            description="This user is not timed out.",
            color=ctx.app.error_color,
        )
        return await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)

    await ctx.respond(hikari.ResponseType.DEFERRED_MESSAGE_CREATE)
    await remove_timeout(member, ctx.member, reason)

    embed = hikari.Embed(
        title="🔉 " + "Timeout removed",
        description=f"**{member}**'s timeout was removed.\n**Reason:** ```{reason}```",
        color=ctx.app.embed_green,
    )
    await ctx.respond(embed=embed)
