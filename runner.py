import argparse
import contextlib
import logging
import os

from logging.handlers import TimedRotatingFileHandler

from obsbot import __version__, __codename__
from obsbot.main import OBSBot

_log_dt_fmt = '%Y-%m-%d %H:%M:%S'
_log_fmt = '[{asctime}] [{levelname}] {name}: {message}'
logging.basicConfig(format=_log_fmt, datefmt=_log_dt_fmt, style='{', level=logging.INFO)
logger = logging.getLogger('runner')


@contextlib.contextmanager
def setup_logging(logfile=None, debug=False):
    try:
        logging.getLogger('discord').setLevel(logging.WARNING)
        logging.getLogger('discord.http').setLevel(logging.WARNING)

        log = logging.getLogger()
        log.setLevel(logging.INFO if not debug else logging.DEBUG)
        if logfile:
            handler = TimedRotatingFileHandler(
                filename=logfile, when='midnight', utc=True, encoding='utf-8', backupCount=5
            )
            fmt = logging.Formatter(_log_fmt, _log_dt_fmt, style='{')
            handler.setFormatter(fmt)
            log.addHandler(handler)
        else:
            logger.warning('Logging to file is disabled')

        yield
    finally:
        handlers = log.handlers[:]
        for handler in handlers:
            handler.close()
            log.removeHandler(handler)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-v', '--debug', dest='debug', action='store_true', help='Set loglevel to debug')
    parser.add_argument('-l', '--logfile', dest='logfile', action='store', help='Logfile (optional)')
    parser.add_argument('-V', '--version', dest='version', action='store_true', help='Print version and exit')
    parser.add_argument(
        '-c', '--config-file', dest='config_file', action='store', help='Configuration file to run bot with'
    )
    args = parser.parse_args()

    if args.version:
        print(f'OBS Bot version: {__version__} - "{__codename__}"')
        exit(0)

    if not args.config_file:
        print('No config file specified!')
        exit(1)

    with setup_logging(logfile=args.logfile, debug=args.debug):
        bot = OBSBot(os.path.realpath(args.config_file))
        bot.run()
