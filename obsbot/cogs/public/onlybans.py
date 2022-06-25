import logging
import math
import re
import time

from disnake import Message, Embed
from disnake.ext.commands import Cog, Context, command

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
        self.sorted_filters = []

        if self.bot.state.get('mod_deletes') is None:
            self.bot.state['mod_deletes'] = 0
        if self.bot.state.get('mod_faster') is None:
            self.bot.state['mod_faster'] = 0
        if self.bot.state.get('mod_bans') is None:
            self.bot.state['mod_bans'] = 0
        if self.bot.state.get('mod_kicks') is None:
            self.bot.state['mod_kicks'] = 0

        # timestamps for stats
        if self.bot.state.get('mod_falsepositive_ts') is None:
            self.bot.state['mod_falsepositive_ts'] = 0
        if self.bot.state.get('mod_first_ban') is None:
            self.bot.state['mod_first_ban'] = 0
        if self.bot.state.get('mod_first_kick') is None:
            self.bot.state['mod_first_kick'] = 0
        if self.bot.state.get('mod_first_delete') is None:
            self.bot.state['mod_first_delete'] = 0

        if admin := self.bot.get_cog('Admin'):
            admin.add_help_section(
                'Moderation',
                [
                    ('.listfilters', 'List existing filters'),
                    ('.addfilter "<name>" `<regex>`', 'Add regex-based message filter'),
                    ('.modfilter "<name>" `<regex>`', 'Update regex of filter'),
                    ('.delfilter "<name>"', 'Delete filter'),
                    ('.setpunishment "<name>" [none/kick/ban]', 'sets additional violation action (default: none)'),
                    ('.testfilters <message>', 'Test if message gets caught by any filter'),
                    ('.togglefiltering', 'Enable/Disable filtering'),
                    ('.filterstats', 'Print some stats'),
                    ('.resettheclock', 'Reset days since last false-positive to 0'),
                ],
                restricted=True,
            )

    def sort_filters(self):
        self.sorted_filters = sorted(
            self.filters.items(), reverse=True, key=lambda a: (a[0] in self.bannable) * 2 + (a[0] in self.kickable)
        )
        logger.info(f'Presorted {len(self.sorted_filters)} filters.')

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
        self.sort_filters()
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

        _ban_filters = []
        _kick_filters = []
        _delete_filters = []
        for name, regex in sorted(self.filters.items()):
            if name in self.bannable:
                _ban_filters.append(f'* "{name}" - `{regex.pattern}`')
            elif name in self.kickable:
                _kick_filters.append(f'* "{name}" - `{regex.pattern}`')
            else:
                _delete_filters.append(f'* "{name}" - `{regex.pattern}`')

        embed = Embed(title='Registered Message Filters')
        if _ban_filters:
            embed.add_field(name='Ban Filters', inline=False, value='```\n{}\n```'.format('\n'.join(_ban_filters)))
        if _kick_filters:
            embed.add_field(name='Kick Filters', inline=False, value='```\n{}\n```'.format('\n'.join(_kick_filters)))
        if _delete_filters:
            embed.add_field(
                name='Delete Filters', inline=False, value='```\n{}\n```'.format('\n'.join(_delete_filters))
            )

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
            self.sort_filters()
        except re.error as e:
            return await ctx.send(f'Compiling regex failed: {e}')

        await self.bot.db.exec(
            f'''INSERT INTO "{self.config["db_table"]}" (name, regex) VALUES ($1, $2)''', name, regex
        )
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
            self.sort_filters()
        except re.error as e:
            return await ctx.send(f'Compiling regex failed: {e}')

        await self.bot.db.exec(f'''UPDATE "{self.config["db_table"]}" SET "regex"=$1 WHERE "name"=$2''', regex, name)
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
            self.sort_filters()

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
            f'''UPDATE "{self.config["db_table"]}" SET "bannable"=$1, "kickable"=$2 WHERE "name"=$3''', ban, kick, name
        )

        if ban:
            self.bannable.add(name)
            await ctx.send(f'Filter `{name}` has been set to ban on match.')
        elif kick:
            self.kickable.add(name)
            await ctx.send(f'Filter `{name}` has been set to kick on match.')
        else:
            await ctx.send(f'Filter `{name}` has been set to delete only.')

        self.sort_filters()

    @command()
    async def togglefiltering(self, ctx: Context):
        if not self.bot.is_admin(ctx.author):
            return
        if not self.bot.is_private(ctx.channel):
            return

        self.filtering_enabled = not self.filtering_enabled
        await ctx.send('Filtering {}.'.format('enabled' if self.filtering_enabled else 'disabled'))

    @command(aliases=['testfilter'])
    async def testfilters(self, ctx: Context, *, message: str):
        if not self.bot.is_admin(ctx.author):
            return
        if not self.bot.is_private(ctx.channel):
            return

        matches = []
        for name, regex in self.sorted_filters:
            m = regex.search(message)
            if m:
                matches.append((name, regex.pattern, m.group()))

        if not matches:
            return await ctx.send('No filters matched.')

        message = ['The following filters matched:']
        for name, pat, res in matches:
            message.append(f'- Name: `{name}`, Regex: `{pat}`, Match: `{res}`')

        return await ctx.send('\n'.join(message))

    @command(aliases=['killcount'])
    async def filterstats(self, ctx: Context):
        if not self.bot.is_admin(ctx.author):
            return
        if not self.bot.is_private(ctx.channel):
            return

        time_now = time.time()
        ban_delta = time_now - self.bot.state['mod_first_ban']
        kick_delta = time_now - self.bot.state['mod_first_kick']
        delete_delta = time_now - self.bot.state['mod_first_delete']

        bans_per_day = self.bot.state["mod_bans"] / (ban_delta / 86400)
        kicks_per_day = self.bot.state["mod_kicks"] / (kick_delta / 86400)
        deletes_per_day = self.bot.state["mod_deletes"] / (delete_delta / 86400)

        days_since_fp = math.floor((time_now - self.bot.state['mod_falsepositive_ts']) / 86400)

        message = [
            f'- Total deletions: {self.bot.state["mod_deletes"]} ({deletes_per_day:.02f} per day)',
            f'- Total kicks: {self.bot.state["mod_kicks"]} ({kicks_per_day:.02f} per day)',
            f'- Total bans: {self.bot.state["mod_bans"]} ({bans_per_day:.02f} per day)',
            f'- Times faster than Dyno: {self.bot.state["mod_faster"]}',
            f'- Days since last false-positive: {days_since_fp:d}',
        ]
        return await ctx.send('\n'.join(message))

    @command()
    async def resettheclock(self, ctx: Context):
        if not self.bot.is_admin(ctx.author):
            return
        if not self.bot.is_private(ctx.channel):
            return

        last_fp = self.bot.state['mod_falsepositive_ts']
        now = time.time()

        delta = now - last_fp
        delta_hours = delta // 3600
        delta_days = delta_hours // 24
        delta_hours = delta_hours % 24

        self.bot.state['mod_falsepositive_ts'] = now
        await ctx.send(f'Clock was reset after {delta_days:.0f} days {delta_hours:.0f} hours.')

    async def run_message_filters(self, msg: Message) -> bool:
        if not self.filtering_enabled:
            return False
        # check if channel is in private (these are ignored)
        if self.bot.is_private(msg.channel):
            return False
        if self.bot.is_supporter(msg.author):
            return False

        # go through bannable rules first, then kickable, then just delete
        for name, regex in self.sorted_filters:
            m = regex.search(msg.content)
            if m:
                break
        else:  # no filter match
            return False

        try:
            await msg.delete()
            deleted = 'Yes'
            self.bot.state['mod_faster'] += 1
        except Exception as e:
            deleted = f'No, failed with error: {e!r}'
        finally:
            self.bot.state['mod_deletes'] += 1
            if not self.bot.state['mod_first_delete']:
                self.bot.state['mod_first_delete'] = time.time()

        embed = Embed(
            colour=0xC90000,  # title='Message Filter Match',
            description=f'**Message by** {msg.author.mention} **in** '
            f'{msg.channel.mention} **matched filter:**\n'
            f'```\n{msg.content}\n```',
        )
        embed.set_footer(text=f'Message ID: {msg.id}')
        embed.add_field(name='Filter name', value=f'`{name}`', inline=True)
        embed.add_field(name='Filter regex', value=f'`{regex.pattern}`', inline=True)
        embed.add_field(name='Regex match', value=f'`{m.group()}`', inline=True)
        embed.add_field(name='Message deleted?', value=deleted)

        if name in self.bannable:
            try:
                await msg.author.ban(delete_message_days=1, reason=f'Filter rule "{name}" matched.')
                embed.add_field(name='User banned?', value='Yes')
                self.bot.state['mod_bans'] += 1
                if not self.bot.state['mod_first_ban']:
                    self.bot.state['mod_first_ban'] = time.time()
            except Exception as e:
                logger.warning(f'Banning user {msg.author} failed: {e!r}')
                embed.add_field(name='User banned?', value=f'No, failed with error: {e!r}')
            else:
                logger.info(f'Banned user {msg.author.id}; Message {msg.id} matched filter "{name}"')
        elif name in self.kickable:
            try:
                await msg.author.kick(reason=f'Filter rule "{name}" matched.')
                embed.add_field(name='User kicked?', value='Yes')
                self.bot.state['mod_kicks'] += 1
                if not self.bot.state['mod_first_kick']:
                    self.bot.state['mod_first_kick'] = time.time()
            except Exception as e:
                logger.warning(f'Banning user {msg.author} failed: {e!r}')
                embed.add_field(name='User kicked?', value=f'No, failed with error: {e!r}')
            else:
                logger.info(f'Kicked user {msg.author.id}; Message {msg.id} matched filter "{name}"')
        else:
            logger.info(f'Deleted message by {msg.author.id}; Message {msg.id} matched filter "{name}"')

        await self.log_channel.send(embed=embed)
        return True

    @Cog.listener()
    async def on_message(self, msg: Message):
        if msg.author == self.bot.user:
            return
        # if any filters hit, do not forward the message
        if await self.run_message_filters(msg):
            return

        self.bot.dispatch('filtered_message', msg)


def setup(bot):
    if bot.config.get('onlybans', {}).get('enabled', False):
        logger.info('Enabling moderation cog...')
        mot = OnlyBans(bot, bot.config['onlybans'])
        bot.add_cog(mot)
        bot.loop.create_task(mot.fetch_filters())
    else:
        logger.info('moderation cog not enabled.')
