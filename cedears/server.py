#!/usr/bin/env python3
'Quart server-ization of cedears.py'
import asyncio
import sys
import logging
import random
import json
from aiocache import cached
from aiocache.serializers import PickleSerializer
from quart import Quart, render_template, jsonify, send_from_directory

sys.argv = [sys.argv[0], '--cache=memcache', '--vol-q=0.25']
import cedears

logging.basicConfig(stream=sys.stderr, level=logging.INFO)
LOGGER = logging.getLogger()

APP = Quart(__name__)
APP.config["RESPONSE_TIMEOUT"] = 1000


async def get_df():
    'Wrapper on cedears module'
    return await cedears.get_main_df(cedears.parseargs())

@APP.route('/static/<path:path>')
async def send_static(path):
    'serve static content'
    return await send_from_directory('static', path)

@APP.route("/table")
@cached(ttl=30,
        **cedears.CACHE,
        serializer=PickleSerializer(),
        namespace="site-table")
async def table():
    '/table json endpoint tailored for JS datatables expected format'
    dframe = await get_df()
    ret_json = json.loads(dframe.to_json(orient="split"))
    ret_data = []
    for i, index_name in enumerate(ret_json["index"]):
        ret_data.append([index_name] + ret_json["data"][i])
    return jsonify(
        data=ret_data,
        columns=[{
            "title": str(col)
        } for col in json.loads(dframe.to_json(orient="split"))["columns"]])


@APP.route("/")
@cached(ttl=30,
        **cedears.CACHE,
        serializer=PickleSerializer(),
        namespace="site-root")
async def root():
    'Main / endpoint'
    dframe = await get_df()
    order_idx = dframe.columns.get_loc("CCL_val")
    body = await render_template('root.html',
                                 table=dframe.to_html(
                                     table_id="dataframe",
                                     classes="display responsive nowrap"),
                                 order_idx=order_idx)
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


CRONTAB = [[refresh, {"min": 60, "max": 90}]]

if __name__ == "__main__":
    LOOP = asyncio.get_event_loop()
    for x in CRONTAB:
        func = x[0]
        r_params = x[1]
        asyncio.ensure_future(func(r_params))
    APP.run(host="0.0.0.0", debug=False, loop=LOOP)
