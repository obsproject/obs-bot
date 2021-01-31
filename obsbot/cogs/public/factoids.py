import logging

from discord import Message, Embed, User
from discord.ext.commands import Cog, command, Context

from discord_slash import SlashContext

logger = logging.getLogger(__name__)


class Factoids(Cog):
    _factoids_colour = 0x36393E

    def __init__(self, bot, config):
        self.bot = bot
        self.alias_map = dict()
        self.factoids = dict()
        self.config = config

        if admin := self.bot.get_cog('Admin'):
            admin.add_help_section('Factoids', [
                ('.add <name> <message>', 'Add new factoid'),
                ('.del <name>', 'Delete factoid'),
                ('.mod <name> <new message>', 'Modify existing factoid'),
                ('.ren <name> <new name>', 'Rename existing factoid or alias'),
                ('.addalias <alias> <name>', 'Add alias to factoid'),
                ('.delalias <alias>', 'Rename existing factoid'),
                ('.setembed <name> [y/n]', 'Set/toggle embed status'),
                ('.setimageembed <name> [y/n]', 'Set/toggle image embed status'),
                ('.info <name>', 'Print factoid info'),
            ])

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
            factoid = dict(name=name, uses=record['uses'], embed=record['embed'], message=record['message'],
                           image_embed=record['image_embed'], aliases=record['aliases'])
            self.factoids[name] = factoid
            for alias in record['aliases']:
                self.alias_map[alias] = name

            # todo only add slash commands for top 10 factoids and also remove them on update if necessary
            if name not in self.bot.slash.commands:
                self.bot.slash.add_slash_command(self.slash_factoid, name=name, description=f'{name} factoid',
                                                 guild_ids=[self.bot.config['bot']['main_guild']],
                                                 options=[dict(type=6, name='mention',
                                                               description='User(s) to mention',
                                                               required=False)],
                                                 auto_convert=dict(mention='user'))

    async def slash_factoid(self, ctx: SlashContext, user_mention=None):
        logger.info(f'Command: {ctx.name} ({ctx.command_id}) was called')
        await self.increment_uses(ctx.name)
        if user_mention:
            if not user_mention.startswith('<@'):
                user_mention = ''
            return await ctx.send(send_type=3, content=f'{user_mention} {self.factoids[ctx.name]["message"]}')
        else:
            return await ctx.send(send_type=3, content=self.factoids[ctx.name]['message'], hidden=True)

    @Cog.listener()
    async def on_message(self, msg: Message):
        if not msg.content.startswith('!'):
            return
        msg_parts = msg.content[1:].split()

        factoid_name = msg_parts[0].lower()

        if factoid_name not in self.factoids:
            if factoid_name in self.alias_map:
                factoid_name = self.alias_map[factoid_name]
            else:  # factoid does not exit
                return

        factoid = self.factoids[factoid_name]
        await self.increment_uses(factoid_name)
        logger.info(f'Factoid "{factoid_name}" requested by "{msg.author.name}"')

        # default reference is the message by the requesting user,
        # but if user was replying to somebody, use that instead
        ref = msg
        if msg.reference:
            ref = msg.reference

        # if users are mentioned, mention them in the bot reply as well
        user_mention = ''
        if msg.mentions:
            user_mention = ' '.join(user.mention for user in msg.mentions)

        if factoid['embed']:
            if factoid['image_embed']:
                # image embeds do not have a message, instead the "message" is the image's URL
                embed = Embed(colour=self._factoids_colour)
                embed.set_image(url=factoid['message'].strip())
            else:
                embed = Embed(colour=self._factoids_colour, description=factoid['message'])

            if user_mention:
                return await msg.channel.send(user_mention, embed=embed)
            else:
                return await msg.channel.send(embed=embed, reference=ref, mention_author=True)
        else:
            if user_mention:
                return await msg.channel.send(f'{user_mention} {factoid["message"]}')
            else:
                return await msg.channel.send(factoid['message'], reference=ref, mention_author=True)

    async def increment_uses(self, factoid_name):
        return await self.bot.db.add_task(
            f'''UPDATE "{self.config["db_table"]}" SET uses=uses+1 WHERE name=$1''',
            factoid_name
        )

    @command()
    async def add(self, ctx: Context, name: str.lower, *, message):
        if not self.bot.is_admin(ctx.author):
            return
        if name in self.factoids or name in self.alias_map:
            return await ctx.send(f'The specified name ("{name}") already exists as factoid or alias!')

        await self.bot.db.exec(
            f'''INSERT INTO "{self.config["db_table"]}" (name, message) VALUES ($1, $2)''',
            name, message
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

        await self.bot.db.exec(
            f'''UPDATE "{self.config["db_table"]}" SET message=$2 WHERE name=$1''',
            _name, message
        )

        await self.fetch_factoids(refresh=True)
        return await ctx.send(f'Factoid "{name}" has been updated.')

    @command(name='del')
    async def _del(self, ctx: Context, name: str.lower):
        if not self.bot.is_admin(ctx.author):
            return
        if name not in self.factoids:
            return await ctx.send(f'The specified factoid name ("{name}") does not exist '
                                  f'(use base name instead of alias)!')

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
                f'''UPDATE "{self.config["db_table"]}" SET aliases=$2 WHERE name=$1''',
                real_name, aliases
            )

            await self.fetch_factoids(refresh=True)
            return await ctx.send(f'Alias "{name}" for "{real_name}" has been renamed to "{new_name}".')
        else:
            await self.bot.db.exec(
                f'''UPDATE "{self.config["db_table"]}" SET name=$2 WHERE name=$1''',
                name, new_name
            )

            await self.fetch_factoids(refresh=True)
            return await ctx.send(f'Factoid "{name}" has been renamed to "{new_name}".')

    @command()
    async def addalias(self, ctx: Context, alias: str.lower, name: str.lower):
        if not self.bot.is_admin(ctx.author):
            return
        _name = name if name in self.factoids else self.alias_map.get(name)
        if not _name or _name not in self.factoids:
            return await ctx.send(f'The specified factoid ("{name}") does not exist!')
        if alias in self.alias_map:
            return await ctx.send(f'The specified alias ("{alias}") already exists!')

        self.factoids[_name]['aliases'].append(alias)

        await self.bot.db.exec(
            f'''UPDATE "{self.config["db_table"]}" SET aliases=$2 WHERE name=$1''',
            _name, self.factoids[_name]['aliases']
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
            f'''UPDATE "{self.config["db_table"]}" SET aliases=$2 WHERE name=$1''',
            real_name, aliases
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
            f'''UPDATE "{self.config["db_table"]}" SET embed=$2 WHERE name=$1''',
            _name, embed_status
        )

        await self.fetch_factoids(refresh=True)
        return await ctx.send(f'Embed mode for "{name}" set to {str(embed_status).lower()}')

    @command()
    async def setimageembed(self, ctx: Context, name: str.lower, yesno: bool = None):
        if not self.bot.is_admin(ctx.author):
            return
        _name = name if name in self.factoids else self.alias_map.get(name)
        if not _name or _name not in self.factoids:
            return await ctx.send(f'The specified factoid ("{name}") does not exist!')

        embed_status = self.factoids[_name]['image_embed']

        if yesno is None:
            embed_status = not embed_status
        else:
            embed_status = yesno

        await self.bot.db.exec(
            f'''UPDATE "{self.config["db_table"]}" SET image_embed=$2 WHERE name=$1''',
            _name, embed_status
        )

        await self.fetch_factoids(refresh=True)
        return await ctx.send(f'Embed mode for "{name}" set to {str(embed_status).lower()}')

    @command()
    async def info(self, ctx: Context, name: str.lower):
        name = name if name in self.factoids else self.alias_map.get(name)
        if name not in self.factoids:
            return await ctx.send(f'The specified factoid ("{name}") does not exist!')

        factoid = self.factoids[name]
        embed = Embed(title=f'Factoid information: {name}',
                      description=f'```{factoid["message"]}```')
        if factoid['aliases']:
            embed.add_field(name='Aliases', value=', '.join(factoid['aliases']))
        embed.add_field(name='Uses (since 2018-06-07)', value=str(factoid['uses']))
        embed.add_field(name='Uses embed', value=str(factoid['embed'] or factoid['image_embed']))
        return await ctx.send(embed=embed)


def setup(bot):
    if 'factoids' in bot.config and bot.config['factoids'].get('enabled', False):
        fac = Factoids(bot, bot.config['factoids'])
        bot.add_cog(fac)
        bot.loop.create_task(fac.fetch_factoids())
    else:
        logger.info('Factoids Cog not enabled.')
