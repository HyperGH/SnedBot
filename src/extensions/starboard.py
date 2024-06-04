import logging
import re
import typing as t

import arc
import hikari
import toolbox

from src.etc import const
from src.models.client import SnedClient, SnedContext, SnedPlugin
from src.models.starboard import StarboardEntry, StarboardSettings
from src.utils import helpers

logger = logging.getLogger(__name__)

plugin = SnedPlugin("Starboard")

IMAGE_URL_REGEX = re.compile(
    r"https?:\/\/(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()!@:%_\+.~#?&\/\/=]*)(?:[.]jpe?g|png|gif|bmp|webp)[-a-zA-Z0-9@:%._\+~#=?&]*"
)

STAR_MAPPING = {
    "⭐": 0,
    "🌟": 5,
    "✨": 10,
    "💫": 15,
}


def get_image_urls(content: str) -> list[str]:
    """Return a list of image URLs found in the message content."""
    return IMAGE_URL_REGEX.findall(content)


def get_img_attach_urls(message: hikari.Message) -> list[str]:
    """Return a list of image attachment URLs found in the message."""
    if not message.attachments:
        return []

    attach_urls = [attachment.url for attachment in message.attachments]
    return [url for url in attach_urls if IMAGE_URL_REGEX.fullmatch(url)]


def create_starboard_payload(
    guild: hikari.SnowflakeishOr[hikari.PartialGuild],
    message: hikari.Message,
    stars: int,
    force_starred: bool,
) -> dict[str, t.Any]:
    """Create message payload for a starboard entry.

    Parameters
    ----------
    guild : hikari.SnowflakeishOr[hikari.PartialGuild]
        The guild the starboard entry is located.
    message : hikari.Message
        The message to create the payload from.
    stars : int
        The amount of stars the message has.
    force_starred : bool
        Replace the star count with a disclaimer instead.

    Returns
    -------
    dict[str, t.Any]
        The payload as keyword arguments.
    """
    guild_id = hikari.Snowflake(guild)
    member = plugin.client.cache.get_member(guild_id, message.author.id)
    emoji = [emoji for emoji, value in STAR_MAPPING.items() if value <= stars][-1]

    content = f"{emoji} **{stars}{' (Forced)' if force_starred else ''}** <#{message.channel_id}>"

    # A url must be set for all embeds to make the image carousel work
    head_embed = (
        hikari.Embed(description=message.content, color=0xFFC20C, url="https://example.com")
        .set_author(
            name=member.display_name if member else "Unknown", icon=member.display_avatar_url if member else None
        )
        .set_footer(f"ID: {message.id}")
    )

    image_urls: list[str] = []

    if message.attachments and (attach_urls := get_img_attach_urls(message)):
        image_urls += attach_urls

    if message.content and (content_urls := get_image_urls(message.content)):
        image_urls += content_urls

    # Remove duplicate attachments
    attachments = [attachment for attachment in message.attachments if attachment.url not in image_urls[:10]]

    if attachments:
        head_embed.add_field(
            "Attachments",
            "\n".join([f"[{attachment.filename[:100]}]({attachment.url})" for attachment in attachments][:5]),
        )

    if message.referenced_message:
        head_embed.add_field(
            "Replying to",
            f"[{message.referenced_message.author}]({message.referenced_message.make_link(guild_id)})",
        )

    head_embed.add_field("Original Message", f"[Jump!]({message.make_link(guild_id)})")

    if image_urls:
        head_embed.set_image(image_urls[0])

    tail_embeds = [hikari.Embed(url="https://example.com").set_image(image_url) for image_url in image_urls[1:][:10]]

    return {"content": content, "embeds": [head_embed, *tail_embeds]}


