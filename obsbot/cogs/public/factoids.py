import logging

from disnake import Message, Embed, Member, ApplicationCommandInteraction
from disnake.ext.commands import Cog, command, Context, InvokableSlashCommand

from .utils.ratelimit import RateLimiter

logger = logging.getLogger(__name__)


class Factoids(Cog):
    _factoids_colour = 0x36393E

    def __init__(self, bot, config):
        self.bot = bot
        self.alias_map = dict()
        self.factoids = dict()
        self.config = config
        self.member_limiter = RateLimiter(self.config.get('cooldown', 20.0))
        self.channel_limiter = RateLimiter(self.config.get('cooldown', 20.0))

        self.initial_commands_sync_done = False

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
            self.member_limiter.is_limited(ctx.data.id, ctx.author.id)
            or self.channel_limiter.is_limited(ctx.data.id, ctx.channel.id)
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
    async def on_message(self, msg: Message):
        # ignore our own messages
        if msg.author == self.bot.user:
            return
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
            self.member_limiter.is_limited(factoid_name, msg.author.id)
            or self.channel_limiter.is_limited(factoid_name, msg.channel.id)
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
        return await ctx.send(f'Factoid "{name}" has been added.')

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

        await self.bot.db.exec(f'''UPDATE "{self.config["db_table"]}" SET message=$2 WHERE name=$1''', _name, message)

        await self.fetch_factoids(refresh=True)
        return await ctx.send(f'Factoid "{name}" has been updated.')

    @command(name='del')
    async def _del(self, ctx: Context, name: str.lower):
        if not self.bot.is_admin(ctx.author):
            return
        if name not in self.factoids:
            return await ctx.send(
                f'The specified factoid name ("{name}") does not exist ' f'(use base name instead of alias)!'
            )

        await self.bot.db.exec(f'''DELETE FROM "{self.config["db_table"]}" WHERE name=$1''', name)
        await self.fetch_factoids(refresh=True)
        return await ctx.send(f'Factoid "{name}" has been deleted.')

    @command()
    async def ren(self, ctx: Context, name: str.lower, new_name: str.lower):
        if not self.bot.is_admin(ctx.author):
            return
        if name not in self.factoids and name not in self.alias_map:
            return await ctx.send(f'The specified name ("{name}") does not exist!')
        if new_name in self.factoids or new_name in self.alias_map:
            return await ctx.send(f'The specified new name ("{name}") already exist as factoid or alias!')

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

        self.factoids[_name]['aliases'].append(alias)

        await self.bot.db.exec(
            f'''UPDATE "{self.config["db_table"]}" SET aliases=$2 WHERE name=$1''',
            _name,
            self.factoids[_name]['aliases'],
        )

        await self.fetch_factoids(refresh=True)
        return await ctx.send(f'Alias "{alias}" added to "{name}".')

    @command()
    async def delalias(self, ctx: Context, alias: str.lower):
        if not self.bot.is_admin(ctx.author):
            return
        if alias not in self.alias_map:
            return await ctx.send(f'The specified name ("{alias}") does not exist!')

        real_name = self.alias_map[alias]
        # get list of aliases minus the old one, then append the new one
        aliases = [i for i in self.factoids[real_name]['aliases'] if i != alias]

        await self.bot.db.exec(
            f'''UPDATE "{self.config["db_table"]}" SET aliases=$2 WHERE name=$1''', real_name, aliases
        )

        await self.fetch_factoids(refresh=True)
        return await ctx.send(f'Alias "{alias}" for "{real_name}" has been removed.')

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

        await self.bot.db.exec(f'''UPDATE "{self.config["db_table"]}" SET image_url=$2 WHERE name=$1''', _name, url)

        await self.fetch_factoids(refresh=True)
        return await ctx.send(f'Image URL for "{name}" set to {url}')

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
    else:
        logger.info('Factoids Cog not enabled.')
