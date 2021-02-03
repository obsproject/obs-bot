import asyncio
import logging

from aiohttp import web
from discord.ext.commands import Cog

from .utils.github import GitHubHelper


logger = logging.getLogger(__name__)

_select_query = '''SELECT * FROM "{}" WHERE commit_hash = $1'''
_insert_query = '''INSERT INTO "{}" (commit_hash, channel_id, message_id) VALUES ($1, $2, $3)'''


class Webhooks(Cog):
    def __init__(self, bot, config):
        self.bot = bot
        self.config = config
        self.server = None

        self.commits_channel = None
        self.brief_channel = None
        self.ci_channels = None

        self.gh_helper = GitHubHelper(bot.session, config['github'], self.bot.state)

    async def http_server(self):
        # Note: Aauthentication for webhooks is handled by nginx, not the bot
        app = web.Application()
        app.router.add_post('/github', self.github_handler)
        # app.router.add_post('/azurepl', self.azure_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        self.server = web.TCPSite(runner, 'localhost', self.config['port'])
        # Silence access logging to console/logfile
        logging.getLogger('aiohttp.access').setLevel(logging.WARNING)
        # wait for bot to be ready, then start and find the channels
        await self.bot.wait_until_ready()
        self.commits_channel = self.bot.get_channel(self.config['github']['commits_channel'])
        self.brief_channel = self.bot.get_channel(self.config['github']['brief_commits_channel'])
        self.ci_channels = []
        for cid in self.config['ci_channels']:
            self.ci_channels.append(self.bot.get_channel(cid))

        logger.info(f'Start listening on localhost:{self.config["port"]}')
        await self.server.start()

    async def github_handler(self, request):
        event = request.headers['X-GitHub-Event']
        body = await request.json()

        if event == 'push':
            messages = []
            # this gets the short and long embeds to send to the respective channels
            brief = await self.gh_helper.get_commit_messages(body, True)
            for embed, commit_hash in brief:
                msg = await self.brief_channel.send(embed=embed)
                if commit_hash:
                    messages.append((msg, commit_hash))

            full = await self.gh_helper.get_commit_messages(body, False)
            for embed, commit_hash in full:
                msg = await self.commits_channel.send(embed=embed)
                if commit_hash:
                    messages.append((msg, commit_hash))

            # finally, save the commit message ids to the database for CI additions later
            self.bot.loop.create_task(self.add_messages_to_db(messages))
        elif event == 'pull_request':
            # we only care about opened PRs
            if body['action'] == 'opened':
                brief, full = await self.gh_helper.get_pr_messages(body)
                await self.brief_channel.send(embed=brief)
                await self.commits_channel.send(embed=full)
        elif event == 'issues':
            # same as PRs basically
            if body['action'] == 'opened':
                brief, full = await self.gh_helper.get_issue_messages(body)
                await self.brief_channel.send(embed=brief)
                await self.commits_channel.send(embed=full)
        elif event == 'check_suite':
            # GitHub's API for actions is trash. We can't enable the "workflow_run" event for
            # webhooks so what we have to do is wait for "check_suite", then fetch the list
            # of workflow runs from the API, then find the one that matches the check suite id
            # that we got from this event, and then *hopefully* we found the right thing...
            # Of course, we won't know if it's the right workflow until we check it because
            # "check_suite" does not include *any* useful information that would tell us
            # which workflow it came from. Great, right?
            # The best thing we can do is look at "latest_check_runs_count" and see if it's
            # at least bigger than one so we know it's not the clang check...

            # Intuitively, pull_requests being empty means that the suite ran on a PR.
            if not body['check_suite']['pull_requests']:
                return web.Response(text='Thanks Github.')

            # some other suite (documentation/clang) and not CI
            if body['check_suite']['latest_check_runs_count'] < 2:
                return web.Response(text='OK')

            if body['action'] == 'completed':
                result = await self.gh_helper.get_ci_results(body)
                if not result:
                    logger.error('Getting GitHub CI result failed.')
                    return web.Response(text='OK but not really')
                build_success, embed, update_info = result
                self.bot.loop.create_task(self.add_ci_info_to_messages(*update_info))

                # only post build result if build failed or status changed (e.g. success->failed)
                if not build_success or (self.bot.state.get('ci_last_result', False) != build_success):
                    for chan in self.ci_channels:
                        await chan.send(embed=embed)

                self.bot.state['ci_last_result'] = build_success
        else:
            logger.debug(f'Unhandled github event: {event}')

        return web.Response(text='OK')

    async def add_messages_to_db(self, messages):
        inserts = [(c, m.channel.id, m.id) for m, c in messages]
        return await self.bot.db.exec_multi(_insert_query.format(self.config['github']['db_table']), inserts)

    async def add_ci_info_to_messages(self, commit_hash, ci_msg, emote, build_url):
        field_value = f'<:{emote}> [{ci_msg}]({build_url})'
        # get matching ci messages from DB
        records = await self.bot.db.query(_select_query.format(self.config['github']['db_table']), commit_hash)

        for record in records:
            try:
                chan = self.bot.get_channel(record['channel_id'])
                msg = await chan.fetch_message(record['message_id'])
                embed = msg.embeds[0]
            except Exception as e:
                logger.error(f'Getting commit message for editing failed with error {repr(e)}')
            else:
                if len(embed.fields) == 2:  # no CI info yet
                    embed.add_field(name='Continuous Integration',
                                    value=field_value, inline=False)
                else:  # append to existing CI info
                    new_val = '\n'.join((embed.fields[2].value, field_value))
                    embed.set_field_at(2, name='Continuous Integration',
                                       value=new_val, inline=False)
                try:
                    await msg.edit(embed=embed)
                except Exception as e:
                    logger.error(f'Editing commit message failed with error {repr(e)}')

    def cog_unload(self):
        if self.server:
            asyncio.create_task(self.server.stop())


def setup(bot):
    if bot.config.get('webhooks', {}).get('enabled', False):
        logger.info('Enabling Webhooks cog...')
        wh = Webhooks(bot, bot.config['webhooks'])
        bot.add_cog(wh)
        bot.loop.create_task(wh.http_server())