async def star_message(
    message: hikari.Message,
    guild: hikari.SnowflakeishOr[hikari.PartialGuild],
    settings: StarboardSettings,
    stars: int,
    force_starred: bool = False,
) -> None:
    """Create or edit an existing star entry on the starboard.

    Parameters
    ----------
    message : hikari.Message
        The message to be starred.
    starboard_channel : hikari.SnowflakeishOr[hikari.TextableGuildChannel]
        The channel where the message should be starred.
    guild : hikari.SnowflakeishOr[hikari.PartialGuild]
        The guild the message and channel are located.
    settings : StarboardSettings
        The settings for the starboard.
    stars : int
        The amount of stars the message is supposed to have when posted.
    force_starred : bool, optional
        Whether the message is forcefully starred or not.
    """
    if not settings.channel_id or not settings.is_enabled:
        return

    def is_starrable():
        return stars >= settings.star_limit or force_starred

    entry = await StarboardEntry.fetch(message)

    # If there is no entry yet, create a new one
    if not entry:
        if not is_starrable():
            return
        payload = create_starboard_payload(guild, message, stars=stars, force_starred=force_starred)
        starboard_msg_id = (await plugin.client.rest.create_message(settings.channel_id, **payload)).id
        entry = StarboardEntry(
            guild_id=hikari.Snowflake(guild),
            channel_id=message.channel_id,
            original_message_id=message.id,
            entry_message_id=starboard_msg_id,
            force_starred=force_starred,
        )
        await entry.update()
        return

    force_starred = entry.force_starred or force_starred
    starboard_msg_id = entry.entry_message_id

    if not is_starrable():
        return

    try:
        payload = create_starboard_payload(guild, message, stars=stars, force_starred=force_starred)
        await plugin.client.rest.edit_message(settings.channel_id, starboard_msg_id, **payload)
    # Starboard message was deleted or missing
    except hikari.NotFoundError:
        await entry.delete()
        await star_message(message, guild, settings, stars)


async def force_star(ctx: SnedContext, message: hikari.Message) -> None:
    """Force star a message.

    Parameters
    ----------
    ctx : SnedApplicationContext
        The context of the command.
    message : hikari.Message
        The message to force star.
    """
    assert ctx.guild_id is not None

    settings = await StarboardSettings.fetch(ctx.guild_id)

    if not settings.is_enabled:
        await ctx.respond(
            embed=hikari.Embed(
                title="❌ Starboard disabled",
                description="The starboard is not enabled on this server! Enable it in `/settings`!",
                color=const.ERROR_COLOR,
            )
        )
        return

    if settings.excluded_channels and ctx.channel_id in settings.excluded_channels:
        await ctx.respond(
            embed=hikari.Embed(
                title="❌ Channel excluded",
                description="This channel is excluded from the starboard! You can change this in `/settings`!",
                color=const.ERROR_COLOR,
            )
        )
        return

    me = ctx.client.cache.get_member(ctx.guild_id, ctx.client.user_id)

    if not me:
        return

    if settings.channel_id and (channel := ctx.client.cache.get_guild_channel(settings.channel_id)):
        perms = toolbox.calculate_permissions(me, channel)
        if not helpers.includes_permissions(
            perms,
            hikari.Permissions.SEND_MESSAGES
            | hikari.Permissions.VIEW_CHANNEL
            | hikari.Permissions.READ_MESSAGE_HISTORY,
        ):
            raise arc.BotMissingPermissionsError(
                hikari.Permissions.SEND_MESSAGES
                | hikari.Permissions.VIEW_CHANNEL
                | hikari.Permissions.READ_MESSAGE_HISTORY
            )

    else:
        # We store a channel_id but the channel was deleted, so we get rid of all data
        if settings.channel_id:
            async with ctx.client.db.acquire() as con:
                await con.execute("""UPDATE starboard SET channel_id = null WHERE guild_id = $1""", ctx.guild_id)
                await con.execute("""DELETE FROM starboard_entries WHERE guild_id = $1""", ctx.guild_id)
            await ctx.client.db_cache.refresh(table="starboard", guild_id=ctx.guild_id)

        await ctx.respond(
            embed=hikari.Embed(
                title="❌ Starboard channel not set",
                description="The starboard channel is not set or is missing!",
                color=const.ERROR_COLOR,
            )
        )
        return

    entry = await StarboardEntry.fetch(message.id)

    if entry and entry.force_starred:
        await ctx.respond(
            embed=hikari.Embed(
                title="❌ Message already force starred",
                description=f"Message `{message.id}` is already forced to the starboard!",
                color=const.ERROR_COLOR,
            )
        )
        return

    stars = next((reaction.count for reaction in message.reactions if str(reaction.emoji) == "⭐"), 0)
    await star_message(message, ctx.guild_id, settings, stars, force_starred=True)

    await ctx.respond(
        embed=hikari.Embed(
            title="✅ Message force starred",
            description=f"Message `{message.id}` has been force starred!",
            color=const.EMBED_GREEN,
        )
    )


