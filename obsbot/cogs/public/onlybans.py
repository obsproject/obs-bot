import re
import logging

from discord import Message, Embed
from discord.ext.commands import Cog, Context, command

logger = logging.getLogger(__name__)


class OnlyBans(Cog):
    def __init__(self, bot, config):
        self.bot = bot
        self.config = config

        self.filtering_enabled = True
        self.log_channel = None
        self.filters = dict()
        self.bannable = set()
        self.kickable = set()

        if not self.bot.state.get('mod_deletes'):
            self.bot.state['mod_deletes'] = 0
        if not self.bot.state.get('mod_faster'):
            self.bot.state['mod_faster'] = 0
        if not self.bot.state.get('mod_bans'):
            self.bot.state['mod_bans'] = 0
        if not self.bot.state.get('mod_kicks'):
            self.bot.state['mod_kicks'] = 0

        if admin := self.bot.get_cog('Admin'):
            admin.add_help_section('Moderation', [
                ('.listfilters', 'List existing filters'),
                ('.addfilter "<name>" `<regex>`', 'Add regex-based message filter'),
                ('.modfilter "<name>" `<regex>`', 'Update regex of filter'),
                ('.delfilter "<name>"', 'Delete filter'),
                ('.setpunishment "<name>" [none/kick/ban]', 'sets additional violation action (default: none)'),
                ('.testfilter <message>', 'Test if message gets caught by any filter'),
                ('.togglefiltering', 'Enable/Disable filtering'),
                ('.killcount', 'Print some stats'),
            ], restricted=True)

    async def fetch_filters(self):
        # fetch existing filters from DB
        rows = await self.bot.db.query(f'SELECT * FROM "{self.config["db_table"]}"')
        if not rows:
            logger.warning('No filters in database!')
            return
        for row in rows:
            try:
                self.filters[row['name']] = re.compile(row['regex'], re.IGNORECASE | re.DOTALL)
                if row.get('bannable', False):
                    self.bannable.add(row['name'])
                elif row.get('kickable', False):
                    self.kickable.add(row['name'])
            except re.error as e:
                logger.error(f'Compiling filter "{row["name"]}" failed with: {e}')
        logger.info(f'Fetched {len(self.filters)} filters from database.')
        # wait for bot to be fully online before trying to find modlog channel
        await self.bot.wait_until_ready()
        # find channel
        self.log_channel = self.bot.get_channel(self.config['log_channel'])
        if self.log_channel:
            logger.info(f'Found moderation log channel: {self.log_channel}')

    @command()
    async def listfilters(self, ctx: Context):
        if not self.bot.is_admin(ctx.author):
            return
        if not self.bot.is_private(ctx.channel):
            return

        _list = ['##. name - regex']
        for num, (name, regex) in enumerate(sorted(self.filters.items()), start=1):
            _list.append(f'{num: 2d}. "{name}" - `{regex.pattern}`')

        list_str = '\n'.join(_list)
        embed = Embed(title='Registered message filters',
                      description=f'```\n{list_str}\n```')

        return await ctx.send(embed=embed)

    @command()
    async def addfilter(self, ctx: Context, name: str, *, regex: str):
        if not self.bot.is_admin(ctx.author):
            return
        if not self.bot.is_private(ctx.channel):
            return
        regex = regex.strip().strip('"').strip('`')
        name = name.strip()

        if name in self.filters:
            return await ctx.send(f'Filter "{name}" already exists.')

        try:
            self.filters[name] = re.compile(regex, re.IGNORECASE | re.DOTALL)
        except re.error as e:
            return await ctx.send(f'Compiling regex failed: {e}')

        await self.bot.db.exec(f'''INSERT INTO "{self.config["db_table"]}" (name, regex) VALUES ($1, $2)''',
                               name, regex)
        return await ctx.send(f'Added filter `{name}` (regex: `{regex}`) to filter list.')

    @command()
    async def modfilter(self, ctx: Context, name: str, *, regex: str):
        if not self.bot.is_admin(ctx.author):
            return
        if not self.bot.is_private(ctx.channel):
            return
        regex = regex.strip().strip('"').strip('`')
        name = name.strip()

        if name not in self.filters:
            return await ctx.send(f'Filter "{name}" does not exists.')

        try:
            self.filters[name] = re.compile(regex, re.IGNORECASE | re.DOTALL)
        except re.error as e:
            return await ctx.send(f'Compiling regex failed: {e}')

        await self.bot.db.exec(f'''UPDATE "{self.config["db_table"]}" SET "regex"=$1 WHERE "name"=$2''',
                               regex, name)
        return await ctx.send(f'Updated filter `{name}` to `{regex}`.')

    @command()
    async def delfilter(self, ctx: Context, *, name: str):
        if not self.bot.is_admin(ctx.author):
            return
        if not self.bot.is_private(ctx.channel):
            return

        if name not in self.filters:
            return await ctx.send(f'No filter named "{name}" exists.')
        else:
            regex = self.filters.pop(name)

        await self.bot.db.exec(f'''DELETE FROM "{self.config["db_table"]}" WHERE "name"=$1''', name)
        return await ctx.send(f'Removed filter `{name}` (regex: `{regex.pattern}`).')

    @command()
    async def setpunishment(self, ctx: Context, name: str, punishment: str = 'none'):
        if not self.bot.is_admin(ctx.author):
            return
        if not self.bot.is_private(ctx.channel):
            return

        if name not in self.filters:
            return await ctx.send(f'No filter named "{name}" exists.')

        punishment = punishment.strip().lower()
        if punishment not in ('none', 'ban', 'kick'):
            return await ctx.send(f'Invalid punishment: "{punishment}"')

        ban = kick = False
        if punishment == 'kick':
            kick = True
        elif punishment == 'ban':
            ban = True

        # remove before update
        if name in self.bannable and not ban:
            self.bannable.remove(name)
        if name in self.kickable and not kick:
            self.kickable.remove(name)

        await self.bot.db.exec(
            f'''UPDATE "{self.config["db_table"]}" SET "bannable"=$1, "kickable"=$2 WHERE "name"=$3''',
            ban, kick, name)
        if ban:
            self.bannable.add(name)
            return await ctx.send(f'Filter `{name}` has been set to ban on match.')
        elif kick:
            self.kickable.add(name)
            return await ctx.send(f'Filter `{name}` has been set to kick on match.')
        else:
            return await ctx.send(f'Filter `{name}` has been set to delete only.')

    @command()
    async def togglefiltering(self, ctx: Context):
        if not self.bot.is_admin(ctx.author):
            return
        if not self.bot.is_private(ctx.channel):
            return

        self.filtering_enabled = not self.filtering_enabled
        await ctx.send('Filtering {}.'.format('enabled' if self.filtering_enabled else 'disabled'))

    @command()
    async def testfilter(self, ctx: Context, *, message: str):
        if not self.bot.is_admin(ctx.author):
            return
        if not self.bot.is_private(ctx.channel):
            return

        matches = []
        for name, regex in self.filters.items():
            m = regex.search(message)
            if m:
                matches.append((name, regex.pattern, m.group()))

        if not matches:
            return await ctx.send('No filters matched.')

        message = ['The following filters matched:']
        for name, pat, res in matches:
            message.append(f'- Name: `{name}`, Regex: `{pat}`, Match: `{res}`')

        return await ctx.send('\n'.join(message))

    @command()
    async def killcount(self, ctx: Context):
        if not self.bot.is_admin(ctx.author):
            return
        if not self.bot.is_private(ctx.channel):
            return

        message = [
            f'- Total deletions: {self.bot.state["mod_deletes"]}',
            f'- Total kicks: {self.bot.state["mod_kicks"]}',
            f'- Total bans: {self.bot.state["mod_bans"]}',
            f'- Times faster than Dyno: {self.bot.state["mod_faster"]}',
            '- Days since last false-positive: 0'
        ]
        return await ctx.send('\n'.join(message))

    @Cog.listener()
    async def on_message(self, msg: Message):
        if not self.filtering_enabled:
            return
        # check if channel is in private (these are ignored)
        if self.bot.is_private(msg.channel):
            return
        if self.bot.is_supporter(msg.author):
            return

        # go through bannable rules first, then kickable, then just delete
        for name, regex in sorted(self.filters.items(), reverse=True,
                                  key=lambda a: (a[0] in self.bannable) * 2 + (a[0] in self.kickable)):
            m = regex.search(msg.content)
            if m:
                break
        else:  # no filter match
            return

        try:
            await msg.delete()
            deleted = 'Yes'
            self.bot.state['mod_faster'] += 1
        except Exception as e:
            deleted = f'No, failed with error: {e!r}'
        finally:
            self.bot.state['mod_deletes'] += 1

        embed = Embed(colour=0xC90000,  # title='Message Filter Match',
                      description=f'**Message by** {msg.author.mention} **in** '
                                  f'{msg.channel.mention} **matched filter:**\n'
                                  f'```\n{msg.content}\n```')
        embed.set_footer(text=f'Message ID: {msg.id}')
        embed.add_field(name='Filter name', value=f'`{name}`', inline=True)
        embed.add_field(name='Filter regex', value=f'`{regex.pattern}`', inline=True)
        embed.add_field(name='Regex match', value=f'`{m.group()}`', inline=True)
        embed.add_field(name='Message deleted?', value=deleted)

        if name in self.bannable:
            try:
                await msg.author.ban(delete_message_days=1, reason=f'Regex rule "{name}" matched.')
                embed.add_field(name='User banned?', value='Yes')
                self.bot.state['mod_bans'] += 1
            except Exception as e:
                logger.warning(f'Banning user {msg.author} failed: {e!r}')
                embed.add_field(name='User banned?', value=f'No, failed with error: {e!r}')

        if name in self.kickable:
            try:
                await msg.author.kick(reason=f'Regex rule "{name}" matched.')
                embed.add_field(name='User kicked?', value='Yes')
                self.bot.state['mod_kicks'] += 1
            except Exception as e:
                logger.warning(f'Banning user {msg.author} failed: {e!r}')
                embed.add_field(name='User kicked?', value=f'No, failed with error: {e!r}')

        return await self.log_channel.send(embed=embed)


def setup(bot):
    if bot.config.get('onlybans', {}).get('enabled', False):
        logger.info('Enabling moderation cog...')
        mot = OnlyBans(bot, bot.config['onlybans'])
        bot.add_cog(mot)
        bot.loop.create_task(mot.fetch_filters())
    else:
        logger.info('moderation cog not enabled.')
