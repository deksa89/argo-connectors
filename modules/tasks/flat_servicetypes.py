import os
import asyncio
import time

from urllib.parse import urlparse

from argo_connectors.io.http import SessionWithRetry
from argo_connectors.parse.flat_servicetypes import ParseFlatServiceTypes
from argo_connectors.parse.webapi_servicetypes import ParseWebApiServiceTypes
from argo_connectors.io.webapi import WebAPI
from argo_connectors.tasks.common import write_state, write_downtimes_json as write_json
from argo_connectors.exceptions import ConnectorHttpError, ConnectorParseError, ConnectorError


def contains_exception(list):
    for a in list:
        if isinstance(a, Exception):
            return (True, a)

    return (False, None)


class TaskFlatServiceTypes(object):
    def __init__(self, loop, logger, connector_name, globopts, auth_opts,
                 webapi_opts, confcust, custname, feed, timestamp, performance,
                 is_csv=False, initsync=False):
        self.logger = logger
        self.loop = loop
        self.connector_name = connector_name
        self.auth_opts = auth_opts
        self.globopts = globopts
        self.webapi_opts = webapi_opts
        self.confcust = confcust
        self.custname = custname
        self.feed = feed
        self.timestamp = timestamp
        self.performance = performance
        self.is_csv = is_csv
        self.initsync = initsync

    async def fetch_data(self):
        start_time = time.time()
        feed_parts = urlparse(self.feed)
        session = SessionWithRetry(self.logger,
                                   os.path.basename(self.connector_name),
                                   self.globopts, custauth=self.auth_opts)
        res = await session.http_get('{}://{}{}?{}'.format(feed_parts.scheme,
                                                           feed_parts.netloc,
                                                           feed_parts.path,
                                                           feed_parts.query))
        elapsed_time = time.time() - start_time
        if self.performance > 0:
            self.logger.info(f'fetch_data completed in {elapsed_time} seconds.')

        return res

    async def fetch_webapi(self):
        start_time = time.time()
        webapi = WebAPI(self.connector_name, self.webapi_opts['webapihost'],
                        self.webapi_opts['webapitoken'], self.logger,
                        int(self.globopts['ConnectionRetry'.lower()]),
                        int(self.globopts['ConnectionTimeout'.lower()]),
                        int(self.globopts['ConnectionSleepRetry'.lower()]),
                        self.globopts['ConnectionRetryRandom'.lower()],
                        int(self.globopts['ConnectionSleepRandomRetryMax'.lower()]),
                        date=self.timestamp)

        elapsed_time = time.time() - start_time
        if self.performance > 0:
            self.logger.info(f'fetch_webapi completed in {elapsed_time} seconds.')
        
        return await webapi.get('service-types', jsonret=False)

    async def send_webapi(self, data):
        start_time = time.time()
        webapi = WebAPI(self.connector_name, self.webapi_opts['webapihost'],
                        self.webapi_opts['webapitoken'], self.logger,
                        int(self.globopts['ConnectionRetry'.lower()]),
                        int(self.globopts['ConnectionTimeout'.lower()]),
                        int(self.globopts['ConnectionSleepRetry'.lower()]),
                        self.globopts['ConnectionRetryRandom'.lower()],
                        int(self.globopts['ConnectionSleepRandomRetryMax'.lower()]),
                        date=self.timestamp)
        await webapi.send(data, 'service-types')
        elapsed_time = time.time() - start_time
        if self.performance > 0:
            self.logger.info(f'send_webapi completed in {elapsed_time} seconds.')

    def parse_webapi_poem(self, res):
        webapi = ParseWebApiServiceTypes(self.logger, res)
        return webapi.get_data(tag='poem')

    def parse_source(self, res):
        flat_servtypes = ParseFlatServiceTypes(self.logger, res, self.is_csv)
        return flat_servtypes.get_data()

    async def run(self):
        try:
            start_time = time.time()
            
            coros = [self.fetch_data()]

            if not self.initsync:
                coros.append(self.fetch_webapi())

            fetched_data = await asyncio.gather(*coros, loop=self.loop, return_exceptions=True)

            exc_raised, exc = contains_exception(fetched_data)
            if exc_raised:
                raise ConnectorError(repr(exc))

            if not self.initsync:
                res, res_webapi = fetched_data
            else:
                res = fetched_data[0]

            # small set data, parsing sequentially
            service_types = self.parse_source(res)
            if not self.initsync:
                service_types_poem = self.parse_webapi_poem(res_webapi)
                service_types = service_types + service_types_poem
                service_types = sorted(service_types,  key=lambda s: s['name'].lower())

            await write_state(self.connector_name, self.globopts, self.confcust, self.timestamp, True)

            if eval(self.globopts['GeneralPublishWebAPI'.lower()]):
                await self.send_webapi(service_types)

            elapsed_time = time.time() - start_time
            if self.performance > 0:
                self.logger.info(f'run completed in {elapsed_time} seconds.')  
            self.logger.info('Customer:' + self.custname + ' Fetched Flat ServiceTypes:%d' % (len(service_types)))
            
        

        except (ConnectorError, ConnectorHttpError, ConnectorParseError, KeyboardInterrupt) as exc:
            self.logger.error(repr(exc))
            await write_state(self.connector_name, self.globopts, self.confcust, self.timestamp, False)
