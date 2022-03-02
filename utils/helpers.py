from __future__ import annotations

import asyncio
import datetime
import re
from typing import TYPE_CHECKING, Optional, TypeVar, Union, List, Any

import hikari
import lightbulb
from lightbulb.utils.parser import CONVERTER_TYPE_MAPPING
import miru

import models
from models import errors
from models.context import SnedSlashContext
from models.db_user import User
from models.components import *

if TYPE_CHECKING:
    from extensions.settings import SettingsView


MESSAGE_LINK_REGEX = re.compile(
    r"https?:\/\/(www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()!@:%_\+.~#?&\/\/=]*)channels[\/][0-9]{1,}[\/][0-9]{1,}[\/][0-9]{1,}"
)
LINK_REGEX = re.compile(
    r"https?:\/\/(www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()!@:%_\+.~#?&\/\/=]*)"
)
INVITE_REGEX = re.compile(r"(?:https?://)?discord(?:app)?\.(?:com/invite|gg)/[a-zA-Z0-9]+/?")


def format_dt(time: datetime.datetime, style: Optional[str] = None) -> str:
    """
    Convert a datetime into a Discord timestamp.
    For styling see this link: https://discord.com/developers/docs/reference#message-formatting-timestamp-styles
    """
    valid_styles = ["t", "T", "d", "D", "f", "F", "R"]

    if style and style not in valid_styles:
        raise ValueError(f"Invalid style passed. Valid styles: {' '.join(valid_styles)}")

    if style:
        return f"<t:{int(time.timestamp())}:{style}>"

    return f"<t:{int(time.timestamp())}>"


def utcnow() -> datetime.datetime:
    """
    A short-hand function to return a timezone-aware utc datetime.
    """
    return datetime.datetime.now(datetime.timezone.utc)


def add_embed_footer(embed: hikari.Embed, invoker: hikari.Member) -> hikari.Embed:
    """
    Add a note about the command invoker in the embed passed.
    """
    avatar_url = invoker.display_avatar_url

    embed.set_footer(text=f"Requested by {invoker}", icon=avatar_url)
    return embed


def get_display_avatar(member: hikari.Member) -> str:
    """
    Gets the currently displayed avatar for the member, returns the URL.
    """
    if member.guild_avatar_url:
        return member.guild_avatar_url

    elif member.avatar_url:
        return member.avatar_url

    else:
        return member.default_avatar_url


def get_avatar(user: Union[hikari.User, hikari.Member]) -> str:
    """
    Return the avatar of the specified user, fallback to default_avatar if not found.
    """
    if user.avatar_url:
        return user.avatar_url
    else:
        return user.default_avatar_url


def get_color(member: hikari.Member) -> hikari.Color:
    roles = member.get_roles().__reversed__()
    if roles:
        for role in roles:
            if role.color != hikari.Color.from_rgb(0, 0, 0):
                return role.color

    return None


def sort_roles(roles: List[hikari.Role]) -> List[hikari.Role]:
    """Sort a list of roles in a descending order based on position."""
    return sorted(roles, key=lambda r: r.position, reverse=True)


def get_badges(ctx: lightbulb.Context, user: hikari.User) -> List[str]:
    """Return a list of badge emojies that the user has."""

    badge_emoji_mapping = {
        hikari.UserFlag.BUG_HUNTER_LEVEL_1: str(ctx.app.cache.get_emoji(927590809241530430)),
        hikari.UserFlag.BUG_HUNTER_LEVEL_2: str(ctx.app.cache.get_emoji(927590820448710666)),
        hikari.UserFlag.DISCORD_CERTIFIED_MODERATOR: str(ctx.app.cache.get_emoji(927582595808657449)),
        hikari.UserFlag.EARLY_SUPPORTER: str(ctx.app.cache.get_emoji(927582684123914301)),
        hikari.UserFlag.EARLY_VERIFIED_DEVELOPER: str(ctx.app.cache.get_emoji(927582706974462002)),
        hikari.UserFlag.HYPESQUAD_EVENTS: str(ctx.app.cache.get_emoji(927582724523450368)),
        hikari.UserFlag.HYPESQUAD_BALANCE: str(ctx.app.cache.get_emoji(927582757587136582)),
        hikari.UserFlag.HYPESQUAD_BRAVERY: str(ctx.app.cache.get_emoji(927582770329444434)),
        hikari.UserFlag.HYPESQUAD_BRILLIANCE: str(ctx.app.cache.get_emoji(927582740977684491)),
        hikari.UserFlag.PARTNERED_SERVER_OWNER: str(ctx.app.cache.get_emoji(927591117304778772)),
        hikari.UserFlag.DISCORD_EMPLOYEE: str(ctx.app.cache.get_emoji(927591104902201385)),
    }

    badges = []

    for flag, emoji in badge_emoji_mapping.items():
        if flag & user.flags:
            badges.append(emoji)

    return badges


