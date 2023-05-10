import logging
import time

import aiohttp
import disnake
import toml

from disnake.ext import commands

from .state_file import StateFile
from .db import DBHelper

from obsbot.cogs.private import cogs as private_cogs
from obsbot.cogs.public import cogs as public_cogs

logger = logging.getLogger(__name__)


class OBSBot(commands.Bot):
    def __init__(self, config_file):
        intents = disnake.Intents(
            bans=True,
            emojis=True,
            guilds=True,
            members=True,
            messages=True,
            reactions=True,
            voice_states=False,
            message_content=True,
        )
        super().__init__(command_prefix='.', help_command=None, intents=intents)  # type: ignore

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
        self.contrib_role = None
        # admin ids, set via config, but can be changed at runtime
        self.admins = set(self.config['bot']['admins'])
        self.admins.add(self.config['bot']['owner'])
        self.supporters = set()
        self.contributors = set()

    async def on_ready(self):
        logger.info('OBS Bot ready!')
        logger.info(f'Name: {self.user} (ID: {self.user.id})')

        self.start_time = time.time()
        self.main_guild = self.get_guild(self.config['bot']['main_guild'])
        self.supporter_role = self.main_guild.get_role(self.config['bot']['supporter_role'])
        if self.supporter_role:
            for user in self.supporter_role.members:
                self.supporters.add(user.id)
        self.contrib_role = self.main_guild.get_role(self.config['bot']['contributor_role'])
        if self.contrib_role:
            for user in self.contrib_role.members:
                self.contributors.add(user.id)

        if game := self.state.get('game', None):
            activity = disnake.Game(game)
        elif song := self.state.get('song', None):
            activity = disnake.Activity(name=song, type=disnake.ActivityType.listening)
        elif stream := self.state.get('stream', None):
            activity = disnake.Activity(name=stream, type=disnake.ActivityType.watching)
        else:
            activity = disnake.Activity(name='your problems', type=disnake.ActivityType.listening)

        await self.change_presence(activity=activity)

    def is_admin(self, user: disnake.Member):
        if user.id in self.admins:
            return True
        return False

    def is_supporter(self, user: disnake.Member):
        if self.is_admin(user):
            return True
        elif user.id in self.supporters:
            return True
        return False

    def is_contributor(self, user: disnake.User):
        if user.id in self.admins:
            return True
        elif user.id in self.contributors:
            return True
        return False

    @staticmethod
    def is_private(channel: disnake.TextChannel):
        # DMs
        if isinstance(channel, disnake.DMChannel):
            return True
        # For threads, use the parent channel
        if isinstance(channel, disnake.Thread):
            channel = channel.parent
        # Guild channels
        if channel.guild.default_role in channel.overwrites:
            if not channel.overwrites[channel.guild.default_role].pair()[0].read_messages:
                return True
        return False

    async def on_command_error(self, context, exception):
        """Swallow some errors we don't care about"""
        if isinstance(exception, commands.errors.CommandNotFound):
            return
        elif isinstance(exception, commands.errors.MissingRequiredArgument):
            return
        raise exception

    async def on_message(self, message):
        if (
            isinstance(message.channel, disnake.DMChannel)
            and not message.author.bot
            and not message.content.startswith('.')
            and not message.content.startswith('!')
            and not self.is_supporter(message.author)
        ):
            await message.channel.send(
                'DMs are not monitored, please use the support channels in discord.gg/obsproject instead.'
            )
        else:
            await self.process_commands(message)

    async def close(self):
        logger.info('Cleaning up on close()...')
        await super().close()
        await self.db.conn.close()
        await self.session.close()

    def run(self):
        return super().run(self.config['bot']['token'], reconnect=True)
