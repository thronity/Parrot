from __future__ import annotations

import os
import typing
from async_property import async_property
import jishaku
import datetime
import asyncio
import traceback
import aiohttp
import topgg
import socket
from collections import Counter, deque, defaultdict
import discord
from discord.ext import commands  # , ipc

from aiohttp import AsyncResolver, ClientSession, TCPConnector
from lru import LRU

from utilities.config import (
    EXTENSIONS,
    OWNER_IDS,
    CASE_INSENSITIVE,
    STRIP_AFTER_PREFIX,
    TOKEN,
    AUTHOR_NAME,
    AUTHOR_DISCRIMINATOR,
    MASTER_OWNER,
)

from time import time

from .Context import Context

os.environ["JISHAKU_HIDE"] = "True"
os.environ["JISHAKU_NO_UNDERSCORE"] = "True"
os.environ["JISHAKU_NO_DM_TRACEBACK"] = "True"

intents = discord.Intents.default()
intents.members = True

CHANGE_LOG_ID = 796932292458315776
SUPPORT_SERVER_ID = 741614680652644382


class Parrot(commands.AutoShardedBot):
    """A custom way to organise a commands.AutoSharedBot."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(
            command_prefix=self.get_prefix,
            case_insensitive=CASE_INSENSITIVE,
            intents=intents,
            activity=discord.Activity(
                type=discord.ActivityType.listening, name="$help"
            ),
            status=discord.Status.dnd,
            strip_after_prefix=STRIP_AFTER_PREFIX,
            owner_ids=OWNER_IDS,
            allowed_mentions=discord.AllowedMentions(
                everyone=False, replied_user=False
            ),
            member_cache_flags=discord.MemberCacheFlags.from_intents(intents),
            shard_count=1,
            **kwargs,
        )
        self._BotBase__cogs = commands.core._CaseInsensitiveDict()
        self._CogMixin__cogs = commands.core._CaseInsensitiveDict()
        self._seen_messages = 0
        self._change_log = None
        self.color = 0x87CEEB
        self.error_channel = None
        self.persistent_views_added = False
        self.spam_control = commands.CooldownMapping.from_cooldown(
            10, 12.0, commands.BucketType.user
        )
        self._auto_spam_count = Counter()
        self.resumes = defaultdict(list)
        self.identifies = defaultdict(list)
        self._prev_events = deque(maxlen=10)

        self.session = aiohttp.ClientSession(loop=self.loop)
        self.http_session = ClientSession(
            connector=TCPConnector(resolver=AsyncResolver(), family=socket.AF_INET)
        )
        # self.ipc = ipc.Server(self,)
        self.server_config = LRU(8)
        for ext in EXTENSIONS:
            try:
                self.load_extension(ext)
                print(f"[EXTENSION] {ext} was loaded successfully!")
            except Exception as e:
                tb = traceback.format_exception(type(e), e, e.__traceback__)
                tbe = "".join(tb) + ""
                print(f"[WARNING] Could not load extension {ext}: {tbe}")

    def __repr__(self):
        return f"<core.{self.user.name}>"

    @property
    def invite(self) -> str:
        clientID = self.user.id
        perms = discord.Permissions.none()
        perms.read_messages = True
        perms.external_emojis = True
        perms.moderate_members = True
        perms.send_messages = True
        perms.manage_roles = True
        perms.manage_channels = True
        perms.ban_members = True
        perms.kick_members = True
        perms.manage_messages = True
        perms.embed_links = True
        perms.read_message_history = True
        perms.attach_files = True
        perms.add_reactions = True

        url = (
            f"https://discord.com/api/oauth2/authorize?client_id={clientID}&permissions={perms.value}"
            f"&redirect_uri={SUPPORT_SERVER}&scope=bot%20applications.commands"
        )
        return url

    def run(self) -> None:
        """To run connect and login into discord"""
        super().run(TOKEN, reconnect=True)

    async def on_ready(self) -> None:
        if not hasattr(self, "uptime"):
            self.uptime = discord.utils.utcnow()

        print(f"Ready: {self.user} (ID: {self.user.id})")
        print(f"Using discord.py of version: {discord.__version__ }")

    async def on_connect(self) -> None:
        print(f"[{self.user.name.title()}] Logged in")
        return

    async def on_disconnect(self) -> None:
        print(f"[{self.user.name.title()}] disconnect from discord")
        return

    async def on_shard_resumed(self, shard_id) -> None:
        print(f"Shard ID {shard_id} has resumed...")
        self.resumes[shard_id].append(discord.utils.utcnow())

    async def process_commands(self, message: discord.Message) -> None:
        ctx = await self.get_context(message, cls=Context)

        if ctx.command is None:
            # ignore if no command found
            return

        if str(ctx.channel.type) == "public_thread":
            # no messages in discord.Thread
            return

        bucket = self.spam_control.get_bucket(message)
        current = message.created_at.timestamp()
        retry_after = bucket.update_rate_limit(current)
        author_id = message.author.id
        if retry_after:
            self._auto_spam_count[author_id] += 1
            if self._auto_spam_count[author_id] >= 3:
                return
        else:
            self._auto_spam_count.pop(author_id, None)

        await self.invoke(ctx)
        await asyncio.sleep(0)

    async def on_message(self, message: discord.Message) -> None:
        self._seen_messages += 1

        if not message.guild:
            # to prevent the usage of command in DMs
            return

        await self.process_commands(message)

    async def resolve_member_ids(self, guild: discord.Guild, member_ids: list):
        needs_resolution = []
        for member_id in member_ids:
            member = guild.get_member(member_id)
            if member is not None:
                yield member
            else:
                needs_resolution.append(member_id)

        total_need_resolution = len(needs_resolution)
        if total_need_resolution == 1:
            shard = self.get_shard(guild.shard_id)
            if shard.is_ws_ratelimited():
                try:
                    member = await guild.fetch_member(needs_resolution[0])
                except discord.HTTPException:
                    pass
                else:
                    yield member
            else:
                members = await guild.query_members(
                    limit=1, user_ids=needs_resolution, cache=True
                )
                if members:
                    yield members[0]
        elif total_need_resolution <= 100:
            # Only a single resolution call needed here
            resolved = await guild.query_members(
                limit=100, user_ids=needs_resolution, cache=True
            )
            for member in resolved:
                yield member
        else:
            # We need to chunk these in bits of 100...
            for index in range(0, total_need_resolution, 100):
                to_resolve = needs_resolution[index : index + 100]
                members = await guild.query_members(
                    limit=100, user_ids=to_resolve, cache=True
                )
                for member in members:
                    yield member

    async def get_or_fetch_member(
        self, guild: discord.Guild, member_id: int
    ) -> typing.Optional[discord.Member]:
        member = guild.get_member(member_id)
        if member is not None:
            return member

        shard = self.get_shard(guild.shard_id)
        if shard.is_ws_ratelimited():
            try:
                member = await guild.fetch_member(member_id)
            except discord.HTTPException:
                return None
            else:
                return member

        members = await guild.query_members(limit=1, user_ids=[member_id], cache=True)
        if not members:
            return None
        return members[0]

    async def fetch_message_by_channel(
        self, channel: discord.TextChannel, messageID: int
    ) -> typing.Optional[discord.Message]:
        async for msg in channel.history(
            limit=1,
            before=discord.Object(messageID + 1),
            after=discord.Object(messageID - 1),
        ):
            return msg

    async def get_prefix(self, message: discord.Message) -> str:
        return commands.when_mentioned_or("$")(self, message)

    async def get_guild_prefixes(self, guild: discord.Guild) -> typing.Optional[str]:
        return "$"

    async def send_raw(
        self, channel_id: int, content: str, **kwargs
    ) -> typing.Optional[discord.Message]:
        await self.http.send_message(channel_id, content, **kwargs)

    async def invoke_help_command(self, ctx: Context) -> None:
        return await ctx.send_help(ctx.command)