async def get_userinfo(ctx: lightbulb.Context, user: hikari.User) -> hikari.Embed:

    db_user: User = await ctx.app.global_config.get_user(user.id, ctx.guild_id)

    member = ctx.app.cache.get_member(ctx.guild_id, user)

    if member:
        roles = [role.mention for role in sort_roles(member.get_roles())]
        roles.remove(f"<@&{ctx.guild_id}>")
        roles = ", ".join(roles) if roles else "`-`"

        embed = hikari.Embed(
            title=f"**User information:** {member.display_name}",
            description=f"""**• Username:** `{member}`
**• Nickname:** `{member.nickname or "-"}`
**• User ID:** `{member.id}`
**• Bot:** `{member.is_bot}`
**• Account creation date:** {format_dt(member.created_at)} ({format_dt(member.created_at, style='R')})
**• Join date:** {format_dt(member.joined_at)} ({format_dt(member.joined_at, style='R')})
**• Badges:** {"   ".join(get_badges(ctx, member)) or "`-`"}
**• Warns:** `{db_user.warns}`
**• Timed out:** {f"Until: {format_dt(member.communication_disabled_until())}" if member.communication_disabled_until() else "`-`"}
**• Flags:** `{",".join(list(db_user.flags.keys())) if db_user.flags and len(db_user.flags) > 0 else "-"}`
**• Journal:** `{f"{len(db_user.notes)} entries" if db_user.notes else "No entries"}`
**• Roles:** {roles}""",
            color=get_color(member),
        )
        user = await ctx.app.rest.fetch_user(user.id)
        embed.set_thumbnail(member.display_avatar_url)
        if user.banner_url:
            embed.set_image(user.banner_url)

    else:
        embed = hikari.Embed(
            title=f"**User information:** {user.username}",
            description=f"""**• Username:** `{user}`
**• Nickname:** `-`
**• User ID:** `{user.id}`
**• Bot:** `{user.is_bot}`
**• Account creation date:** {format_dt(user.created_at)} ({format_dt(user.created_at, style='R')})
**• Join date:** `-`
**• Badges:** {"   ".join(get_badges(ctx, user)) or "`-`"}
**• Warns:** `{db_user.warns}`
**• Timed out:** `-`
**• Flags:** `{",".join(list(db_user.flags.keys())) if db_user.flags and len(db_user.flags) > 0 else "-"}`
**• Journal:** `{f"{len(db_user.notes)} entries" if db_user.notes else "No entries"}`
**• Roles:** `-`
*Note: This user is not a member of this server*""",
            color=ctx.app.embed_blue,
        )
        embed.set_thumbnail(user.display_avatar_url)
        user = await ctx.app.rest.fetch_user(user.id)
        if user.banner_url:
            embed.set_image(user.banner_url)

    if ctx.member.id in ctx.app.owner_ids:
        records = await ctx.app.db_cache.get(table="blacklist", guild_id=0, user_id=user.id)
        is_blacklisted = True if records and records[0]["user_id"] == user.id else False
        embed.description = f"{embed.description}\n**• Blacklisted:** `{is_blacklisted}`"

    embed = add_embed_footer(embed, ctx.member)
    return embed


def includes_permissions(permissions: hikari.Permissions, should_include: hikari.Permissions) -> bool:
    """Check if permissions includes should_includes."""

    missing_perms = ~permissions & should_include
    if missing_perms is not hikari.Permissions.NONE:
        return False
    return True


def len_embed(embed: hikari.Embed) -> int:
    """Return the total length of an embed object.

    Parameters
    ----------
    embed : hikari.Embed
        The embed to get the length of.

    Returns
    -------
    int
        The length of the embed.
    """

    if not isinstance(embed, hikari.Embed):
        return 0

    text = []
    text.append(embed.title or "")
    text.append(embed.description or "")
    if embed.footer:
        text.append(embed.footer.text or "")
    if embed.author:
        text.append(embed.author.name or "")

    text += [f"{field.name}{field.value}" for field in embed.fields]

    return len("".join(text))


