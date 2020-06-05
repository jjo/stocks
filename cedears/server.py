#!/usr/bin/env python3
import asyncio
import sys
import logging
import random
from aiocache import cached, Cache
from aiocache.serializers import PickleSerializer

sys.argv = [sys.argv[0], '--cache=memcache']
import cedears

from quart import Quart, render_template

logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger=logging.getLogger()

app = Quart(__name__)

async def get_df():
    return await cedears.get_main_df(cedears.parseargs())

@app.route("/")
@cached(ttl=10,
        **cedears.CACHE,
        serializer=PickleSerializer(),
        namespace="site-root")
async def root():
    df = await get_df()
    body = await render_template('root.html', table = df.to_html())
    return body

async def refresh(kv):
    min = kv["min"]
    max = kv["max"]
    while True:
        logger.info("Background refresh: starting ...")
        await get_df()
        period = int(min + (max - min) * random.random())
        logger.info("Background refresh: sleeping for {} secs.".format(period))
        await asyncio.sleep(period)

CRONTAB = [
    [refresh, {"min": 60, "max": 90}]
]

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    for x in CRONTAB:
        func = x[0]
        kv = x[1]
        asyncio.ensure_future(func(kv))
    app.run(debug=False, loop=loop)
