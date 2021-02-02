import logging
import json
import random

from urllib.parse import quote_plus as urlencode

from aiohttp import ClientResponseError
from discord import Message, Embed, Colour
from discord.ext.commands import Cog, command, Context

logger = logging.getLogger(__name__)

_increment_query = '''UPDATE "{}" SET counts=counts+1 WHERE gpu_id=$1 AND cpu_id=$2'''
_insert_query = '''INSERT INTO "{}" (gpu_id, cpu_id, name, counts) VALUES ($1, $2, $3, $4)'''


class LogAnalyser(Cog):
    _analysis_colour = 0x5a7474
    _potato = '🥔'

    _filtered_log_needles = ('obs-streamelements.dll', 'ftl_stream_create')
    _log_hosts = ('https://obsproject.com/logs/', 'https://hastebin.com/', 'https://pastebin.com/')

    def __init__(self, bot, config):
        self.bot = bot
        self.config = config

        # this gets filled from the DB when the bot loads the cog
        self.hardware_stats = dict(cpu=dict(), gpu=dict())
        # The benchmark entries are sorted by name length to improve matching I guess?
        # To be honest I forgot why I did that, I just copies this from the old bot code.
        self.benchmark_data = dict(
            gpus=sorted(json.load(open('data/gpu_db.json')), key=lambda a: len(a['name'])),
            cpus=sorted(json.load(open('data/cpu_db.json')), key=lambda a: len(a['name'])),
        )

        if 'hw_check_enabled' not in self.bot.state:
            self.bot.state['hw_check_enabled'] = self.config.get('hw_check_enabled', False)

        self.channel_blacklist = set(self.config.get('channel_blacklist', {}))

        if admin := self.bot.get_cog('Admin'):
            admin.add_help_section('Log Analyser', [
                ('.togglehwcheck', 'Enable/Disable hardware check'),
                ('.tophardware', 'List most commonly seen CPUs and GPUs'),
            ])

    @Cog.listener()
    async def on_message(self, msg: Message):
        # check if channel is in blacklist, has possible log urls, or an attachment
        if msg.channel.id in self.channel_blacklist:
            return
        if not msg.attachments and not any(lh in msg.content for lh in self._log_hosts):
            return

        # list of candidate tuples consisting of (raw_url, web_url)
        log_candidates = []
        # message attachments
        for attachment in msg.attachments:
            if attachment.url.endswith('.log'):
                log_candidates.append((attachment.url, attachment.url))
        # links in message
        for part in [p.strip() for p in msg.content.split()]:
            if any(part.startswith(lh) for lh in self._log_hosts):
                if 'obsproject.com' in part:
                    log_candidates.append((part, part))
                elif 'hastebin.com' in part:
                    hastebin_id = part.rsplit('/', 1)[1]
                    if not hastebin_id:
                        continue
                    log_candidates.append((f'https://hastebin.com/raw/{hastebin_id}',
                                           f'https://hastebin.com/{hastebin_id}'))
                elif 'pastebin.com' in part:
                    pastebin_id = part.rsplit('/', 1)[1]
                    if not pastebin_id:
                        continue
                    log_candidates.append((f'https://pastebin.com/raw/{pastebin_id}',
                                           f'https://pastebin.com/{pastebin_id}'))

        if len(log_candidates) > 3:
            logger.warning('There are too many possible log URLs, cutting down to 3...')
            log_candidates = log_candidates[:3]

        async with msg.channel.typing():
            for raw_url, html_url in log_candidates:
                # download log for local analysis
                try:
                    log_content = await self.download_log(raw_url)
                except ValueError:  # not a valid OBS log
                    continue
                except ClientResponseError:  # file download failed
                    logger.error(f'Failed retrieving log from "{raw_url}"')
                    continue
                except Exception as e:  # catch everything else
                    logger.error(f'Unhanled exception when downloading log: {repr(e)}')
                    continue

                # fetch log analysis from OBS analyser
                try:
                    log_analysis = await self.fetch_log_analysis(raw_url)
                except ClientResponseError:  # file download failed
                    logger.error(f'Failed retrieving log from "{raw_url}"')
                    continue
                except Exception as e:  # catch everything else
                    logger.error(f'Unhanled exception when downloading log: {repr(e)}')
                    continue

                # check if analysis json is actually valid
                if not all(i in log_analysis for i in ('critical', 'warning', 'info')):
                    logger.error(f'Analyser result for "{raw_url}" is invalid.')
                    continue

                anal_url = f'https://obsproject.com/tools/analyzer?log_url={urlencode(html_url)}'
                embed = Embed(colour=Colour(0x5a7474), url=anal_url)

                def pretty_print_messages(msgs):
                    ret = []
                    for _msg in msgs:
                        ret.append(f'- {_msg}')
                    return '\n'.join(ret)

                if log_analysis['critical']:
                    embed.add_field(name="🛑 Critical",
                                    value=pretty_print_messages(log_analysis['critical']))
                if log_analysis['warning']:
                    embed.add_field(name="⚠️ Warning",
                                    value=pretty_print_messages(log_analysis['warning']))
                if log_analysis['info']:
                    embed.add_field(name="ℹ️ Info",
                                    value=pretty_print_messages(log_analysis['info']))

                # do local hardware check/stats collection and include results if enabled
                hw_results = await self.match_hardware(log_content)
                if self.bot.state.get('hw_check_enabled', False):
                    if hardware_check_msg := self.hardware_check(hw_results):
                        embed.add_field(name='Hardware Check', inline=False,
                                        value=' / '.join(hardware_check_msg))

                embed.add_field(name='Analyzer Report', inline=False,
                                value=f'[**Click here for solutions / full analysis**]({anal_url})')

                # include filtered log in case SE or FTL spam is detected
                if 'obsproject.com' in raw_url and any(elem in log_content for
                                                       elem in self._filtered_log_needles):
                    clean_url = raw_url.replace('obsproject.com', 'obsbot.rodney.io')
                    embed.description = f'*Log contains debug messages (browser/ftl/etc), ' \
                                        f'for a filtered version [click here]({clean_url})*\n'

                return await msg.channel.send(embed=embed, reference=msg, mention_author=True)

    async def fetch_log_analysis(self, url):
        async with self.bot.session.get('https://obsproject.com/analyzer-api/',
                                        params=dict(url=url, format='json')) as r:
            if r.status == 200:
                return await r.json()
            else:
                r.raise_for_status()

    async def download_log(self, url):
        async with self.bot.session.get(url) as r:
            if r.status == 200:
                try:
                    log = await r.text()
                except UnicodeDecodeError:
                    logger.warning('Decoding log failed, trying with ISO-8859-1 encoding forced...')
                    log = await r.text(encoding='ISO-8859-1')

                if 'Stack' in log and 'EIP' in log or 'Anonymous UUID' in log or 'Fault address:' in log:
                    raise ValueError('Log is crash log')

                if 'log file uploaded at' not in log:  # uploaded within OBS
                    if 'Startup complete' not in log:  # not uploaded within OBS but still a log
                        raise ValueError('Not a (valid) OBS log')

                return log
            else:
                # Raise if status >= 400
                r.raise_for_status()

    def hardware_check(self, hw_results):
        hw_heck_msg = []

        if hw_results['cpu_bench']:
            try:
                cpu_score = int(hw_results['cpu_bench']['cpu_mark'])

                rating = '***Below minimum requirements***'
                if random.randint(0, 100) == 99:
                    rating = self._potato

                if cpu_score > 3250:
                    rating = '**Below cpu encoding requirements**'
                if cpu_score > 4500:
                    rating = '*Possible bottleneck*'
                if cpu_score > 7500:
                    rating = 'OK!'

                hw_heck_msg.append(f'{hw_results["cpu_bench"]["name"]} - {rating}')
            except Exception as e:
                logger.error('Parsing CPU benchmark score failed:', repr(e))
        elif hw_results['cpu_name']:
            hw_heck_msg.append(f'{hw_results["cpu_name"]} (not in benchmark DB)')

        if hw_results['gpu_bench']:
            try:
                gpu_score = int(hw_results['gpu_bench']['gpu_3d_mark'])

                rating = '***Below minimum requirements***'
                if random.randint(0, 100) == 99:
                    rating = self._potato

                if gpu_score > 400:
                    rating = '*Possible bottleneck*'
                if gpu_score > 3000:
                    rating = 'OK!'

                hw_heck_msg.append(f'{hw_results["gpu_bench"]["name"]} - {rating}')
            except Exception as e:
                logger.error('Parsing CPU benchmark score failed:', repr(e))
        elif hw_results['gpu_name']:
            hw_heck_msg.append(f'{hw_results["gpu_name"]} (not in benchmark DB)')

        return hw_heck_msg

    async def match_hardware(self, log_content):
        res = dict(cpu_name='', cpu_bench=None,
                   gpu_name='', gpu_bench=None)

        # most of this is old an ugly and probably needs a rewrite.
        # check if video initialization even happens in log
        if 'Loading up D3D11' in log_content or 'Loading up OpenGL' in log_content:
            for line in log_content.splitlines():
                if 'CPU Name:' in line:
                    cpu = line.rpartition('CPU Name: ')[2].strip()
                    res['cpu_name'] = cpu

                    # find CPU in DB, first remove certain nonsense that makes matching harder
                    cpu_parts = cpu.lower().replace('(tm)', '').replace('(r)', '')\
                        .replace('-', ' ').replace('@', ' ').split()
                    # iterate over benchmark data and find closest match
                    best_match = (0, None)
                    for cpu_bench in self.benchmark_data['cpus']:
                        bench_parts = [p for p in cpu_bench['name_lower'].split()
                                       if p not in ('-', '(', ')')]
                        s = sum(i in bench_parts for i in cpu_parts)
                        if s > best_match[0]:
                            logger.debug(f'[CPU] New best match (score: {s}): {cpu} => {cpu_bench["name"]}')
                            best_match = (s, cpu_bench)

                    if best_match[1] is None:
                        logger.warning('Could not find CPU in CPU DB (update required?):', cpu)
                    else:
                        # Filter out false positives by having a minimum threshold.
                        # Experimentation shows that different values for Intel/AMD work best
                        min_match = 3
                        if 'Intel' in best_match[1]['name'] and len(cpu_parts) >= 5:
                            if not any(sku in cpu for sku in ('Atom', 'Celeron', 'Xeon', 'Pentium')):
                                min_match = 5

                        if best_match[0] < min_match:
                            logger.warning('Could not find acceptable match (update required?):', cpu)
                        else:
                            res['cpu_bench'] = best_match[1]

                        # only save CPU stats when we're using DX11 on Windows
                        if 'Loading up D3D11' in log_content and res['cpu_bench']:
                            self.bot.loop.create_task(self.update_hardware_stats(cpu_bench=res['cpu_bench']))

                if 'Loading up D3D11' in line or 'Loading up OpenGL' in line:
                    if 'NSMACHOperatingSystem' in log_content:  # macOS
                        gpu = line.partition('adapter')[2].strip()
                    else:
                        gpu = line.partition('adapter')[2].rsplit('(', 1)[0].strip()

                    res['gpu_name'] = gpu
                    # Find GPU in DB, first remove certain nonsense that makes matching harder
                    gpu_parts = gpu.lower().replace('(tm)', '').replace('(r)', '') \
                        .replace('/', ' ').split()
                    # iterate over benchmark data and find closest match
                    best_match = (0, None)
                    for gpu_bench in self.benchmark_data['gpus']:
                        bench_parts = [p for p in gpu_bench['name_lower'].split()
                                       if p not in ('-', '(', ')')]
                        s = sum(i in bench_parts for i in gpu_parts)
                        if s > best_match[0]:
                            logger.debug(f'[GPU] New best match (score: {s}): {gpu} => {gpu_bench["name"]}')
                            best_match = (s, gpu_bench)

                    if best_match[1] is None:
                        logger.warning('Could not find GPU in GPU DB (update required?):', gpu)
                    else:
                        # vendor match quality is about the same, but some GPU names are too short
                        min_match = 2 if len(gpu_parts) <= 4 else 3
                        if best_match[0] < min_match:
                            logger.warning('Could not find acceptable match (update required?):', gpu)
                        else:
                            res['gpu_bench'] = best_match[1]

                        # only save GPU info when we're running DX11
                        if 'D3D11' in line and res['gpu_bench']:
                            self.bot.loop.create_task(self.update_hardware_stats(gpu_bench=res['gpu_bench']))

        return res

    async def update_hardware_stats(self, gpu_bench=None, cpu_bench=None):
        increment = []
        insert = []

        if gpu_bench:
            _id = gpu_bench['id']
            if _id not in self.hardware_stats['gpu']:
                self.hardware_stats['gpu'][_id] = dict(count=1, name=gpu_bench['name'])
                insert.append((_id, None, gpu_bench['name'], 1))
            else:
                self.hardware_stats['gpu'][_id]['count'] += 1
                increment.append((_id, None))

        if cpu_bench:
            _id = cpu_bench['id']
            if _id not in self.hardware_stats['cpu']:
                self.hardware_stats['cpu'][_id] = dict(count=1, name=cpu_bench['name'])
                insert.append((None, _id, cpu_bench['name'], 1))
            else:
                self.hardware_stats['cpu'][_id]['count'] += 1
                increment.append((None, _id))

        if insert:
            await self.bot.db.exec_multi(_insert_query.format(self.config['db_table']), insert)
        if increment:
            await self.bot.db.exec_multi(_increment_query.format(self.config['db_table']), increment)

    async def fetch_hardware_stats(self):
        """Get hardware stats from DB"""
        res = await self.bot.db.query(f'''SELECT * FROM {self.config["db_table"]}''')
        if not res:
            logger.warning('No hardware stats received from DB!')
            return

        logger.info(f'Received {len(res)} hardware stats entries from DB.')
        for record in res:
            if record['gpu_id']:
                self.hardware_stats['gpu'][record['gpu_id']] = dict(name=record['name'],
                                                                    count=record['counts'])
            elif record['cpu_id']:
                self.hardware_stats['cpu'][record['cpu_id']] = dict(name=record['name'],
                                                                    count=record['counts'])

    @command()
    async def togglehwcheck(self, ctx: Context):
        if not self.bot.is_admin(ctx.author):
            return
        self.bot.state['hw_check_enabled'] = not self.bot.state['hw_check_enabled']
        _state = 'enabled' if self.bot.state['hw_check_enabled'] else 'disabled'
        return await ctx.send(f'Analysis hardware check is now {_state}')

    @command()
    async def tophardware(self, ctx: Context):
        embed = Embed(title='Top Hardware')

        cpus = []
        for pos, cpu in enumerate(sorted(self.hardware_stats['cpu'].values(),
                                         key=lambda a: a['count'], reverse=True)[:10], start=1):
            cpus.append(f'{pos:2d}. - {cpu["name"]} ({cpu["count"]})')
        embed.add_field(name='CPUs', value='```{}```'.format('\n'.join(cpus)), inline=False)

        gpus = []
        for pos, gpu in enumerate(sorted(self.hardware_stats['gpu'].values(),
                                         key=lambda a: a['count'], reverse=True)[:10], start=1):
            gpus.append(f'{pos:2d}. - {gpu["name"]} ({gpu["count"]})')
        embed.add_field(name='GPUs', value='```{}```'.format('\n'.join(gpus)), inline=False)

        return await ctx.send(embed=embed)


def setup(bot):
    if 'log_analyser' in bot.config and bot.config['log_analyser'].get('enabled', False):
        la = LogAnalyser(bot, bot.config['log_analyser'])
        bot.add_cog(la)
        bot.loop.create_task(la.fetch_hardware_stats())
    else:
        logger.info('Log analysis cog not enabled.')
