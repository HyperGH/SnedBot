import logging

import hikari
import lightbulb
import miru
from models.bot import SnedBot
from utils import helpers
import typing as t
from models import SnedSlashContext

logger = logging.getLogger(__name__)

reports = lightbulb.Plugin("Reports")


class ReportModal(miru.Modal):
    def __init__(self, member: hikari.Member) -> None:
        super().__init__(f"Reporting {member}", autodefer=False)
        self.add_item(
            miru.TextInput(
                label="Reason for the Report",
                placeholder="Please enter why you believe this user should be investigated...",
                style=hikari.TextInputStyle.PARAGRAPH,
                max_length=1000,
                required=True,
            )
        )
        self.add_item(
            miru.TextInput(
                label="Additional Context",
                placeholder="If you have any additional information or proof (e.g. screenshots), please link them here.",
                style=hikari.TextInputStyle.PARAGRAPH,
                max_length=1000,
            )
        )
        self.reason: str = None
        self.info: str = None

    async def callback(self, ctx: miru.ModalContext) -> None:
        if not ctx.values:
            return

        for item, value in ctx.values.items():
            if item.label == "Reason for the Report":
                self.reason = value
            elif item.label == "Additional Context":
                self.info = value

        await ctx.defer(flags=hikari.MessageFlag.EPHEMERAL)


async def report_error(ctx: SnedSlashContext) -> None:
    guild = ctx.get_guild()
    embed = hikari.Embed(
        title="❌ Oops!",
        description=f"It looks like the moderators of **{guild.name}** did not enable this functionality.",
        color=ctx.app.error_color,
    )
    await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)


async def report_perms_error(ctx: SnedSlashContext) -> None:
    guild = ctx.get_guild()
    embed = hikari.Embed(
        title="❌ Oops!",
        description=f"It looks like I do not have permissions to create a message in the reports channel. Please notify a moderator!",
        color=ctx.app.error_color,
    )
    await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)


async def report(ctx: SnedSlashContext, member: hikari.Member, message: t.Optional[hikari.Message] = None) -> None:

    if member.id == ctx.member.id or member.is_bot:
        embed = hikari.Embed(
            title="❌ Huh?",
            description=f"I'm not sure how that would work...",
            color=ctx.app.error_color,
        )
        return await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)

    records = await ctx.app.db_cache.get(table="reports", guild_id=ctx.guild_id)

    if not records or not records[0]["is_enabled"]:
        return await report_error(ctx)

    channel = ctx.app.cache.get_guild_channel(records[0]["channel_id"])

    if not channel:
        await ctx.app.pool.execute(
            """INSERT INTO reports (is_enabled, guild_id)
            VALUES ($1, $2)
            ON CONFLICT (guild_id) DO
            UPDATE SET is_enabled = $1""",
            False,
            ctx.guild_id,
        )
        await ctx.app.db_cache.refresh(table="reports", guild_id=ctx.guild_id)
        return await report_error(ctx)

    perms = lightbulb.utils.permissions_in(channel, ctx.app.cache.get_member(ctx.guild_id, ctx.app.user_id))

    if not (perms & hikari.Permissions.SEND_MESSAGES):
        return await report_perms_error(ctx)

    modal = ReportModal(member)
    await modal.send(ctx.interaction)
    await modal.wait()

    if not modal.reason:  # Modal was closed/timed out
        return

    role_ids = records[0]["pinged_role_ids"] or []
    roles = filter(lambda r: r is not None, [ctx.app.cache.get_role(role_id) for role_id in role_ids])
    role_mentions = [role.mention for role in roles]

    embed = hikari.Embed(
        title="⚠️ New Report",
        description=f"""
**Reporter:** {ctx.member.mention} `({ctx.member.id})`
**Reported User:**  {member.mention} `({member.id})`
**Reason:** ```{modal.reason}```
**Additional Context:** ```{modal.info or "Not provided."}```""",
        color=ctx.app.warn_color,
    )

    feedback_embed = hikari.Embed(
        title="✅ Report Submitted",
        description="A moderator will review your report shortly!",
        color=ctx.app.embed_green,
    )

    components = hikari.UNDEFINED

    if message:
        view = miru.View()
        view.add_item(miru.Button(label="Associated Message", url=message.make_link(ctx.guild_id)))
        components = view.build()

    await channel.send(
        " ".join(role_mentions) or hikari.UNDEFINED, embed=embed, components=components, role_mentions=True
    )
    await modal.get_response_context().respond(embed=feedback_embed, flags=hikari.MessageFlag.EPHEMERAL)


@reports.command()
@lightbulb.option("user", "The user that is to be reported.", type=hikari.Member, required=True)
@lightbulb.command("report", "Report a user to the moderation team of this server.")
@lightbulb.implements(lightbulb.SlashCommand)
async def report_cmd(ctx: lightbulb.SlashContext) -> None:
    helpers.is_member(ctx.options.user)
    await report(ctx, ctx.options.user)


@reports.command()
@lightbulb.command("Report User", "Report the targeted user to the moderation team of this server.")
@lightbulb.implements(lightbulb.UserCommand)
async def report_user_cmd(ctx: lightbulb.UserContext) -> None:
    await report(ctx, ctx.options.target)


@reports.command()
@lightbulb.command("Report Message", "Report the targeted message to the moderation team of this server.")
@lightbulb.implements(lightbulb.MessageCommand)
async def report_msg_cmd(ctx: lightbulb.MessageContext) -> None:
    member = ctx.app.cache.get_member(ctx.guild_id, ctx.options.target.author)
    await report(ctx, member, ctx.options.target)


def load(bot: SnedBot) -> None:
    bot.add_plugin(reports)


def unload(bot: SnedBot) -> None:
    bot.remove_plugin(reports)