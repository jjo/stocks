import asyncio
import sys
sys.argv = [sys.argv[0], '--cache=memcache']
import cedears
from quart import Quart

async def abar(a):
    return(a)

app = Quart(__name__)

async def get_df():
    return await cedears.get_main_df(cedears.parseargs())

@app.route("/")
async def root():
    df = await get_df()
    return df.to_html()

async def refresh():
    while True:
        await get_df()
        await asyncio.sleep(60)

CRONTAB = [
    refresh
]

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    for x in CRONTAB:
        asyncio.ensure_future(x())
    app.run(debug=False, loop=loop)