@plugin.listen()
async def on_reaction(event: hikari.GuildReactionAddEvent | hikari.GuildReactionDeleteEvent) -> None:
    """Listen for reactions & star messages where appropriate."""
    if not event.is_for_emoji("⭐") or not plugin.client.is_started:
        return

    settings = await StarboardSettings.fetch(event.guild_id)

    if settings.excluded_channels and event.channel_id in settings.excluded_channels:
        return

    me = plugin.client.cache.get_member(event.guild_id, plugin.client.user_id)

    if not me:
        return

    if settings.channel_id and (channel := plugin.client.cache.get_guild_channel(settings.channel_id)):
        perms = toolbox.calculate_permissions(me, channel)
        if not helpers.includes_permissions(
            perms,
            hikari.Permissions.SEND_MESSAGES
            | hikari.Permissions.VIEW_CHANNEL
            | hikari.Permissions.READ_MESSAGE_HISTORY,
        ):
            return

    elif settings.channel_id:
        # We store a channel_id but the channel was deleted, so we get rid of all data
        async with plugin.client.db.acquire() as con:
            await con.execute("""UPDATE starboard SET channel_id = null WHERE guild_id = $1""", event.guild_id)
            await con.execute("""DELETE FROM starboard_entries WHERE guild_id = $1""", event.guild_id)
        await plugin.client.db_cache.refresh(table="starboard", guild_id=event.guild_id)
        await plugin.client.db_cache.refresh(table="starboard_entries", guild_id=event.guild_id)
        return

    # Check perms if channel is cached
    if channel := plugin.client.cache.get_guild_channel(event.channel_id):
        perms = toolbox.calculate_permissions(me, channel)
        if not helpers.includes_permissions(
            perms,
            hikari.Permissions.VIEW_CHANNEL | hikari.Permissions.READ_MESSAGE_HISTORY,
        ):
            return

    message: hikari.Message = await plugin.client.rest.fetch_message(event.channel_id, event.message_id)
    stars = next((reaction.count for reaction in message.reactions if str(reaction.emoji) == "⭐"), 0)

    await star_message(message, event.guild_id, settings, stars)


star = plugin.include_slash_group(
    "star", "Handle the starboard.", default_permissions=hikari.Permissions.MANAGE_MESSAGES
)


@star.include
@arc.slash_subcommand("show", "Show a starboard entry.")
async def star_show(
    ctx: SnedContext,
    id: arc.Option[str, arc.StrParams("The ID of the starboard entry. You can find this in the footer of the entry.")],
) -> None:
    assert ctx.guild_id is not None

    try:
        orig_id = abs(int(id))
    except (TypeError, ValueError):
        embed = hikari.Embed(
            title="❌ Invalid value",
            description="Expected an integer value for parameter `id`.",
            color=const.ERROR_COLOR,
        )
        await ctx.respond(embed=embed)
        return

    settings = await StarboardSettings.fetch(ctx.guild_id)

    if not settings.is_enabled or not settings.channel_id:
        embed = hikari.Embed(
            title="❌ Starboard disabled",
            description="The starboard is not enabled on this server!",
            color=const.ERROR_COLOR,
        )
        await ctx.respond(embed=embed)
        return

    entry = await StarboardEntry.fetch(orig_id)

    if not entry:
        embed = hikari.Embed(title="❌ Not found", description="Starboard entry not found!", color=const.ERROR_COLOR)
        await ctx.respond(embed=embed)
        return

    message = await ctx.client.rest.fetch_message(settings.channel_id, entry.entry_message_id)
    await ctx.respond(
        f"Showing entry: `{orig_id}`\n[Jump to entry!]({message.make_link(ctx.guild_id)})", embed=message.embeds[0]
    )


@star.include
@arc.slash_subcommand("force", "Force a message onto the starboard.")
async def star_force(
    ctx: SnedContext,
    link: arc.Option[
        str,
        arc.StrParams('The link to the message. You can get this by right-clicking and choosing "Copy Message Link".'),
    ],
) -> None:
    message = await helpers.parse_message_link(ctx, link)
    if not message:
        return

    await force_star(ctx, message)


@plugin.include
@arc.message_command("Force Star")
async def star_force_context(ctx: SnedContext, message: hikari.Message) -> None:
    await force_star(ctx, message)


@arc.loader
def load(bot: SnedClient) -> None:
    bot.add_plugin(plugin)


@arc.unloader
def unload(bot: SnedClient) -> None:
    bot.remove_plugin(plugin)


# Copyright (C) 2022-present hypergonial

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program.  If not, see: https://www.gnu.org/licenses