def is_above(me: hikari.Member, member: hikari.Member) -> bool:
    """
    Returns True if me's top role's position is higher than the specified member's.
    """
    if me.get_top_role().position > member.get_top_role().position:
        return True
    return False


def can_harm(
    me: hikari.Member, member: hikari.Member, permission: hikari.Permissions, *, raise_error: bool = False
) -> bool:
    """
    Returns True if "member" can be harmed by "me", also checks if "me" has "permission".
    """

    perms = lightbulb.utils.permissions_for(me)

    if not includes_permissions(perms, permission):
        if raise_error:
            raise lightbulb.BotMissingRequiredPermission(perms=permission)
        return False

    if not is_above(me, member):
        if raise_error:
            raise errors.RoleHierarchyError
        return False

    return True


def get_jump_url(message: hikari.Message):
    """
    Get jump URL for a message.
    """
    guild_id = "@me" if not message.guild_id else message.guild_id
    return f"https://discord.com/channels/{guild_id}/{message.channel_id}/{message.id}"


def is_url(string: str, *, fullmatch: bool = True) -> bool:
    """
    Returns True if the provided string is an URL, otherwise False.
    """

    if fullmatch and LINK_REGEX.fullmatch(string):
        return True
    elif not fullmatch and LINK_REGEX.match(string):
        return True

    return False


def is_invite(string: str, *, fullmatch: bool = True) -> bool:
    """
    Returns True if the provided string is a Discord invite, otherwise False.
    """

    if fullmatch and INVITE_REGEX.fullmatch(string):
        return True
    elif not fullmatch and INVITE_REGEX.match(string):
        return True

    return False


def get_or_fetch_user(bot: hikari.GatewayBot, user_id: int):
    user = bot.cache.get_user(user_id)
    if not user:
        bot.rest.fetch_user(user_id)
    return user


def is_member(user: hikari.PartialUser) -> bool:
    if isinstance(user, hikari.Member):
        return True

    raise errors.MemberExpectedError(f"Expected an instance of hikari.Member, not {user.__class__.__name__}!")


async def resolve_response(response: Union[lightbulb.ResponseProxy, hikari.Message]) -> hikari.Message:
    """
    Resolve a potential ResponseProxy into a hikari message object. If a hikari.Message is passed, it is returned directly.
    """
    if isinstance(response, hikari.Message):
        return response
    elif isinstance(response, lightbulb.ResponseProxy):
        return await response.message()
    else:
        raise TypeError(f"response must be of type hikari.Message or lightbulb.ResponseProxy, not {type(response)}")


T = TypeVar("T")


async def parse_message_link(ctx: SnedSlashContext, message_link: str) -> Optional[hikari.Message]:
    """Parse a message_link string into a message object."""

    if not MESSAGE_LINK_REGEX.fullmatch(message_link):
        embed = hikari.Embed(
            title="❌ Invalid link",
            description="This does not appear to be a valid message link! You can get a message's link by right-clicking it and selecting `Copy Message Link`!",
            color=ctx.app.error_color,
        )
        await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)
        return None

    snowflakes = message_link.split("/channels/")[1].split("/")
    guild_id = hikari.Snowflake(snowflakes[0]) if snowflakes[0] != "@me" else None
    channel_id = hikari.Snowflake(snowflakes[1])
    message_id = hikari.Snowflake(snowflakes[2])

    if ctx.guild_id != guild_id:
        embed = hikari.Embed(
            title="❌ Invalid link",
            description="The message seems to be from another server! Please copy a message link from this server!",
            color=ctx.app.error_color,
        )
        await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)
        return None

    channel = ctx.app.cache.get_guild_channel(channel_id)

    perms = lightbulb.utils.permissions_in(channel, ctx.app.cache.get_member(ctx.guild_id, ctx.app.user_id))
    if not (perms & hikari.Permissions.READ_MESSAGE_HISTORY):
        raise lightbulb.BotMissingRequiredPermission(perms=hikari.Permissions.READ_MESSAGE_HISTORY)

    try:
        message = await ctx.app.rest.fetch_message(channel, message_id)
    except (hikari.NotFoundError, hikari.ForbiddenError):
        embed = hikari.Embed(
            title="❌ Unknown message",
            description="Could not find message with this link. Ensure the link is valid, and that the bot has permissions to view the channel.",
            color=ctx.app.error_color,
        )
        await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)
        return None

    return message


