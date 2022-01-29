import logging
from difflib import get_close_matches
from itertools import chain
from typing import List

import hikari
import lightbulb
import miru
from miru.ext import nav
from objects.models.errors import TagAlreadyExists, TagNotFound
from objects.models.tag import Tag
from objects.models.views import AuthorOnlyNavigator
from objects.tag_handler import TagHandler
from objects.utils import helpers

logger = logging.getLogger(__name__)

tags = lightbulb.Plugin("Tag", include_datastore=True)


@tags.command()
@lightbulb.command("tag", "All commands involving tags.")
@lightbulb.implements(lightbulb.SlashCommandGroup)
async def tag(ctx: lightbulb.SlashContext) -> None:
    pass


@tag.child()
@lightbulb.option("name", "The name of the tag you want to call.")
@lightbulb.command("call", "Call a tag and display it's contents.")
@lightbulb.implements(lightbulb.SlashSubCommand)
async def tag_call(ctx: lightbulb.SlashContext) -> None:
    tag: Tag = await tags.d.tag_handler.get(ctx.options.name.lower(), ctx.guild_id)

    if tag:
        await ctx.respond(content=tag.content)
    else:
        embed = hikari.Embed(
            title="❌ Unknown tag",
            description="Cannot find tag by that name.",
            color=ctx.app.error_color,
        )
        await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)


@tag.child()
@lightbulb.option("content", "The content of the tag to create.")
@lightbulb.option("name", "The name of the tag to create.")
@lightbulb.command("create", "Create a new tag with the specified name and content.")
@lightbulb.implements(lightbulb.SlashSubCommand)
async def tag_create(ctx: lightbulb.SlashContext) -> None:
    tag: Tag = await tags.d.tag_handler.get(ctx.options.name.lower(), ctx.guild_id)
    if tag:
        embed = hikari.Embed(
            title="❌ Tag exists",
            description=f"This tag already exists. If the owner of this tag is no longer in the server, you can try doing `/tag claim {ctx.options.name.lower()}`",
            color=ctx.app.error_color,
        )
        return await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)

    new_tag = Tag(
        guild_id=ctx.guild_id,
        name=ctx.options.name.lower(),
        owner_id=ctx.author.id,
        aliases=None,
        content=ctx.options.content,
    )
    await tags.d.tag_handler.create(new_tag)
    embed = hikari.Embed(
        title="✅ Tag created!",
        description=f"You can now call it with `/tag call {ctx.options.name.lower()}`",
        color=ctx.app.embed_green,
    )
    embed = helpers.add_embed_footer(embed, ctx.member)
    await ctx.respond(embed=embed)


@tag.child()
@lightbulb.option("name", "The name of the tag to get information about.")
@lightbulb.command("info", "Display information about the specified tag.")
@lightbulb.implements(lightbulb.SlashSubCommand)
async def tag_info(ctx: lightbulb.SlashContext) -> None:
    tag: Tag = await tags.d.tag_handler.get(ctx.options.name.lower(), ctx.guild_id)
    if tag:
        owner = await ctx.app.rest.fetch_user(tag.owner_id)
        if tag.aliases:
            aliases = ", ".join(tag.aliases)
        else:
            aliases = None
        embed = hikari.Embed(
            title=f"💬 Tag Info: {tag.name}",
            description=f"**Aliases:** `{aliases}`\n**Tag owner:** `{owner}`\n",
            color=ctx.app.embed_blue,
        )
        embed.set_author(name=str(owner), icon=helpers.get_avatar(owner))
        embed = helpers.add_embed_footer(embed, ctx.member)
        await ctx.respond(embed=embed)

    else:
        embed = hikari.Embed(
            title="❌ Unknown tag",
            description="Cannot find tag by that name.",
            color=ctx.app.error_color,
        )
        await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)


@tag.child()
@lightbulb.option("alias", "The alias to add to this tag.")
@lightbulb.option("name", "The tag to add an alias for.")
@lightbulb.command("alias", "Adds an alias to a tag you own.")
@lightbulb.implements(lightbulb.SlashSubCommand)
async def tag_alias(ctx: lightbulb.SlashContext) -> None:
    alias_tag: Tag = await tags.d.tag_handler.get(ctx.options.alias.lower(), ctx.guild_id)
    if alias_tag:
        embed = hikari.Embed(
            title="❌ Alias taken",
            description=f"A tag or alias already exists with a same name. Try picking a different alias.",
            color=ctx.app.error_color,
        )
        return await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)

    tag: Tag = await tags.d.tag_handler.get(ctx.options.name.lower(), ctx.guild_id)
    if tag and tag.owner_id == ctx.author.id:
        tag.aliases = tag.aliases if tag.aliases is not None else []

        if ctx.options.alias.lower() not in tag.aliases and len(tag.aliases) <= 5:
            tag.aliases.append(ctx.options.alias.lower())

        else:
            embed = hikari.Embed(
                title="❌ Too many aliases",
                description=f"Tag `{tag.name}` can only have up to **5** aliases.",
                color=ctx.app.error_color,
            )
            return await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)

        await tags.d.tag_handler.delete(tag.name, ctx.guild_id)
        await tags.d.tag_handler.create(tag)  # TODO: Add an update method to tag handler
        embed = hikari.Embed(
            title="✅ Alias created",
            description=f"You can now call it with `/tag {ctx.options.alias.lower()}`",
            color=ctx.app.embed_green,
        )
        embed = helpers.add_embed_footer(embed, ctx.member)
        await ctx.respond(embed=embed)

    else:
        embed = hikari.Embed(
            title="❌ Invalid tag",
            description="You either do not own this tag or it does not exist.",
            color=ctx.app.error_color,
        )
        return await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)


