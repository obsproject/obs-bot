import logging

from copy import deepcopy
from typing import Optional

from disnake.abc import Messageable
from disnake import Message, Embed, Member, ApplicationCommandInteraction
from disnake.ext.commands import Cog, command, Context, InvokableSlashCommand

from .utils.ratelimit import RateLimiter

logger = logging.getLogger(__name__)


class Factoids(Cog):
    _factoids_colour = 0x36393E
    _log_colour = 0xFFB400

    def __init__(self, bot, config):
        self.bot = bot
        self.alias_map = dict()
        self.factoids = dict()
        self.config = config
        self.limiter = RateLimiter(self.config.get('cooldown', 20.0))

        self.initial_commands_sync_done = False
        self.log_channel: Optional[Messageable] = None

        # The variables map to state variables, can be added at runtime
        self.variables = {
            '%nightly_url%': 'nightly_windows',
            '%mac_nightly_url%': 'nightly_macos',
            '%mac_m1_nightly_url%': 'nightly_macos_m1',
        }

        if 'factoid_variables' in self.bot.state:
            self.variables.update(self.bot.state['factoid_variables'])

        if admin := self.bot.get_cog('Admin'):
            admin.add_help_section(
                'Factoids',
                [
                    ('.add <name> <message>', 'Add new factoid'),
                    ('.del <name>', 'Delete factoid'),
                    ('.mod <name> <new message>', 'Modify existing factoid ("" to clear)'),
                    ('.ren <name> <new name>', 'Rename existing factoid or alias'),
                    ('.addalias <alias> <name>', 'Add alias to factoid'),
                    ('.delalias <alias>', 'Rename existing factoid'),
                    ('.setembed <name> [y/n]', 'Set/toggle embed status'),
                    ('.setimgurl <name> [url]', 'set image url (empty to clear)'),
                    ('.info <name>', 'Print factoid info'),
                    ('.top', 'Print most used commands'),
                    ('.bottom', 'Print least used commands'),
                    ('.unused', 'Print unused commands'),
                ],
            )

    async def fetch_factoids(self, refresh=False):
        rows = await self.bot.db.query(f'SELECT * FROM "{self.config["db_table"]}"')
        if not rows:
            logger.warning('No factoids in database!')
            return
        elif not refresh:
            logger.info(f'Received {len(rows)} factoid entries from database.')
        else:
            # clear existing factoid information
            self.factoids = dict()
            self.alias_map = dict()

        for record in rows:
            name = record['name']
            factoid = dict(
                name=name,
                uses=record['uses'],
                embed=record['embed'],
                message=record['message'],
                image_url=record['image_url'],
                aliases=record['aliases'],
            )
            self.factoids[name] = factoid
            for alias in record['aliases']:
                self.alias_map[alias] = name

        # Get top N commands, register new and unregister old ones
        rows = await self.bot.db.query(
            f'SELECT "name" FROM "{self.config["db_table"]}" '
            f'ORDER BY "uses" DESC LIMIT {self.config["slash_command_limit"]}'
        )
        # some simple set maths to get new/old/current commands
        commands = set(r['name'] for r in rows)
        old_commands = set(c.name for c in self.bot.slash_commands)
        new_commands = commands - old_commands
        old_commands -= commands

        for factoid in new_commands:
            logger.info(f'Adding slash command for "{factoid}"')
            self.bot.add_slash_command(
                InvokableSlashCommand(
                    self.slash_factoid,
                    name=factoid,
                    description=f'Sends "{factoid}" factoid',
                    guild_ids=[self.bot.config['bot']['main_guild']],
                )
            )

        # Delete commands that are now obsolete
        for obsolete in old_commands:
            logger.info(f'Removing slash command "{obsolete}"')
            self.bot.remove_slash_command(obsolete)

        # sync commands with discord API (only run if commands have already been registered)
        if new_commands or new_commands or not self.initial_commands_sync_done:
            self.bot._schedule_delayed_command_sync()

        self.initial_commands_sync_done = True

    async def init_logging(self):
        if 'log_channel' not in self.config:
            return
        await self.bot.wait_until_ready()
        self.log_channel = self.bot.get_channel(self.config['log_channel'])
        if self.log_channel:
            logger.info(f'Found factoid changelog channel: {self.log_channel}')

    async def _log_action(self, actor: Member, new: dict = None, old: dict = None):
        if not self.log_channel:
            return
        if not old and not new:
            return

        # New factoid created
        if new and not old:
            embed = Embed(
                title=f'Factoid `{new["name"]}` was created',
                description=f'**User:** {actor.mention}',
                colour=self._log_colour,
            )
            embed.add_field('Message', f'```\n{new["message"]}\n```')
            return await self.log_channel.send(embed=embed)

        # Factoid deleted
        if old and not new:
            embed = Embed(
                title=f'Factoid `{old["name"]}` was deleted',
                description=f'**User:** {actor.mention}',
                colour=self._log_colour,
            )

            embed.add_field('Message', f'```\n{old["message"]}\n```', inline=False)
            if old['image_url']:
                embed.add_field('Image URL', old['image_url'], inline=False)
            if old['aliases']:
                embed.add_field(
                    'Aliases', '`{}`'.format(', '.join(old['aliases']) if old['aliases'] else '<Empty>'), inline=False
                )
            embed.add_field('Uses', old['uses'], inline=False)
            return await self.log_channel.send(embed=embed)

        # Factoid modified
        embed = Embed(
            title=f'Factoid `{old["name"]}` was updated',
            description=f'**User:** {actor.mention}',
            colour=self._log_colour,
        )

        if old['message'] != new['message']:
            embed.add_field('Old message', f'```\n{old["message"]}\n```', inline=False)
            embed.add_field('New message', f'```\n{new["message"]}\n```', inline=False)

        if old['image_url'] != new['image_url']:
            embed.add_field('Old Image URL', old['image_url'], inline=False)
            embed.add_field('New Image URL', new['image_url'], inline=False)

        if old['aliases'] != new['aliases']:
            embed.add_field(
                'Old Aliases', '`{}`'.format(', '.join(old['aliases']) if old['aliases'] else '<Empty>'), inline=False
            )
            embed.add_field(
                'New Aliases', '`{}`'.format(', '.join(new['aliases']) if new['aliases'] else '<Empty>'), inline=False
            )

        # If no loggable changes were made, ignore it
        if not embed.fields:
            return

        await self.log_channel.send(embed=embed)

    def set_variable(self, variable, value):
        self.variables[variable] = value
        self.bot.state['factoid_variables'] = self.variables.copy()

    def resolve_variables(self, factoid_message):
        if '%' not in factoid_message:
            return factoid_message

        for variable, state_variable in self.variables.items():
            value = self.bot.state.get(state_variable, 'https://obsproject.com/4oh4')
            factoid_message = factoid_message.replace(variable, value)
        return factoid_message

    async def slash_factoid(self, ctx: ApplicationCommandInteraction, mention: Member = None):
        if not self.bot.is_supporter(ctx.author) and (
            self.limiter.is_limited(ctx.data.id, ctx.channel_id) or self.limiter.is_limited(ctx.data.id, ctx.author.id)
        ):
            logger.debug(f'rate-limited (sc): "{ctx.author}", channel: "{ctx.channel}", factoid: "{ctx.data.name}"')
            return

        logger.info(f'factoid requested (sc) by: "{ctx.author}", channel: "{ctx.channel}", factoid: "{ctx.data.name}"')
        await self.increment_uses(ctx.data.name)
        message = self.resolve_variables(self.factoids[ctx.data.name]['message'])

        embed = None
        if self.factoids[ctx.data.name]['embed']:
            embed = Embed(colour=self._factoids_colour, description=message)
            message = ''
            if self.factoids[ctx.data.name]['image_url']:
                embed.set_image(url=self.factoids[ctx.data.name]['image_url'])

        if mention and isinstance(mention, Member):
            return await ctx.send(content=f'{mention.mention} {message}', embed=embed)
        else:
            return await ctx.send(content=message, embed=embed)

    @Cog.listener()
    async def on_filtered_message(self, msg: Message):
        if not msg.content or len(msg.content) < 2 or msg.content[0] != '!':
            return
        msg_parts = msg.content[1:].split()

        factoid_name = msg_parts[0].lower()

        if factoid_name not in self.factoids:
            if factoid_name in self.alias_map:
                factoid_name = self.alias_map[factoid_name]
            else:  # factoid does not exit
                return

        if not self.bot.is_supporter(msg.author) and (
            self.limiter.is_limited(factoid_name, msg.channel.id)
            or self.limiter.is_limited(factoid_name, msg.author.id)
        ):
            logger.debug(f'rate-limited: "{msg.author}", channel: "{msg.channel}", factoid: "{factoid_name}"')
            return

        logger.info(f'factoid requested by: "{msg.author}", channel: "{msg.channel}", factoid: "{factoid_name}"')
        factoid = self.factoids[factoid_name]
        await self.increment_uses(factoid_name)
        message = self.resolve_variables(factoid['message'])

        # attempt to delete the message requesting the factoid if it's within a reply and only contains command
        if msg.reference and len(msg_parts) == 1:
            await msg.delete(delay=0.0)

        # if users are mentioned (but it's not a reply), mention them in the bot reply as well
        user_mention = None
        if msg.mentions and not msg.reference:
            if self.bot.is_supporter(msg.author):
                user_mention = ' '.join(user.mention for user in msg.mentions)
            else:
                user_mention = msg.mentions[0].mention

        embed = None
        if factoid['embed']:
            embed = Embed(colour=self._factoids_colour, description=message)
            message = ''
            if factoid['image_url']:
                embed.set_image(url=factoid['image_url'])

        if user_mention and embed is not None:
            return await msg.channel.send(user_mention, embed=embed)
        elif user_mention:
            return await msg.channel.send(f'{user_mention} {message}')
        else:
            msg_reference = msg.reference
            # If reference is a message from a bot, try resolving the referenced message's reference
            if msg_reference and msg.reference.resolved.author.bot and (ref := msg.reference.resolved.reference):
                msg_reference = ref

            return await msg.channel.send(
                message, embed=embed, reference=msg_reference, mention_author=True  # type: ignore
            )

    async def increment_uses(self, factoid_name):
        return await self.bot.db.add_task(
            f'''UPDATE "{self.config["db_table"]}" SET uses=uses+1 WHERE name=$1''', factoid_name
        )

    @command()
    async def add(self, ctx: Context, name: str.lower, *, message):
        if not self.bot.is_admin(ctx.author):
            return
        if name in self.factoids or name in self.alias_map:
            return await ctx.send(f'The specified name ("{name}") already exists as factoid or alias!')

        await self.bot.db.exec(
            f'''INSERT INTO "{self.config["db_table"]}" (name, message) VALUES ($1, $2)''', name, message
        )
        await self.fetch_factoids(refresh=True)
        await ctx.send(f'Factoid "{name}" has been added.')
        await self._log_action(ctx.author, new=self.factoids[name])

    @command()
    async def mod(self, ctx: Context, name: str.lower, *, message):
        if not self.bot.is_admin(ctx.author):
            return
        _name = name if name in self.factoids else self.alias_map.get(name)
        if not _name or _name not in self.factoids:
            return await ctx.send(f'The specified name ("{name}") does not exist!')

        # allow clearing message of embeds
        if self.factoids[_name]['embed'] and message == '""':
            message = ''

        old_factoid = self.factoids[_name].copy()
        await self.bot.db.exec(f'''UPDATE "{self.config["db_table"]}" SET message=$2 WHERE name=$1''', _name, message)

        await self.fetch_factoids(refresh=True)
        await ctx.send(f'Factoid "{name}" has been updated.')
        await self._log_action(ctx.author, new=self.factoids[_name], old=old_factoid)

    @command(name='del')
    async def _del(self, ctx: Context, name: str.lower):
        if not self.bot.is_admin(ctx.author):
            return
        if name not in self.factoids:
            return await ctx.send(
                f'The specified factoid name ("{name}") does not exist ' f'(use base name instead of alias)!'
            )

        factoid = self.factoids[name].copy()
        await self.bot.db.exec(f'''DELETE FROM "{self.config["db_table"]}" WHERE name=$1''', name)
        await self.fetch_factoids(refresh=True)
        await ctx.send(f'Factoid "{name}" has been deleted.')
        await self._log_action(ctx.author, old=factoid)

    @command()
    async def ren(self, ctx: Context, name: str.lower, new_name: str.lower):
        if not self.bot.is_admin(ctx.author):
            return
        if name not in self.factoids and name not in self.alias_map:
            return await ctx.send(f'The specified name ("{name}") does not exist!')
        if new_name in self.factoids or new_name in self.alias_map:
            return await ctx.send(f'The specified new name ("{name}") already exist as factoid or alias!')

        # ToDo log renaming
        # if name is an alias, rename the alias instead
        if name in self.alias_map:
            real_name = self.alias_map[name]
            # get list of aliases minus the old one, then append the new one
            aliases = [i for i in self.factoids[real_name]['aliases'] if i != name]
            aliases.append(new_name)

            await self.bot.db.exec(
                f'''UPDATE "{self.config["db_table"]}" SET aliases=$2 WHERE name=$1''', real_name, aliases
            )

            await self.fetch_factoids(refresh=True)
            return await ctx.send(f'Alias "{name}" for "{real_name}" has been renamed to "{new_name}".')
        else:
            await self.bot.db.exec(f'''UPDATE "{self.config["db_table"]}" SET name=$2 WHERE name=$1''', name, new_name)

            await self.fetch_factoids(refresh=True)
            return await ctx.send(f'Factoid "{name}" has been renamed to "{new_name}".')

    @command()
    async def addalias(self, ctx: Context, alias: str.lower, name: str.lower):
        if not self.bot.is_admin(ctx.author):
            return
        _name = name if name in self.factoids else self.alias_map.get(name)
        if not _name or _name not in self.factoids:
            return await ctx.send(f'The specified factoid ("{name}") does not exist!')
        if alias in self.factoids:
            return await ctx.send(f'The specified alias ("{alias}") is the name of an existing factoid!')
        if alias in self.alias_map:
            return await ctx.send(f'The specified alias ("{alias}") already exists!')

        old_factoid = deepcopy(self.factoids[_name])
        self.factoids[_name]['aliases'].append(alias)

        await self.bot.db.exec(
            f'''UPDATE "{self.config["db_table"]}" SET aliases=$2 WHERE name=$1''',
            _name,
            self.factoids[_name]['aliases'],
        )

        await self.fetch_factoids(refresh=True)
        await ctx.send(f'Alias "{alias}" added to "{name}".')
        await self._log_action(ctx.author, new=self.factoids[_name], old=old_factoid)

    @command()
    async def delalias(self, ctx: Context, alias: str.lower):
        if not self.bot.is_admin(ctx.author):
            return
        if alias not in self.alias_map:
            return await ctx.send(f'The specified name ("{alias}") does not exist!')

        real_name = self.alias_map[alias]
        old_factoid = deepcopy(self.factoids[real_name])
        # get list of aliases minus the old one, then append the new one
        aliases = [i for i in self.factoids[real_name]['aliases'] if i != alias]

        await self.bot.db.exec(
            f'''UPDATE "{self.config["db_table"]}" SET aliases=$2 WHERE name=$1''', real_name, aliases
        )

        await self.fetch_factoids(refresh=True)
        await ctx.send(f'Alias "{alias}" for "{real_name}" has been removed.')
        await self._log_action(ctx.author, new=self.factoids[real_name], old=old_factoid)

    @command()
    async def setembed(self, ctx: Context, name: str.lower, yesno: bool = None):
        if not self.bot.is_admin(ctx.author):
            return
        _name = name if name in self.factoids else self.alias_map.get(name)
        if not _name or _name not in self.factoids:
            return await ctx.send(f'The specified factoid ("{name}") does not exist!')

        factoid = self.factoids[_name]
        embed_status = factoid['embed']

        if yesno is None:
            embed_status = not embed_status
        else:
            embed_status = yesno

        await self.bot.db.exec(
            f'''UPDATE "{self.config["db_table"]}" SET embed=$2 WHERE name=$1''', _name, embed_status
        )

        await self.fetch_factoids(refresh=True)
        return await ctx.send(f'Embed mode for "{name}" set to {str(embed_status).lower()}')

    @command()
    async def setimgurl(self, ctx: Context, name: str.lower, url: str = None):
        if not self.bot.is_admin(ctx.author):
            return
        _name = name if name in self.factoids else self.alias_map.get(name)
        if not _name or _name not in self.factoids:
            return await ctx.send(f'The specified factoid ("{name}") does not exist!')

        factoid = self.factoids[_name]
        if not factoid['embed']:
            return await ctx.send(f'The specified factoid ("{name}") is not en embed!')

        old_factoid = factoid.copy()
        await self.bot.db.exec(f'''UPDATE "{self.config["db_table"]}" SET image_url=$2 WHERE name=$1''', _name, url)

        await self.fetch_factoids(refresh=True)
        await ctx.send(f'Image URL for "{name}" set to {url}')
        await self._log_action(ctx.author, new=self.factoids[_name], old=old_factoid)

    @command()
    async def info(self, ctx: Context, name: str.lower):
        _name = name if name in self.factoids else self.alias_map.get(name)
        if not _name or _name not in self.factoids:
            return await ctx.send(f'The specified factoid ("{name}") does not exist!')

        factoid = self.factoids[_name]
        message = factoid["message"].replace('`', '\\`') if factoid["message"] else '<no message>'
        embed = Embed(title=f'Factoid information: {_name}', description=f'```{message}```')
        if factoid['aliases']:
            embed.add_field(name='Aliases', value=', '.join(factoid['aliases']))
        embed.add_field(name='Uses (since 2018-06-07)', value=str(factoid['uses']))
        embed.add_field(name='Is Embed', value=str(factoid['embed']))
        if factoid['image_url']:
            embed.add_field(name='Image URL', value=factoid['image_url'], inline=False)
        return await ctx.send(embed=embed)

    @command()
    async def top(self, ctx: Context):
        embed = Embed(title='Top Factoids')
        description = ['Pos - Factoid (uses)', '--------------------------------']
        for pos, fac in enumerate(sorted(self.factoids.values(), key=lambda a: a['uses'], reverse=True)[:10], start=1):
            description.append(f'{pos:2d}. - {fac["name"]} ({fac["uses"]})')
        embed.description = '```{}```'.format('\n'.join(description))
        return await ctx.send(embed=embed)

    @command()
    async def bottom(self, ctx: Context):
        embed = Embed(title='Least used Factoids')
        description = ['Pos - Factoid (uses)', '--------------------------------']
        for pos, fac in enumerate(sorted(self.factoids.values(), key=lambda a: a['uses'])[:10], start=1):
            description.append(f'{pos:2d}. - {fac["name"]} ({fac["uses"]})')
        embed.description = '```{}```'.format('\n'.join(description))
        return await ctx.send(embed=embed)

    @command()
    async def unused(self, ctx: Context):
        embed = Embed(title='Unused Factoids')
        description = []
        for fac in sorted(self.factoids.values(), key=lambda a: a['uses']):
            if fac['uses'] > 0:
                break
            description.append(f'- {fac["name"]}')
        embed.description = '```{}```'.format('\n'.join(description))
        return await ctx.send(embed=embed)


def setup(bot):
    if 'factoids' in bot.config and bot.config['factoids'].get('enabled', False):
        fac = Factoids(bot, bot.config['factoids'])
        bot.add_cog(fac)
        bot.loop.create_task(fac.fetch_factoids())
        bot.loop.create_task(fac.init_logging())
    else:
        logger.info('Factoids Cog not enabled.')
