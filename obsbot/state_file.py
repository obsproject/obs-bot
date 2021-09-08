import os
import json
import logging

from collections.abc import MutableMapping

logger = logging.getLogger(__name__)


class StateFile(MutableMapping):
    """
    Simple class to manage the state file, with automatic saving on changes.
    """

    def __init__(self, filename):
        self._filename = filename
        self.store = dict()

        if os.path.exists(self._filename):
            self.update(json.load(open(self._filename)))
        else:
            logger.info('No state file found, starting new one!')

    def __getitem__(self, key):
        return self.store[key]

    def __setitem__(self, key, value):
        self.store[key] = value
        json.dump(self.store, open(self._filename, 'w'), indent=2, sort_keys=True)

    def __delitem__(self, key):
        del self.store[key]
        json.dump(self.store, open(self._filename, 'w'), indent=2, sort_keys=True)

    def __iter__(self):
        return iter(self.store)

    def __len__(self):
        return len(self.store)
