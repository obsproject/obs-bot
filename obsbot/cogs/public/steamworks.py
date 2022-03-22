import asyncio
import logging

from collections import defaultdict

from disnake import Embed, MessageInteraction
from disnake.enums import ButtonStyle
from disnake.ext import tasks
from disnake.ext.commands import Cog, command
from disnake.ext.commands.context import Context
from disnake.ui.action_row import ActionRow


logger = logging.getLogger(__name__)
STEAMWORKS_COLOUR = 0x1b1e22
STEAMWORKS_API_URL = 'https://partner.steam-api.com'
GITHUB_API_URL = 'https://api.github.com'


class Steamworks(Cog):
    def __init__(self, bot, config):
        self.bot = bot
        self.config = config
        self.session = bot.session

        if not self.bot.state.get('last_build_map'):
            self.bot.state['last_build_map'] = dict()

        self.steam_channel = None

        self.build_update_cron.start()

    @Cog.listener()
    async def on_ready(self):
        # find channel
        self.steam_channel = self.bot.get_channel(self.config['steam_channel'])
        if self.steam_channel:
            logger.info(f'Found Steam channel: {self.steam_channel}')

    async def get_builds(self):
        builds = await self.get_with_retry(f'{STEAMWORKS_API_URL}/ISteamApps/GetAppBuilds/v1/',
                                           params=dict(appid=self.config['app_id'], key=self.config['api_key'],
                                                       count=50))
        return builds.get('response', {}).get('builds', {})

    async def get_branches(self):
        builds = await self.get_with_retry(f'{STEAMWORKS_API_URL}/ISteamApps/GetAppBetas/v1/',
                                           params=dict(appid=self.config['app_id'], key=self.config['api_key']))
        return builds.get('response', {}).get('betas', {})

    async def set_build_live(self, build_id, branch='public', desc=''):
        builds = await self.post_with_retry(f'{STEAMWORKS_API_URL}/ISteamApps/SetAppBuildLive/v1/',
                                            data=dict(appid=self.config['app_id'], key=self.config['api_key'],
                                                      buildid=build_id, betakey=branch, description=desc))
        return builds.get('response', {})

    async def get_with_retry(self, url, params=None, retries=5, retry_interval=5.0):
        for i in range(retries):
            try:
                async with self.session.get(url, params=params) as r:
                    r.raise_for_status()
                    return await r.json()
            except Exception as e:
                logger.warning(f'Steamworks API request failed with {repr(e)}, retrying in {retry_interval} seconds')
                await asyncio.sleep(retry_interval)

        logger.error('Retries exhausted!')
        return None

    async def post_with_retry(self, url, data=None, retries=5, retry_interval=5.0):
        for i in range(retries):
            try:
                async with self.session.post(url, data=data) as r:
                    r.raise_for_status()
                    return await r.json()
            except Exception as e:
                logger.warning(f'Steamworks API request failed with {repr(e)}, retrying in {retry_interval} seconds')
                await asyncio.sleep(retry_interval)

        logger.error('Retries exhausted!')
        return None

    @tasks.loop(minutes=60)
    async def build_update_cron(self):
        self.bot.loop.create_task(self.build_update())

    async def build_update(self, run_data=None):
        if not self.steam_channel:
            logger.info('No Steam channel found, skipping build update')
            return

        recent_builds = await self.get_builds()
        branches = await self.get_branches()
        # build map from id to branch
        # don't do this, builds can have multiple branches!
        id_branch_map = defaultdict(set)
        for branch_name, branch in branches.items():
            id_branch_map[branch['BuildID']].add(branch_name)

        build_map = self.bot.state['last_build_map']
        new_build_map = dict()

        # iterate over builds, post info on all unseen builds that are on branches
        for build_id, build_info in sorted(recent_builds.items(), key=lambda a: int(a[0])):
            # for some reason, the build_id is a string here but int everywhere else
            build_id = int(build_id)
            if build_id not in id_branch_map:
                continue

            current_branches = id_branch_map[build_id]
            # if build is not new to this branch, skip
            previous_branches = set(branch for branch, old_build_id in build_map.items() if old_build_id == build_id)

            # copy/write to new map
            for branch in current_branches:
                new_build_map[branch] = build_id

            if previous_branches == current_branches:
                # no changes
                continue

            # we only care about branches the build is "new" to
            current_branches -= previous_branches
            # if no branches are left build was removed from a previous one, but not added to a new one
            if not current_branches:
                continue

            description = build_info['Description']
            embed = Embed(title='New Build pushed to branch', colour=STEAMWORKS_COLOUR)
            embed.add_field(name='Build ID', value=build_id)
            embed.add_field(name='Branch(es)', value=', '.join(sorted(current_branches)))
            embed.add_field(name='Description', value=description)

            # If update was triggered by webhook, add extra info to the corresponding commit (if possible).
            # Also add info for nightly builds which include a commit hash in the description
            if 'nightly-g' in description:
                # fetch commit metadata based on description
                shorthash = description.rpartition('g')[2]
                commit_info = await self.get_with_retry(f'{GITHUB_API_URL}/repos/{self.config["repo"]}'
                                                        f'/commits/{shorthash}')
                base_name = commit_info['commit']['message'].partition('\n')[0]
                base_url = commit_info['html_url']

                embed.add_field(name='Trigger', value='cronjob/manual run' if not run_data else run_data['event'])
                embed.description = f'Based on Commit [{base_name}]({base_url})'
            elif run_data and run_data['event'] == 'release':
                release_tag = run_data['head_branch']

                # check if build matches release tag
                if description.endswith(release_tag):
                    release_info = await self.get_with_retry(f'{GITHUB_API_URL}/repos/{self.config["repo"]}'
                                                             f'/releases/tags/{release_tag}')
                    # fetch release metadata?
                    base_type = 'Pre-Release' if release_info['prerelease'] else 'Release'
                    base_name = release_info['name']
                    base_url = release_info['html_url']

                    embed.add_field(name='Trigger', value='release')
                    embed.description = f'Based on {base_type} [{base_name}]({base_url})'

            row = ActionRow()
            row.add_button(style=ButtonStyle.link, label='Manage builds',
                           url=f'https://partner.steamgames.com/apps/builds/{self.config["app_id"]}')
            row.add_button(style=ButtonStyle.link, label='Build details',
                           url=f'https://partner.steamgames.com/apps/builddetails/{self.config["app_id"]}/{build_id}')

            # if it's a known staging branch, offer the push-to-live button
            for current_branch in current_branches:
                target_branch = self.config['branches'].get(current_branch)
                if target_branch and target_branch not in current_branches:
                    row.add_button(label=f'Push to "{target_branch}" branch',
                                   style=ButtonStyle.danger,
                                   custom_id=f'steamworks_{build_id}_{target_branch}')

            await self.steam_channel.send(embed=embed, components=row)

        self.bot.state['last_build_map'] = new_build_map

    @Cog.listener()
    async def on_button_click(self, interaction: MessageInteraction):
        if not interaction.data.custom_id.startswith('steamworks_'):
            return
        if not self.bot.is_contributor(interaction.author):
            return await interaction.response.send_message('You do not have permission to use this.', ephemeral=True)

        _, build_id, target_branch = interaction.data.custom_id.split('_')
        res = await self.set_build_live(build_id, target_branch, desc=f'Requested by {interaction.author}')
        if res['result'] != 1:
            embed = Embed(title='Build publishing failed.',
                          description=f'Failed to push build to "{target_branch}":\n'
                                      f'```\n{res["message"]}\n```')
            return await interaction.response.send_message(embed=embed)

        embed = interaction.message.embeds[0]
        embed.add_field(name='Published', value='Yes')
        await interaction.response.edit_message(embed=embed, components=None)
        await interaction.followup.send(content='Build published successfully. Scheduling refresh...', ephemeral=True)
        # tell bot to fetch update
        self.bot.loop.create_task(self.build_update())

    @command()
    async def update_builds(self, context: Context):
        if not self.bot.is_contributor(context.author):
            return
        if context.channel != self.steam_channel:
            return
        self.bot.loop.create_task(self.build_update())
        return await context.reply('Scheduled fetching build update.')


def setup(bot):
    if bot.config.get('steamworks', {}).get('enabled', False):
        logger.info('Enabling steamworks cog...')
        sw = Steamworks(bot, bot.config['steamworks'])
        bot.add_cog(sw)
