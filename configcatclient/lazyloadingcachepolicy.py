import datetime
from requests import HTTPError
import traceback

from .readwritelock import ReadWriteLock
from .interfaces import CachePolicy, ConfigCatLogger


class LazyLoadingCachePolicy(CachePolicy):

    def __init__(self, config_fetcher, config_cache, logger: ConfigCatLogger, cache_time_to_live_seconds=60):
        if cache_time_to_live_seconds < 1:
            cache_time_to_live_seconds = 1
        self._logger = logger
        self._config_fetcher = config_fetcher
        self._config_cache = config_cache
        self._cache_time_to_live = datetime.timedelta(seconds=cache_time_to_live_seconds)
        self._lock = ReadWriteLock()
        self._last_updated = None

    def get(self):
        try:
            self._lock.acquire_read()

            utc_now = datetime.datetime.utcnow()
            if self._last_updated is not None and self._last_updated + self._cache_time_to_live > utc_now:
                config = self._config_cache.get()
                if config is not None:
                    return config
        finally:
            self._lock.release_read()

        self.force_refresh()

        try:
            self._lock.acquire_read()
            config = self._config_cache.get()
            return config
        finally:
            self._lock.release_read()

    def force_refresh(self):
        try:
            configuration = self._config_fetcher.get_configuration_json()

            try:
                self._lock.acquire_write()
                self._config_cache.set(configuration)
                self._last_updated = datetime.datetime.utcnow()
            finally:
                self._lock.release_write()

        except HTTPError as e:
            self._logger.error('Double-check your API KEY at https://app.configcat.com/apikey.'
                               ' Received unexpected response: %s' % str(e.response))
        except:
            self._logger.error('Exception in LazyLoadingCachePolicy.force_refresh: %s' % traceback.format_exc())

    def stop(self):
        pass
