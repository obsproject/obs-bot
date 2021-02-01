import logging
import time

from discord import Embed, Game, CustomActivity
from discord.ext.commands import Cog, command, Context

from obsbot import __version__, __codename__

logger = logging.getLogger(__name__)


class Admin(Cog):
    def __init__(self, bot):
        self.bot = bot
        self.help_sections = {
            'Admininistrative': [
                ('.help', 'Display this help'),
                ('.status', 'prints the bot\'s current status')
            ]
        }

    @command()
    async def help(self, ctx: Context):
        # unfortunately the @check decorator doesn't really work for this use case.
        if not self.bot.is_admin(ctx.author):
            return

        embed = Embed(title='OBS Bot Help')
        for section, commands in self.help_sections.items():
            longest = max(len(cmd) for cmd, _ in commands)
            content = '\n'.join(f'{cmd}{" " * (longest - len(cmd) + 1)}- {helptext}'
                                for cmd, helptext in commands)
            embed.add_field(name=section,
                            value=f'```{content}```',
                            inline=False)
        return await ctx.channel.send(embed=embed)

    @command()
    async def status(self, ctx: Context):
        if not self.bot.is_admin(ctx.author):
            return

        embed = Embed(title='OBS Bot Status')
        embed.add_field(name='Core', inline=False,
                        value=(f'Version:  {__version__} - "{__codename__}" Edition\n'
                               f'Uptime:  {time.time() - self.bot.start_time:.0f} seconds\n'))

        # todo actually implement this for real
        embed.add_field(name='Bot admins', inline=False,
                        value=', '.join(['Rodney', 'Also Rodney']))

        # get information from other Cogs if possible
        if fac := self.bot.get_cog('Factoids'):
            total_uses = sum(i['uses'] for i in fac.factoids.values())
            embed.add_field(name='Factoid module', inline=False,
                            value=(f'Factoids:  {len(fac.factoids)}\n'
                                   f'Aliases:  {len(fac.alias_map)}\n'
                                   f'Total uses:  {total_uses} (since 2018-06-07)'))

        if cron := self.bot.get_cog('Cron'):
            embed.add_field(name='Cron module', inline=False,
                            value=f'Last Fider ID: N/A\n')

        return await ctx.channel.send(embed=embed)

    @command(aliases=['setgame', 'changegame'])
    async def setpresence(self, ctx: Context, *, activity):
        if not self.bot.is_admin(ctx.author):
            return
        logger.info(f'Game changed to "{activity}" by {str(ctx.author)}')
        self.bot.state['game'] = activity
        return await self.bot.change_presence(activity=Game(activity))

    def add_help_section(self, section_name, command_list):
        """Allows external Cogs to register their own help section"""
        self.help_sections[section_name] = command_list


def setup(bot):
    bot.add_cog(Admin(bot))
