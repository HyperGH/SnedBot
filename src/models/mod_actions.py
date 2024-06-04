from __future__ import annotations

import datetime
import enum
import json
import logging
import typing as t
from contextlib import suppress

import attr
import hikari
import lightbulb
import miru
import toolbox
from miru.abc import ViewItem

from src.etc import const
from src.etc.settings_static import default_automod_policies
from src.models.db_user import DatabaseUser, DatabaseUserFlag
from src.models.errors import DMFailedError, RoleHierarchyError
from src.models.events import TimerCompleteEvent, WarnCreateEvent, WarnRemoveEvent, WarnsClearEvent
from src.models.journal import JournalEntry
from src.models.timer import TimerEvent
from src.models.views import AuthorOnlyNavigator
from src.utils import helpers

if t.TYPE_CHECKING:
    from src.models.client import SnedClient

logger = logging.getLogger(__name__)


class ActionType(enum.Enum):
    """Enum containing all possible moderation actions."""

    BAN = "Ban"
    SOFTBAN = "Softban"
    TEMPBAN = "Tempban"
    KICK = "Kick"
    TIMEOUT = "Timeout"
    WARN = "Warn"


class ModerationFlags(enum.Flag):
    """A set of flags governing behaviour of moderation actions."""

    NONE = 0
    """An empty set of moderation setting flags."""

    DM_USERS_ON_PUNISH = 1 << 0
    """DM users when punishing them via a moderation action."""

    IS_EPHEMERAL = 1 << 1
    """Responses to moderation actions should be done ephemerally."""


@attr.define()
class ModerationSettings:
    """Settings for moderation actions."""

    flags: ModerationFlags = ModerationFlags.DM_USERS_ON_PUNISH
    """Flags governing behaviour of moderation actions."""


MAX_TIMEOUT_SECONDS = 2246400  # Duration of segments to break timeouts up to