@tag.child()
@lightbulb.option("alias", "The name of the alias to remove.")
@lightbulb.option("name", "The tag to remove the alias from.")
@lightbulb.command("delalias", "Remove an alias from a tag you own.")
@lightbulb.implements(lightbulb.SlashSubCommand)
async def tag_delalias(ctx: lightbulb.SlashContext) -> None:
    tag: Tag = await tags.d.tag_handler.get(ctx.options.name.lower(), ctx.guild_id)
    if tag and tag.owner_id == ctx.author.id:

        if ctx.options.alias.lower() in tag.aliases:
            tag.aliases.remove(ctx.options.alias.lower())

        else:
            embed = hikari.Embed(
                title="❌ Unknown alias",
                description=f"Tag `{tag.name}` does not have an alias called `{ctx.options.alias.lower()}`",
                color=ctx.app.error_color,
            )
            return await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)

        await tags.d.tag_handler.delete(tag.name, ctx.guild_id)
        await tags.d.tag_handler.create(tag)
        embed = hikari.Embed(
            title="✅ Alias removed",
            description=f"Alias {ctx.options.alias.lower()} for tag {tag.name} has been deleted.",
            color=ctx.app.embed_green,
        )
        embed = helpers.add_embed_footer(embed, ctx.member)
        await ctx.respond(embed=embed)

    else:
        embed = hikari.Embed(
            title="❌ Invalid tag",
            description="You either do not own this tag or it does not exist.",
            color=ctx.app.error_color,
        )
        return await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)


@tag.child()
@lightbulb.option("receiver", "The user to receive the tag.", type=hikari.Member)
@lightbulb.option("name", "The name of the tag to transfer.")
@lightbulb.command("transfer", "Transfer ownership of a tag to another user, letting them modify or delete it.")
@lightbulb.implements(lightbulb.SlashSubCommand)
async def tag_transfer(ctx: lightbulb.SlashContext) -> None:
    tag: Tag = await tags.d.tag_handler.get(ctx.options.name.lower(), ctx.guild_id)
    if tag and tag.owner_id == ctx.author.id:
        await tags.d.tag_handler.delete(tag.name, ctx.guild_id)
        tag.owner_id = ctx.options.receiver.id
        await tags.d.tag_handler.create(tag)
        embed = hikari.Embed(
            title="✅ Tag transferred",
            description=f"Tag `{tag.name}`'s ownership was successfully transferred to {ctx.options.receiver.mention}",
            color=ctx.app.embed_green,
        )
        embed = helpers.add_embed_footer(embed, ctx.member)
        await ctx.respond(embed=embed)

    else:
        embed = hikari.Embed(
            title="❌ Invalid tag",
            description="You either do not own this tag or it does not exist.",
            color=ctx.app.error_color,
        )
        return await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)


@tag.child()
@lightbulb.option("name", "The name of the tag to claim.")
@lightbulb.command("claim", "Claim a tag that has been created by a user that has since left the server.")
@lightbulb.implements(lightbulb.SlashSubCommand)
async def tag_claim(ctx: lightbulb.SlashContext) -> None:
    tag: Tag = await tags.d.tag_handler.get(ctx.options.name.lower(), ctx.guild_id)

    if tag:
        members = ctx.app.cache.get_members_view_for_guild(ctx.guild_id)
        if tag.owner_id not in members.keys():
            await tags.d.tag_handler.delete(tag.name, ctx.guild_id)
            tag.owner_id = ctx.author.id
            await tags.d.tag_handler.create(tag)
            embed = hikari.Embed(
                title="✅ Tag claimed", description=f"Tag `{tag.name}` now belongs to you.", color=ctx.app.embed_green
            )
            embed = helpers.add_embed_footer(embed, ctx.member)
            await ctx.respond(embed=embed)

        else:
            embed = hikari.Embed(
                title="❌ Owner present",
                description="Tag owner is still in the server. You can only claim tags that have been abandoned.",
                color=ctx.app.error_color,
            )
            return await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)

    else:
        embed = hikari.Embed(
            title="❌ Unknown tag",
            description="Cannot find tag by that name.",
            color=ctx.app.error_color,
        )
        return await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)


