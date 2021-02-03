import asyncio
import logging
import time

import aiohttp
import discord
import toml

from discord.ext import commands
from discord_slash import SlashCommand

from .state_file import StateFile
from .db import DBHelper

from obsbot.cogs.private import cogs as private_cogs
from obsbot.cogs.public import cogs as public_cogs

logger = logging.getLogger(__name__)


class OBSBot(commands.Bot):
    def __init__(self, config_file):
        intents = discord.Intents(bans=True, emojis=True, guilds=True, members=True,
                                  messages=True, reactions=True, voice_states=False)
        super().__init__(command_prefix='.', help_command=None, intents=intents)
        # enable slash commands
        self.slash = SlashCommand(self, auto_register=True, auto_delete=True)

        self.config = toml.load(open(config_file))
        self.state = StateFile(self.config['bot']['state_file'])
        self.db = DBHelper()
        self.loop.run_until_complete(self.db.connect(self.config['db']))

        timeout = aiohttp.ClientTimeout(total=60)
        self.session = aiohttp.ClientSession(loop=self.loop, timeout=timeout)

        # load cogs
        for module in public_cogs:
            logger.info(f'Loading public extension: {module}')
            self.load_extension('obsbot.cogs.public.' + module)
        for module in private_cogs:
            logger.info(f'Loading private extension: {module}')
            self.load_extension('obsbot.cogs.private.' + module)

        # set by on_ready
        self.start_time = None
        self.main_guild = None
        self.supporter_role = None
        # admin ids, set via config, but can be changed at runtime
        self.admins = set(self.config['bot']['admins'])
        self.admins.add(self.config['bot']['owner'])
        self.supporters = set()

    async def on_ready(self):
        logger.info('OBS Bot ready!')
        logger.info(f'Name: {self.user} (ID: {self.user.id})')

        self.start_time = time.time()
        self.main_guild = self.get_guild(self.config['bot']['main_guild'])
        self.supporter_role = self.main_guild.get_role(self.config['bot']['supporter_role'])
        for user in self.supporter_role.members:
            self.supporters.add(user.id)

        if game := self.state.get('game', None):
            activity = discord.Game(game)
        else:
            activity = discord.Activity(name='your problems', type=discord.ActivityType.listening)

        await self.change_presence(activity=activity)

    def is_admin(self, user: discord.Member):
        if user.id in self.admins:
            return True
        return False

    def is_supporter(self, user: discord.Member):
        if self.is_admin(user):
            return True
        elif user.id in self.supporters:
            return True
        return False

    async def on_command_error(self, context, exception):
        """Swallow somwe errors we don't care about"""
        if isinstance(exception, commands.errors.CommandNotFound):
            return
        elif isinstance(exception, commands.errors.MissingRequiredArgument):
            return
        raise exception

    async def close(self):
        logger.info('Cleaning up on close()...')
        await super().close()
        await self.db.conn.close()
        await self.session.close()

    def run(self):
        return super().run(self.config['bot']['token'], reconnect=True)