class ModActions:
    """Class containing all moderation actions that can be performed by the bot.
    It also handles miscallaneous moderation tasks such as tempban timers, timeout chunks & more.
    """

    def __init__(self, client: SnedClient) -> None:
        self._client = client
        self._client.subscribe(TimerCompleteEvent, self.timeout_extend)
        self._client.subscribe(hikari.MemberCreateEvent, self.reapply_timeout_extensions)
        self._client.subscribe(hikari.MemberUpdateEvent, self.remove_timeout_extensions)
        self._client.subscribe(TimerCompleteEvent, self.tempban_expire)
        # TODO: Add when miru templates exist
        self._client.subscribe(hikari.InteractionCreateEvent, self.handle_mod_buttons)

    async def get_settings(self, guild: hikari.SnowflakeishOr[hikari.PartialGuild]) -> ModerationSettings:
        """Get moderation settings for a guild.

        Parameters
        ----------
        guild : hikari.SnowflakeishOr[hikari.PartialGuild]
            The guild to get moderation settings for.

        Returns
        -------
        dict[str, bool]
            The guild's moderation settings.
        """
        records = await self._client.db_cache.get(table="mod_config", guild_id=hikari.Snowflake(guild))
        if records:
            return ModerationSettings(flags=ModerationFlags(records[0].get("flags")))

        return ModerationSettings()

    async def get_msg_flags(self, guild: hikari.SnowflakeishOr[hikari.PartialGuild]) -> hikari.MessageFlag:
        """Get the message flags for a guild.

        Parameters
        ----------
        guild : hikari.SnowflakeishOr[hikari.PartialGuild]
            The guild to get message flags for.

        Returns
        -------
        hikari.MessageFlag
            The guild's message flags.
        """
        return (
            hikari.MessageFlag.EPHEMERAL
            if (await self.is_ephemeral(hikari.Snowflake(guild)))
            else hikari.MessageFlag.NONE
        )

    async def is_ephemeral(self, guild: hikari.SnowflakeishOr[hikari.PartialGuild]) -> bool:
        """Check if responses to moderation actions should be done ephemerally."""
        return bool((await self.get_settings(hikari.Snowflake(guild))).flags & ModerationFlags.IS_EPHEMERAL)

    # TODO: Purge this cursed abomination
    async def get_automod_policies(self, guild: hikari.SnowflakeishOr[hikari.Guild]) -> dict[str, t.Any]:
        """Return auto-moderation policies for the specified guild.

        Parameters
        ----------
        guild : hikari.SnowflakeishOr[hikari.Guild]
            The guild to get policies for.

        Returns
        -------
        dict[str, t.Any]
            The guild's auto-moderation policies.
        """
        guild_id = hikari.Snowflake(guild)

        records = await self._client.db_cache.get(table="mod_config", guild_id=guild_id)

        policies = json.loads(records[0]["automod_policies"]) if records else default_automod_policies

        for key in default_automod_policies:
            if key not in policies:
                policies[key] = default_automod_policies[key]

            for nested_key in default_automod_policies[key]:
                if nested_key not in policies[key]:
                    policies[key][nested_key] = default_automod_policies[key][nested_key]

        invalid = []
        for key in policies:
            if key not in default_automod_policies:
                invalid.append(key)

        for key in invalid:
            policies.pop(key)

        return policies

    async def pre_mod_actions(
        self,
        guild: hikari.SnowflakeishOr[hikari.Guild],
        target: hikari.Member | hikari.User,
        action_type: ActionType,
        reason: str | None = None,
    ) -> None:
        """Actions that need to be executed before a moderation action takes place."""
        helpers.format_reason(reason, max_length=1500)
        guild_id = hikari.Snowflake(guild)
        settings = await self.get_settings(guild_id)
        types_conj = {
            ActionType.WARN: "warned in",
            ActionType.TIMEOUT: "timed out in",
            ActionType.KICK: "kicked from",
            ActionType.BAN: "banned from",
            ActionType.SOFTBAN: "soft-banned from",
            ActionType.TEMPBAN: "temp-banned from",
        }

        if settings.flags & ModerationFlags.DM_USERS_ON_PUNISH and isinstance(target, hikari.Member):
            gateway_guild = self._client.cache.get_guild(guild_id)
            assert isinstance(gateway_guild, hikari.GatewayGuild)
            guild_name = gateway_guild.name if gateway_guild else "Unknown server"
            try:
                await target.send(
                    embed=hikari.Embed(
                        title=f"❗ You have been {types_conj[action_type]} **{guild_name}**",
                        description=f"You have been {types_conj[action_type]} **{guild_name}**.\n**Reason:** ```{reason}```",
                        color=const.ERROR_COLOR,
                    )
                )
            except (hikari.ForbiddenError, hikari.HTTPError):
                raise DMFailedError("Failed delivering direct message to user.")

    async def post_mod_actions(
        self,
        guild: hikari.SnowflakeishOr[hikari.Guild],
        target: hikari.Member | hikari.User,
        action_type: ActionType,
        reason: str | None = None,
    ) -> None:
        """Actions that need to be executed after a moderation action took place."""
        pass

    async def handle_mod_buttons(self, event: hikari.InteractionCreateEvent) -> None:
        """Handle buttons related to moderation quick-actions."""
        # Format: ACTION:<user_id>:<moderator_id>
        if not isinstance(event.interaction, hikari.ComponentInteraction):
            return

        inter = event.interaction

        if not inter.custom_id.startswith(("UNBAN:", "JOURNAL:")):
            return

        moderator_id = hikari.Snowflake(inter.custom_id.split(":")[2])

        if moderator_id != inter.user.id:
            await inter.create_initial_response(
                hikari.ResponseType.MESSAGE_CREATE,
                embed=hikari.Embed(
                    title="❌ Action prohibited",
                    description="This action is only available to the moderator who executed the command.",
                    color=const.ERROR_COLOR,
                ),
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        action = inter.custom_id.split(":")[0]
        user_id = hikari.Snowflake(inter.custom_id.split(":")[1])

        assert inter.member and inter.guild_id

        if action == "UNBAN":
            perms = toolbox.calculate_permissions(inter.member)
            if not helpers.includes_permissions(perms, hikari.Permissions.BAN_MEMBERS):
                await inter.create_initial_response(
                    hikari.ResponseType.MESSAGE_CREATE,
                    embed=hikari.Embed(
                        title="❌ Missing Permissions",
                        description="You do not have the required permissions to unban members.",
                        color=const.ERROR_COLOR,
                    ),
                    flags=hikari.MessageFlag.EPHEMERAL,
                )
                return

            user = await event.app.rest.fetch_user(user_id)
            embed = await self.unban(
                user,
                inter.member,
                reason=helpers.format_reason("Unbanned via quick-action button.", moderator=inter.member),
            )
            await inter.create_initial_response(
                hikari.ResponseType.MESSAGE_CREATE, embed=embed, flags=hikari.MessageFlag.EPHEMERAL
            )

        if action == "JOURNAL":
            journal = await JournalEntry.fetch_journal(user_id, inter.guild_id)

            if journal:
                navigator = AuthorOnlyNavigator(event.context, pages=helpers.build_journal_pages(journal))  # type: ignore
                await (await navigator.build_response_async(self._client.miru, ephemeral=True)).create_initial_response(
                    inter
                )
                self._client.miru.start_view(navigator)

            else:
                await inter.create_initial_response(
                    hikari.ResponseType.MESSAGE_CREATE,
                    embed=hikari.Embed(
                        title="📒 Journal entries for this user:",
                        description=f"There are no journal entries for this user yet. Any moderation-actions will leave an entry here, or you can set one manually with `/journal add {inter.user}`",
                        color=const.EMBED_BLUE,
                    ),
                    flags=hikari.MessageFlag.EPHEMERAL,
                )

        view = miru.View.from_message(inter.message)

        for item in view.children:
            assert isinstance(item, ViewItem)
            if item.custom_id == inter.custom_id:
                item.disabled = True

        with suppress(hikari.ForbiddenError, hikari.NotFoundError):
            await inter.message.edit(components=view)

    async def timeout_extend(self, event: TimerCompleteEvent) -> None:
        """Extends timeouts longer than 28 days by breaking them into multiple chunks."""
        timer = event.timer

        if timer.event != TimerEvent.TIMEOUT_EXTEND:
            return

        if not event.get_guild():
            return

        member = event.app.cache.get_member(timer.guild_id, timer.user_id)
        assert timer.notes is not None
        expiry = int(timer.notes)

        if member:
            me = self._client.cache.get_member(timer.guild_id, self._client.user_id)
            assert me is not None

            if not helpers.can_harm(me, member, hikari.Permissions.MODERATE_MEMBERS):
                return

            if expiry - helpers.utcnow().timestamp() > MAX_TIMEOUT_SECONDS:
                await self._client.scheduler.create_timer(
                    helpers.utcnow() + datetime.timedelta(seconds=MAX_TIMEOUT_SECONDS),
                    TimerEvent.TIMEOUT_EXTEND,
                    timer.guild_id,
                    member,
                    notes=timer.notes,
                )
                await member.edit(
                    communication_disabled_until=helpers.utcnow() + datetime.timedelta(seconds=MAX_TIMEOUT_SECONDS),
                    reason=f"Automatic timeout extension applied. Timed out until {datetime.datetime.fromtimestamp(expiry, datetime.timezone.utc).isoformat()}.",
                )

            else:
                timeout_for = helpers.utcnow() + datetime.timedelta(
                    seconds=expiry - round(helpers.utcnow().timestamp())
                )
                await member.edit(
                    communication_disabled_until=timeout_for, reason="Automatic timeout extension applied."
                )

        else:
            db_user = await DatabaseUser.fetch(timer.user_id, timer.guild_id)

            if DatabaseUserFlag.TIMEOUT_ON_JOIN ^ db_user.flags:
                db_user.flags = db_user.flags | DatabaseUserFlag.TIMEOUT_ON_JOIN
                db_user.data["timeout_expiry"] = expiry
                await db_user.update()

    async def reapply_timeout_extensions(self, event: hikari.MemberCreateEvent):
        """Reapply timeout if a member left between two timeout extension cycles."""
        me = self._client.cache.get_member(event.guild_id, self._client.user_id)
        assert me is not None

        if not helpers.can_harm(me, event.member, hikari.Permissions.MODERATE_MEMBERS):
            return

        db_user = await DatabaseUser.fetch(event.member.id, event.guild_id)

        if db_user.flags ^ DatabaseUserFlag.TIMEOUT_ON_JOIN:
            return

        expiry = db_user.data.pop("timeout_expiry", 0)
        db_user.flags = db_user.flags ^ DatabaseUserFlag.TIMEOUT_ON_JOIN
        await db_user.update()

        if expiry - helpers.utcnow().timestamp() < 0:
            # If this is in the past already
            return

        if expiry - helpers.utcnow().timestamp() > MAX_TIMEOUT_SECONDS:
            await self._client.scheduler.create_timer(
                helpers.utcnow() + datetime.timedelta(seconds=MAX_TIMEOUT_SECONDS),
                TimerEvent.TIMEOUT_EXTEND,
                event.member.guild_id,
                event.member,
                notes=str(expiry),
            )
            await event.member.edit(
                communication_disabled_until=helpers.utcnow() + datetime.timedelta(seconds=MAX_TIMEOUT_SECONDS),
                reason=f"Automatic timeout extension applied. Timed out until {datetime.datetime.fromtimestamp(expiry, datetime.timezone.utc).isoformat()}.",
            )

        else:
            await event.member.edit(
                communication_disabled_until=helpers.utcnow() + datetime.timedelta(seconds=expiry),
                reason="Automatic timeout extension applied.",
            )

    async def remove_timeout_extensions(self, event: hikari.MemberUpdateEvent):
        """Remove all timeout extensions if a user's timeout was removed."""
        if not event.old_member:
            return

        if event.old_member.communication_disabled_until() == event.member.communication_disabled_until():
            return

        if event.member.communication_disabled_until() is None:
            records = await self._client.db.fetch(
                """SELECT id FROM timers WHERE guild_id = $1 AND user_id = $2 AND event = $3""",
                event.guild_id,
                event.member.id,
                TimerEvent.TIMEOUT_EXTEND.value,
            )

            if not records:
                return

            for record in records:
                await self._client.scheduler.cancel_timer(record["id"], event.guild_id)

    async def tempban_expire(self, event: TimerCompleteEvent) -> None:
        """Handle tempban timer expiry and unban user."""
        if event.timer.event != TimerEvent.TEMPBAN:
            return

        # Ensure the guild still exists
        guild = event.get_guild()

        if not guild:
            return

        try:
            await guild.unban(event.timer.user_id, reason="User unbanned: Tempban expired.")
        except Exception as e:
            logger.info(f"Failed unbanning {event.timer.user_id} from {event.timer.guild_id}: {e.__class__}: {e}")

    async def warn(self, member: hikari.Member, moderator: hikari.Member, reason: str | None = None) -> hikari.Embed:
        """Warn a user, incrementing their warn counter, and logging the event if it is set up.

        Parameters
        ----------
        member : hikari.Member
            The member to be warned.
        moderator : hikari.Member
            The moderator who warned the member.
        reason : str | None, optional
            The reason for this action, by default None

        Returns
        -------
        hikari.Embed
            The response to show to the invoker.
        """
        db_user = await DatabaseUser.fetch(member.id, member.guild_id)
        db_user.warns += 1
        await db_user.update()

        reason = helpers.format_reason(reason, max_length=1000)

        embed = hikari.Embed(
            title="⚠️ Warning issued",
            description=f"**{member}** has been warned by **{moderator}**.\n**Reason:** ```{reason}```",
            color=const.WARN_COLOR,
        )
        try:
            await self.pre_mod_actions(member.guild_id, member, ActionType.WARN, reason)
        except DMFailedError:
            embed.set_footer("Failed sending DM to user.")

        await self._client.app.dispatch(
            WarnCreateEvent(self._client.app, member.guild_id, member, moderator, db_user.warns, reason)
        )
        await self.post_mod_actions(member.guild_id, member, ActionType.WARN, reason)
        return embed

    async def clear_warns(
        self, member: hikari.Member, moderator: hikari.Member, reason: str | None = None
    ) -> hikari.Embed:
        """Clear a user's warns, dispatches a WarnsClearEvent.

        Parameters
        ----------
        member : hikari.Member
            The member to clear warnings for.
        moderator : hikari.Member
            The moderator responsible for clearing the warnings.
        reason : str | None, optional
            The reason for clearing the warnings, by default None

        Returns
        -------
        hikari.Embed
            The response to show to the invoker.
        """
        db_user = await DatabaseUser.fetch(member, member.guild_id)
        db_user.warns = 0
        await db_user.update()

        reason = helpers.format_reason(reason)

        await self._client.app.dispatch(
            WarnsClearEvent(self._client.app, member.guild_id, member, moderator, db_user.warns, reason)
        )

        return hikari.Embed(
            title="✅ Warnings cleared",
            description=f"**{member}**'s warnings have been cleared.\n**Reason:** ```{reason}```",
            color=const.EMBED_GREEN,
        )

    async def remove_warn(
        self, member: hikari.Member, moderator: hikari.Member, reason: str | None = None
    ) -> hikari.Embed:
        """Removes a warning from the user, dispatches a WarnRemoveEvent.

        Parameters
        ----------
        member : hikari.Member
            The member to remove a warning from.
        moderator : hikari.Member
            The moderator responsible for removing the warning.
        reason : str | None, optional
            The reason for removing the warning, by default None

        Returns
        -------
        hikari.Embed
            The response to show to the invoker.
        """
        db_user = await DatabaseUser.fetch(member, member.guild_id)

        if db_user.warns <= 0:
            return hikari.Embed(
                title="❌ No Warnings",
                description="This user has no warnings!",
                color=const.ERROR_COLOR,
            )

        db_user.warns -= 1
        await db_user.update()

        reason = helpers.format_reason(reason)

        await self._client.app.dispatch(
            WarnRemoveEvent(self._client.app, member.guild_id, member, moderator, db_user.warns, reason)
        )

        return hikari.Embed(
            title="✅ Warning removed",
            description=f"Warning removed from **{member}**.\n**Current count:** `{db_user.warns}`\n**Reason:** ```{reason}```",
            color=const.EMBED_GREEN,
        )

    async def timeout(
        self,
        member: hikari.Member,
        moderator: hikari.Member,
        duration: datetime.datetime,
        reason: str | None = None,
    ) -> hikari.Embed:
        """Time out the member for the specified duration.

        Parameters
        ----------
        member : hikari.Member
            The member to time out.
        moderator : hikari.Member
            The moderator to log the timeout under.
        duration : datetime.datetime
            The duration of the timeout.
        reason : str | None, optional
            The reason for the timeout, by default None

        Returns
        -------
        hikari.Embed
            The response to display to the user.
        """
        raw_reason = helpers.format_reason(reason, max_length=1400)
        reason = helpers.format_reason(f"Timed out until {duration.isoformat()} - {reason}", moderator, max_length=512)

        me = self._client.cache.get_member(member.guild_id, self._client.user_id)
        assert me is not None
        # Raise error if cannot harm user
        helpers.can_harm(me, member, hikari.Permissions.MODERATE_MEMBERS, raise_error=True)

        embed = hikari.Embed(
            title="🔇 " + "User timed out",
            description=f"**{member}** has been timed out until {helpers.format_dt(duration)}.\n**Reason:** ```{raw_reason}```",
            color=const.ERROR_COLOR,
        )

        try:
            await self.pre_mod_actions(
                member.guild_id, member, ActionType.TIMEOUT, reason=f"Timed out until {duration} - {raw_reason}"
            )
        except DMFailedError:
            embed.set_footer("Failed sending DM to user.")

        if duration > helpers.utcnow() + datetime.timedelta(seconds=MAX_TIMEOUT_SECONDS):
            await self._client.scheduler.create_timer(
                helpers.utcnow() + datetime.timedelta(seconds=MAX_TIMEOUT_SECONDS),
                TimerEvent.TIMEOUT_EXTEND,
                member.guild_id,
                member,
                notes=str(round(duration.timestamp())),
            )
            await member.edit(
                communication_disabled_until=helpers.utcnow() + datetime.timedelta(seconds=MAX_TIMEOUT_SECONDS),
                reason=reason or hikari.UNDEFINED,
            )

        else:
            await member.edit(communication_disabled_until=duration, reason=reason or hikari.UNDEFINED)

        await self.post_mod_actions(
            member.guild_id, member, ActionType.TIMEOUT, reason=f"Timed out until {duration} - {raw_reason}"
        )
        return embed

    async def remove_timeout(self, member: hikari.Member, moderator: hikari.Member, reason: str | None = None) -> None:
        """Removes a timeout from a user with the specified reason.

        Parameters
        ----------
        member : hikari.Member
            The member to remove timeout from.
        moderator : hikari.Member
            The moderator to log the timeout removal under.
        reason : str | None, optional
            The reason for the timeout removal, by default None
        """
        reason = helpers.format_reason(reason, moderator)

        await member.edit(communication_disabled_until=None, reason=reason or hikari.UNDEFINED)

    async def ban(
        self,
        user: hikari.User | hikari.Member,
        moderator: hikari.Member,
        duration: datetime.datetime | None = None,
        *,
        soft: bool = False,
        days_to_delete: int = 0,
        reason: str | None = None,
    ) -> hikari.Embed:
        """Ban a user from a guild.

        Parameters
        ----------
        user : Union[hikari.User, hikari.Member]
            The user that needs to be banned.
        guild_id : Snowflake
            The guild this ban is taking place in.
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
            The response embed to display to the user.

        Raises
        ------
        RuntimeError
            Both soft & tempban were specified.
        """
        reason = reason or "No reason provided."

        if duration and soft:
            raise RuntimeError("Ban type cannot be soft when a duration is specified.")

        me = self._client.cache.get_member(moderator.guild_id, self._client.user_id)
        assert me is not None

        perms = lightbulb.utils.permissions_for(me)

        if not helpers.includes_permissions(perms, hikari.Permissions.BAN_MEMBERS):
            raise lightbulb.BotMissingRequiredPermission(perms=hikari.Permissions.BAN_MEMBERS)

        if isinstance(user, hikari.Member) and not helpers.is_above(me, user):
            raise RoleHierarchyError

        if duration:
            reason = f"[TEMPBAN] Banned until: {duration} (UTC)  |  {reason}"

        elif soft:
            reason = f"[SOFTBAN] {reason}"

        raw_reason = reason
        reason = helpers.format_reason(reason, moderator, max_length=512)

        embed = hikari.Embed(
            title="🔨 User banned",
            description=f"**{user}** has been banned.\n**Reason:** ```{raw_reason}```",
            color=const.ERROR_COLOR,
        )

        try:
            try:
                await self.pre_mod_actions(moderator.guild_id, user, ActionType.BAN, reason=raw_reason)
            except DMFailedError:
                embed.set_footer("Failed sending DM to user.")

            await self._client.rest.ban_user(
                moderator.guild_id, user.id, delete_message_seconds=days_to_delete * 86400, reason=reason
            )

            record = await self._client.db.fetchrow(
                """SELECT * FROM timers WHERE guild_id = $1 AND user_id = $2 AND event = $3""",
                moderator.guild_id,
                user.id,
                "tempban",
            )
            if record:
                await self._client.scheduler.cancel_timer(record["id"], moderator.guild_id)

            if soft:
                await self._client.rest.unban_user(moderator.guild_id, user.id, reason="Automatic unban by softban.")

            elif duration:
                await self._client.scheduler.create_timer(
                    expires=duration, event=TimerEvent.TEMPBAN, guild=moderator.guild_id, user=user
                )

            await self.post_mod_actions(moderator.guild_id, user, ActionType.BAN, reason=raw_reason)
            return embed

        except (hikari.ForbiddenError, hikari.HTTPError):
            return hikari.Embed(
                title="❌ Ban failed",
                description="This could be due to a configuration or network error. Please try again later.",
                color=const.ERROR_COLOR,
            )

    async def unban(self, user: hikari.User, moderator: hikari.Member, reason: str | None = None) -> hikari.Embed:
        """Unban a user from a guild.

        Parameters
        ----------
        user : hikari.User
            The user to be unbanned.
        moderator : hikari.Member
            The moderator who is unbanning this user.
        reason : str | None, optional
            The reason for the unban, by default None

        Returns
        -------
        hikari.Embed
            The response to show to the invoker.

        Raises
        ------
        lightbulb.BotMissingRequiredPermission
            Application is missing permissions to BAN_MEMBERS.
        """
        me = self._client.cache.get_member(moderator.guild_id, self._client.user_id)
        assert me is not None

        perms = lightbulb.utils.permissions_for(me)

        raw_reason = reason
        reason = helpers.format_reason(reason, moderator, max_length=512)

        if not helpers.includes_permissions(perms, hikari.Permissions.BAN_MEMBERS):
            raise lightbulb.BotMissingRequiredPermission(perms=hikari.Permissions.BAN_MEMBERS)

        try:
            await self._client.rest.unban_user(moderator.guild_id, user.id, reason=reason)
            return hikari.Embed(
                title="🔨 User unbanned",
                description=f"**{user}** has been unbanned.\n**Reason:** ```{raw_reason}```",
                color=const.EMBED_GREEN,
            )
        except (hikari.HTTPError, hikari.ForbiddenError, hikari.NotFoundError) as e:
            if isinstance(e, hikari.NotFoundError):
                return hikari.Embed(
                    title="❌ Unban failed",
                    description="This user does not appear to be banned!",
                    color=const.ERROR_COLOR,
                )

            return hikari.Embed(
                title="❌ Unban failed",
                description="This could be due to a configuration or network error. Please try again later.",
                color=const.ERROR_COLOR,
            )

    async def kick(
        self,
        member: hikari.Member,
        moderator: hikari.Member,
        *,
        reason: str | None = None,
    ) -> hikari.Embed:
        """Kick a member from the specified guild.

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

        me = self._client.cache.get_member(member.guild_id, self._client.user_id)
        assert me is not None
        # Raise error if cannot harm user
        helpers.can_harm(me, member, hikari.Permissions.MODERATE_MEMBERS, raise_error=True)

        embed = hikari.Embed(
            title="🚪👈 User kicked",
            description=f"**{member}** has been kicked.\n**Reason:** ```{raw_reason}```",
            color=const.ERROR_COLOR,
        )

        try:
            try:
                await self.pre_mod_actions(member.guild_id, member, ActionType.KICK, reason=raw_reason)
            except DMFailedError:
                embed.set_footer("Failed sending DM to user.")

            await self._client.rest.kick_user(member.guild_id, member, reason=reason)
            await self.post_mod_actions(member.guild_id, member, ActionType.KICK, reason=raw_reason)
            return embed

        except (hikari.ForbiddenError, hikari.HTTPError):
            return hikari.Embed(
                title="❌ Kick failed",
                description="This could be due to a configuration or network error. Please try again later.",
                color=const.ERROR_COLOR,
            )


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
