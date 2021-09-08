import logging

import dateutil.parser

from discord import Embed, Colour
from discord.ext import commands, tasks
from peony import PeonyClient
from peony.exceptions import PeonyException

logger = logging.getLogger(__name__)


class Cron(commands.Cog):
    _fider_colour = 0x8B572A

    def __init__(self, bot, config):
        self.bot = bot
        self.config = config

        # this will be the channels the cron updates are sent to
        self.fider_channel = None
        self.twitter_channel = None

        if intv := self.config.get('interval'):
            logger.info(f'Changing task interval to {intv} seconds')
            self.fider.change_interval(seconds=intv)
            self.twitter.change_interval(seconds=intv)

        if 'twitter' in self.config and (creds := self.config['twitter'].get('credentials')):
            # silence peony
            logging.getLogger('peony.data_processing').setLevel(logging.WARNING)
            self.twitter_client = PeonyClient(**creds, loop=self.bot.loop,
                                              session=self.bot.session)
            logger.info('Starting twitter cronjob...')
            self.twitter.add_exception_type(PeonyException)
            self.twitter.start()
        else:
            logger.warning('No twitter credentials, disabling...')

        logger.info('Starting Fider cronjob...')
        self.fider.start()

    @tasks.loop(minutes=5.0)
    async def fider(self):
        items = []
        async with self.bot.session.get('https://ideas.obsproject.com/api/v1/posts?view=recent') as r:
            if r.status == 200:
                feed = await r.json()
                for entry in feed:
                    if entry['id'] <= self.bot.state['fider_last_id']:
                        continue
                    items.append(entry)
            else:
                logger.warning(f'Fetching fider posts failed with status code {r.status}')
                return

        for item in reversed(items):
            if item['id'] > self.bot.state['fider_last_id']:
                self.bot.state['fider_last_id'] = item['id']

            url = f'https://ideas.obsproject.com/posts/{item["id"]}/'
            logger.info(f'Got new Fider post: {url}')

            description = item['description']
            if len(description) > 180:
                description = description[:180] + ' [...]'

            embed = Embed(title=f'{item["id"]}: {item["title"]}',
                          colour=Colour(self._fider_colour),
                          url=url, description=description,
                          timestamp=dateutil.parser.parse(item['createdAt']))

            embed.set_author(name='Fider', url='https://ideas.obsproject.com/',
                             icon_url='https://cdn.rodney.io/stuff/obsbot/fider.png')
            embed.set_footer(text='New Idea on Fider')
            name = 'Anonymous' if not item['user']['name'] else item['user']['name']
            embed.add_field(name='Created By', value=name, inline=True)
            await self.fider_channel.send(embed=embed)

    @fider.before_loop
    async def before_fider(self):
        # wait for bot to be ready, then get channel
        await self.bot.wait_until_ready()
        # override last id if not yet set to prevent spam
        if not self.bot.state.get('fider_last_id'):
            self.bot.state['fider_last_id'] = self.config['fider']['default_last_id']
        cid = self.config['fider']['channel_id']
        self.fider_channel = self.bot.get_channel(cid)
        logger.info(f'Found fider channel: {str(self.fider_channel)}')

    @tasks.loop(minutes=5.0)
    async def twitter(self):
        _user_id = self.config['twitter']['account_id']
        _user_name = self.config['twitter']['screen_name']

        tweets = await self.twitter_client.api.statuses.user_timeline.get(
            screen_name=_user_name.lower(), _timeout=1, count=100, trim_user=True,
            since_id=self.bot.state['twitter_last_id']
        )

        for tweet in sorted(tweets, key=lambda a: a['id']):
            # exclude replies not to self
            if tweet['in_reply_to_user_id'] and tweet['in_reply_to_user_id'] != _user_id:
                continue
            # exclude replies to self *and* others
            if any(um['id'] != _user_id for um in tweet['entities']['user_mentions']):
                continue
            # just send URL to channel, Discord will take care of the embedding
            await self.twitter_channel.send(f'https://twitter.com/{_user_name}/status/{tweet["id_str"]}')
            self.bot.state['twitter_last_id'] = tweet['id']

    @twitter.before_loop
    async def before_twitter(self):
        await self.bot.wait_until_ready()
        # override last id if not yet set to prevent spam
        if not self.bot.state.get('twitter_last_id'):
            self.bot.state['twitter_last_id'] = self.config['twitter']['default_last_id']
        cid = self.config['twitter']['channel_id']
        self.twitter_channel = self.bot.get_channel(cid)
        logger.info(f'Found twitter channel: {str(self.twitter_channel)}')


def setup(bot):
    if bot.config.get('cron', {}).get('enabled', False):
        logger.info('Enabling cronjob cog...')
        bot.add_cog(Cron(bot, bot.config['cron']))
    else:
        logger.info('cronjob cog not enabled.')
