import time


class RateLimiter:
    """Custom rate limiter for factoids/log analysis"""
    def __init__(self, cooldown=5.0):
        self.cache = dict()
        self.cooldown = cooldown

    def _cleanup(self, now):
        """Remove keys that are expired (older than t + cooldown)"""
        expired_keys = [k for k, t in self.cache.items() if (now - t) >= self.cooldown]
        for key in expired_keys:
            del self.cache[key]

    def is_limited(self, *key):
        now = time.time()
        # remove expired keys
        self._cleanup(now)

        if key not in self.cache:
            self.cache[key] = now
            return False

        return True