@tag.child()
@lightbulb.option("new_content", "The new content for this tag.")
@lightbulb.option("name", "The name of the tag to edit.")
@lightbulb.command("edit", "Edit the content of a tag you own.")
@lightbulb.implements(lightbulb.SlashSubCommand)
async def tag_edit(ctx: lightbulb.SlashContext) -> None:
    tag: Tag = await tags.d.tag_handler.get(ctx.options.name.lower(), ctx.guild_id)
    if tag and tag.owner_id == ctx.author.id:
        await tags.d.tag_handler.delete(tag.name, ctx.guild_id)
        tag.content = ctx.options.new_content
        await tags.d.tag_handler.create(tag)

        embed = hikari.Embed(
            title="✅ Tag edited",
            description=f"Tag `{tag.name}` has been successfully edited.",
            color=ctx.app.embed_green,
        )
        embed = helpers.add_embed_footer(embed, ctx.member)
        await ctx.respond(embed=embed)

    else:
        embed = hikari.Embed(
            title="❌ Invalid tag",
            description="You either do not own this tag or it does not exist.",
            color=ctx.app.error_color,
        )
        return await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)


@tag.child()
@lightbulb.option("name", "The name of the tag to delete.")
@lightbulb.command("delete", "Delete a tag you own.")
@lightbulb.implements(lightbulb.SlashSubCommand)
async def tag_delete(ctx: lightbulb.SlashContext) -> None:
    tag: Tag = await tags.d.tag_handler.get(ctx.options.name.lower(), ctx.guild_id)
    if tag and tag.owner_id == ctx.author.id:
        await tags.d.tag_handler.delete(ctx.options.name.lower(), ctx.guild_id)
        embed = hikari.Embed(
            title="✅ Tag deleted", description=f"Tag `{tag.name}` has been deleted.", color=ctx.app.embed_green
        )
        embed = helpers.add_embed_footer(embed, ctx.member)
        await ctx.respond(embed=embed)

    else:
        embed = hikari.Embed(
            title="❌ Invalid tag",
            description="You either do not own this tag or it does not exist.",
            color=ctx.app.error_color,
        )
        return await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)


@tag.child()
@lightbulb.command("list", "List all tags this server has.")
@lightbulb.implements(lightbulb.SlashSubCommand)
async def tag_list(ctx: lightbulb.SlashContext) -> None:
    tags_list: List[Tag] = await tags.d.tag_handler.get_all(ctx.guild_id)

    if tags_list:
        tags_fmt = []
        for i, tag in enumerate(tags_list):
            tags_fmt.append(f"**#{i+1}** {tag.name}")
        # Only show 10 tags per page
        tags_fmt = [tags_fmt[i * 10 : (i + 1) * 10] for i in range((len(tags_fmt) + 10 - 1) // 10)]
        embeds = []
        for contents in tags_fmt:
            embed = hikari.Embed(
                title="💬 Available tags for this server:", description="\n".join(contents), color=ctx.app.embed_blue
            )
            helpers.add_embed_footer(embed, ctx.member)
            embeds.append(embed)

        navigator = AuthorOnlyNavigator(ctx, pages=embeds)
        await navigator.send(ctx.interaction)

    else:
        embed = hikari.Embed(
            title="💬 Available tags for this server:",
            description="There are no tags on this server yet! You can create one via `/tag create`",
            color=ctx.app.embed_blue,
        )
        helpers.add_embed_footer(embed, ctx.member)
        await ctx.respond(embed=embed)


@tag.child()
@lightbulb.option("query", "The tag name or alias to search for.")
@lightbulb.command("search", "Search for a tag name or alias.")
@lightbulb.implements(lightbulb.SlashSubCommand)
async def tag_search(ctx: lightbulb.SlashContext) -> None:
    tags_list = await tags.d.tag_handler.get_all(ctx.guild_id)

    if tags_list:
        names = [tag.name for tag in tags_list]
        aliases = []
        for tag in tags_list:
            if tag.aliases:
                aliases.append(tag.aliases)
        aliases = list(chain(*aliases))

        name_matches = get_close_matches(ctx.options.query.lower(), names)
        alias_matches = get_close_matches(ctx.options.query.lower(), aliases)

        response = []
        if len(name_matches) > 0:
            for name in name_matches:
                response.append(name)

        if len(alias_matches) > 0:
            for name in alias_matches:
                response.append(f"*{name}*")

        if len(response) > 0:
            if len(response) < 10:
                response = response[0:10]
            embed = hikari.Embed(title="🔎 Search results:", description="\n".join(response))
            embed = helpers.add_embed_footer(embed, ctx.member)
            await ctx.respond(embed=embed)

        else:
            embed = hikari.Embed(
                title="Not found", description="Unable to find tags with that name.", color=ctx.app.warn_color
            )
            embed = helpers.add_embed_footer(embed, ctx.member)
            await ctx.respond(embed=embed)

    else:
        embed = hikari.Embed(
            title="🔎 Search failed",
            description="There are no tags on this server yet! You can create one via `/tag create`",
            color=ctx.app.warn_color,
        )
        helpers.add_embed_footer(embed, ctx.member)
        await ctx.respond(embed=embed)


def load(bot):
    logging.info("Adding plugin: Tags")
    tag_handler = TagHandler(bot)
    bot.add_plugin(tags)
    tags.d.tag_handler = tag_handler


def unload(bot):
    logging.info("Removing plugin: Tags")
    bot.remove_plugin(tags)