async def ask(
    ctx: Union[lightbulb.Context, miru.Context],
    *,
    options: List[miru.SelectOption],
    return_type: T,
    embed_or_content: Union[str, hikari.Embed],
    placeholder: str = None,
    ignore: Optional[List[Any]] = None,
    message: Optional[hikari.Message] = None,
) -> Union[T, Any]:
    """A function that abstracts away the limitations of select menus
    by falling back to a text input from the user if limits are exceeded.

    Parameters
    ----------
    ctx : lightbulb.Context
        The lightbulb context to use for converters.
    options : List[miru.SelectOption]
        A list of select options to use, if not falling back for text input.
    return_type : T
        The expected return type, an appropriate converter will be used.
    embed_or_content : Union[str, hikari.Embed]
        The embed or content to use for the prompt.
    placeholder : str, optional
        The select menu's placeholder, by default None
    ignore : List[Any], optional
        Return values to skip conversion for, by default None
    message : Optional[hikari.Message], optional
        If provided, a message to edit, by default None

    Returns
    -------
    Union[T, Any]
        An object corresponding to the specified return type, or raw value if in ignore.

    Raises
    ------
    TypeError
        An invalid return_type was provided or conversion to it failed.
    asyncio.TimeoutError
        Timed out without a response.
    """
    if return_type not in CONVERTER_TYPE_MAPPING.keys():
        return TypeError(
            f"return_type must be of types: {' '.join(list(CONVERTER_TYPE_MAPPING.keys()))}, not {return_type}"
        )

    # Get appropiate converter for return type
    converter: lightbulb.BaseConverter = CONVERTER_TYPE_MAPPING[return_type](ctx)

    # If the select will result in a Bad Request or not
    invalid_select: bool = False
    if len(options) > 25:
        invalid_select = True
    else:
        for option in options:
            if len(option.label) > 100 or (option.description and len(option.description) > 100):
                invalid_select = True

    if isinstance(embed_or_content, str):
        content = embed_or_content
        embeds = []
    elif isinstance(embed_or_content, hikari.Embed):
        content = ""
        embeds = [embed_or_content]
    else:
        raise TypeError(f"embed_or_content must be of type str or hikari.Embed, not {type(embed_or_content)}")

    if not invalid_select:
        view = models.AuthorOnlyView(ctx)
        view.add_item(models.StopSelect(placeholder=placeholder, options=options))

        if message:
            message = await message.edit(content=content, embeds=embeds, components=view.build())
        else:
            response = await ctx.respond(content=content, embeds=embeds, components=view.build())
            message = await resolve_response(response)

        view.start(message)
        await view.wait()
        if view.children[0].values:
            if ignore and view.children[0].values[0] in ignore:
                return view.children[0].values[0]
            return await converter.convert(view.children[0].values[0])

        raise asyncio.TimeoutError("View timed out without response.")

    else:
        if embeds:
            embeds[0].description = f"{embeds[0].description}\n\nPlease type your response below!"
        elif content:
            content = f"{content}\n\nPlease type your response below!"

        if message:
            message = await message.edit(content=content, embeds=embeds)
        else:
            response = await ctx.respond(content=content, embeds=embeds)
            message = await resolve_response(response)

        predicate = lambda e: e.author_id == ctx.author.id and e.channel_id == ctx.channel_id

        event = await ctx.app.wait_for(hikari.MessageCreateEvent, timeout=120.0, predicate=predicate)
        if event.content:
            if ignore and event.content in ignore:
                return event.content
            return await converter.convert(event.content)


async def maybe_delete(message: hikari.Message) -> None:
    try:
        await message.delete()
    except (hikari.NotFoundError, hikari.ForbiddenError, hikari.HTTPError):
        pass


async def maybe_edit(message: hikari.Message, *args, **kwargs) -> None:
    try:
        return await message.edit(*args, **kwargs)
    except (hikari.NotFoundError, hikari.ForbiddenError, hikari.HTTPError):
        pass


def format_reason(
    reason: str = None, moderator: Optional[hikari.Member] = None, *, max_length: Optional[int] = 512
) -> str:
    """
    Format a reason for a moderation action
    """
    if not reason:
        reason = "No reason provided."

    if moderator:
        reason = f"{moderator} ({moderator.id}): {reason}"

    if max_length and len(reason) > max_length:
        reason = reason[: max_length - 3] + "..."

    return reason