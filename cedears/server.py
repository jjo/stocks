#!/usr/bin/env python3
'Quart server-ization of cedears.py'
import asyncio
import sys
import logging
import random
from aiocache import cached
from aiocache.serializers import PickleSerializer
from quart import Quart, render_template

sys.argv = [sys.argv[0], '--cache=memcache']
import cedears


logging.basicConfig(stream=sys.stderr, level=logging.INFO)
LOGGER = logging.getLogger()

APP = Quart(__name__)

async def get_df():
    'Wrapper on cedears module'
    return await cedears.get_main_df(cedears.parseargs())

@APP.route("/")
@cached(ttl=10,
        **cedears.CACHE,
        serializer=PickleSerializer(),
        namespace="site-root")
async def root():
    'Main / endpoint'
    dframe = await get_df()
    body = await render_template('root.html', table=dframe.to_html())
    return body

async def refresh(params):
    'Background refreshing "task"'
    r_min = params["min"]
    r_max = params["max"]
    while True:
        LOGGER.info("Background refresh: starting ...")
        await get_df()
        period = int(r_min + (r_max - r_min) * random.random())
        LOGGER.info("Background refresh: sleeping for %d secs.", period)
        await asyncio.sleep(period)

CRONTAB = [
    [refresh, {"min": 60, "max": 90}]
]

if __name__ == "__main__":
    LOOP = asyncio.get_event_loop()
    for x in CRONTAB:
        func = x[0]
        r_params = x[1]
        asyncio.ensure_future(func(r_params))
    APP.run(debug=False, loop=LOOP)
