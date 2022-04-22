import asyncio
import logging
from typing import Union, List

import asyncpg

logger = logging.getLogger(__name__)


class DBHelper:
    """
    Helper class that abstracts DB operations and provides a simpler interface to create tasks
    that can run asynchronously in case the result of the operation is not important.
    """

    def __init__(self):
        self.conn = None

    async def connect(self, config):
        logger.info(f'Connecting to database {config["host"]}:{config["port"]} as "{config["user"]}"...')
        self.conn = await asyncpg.create_pool(
            host=config['host'], port=config['port'], user=config['user'], password=config['pass'], command_timeout=60
        )

    async def query(self, query, *args, **kwargs) -> Union[List[asyncpg.Record], None]:
        """Execute query and return results"""
        logger.debug(f'Fetching from DB with query "{query}" and args {args}, {kwargs}')
        return await self.conn.fetch(query, *args, **kwargs)

    async def exec(self, command, *args, **kwargs) -> Union[List[asyncpg.Record], None]:
        logger.debug(f'Sending DB execute "{command}" with args {args}, {kwargs}')
        return await self.conn.execute(command, *args, **kwargs)

    async def exec_multi(self, command, arglist, **kwargs) -> Union[List[asyncpg.Record], None]:
        logger.debug(f'Sending DB multi-execute "{command}" with {len(arglist)} inputs')
        return await self.conn.executemany(command, arglist, **kwargs)

    async def add_task(self, query, *args, **kwargs) -> asyncio.Task:
        """Create task that will execute async, can be optionally awaited by caller"""
        return asyncio.create_task(self.exec(query, *args, **kwargs))

    async def add_muli_task(self, query, arglist, **kwargs) -> asyncio.Task:
        """Create task that will execute async, can be optionally awaited by caller"""
        return asyncio.create_task(self.exec_multi(query, arglist, **kwargs))
