import asyncio
import logging
import time

import dateutil.parser
from discord import Embed, Colour

logger = logging.getLogger(__name__)


class GitHubHelper:
    """Helper for processing github webhooks and API stuff"""
    _commit_colour = 0xffffff
    _skipped_commit_colour = 0x9b9b9b
    _pull_request_colour = 0x366d6
    _issue_colour = 0x2CBE4E
    _discussion_colour = 0x9a66ee
    _ci_failed_colour = 0xd0021b
    _ci_some_failed_colour = 0xF5A623
    _ci_passed_colour = 0x7ED321
    _wiki_colour = 0xf8c8dc

    def __init__(self, session, config, state):
        self.session = session
        self.config = config
        self.state = state

        # internal user cache, gets invalidated on refreshes
        self.user_cache = dict()
        self.user_cache_max_age = 3600 * 24 * 7

    async def get_commit_messages(self, event_body, brief=False):
        embed_commits = []
        branch = event_body['ref'].split('/', 2)[2]
        project = event_body['repository']['full_name']
        commits = event_body['commits']

        if brief and len(commits) > self.config['commit_truncation_limit']:
            first_hash = commits[0]['id']
            last_hash = commits[-2]['id']
            compare_url = f'https://github.com/{project}/compare/{first_hash}^...{last_hash}'
            embed = Embed(title=f'Skipped {len(commits) - 1} commits... (click link for diff)',
                          colour=Colour(self._skipped_commit_colour), url=compare_url)
            embed_commits.append((embed, None))
            commits = commits[-1:]

        for commit in commits:
            author_username = commit['author'].get('username', None)
            author_name = commit['author'].get('name', None)
            timestamp = dateutil.parser.parse(commit['timestamp'])
            commit_message = commit['message'].split('\n')
            embed = Embed(title=commit_message[0], colour=Colour(self._commit_colour),
                          url=commit['url'], timestamp=timestamp)

            if len(commit_message) > 2 and not brief:
                commit_body = '\n'.join(commit_message[2:])
                embed.description = commit_body[:4096]

            author = await self.get_author_info(author_username)

            if author:
                if author['name'] and author['name'] != author['login']:
                    author_name = f'{author["name"]} ({author["login"]})'
                else:
                    author_name = author['login']

                embed.set_author(name=author_name, url=author['html_url'], icon_url=author['avatar_url'])
            elif author_name:
                embed.set_author(name=author_name)
            else:
                embed.set_author(name='<No Name>')

            embed.set_footer(text='Commit')
            embed.add_field(name='Repository', value=project, inline=True)
            embed.add_field(name='Branch', value=branch, inline=True)
            embed_commits.append((embed, commit['id']))

        return embed_commits

    async def get_pr_messages(self, event_body):
        pr_number = event_body['number']
        title = event_body['pull_request']['title']
        timestamp = dateutil.parser.parse(event_body['pull_request']['created_at'])
        embed = Embed(title=f'#{pr_number}: {title}', colour=Colour(self._pull_request_colour),
                      url=event_body['pull_request']['html_url'], timestamp=timestamp)

        author_name = event_body['pull_request']['user']['login']
        author = await self.get_author_info(author_name)
        if author and author['name'] and author['name'] != author['login']:
            author_name = f'{author["name"]} ({author["login"]})'

        embed.set_author(name=author_name, url=event_body['pull_request']['user']['html_url'],
                         icon_url=event_body['pull_request']['user']['avatar_url'])

        embed.set_footer(text='Pull Request')
        embed.add_field(name='Repository', value=event_body['repository']['full_name'], inline=True)
        # create copy without description text for brief channel
        brief_embed = embed.copy()
        # filter out comments in template
        event_body['pull_request']['body'] = '\n'.join(
            l.strip() for l in event_body['pull_request']['body'].splitlines() if not l.startswith('<!-')
        )

        # trim message to discord limits
        if len(event_body['pull_request']['body']) >= 2048:
            embed.description = event_body['pull_request']['body'][:2000] + ' [... message trimmed]'
        else:
            embed.description = event_body['pull_request']['body']

        return brief_embed, embed

    async def get_issue_messages(self, event_body):
        issue_number = event_body['issue']['number']
        title = event_body['issue']['title']
        timestamp = dateutil.parser.parse(event_body['issue']['created_at'])
        embed = Embed(title=f'#{issue_number}: {title}', colour=Colour(self._issue_colour),
                      url=event_body['issue']['html_url'], timestamp=timestamp)

        author_name = event_body['issue']['user']['login']
        author = await self.get_author_info(author_name)
        if author and author['name'] and author['name'] != author['login']:
            author_name = f'{author["name"]} ({author["login"]})'

        embed.set_author(name=author_name, url=event_body['issue']['user']['html_url'],
                         icon_url=event_body['issue']['user']['avatar_url'])

        embed.set_footer(text='Issue')
        embed.add_field(name='Repository', value=event_body['repository']['full_name'], inline=True)
        # create copy without description text for brief channel
        brief_embed = embed.copy()
        event_body['issue']['body'] = '\n'.join(
            l.strip() for l in event_body['issue']['body'].splitlines() if not l.startswith('<!-')
        )

        issue_text = event_body['issue']['body']
        # strip double-newlines from issue forms
        if '\n\n' in issue_text:
            issue_text = issue_text.replace('\n\n', '\n')

        if len(issue_text) >= 2048:
            embed.description = issue_text[:2000] + ' [... message trimmed]'
        else:
            embed.description = issue_text

        return brief_embed, embed

    async def get_discussion_messages(self, event_body):
        discussion_number = event_body['discussion']['number']
        title = event_body['discussion']['title']
        category = event_body['discussion']['category']['name']
        timestamp = dateutil.parser.parse(event_body['discussion']['created_at'])
        embed = Embed(title=f'#{discussion_number}: {category} - {title}',
                      colour=Colour(self._discussion_colour), timestamp=timestamp,
                      url=event_body['discussion']['html_url'])

        author_name = event_body['discussion']['user']['login']
        author = await self.get_author_info(author_name)
        if author and author['name'] and author['name'] != author['login']:
            author_name = f'{author["name"]} ({author["login"]})'

        embed.set_author(name=author_name, url=event_body['discussion']['user']['html_url'],
                         icon_url=event_body['discussion']['user']['avatar_url'])

        embed.set_footer(text='Discussion')
        embed.add_field(name='Repository', value=event_body['repository']['full_name'], inline=True)
        # create copy without description text for brief channel
        brief_embed = embed.copy()
        event_body['discussion']['body'] = '\n'.join(
            l.strip() for l in event_body['discussion']['body'].splitlines() if not l.startswith('<!-')
        )

        if len(event_body['discussion']['body']) >= 1024:
            embed.description = event_body['discussion']['body'][:1024] + ' [... message trimmed]'
        else:
            embed.description = event_body['discussion']['body']

        return brief_embed, embed

    async def get_wiki_message(self, event_body):
        embed = Embed(colour=Colour(self._wiki_colour))
        embed.set_footer(text='GitHub Wiki Changes')
        # All edits in the response are from a single author
        author_name = event_body['sender']['login']
        author = await self.get_author_info(author_name)
        if author and author['name'] and author['name'] != author['login']:
            author_name = f'{author["name"]} ({author["login"]})'
        embed.set_author(name=author_name, url=event_body['sender']['html_url'],
                         icon_url=event_body['sender']['avatar_url'])
        body = []
        for page in event_body['pages']:
            diff_url = f'{page["html_url"]}/_compare/{page["sha"]}^...{page["sha"]}'
            page_url = f'{page["html_url"]}/{page["sha"]}'
            body.append(f'**{page["action"]}:** [{page["title"]}]({page_url}) [[diff]({diff_url})]')
        embed.description = '\n'.join(body)
        return embed

    async def get_ci_results(self, event_body):
        check_suite_id = event_body['check_suite']['id']
        # todo allow for different workflows per repo

        # because the github API might not be updated by the time we get the webhook, we 'd want to retry
        # this request a few times before giving up (did I mention the API related to actions is "great"? *sigh*)
        run = None
        for _try in range(1, 6):
            runs = await self.get_with_retry(f'https://api.github.com/repos/obsproject/obs-studio/'
                                             f'actions/workflows/{self.config["workflow_id"]}/runs',
                                             params=dict(event='push', status='completed', per_page=50,
                                                         t=int(time.time())))
            # if request + all retries failed, just give up
            if not runs and _try == 5:
                logger.error('Getting GitHub workflow runs failed horribly.')
                return None

            if runs:
                for _run in runs['workflow_runs']:
                    if _run['check_suite_id'] == check_suite_id:
                        run = _run
                        break
            if run:
                break
            # exponential backoff for subsequent tries
            logger.warning(f'Check suite ID wasn\'t in workflow results, retrying in {2**_try} seconds...')
            await asyncio.sleep(2.0 ** _try)
        else:
            logger.error('Could not find check suite id in workflow runs after 5 retries.')
            return None

        # get some useful metadata from run information
        commit_hash = run['head_sha']
        finished = dateutil.parser.parse(run['updated_at'])
        started = dateutil.parser.parse(run['created_at'])
        delta = (finished - started).seconds
        seconds = delta % 60
        minutes = delta // 60
        repo = run['repository']['full_name']
        branch = run['head_branch']
        web_url = run['html_url']

        jobs = await self.get_with_retry(run['jobs_url'])
        if not jobs:
            logger.error('Getting GitHub workflow run jobs failed.')
            return None
        jobs = jobs['jobs']

        total_jobs = len(jobs)
        failed = sum(i['conclusion'] != 'success' for i in jobs)
        build_success = failed == 0

        if failed == 0:
            colour = self._ci_passed_colour
            reaction_emote = self.config['emotes']['passed']
            build_result = 'succeeded'
            message = [f'All jobs succeeded after {minutes}m{seconds}s']
        elif failed < total_jobs:
            colour = self._ci_some_failed_colour
            reaction_emote = self.config['emotes']['partial']
            build_result = 'partially failed'
            message = [f'{failed} out of {total_jobs} jobs failed after {minutes}m{seconds}s']
        else:
            colour = self._ci_failed_colour
            reaction_emote = self.config['emotes']['failed']
            build_result = 'failed'
            message = [f'All jobs failed after {minutes}m{seconds}s']

        if succeeded := [job['name'] for job in jobs if job['conclusion'] == 'success']:
            message.append('**Succeeded:** {}'.format(', '.join(succeeded)))
        if failed := [job['name'] for job in jobs if job['conclusion'] != 'success']:
            message.append('**Failed:** {}'.format(', '.join(failed)))

        artifacts = await self.get_with_retry(run['artifacts_url'])
        if not artifacts:
            logger.error('Getting GitHub workflow run artifacts failed.')
            return None

        artifacts_entries = []
        for artifact in artifacts['artifacts']:
            # did I mention this API is great?
            artifact['archive_download_url'] = f'https://github.com/obsproject/obs-studio/suites/' \
                                               f'{check_suite_id}/artifacts/{artifact["id"]}'
            # update nightly build downloads in internal state
            if build_success and branch == 'master':
                if 'macOS' in artifact['name']:
                    self.state['nightly_macos'] = self.config['artifact_service'].format(artifact['id'])
                elif 'win64' in artifact['name']:
                    self.state['nightly_windows'] = self.config['artifact_service'].format(artifact['id'])

            artifact_name = artifact["name"].rpartition('-')[2]
            artifacts_entries.append(f'[{artifact_name}]({artifact["archive_download_url"]})')

        embed = Embed(title=f'Build {run["run_number"]} {build_result}', url=web_url,
                      description='\n'.join(message), timestamp=finished,
                      colour=colour)
        embed.set_author(name='GitHub Actions',
                         icon_url='https://cdn.rodney.io/stuff/obsbot/github_actions.png')
        embed.add_field(name="Project", value=repo, inline=True)
        embed.add_field(name="Branch", value=branch, inline=True)
        # only attach artifact urls if build succeeded
        if build_success:
            embed.add_field(name='Artifacts', inline=False, value=', '.join(artifacts_entries))

        return build_success, embed, (commit_hash, message[0], reaction_emote, web_url)

    async def get_with_retry(self, url, params=None, retries=5, retry_interval=5.0):
        for i in range(retries):
            try:
                async with self.session.get(
                        url, params=params, headers=dict(Authorization=self.config['github_api_auth'])) as r:
                    r.raise_for_status()
                    return await r.json()
            except Exception as e:
                logger.warning(f'Github API request failed with {repr(e)}, retrying in {retry_interval} seconds')
                await asyncio.sleep(retry_interval)

        logger.error('Retries exhausted!')
        return None

    async def get_author_info(self, username):
        if not username:
            return None

        if username in self.user_cache:
            # check if data is stale, if not try refetching
            if (time.time() - self.user_cache[username].get('_timestamp', 0)) > self.user_cache_max_age:
                return self.user_cache[username]

        try:
            async with self.session.get(f'https://api.github.com/users/{username}',
                                        headers={'Authorization': self.config['github_api_auth']}) as r:
                r.raise_for_status()
                author = await r.json()
                self.user_cache[username] = author
                self.user_cache[username]['_timestamp'] = time.time()
        except Exception as e:
            logger.warning(f'Fetching github userdata failed with {repr(e)}')
            # return potentially stale data if request fails
            return self.user_cache.get(username, None)

        return self.user_cache[username]
