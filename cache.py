import time

class DataCache:
    def __init__(self):
        self._store = {}
        self._times = {}

    def set(self, key, value, ttl_hours=6):
        self._store[key] = (value, time.time() + ttl_hours * 3600)
        self._times[key] = time.strftime("%Y-%m-%d %H:%M")

    def get(self, key):
        if key not in self._store: return None
        value, expire = self._store[key]
        if time.time() > expire:
            del self._store[key]
            return None
        return value

    def get_time(self, key):
        return self._times.get(key, "")