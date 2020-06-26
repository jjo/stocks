#!/usr/bin/env python3
"""
jjo's foo cedears
"""
#
# Copyright Juanjo Ciarlante <http://twitter.com/xjjo>
#
# License: MIT
#
import argparse
import sys
import re
import os
import asyncio
import logging
import json
import jsonpath_rw as jp
import urllib3

import pandas as pd
import httpx
import httpcore
from aiocache import cached, Cache
from aiocache.serializers import PickleSerializer

logging.basicConfig(stream=sys.stderr, level=logging.INFO)
LOGGER = logging.getLogger()

CONCURRENCY = 20
CEDEARS_RATIOS_URL = r'https://www.comafi.com.ar/2254-CEADEAR-SHARES.note.aspx'
CEDEARS_LIVE_URL = r'https://www.byma.com.ar/wp-admin/admin-ajax.php'
CEDEARS_LIVE_PARAMS = {'action': 'get_panel', 'panel_id': '5'}
USER_AGENT = (
    'Mozilla/5.0 (iPhone; U; CPU iPhone OS 4_3_3 like Mac OS X; en-us) '
    'AppleWebKit/533.17.9 (KHTML, like Gecko)'
    'Version/5.0.2 Mobile/8J2 Safari/6533.18.5')
ZACKS_URL = r'https://www.zacks.com/stock/quote/{}'
YAHOOFIN_URL = r'https://query1.finance.yahoo.com/v10/finance/quoteSummary/{}'
YAHOOFIN_PARAMS = {'modules': 'financialData'}

SEM = asyncio.Semaphore(CONCURRENCY)
VOLUME_QUANTILE = 0.75

ARGS = None
CACHE = {"cache": Cache.MEMORY}


def parseargs():
    'std parseargs'
    parser = argparse.ArgumentParser(description="CEDEARS CCL tool by jjo")
    parser.add_argument('--vol-q',
                        type=float,
                        default=VOLUME_QUANTILE,
                        help="min vol quantile, default: %s" % VOLUME_QUANTILE)
    parser.add_argument('--no-filter',
                        action="store_true",
                        help="Get them all(!)")
    parser.add_argument('--tickers',
                        default="",
                        help="comma delimited list of stocks to include")
    parser.add_argument('--cache',
                        default="memory",
                        help="cache to use, eg --cache=memcache")
    return parser.parse_args()


ARGS = parseargs()
if ARGS.cache == "memcache":
    LOGGER.info("Using cache=%s", ARGS.cache)

    MC_ENDPOINT = os.getenv('MEMCACHE_ENDPOINT', '127.0.0.1:11211')
    MC_HOST, MC_PORT = tuple(MC_ENDPOINT.split(':'))
    MC_PORT = int(MC_PORT)

    CACHE = {
        "cache": Cache.MEMCACHED,
        "endpoint": MC_HOST,
        "port": MC_PORT,
        "pool_size": 10,
    }


@cached(ttl=60, **CACHE, serializer=PickleSerializer(), namespace="url")
async def url_get(url, **kwargs):
    'client.get wrapper with force USER_AGENT'
    async with SEM:
        async with httpx.AsyncClient(verify=False) as client:
            reply = await client.get(url,
                                     headers={'User-Agent': USER_AGENT},
                                     **kwargs)
            LOGGER.info("url=%s", reply.url)
            return reply.text


@cached(ttl=3600, **CACHE, serializer=PickleSerializer(), namespace="ratios")
async def get_ratios():
    'get CEDEARS ratios'

    def _ratio(colon_sep):
        ratio_args = colon_sep.split(':')
        return float(ratio_args[0]) / float(ratio_args[1])

    LOGGER.info("CEDEARS ratios: fetching from %s", CEDEARS_RATIOS_URL)
    # Returns list of all tables on page
    resp = await url_get(CEDEARS_RATIOS_URL, timeout=60)
    tables = pd.read_html(resp)

    # Single table in page
    table = tables[0]
    # Translate field names ES -> EN
    table.columns = [col.split(" ")[0] for col in table.columns]
    table.rename(columns={
        'Simbolo': 'Ticker',
        'Trading': 'US_Ticker',
    },
                 inplace=True)
    # Transform X:Y string ratio to X/Y float
    table['Ratio'] = table['Ratio'].apply(_ratio)
    table = table.set_index('Ticker', drop=False)
    LOGGER.info("CEDEARS ratios: got %s entries", len(table))
    return table


@cached(ttl=60,
        **CACHE,
        serializer=PickleSerializer(),
        key="byma",
        namespace="byma")
async def get_byma(ratios):
    'Get BYMA live quotes'

    LOGGER.info("CEDEARS quotes: fetching from %s", CEDEARS_LIVE_URL)
    # WTF CEDEARS_LIVE_URL doesn't have a proper TLS cert(?)
    resp = await url_get(CEDEARS_LIVE_URL, timeout=30,
                         params=CEDEARS_LIVE_PARAMS)
    # Parse JSON into DF
    dframe = pd.DataFrame(columns=[
        'Ticker', 'AR_val', 'Ratio', 'AR_Vol', 'AR_Buy', 'AR_Sel',
        'AR_chg', 'US_Ticker'
    ])
    for quote in json.loads(resp)["Cotizaciones"]:
        # - skip tickers w/o value
        # - only delayed quotes, for better volume
        # - skip tickers w/no Volume
        # - only ARS tickers
        if (quote['Ultimo'] == 0
                or quote['Vencimiento'] not in ("48hs", "24hs")
                or quote['Tipo_Liquidacion'] != "Pesos"):
            continue
        ticker = quote['Simbolo']
        ars_value = quote['Ultimo']
        period = quote['Vencimiento']
        volume = quote['Volumen_Nominal']
        ars_buy = quote['Cantidad_Nominal_Compra']
        ars_sell = quote['Cantidad_Nominal_Venta']
        ars_delta = quote['Variacion']
        #LOGGER.info('ticker={} ars_value={}'.format(ticker, ars_value))
        try:
            ratio = ratios.loc[ticker, 'Ratio']
        except KeyError:
            continue
        us_ticker = ratios.loc[ticker, 'US_Ticker']
        dframe = dframe.append(
            {
                'Ticker': ticker,
                'US_Ticker': us_ticker,
                'AR_val': ars_value,
                'Ratio': round(ratio, 2),
                'AR_Vol': volume,
                'AR_Buy': ars_buy,
                'AR_Sel': ars_sell,
                'AR_hrs': period,
                'AR_chg': ars_delta,
            },
            ignore_index=True)
    # Index the DF by ticker
    dframe = dframe.set_index("Ticker")
    LOGGER.info("CEDEARS quotes: got %d entries", len(dframe))
    if len(dframe) == 0:
        LOGGER.warning("CEDEARS quotes: ZERO -- Market not yet open ?")
    return dframe


@cached(ttl=1800, **CACHE, serializer=PickleSerializer(), namespace="zrank")
async def get_zacks_rank(stock):
    'get Zacks rank from ZACKS_URL and parse dirty HTML'

    url = ZACKS_URL.format(stock)
    rank = "N/A"
    try:
        resp = await url_get(url, timeout=30)
        rank_match = re.search(
            r'\n\s*([^<]+).+rank_chip.rankrect_1.*rank_chip.rankrect_2', resp)
    except (httpcore._exceptions.ProtocolError,
            httpcore._exceptions.ReadTimeout,
            asyncio.exceptions.TimeoutError):
        return rank
    try:
        rank = rank_match.groups(1)[0]
    except AttributeError:
        return rank

    # Save found rank into cache
    rank = "{:8}".format(rank.replace("Strong ", "S"))
    LOGGER.debug("stock={:8} rank={:8}".format(stock, rank))
    return rank


@cached(ttl=60, **CACHE, serializer=PickleSerializer(), namespace="usd_value")
async def get_usd_value(stock):
    'Get live quote from YAHOO'
    url = YAHOOFIN_URL.format(stock)
    resp = await url_get(url, timeout=30, params=YAHOOFIN_PARAMS)
    # Use jsonpath to traverse it down to the data we want
    jp_exp = jp.parse('$.quoteSummary.result..financialData.currentPrice.raw')
    try:
        price = jp_exp.find(json.loads(resp))[0].value
    except IndexError:
        return
    if resp == "":
        return
    # Save found price into cache
    LOGGER.debug("stock={:8} price={:0.2f}".format(stock, price))
    return price


# Just a convenient function that's called couple times below


def get_ccl_val(price_ars, price, ratio):
    'just a math wrapper for the CCL calculation'
    return price_ars / price * ratio


def df_loc1(dframe, index, col):
    'f*cking panda behavior single value or Series depending 1 or N'
    return dframe.loc[dframe.index == index, col].head(1).iloc[0]


async def warmcache(dframe):
    'Pre-warm cache'
    # Stocks list is DF index
    stocks = set(dframe.index.values.tolist())
    # Async stanza, to concurrently fetch stocks' price and zacks rank
    futures = []
    # Warm caches
    for stock in stocks:
        # We may have several entries for (AR)stock, just choose one:
        us_stock = df_loc1(dframe, stock, 'US_Ticker')
        assert isinstance(us_stock,
                          str), ("stock={} returned type(us_stock)={}".format(
                              stock, type(us_stock)))
        futures.append(get_usd_value(us_stock))
        futures.append(get_zacks_rank(us_stock))

    # Called functions will be cached, discard values
    for _ in await asyncio.gather(*futures):
        pass
    return


async def fetch(dframe):
    'Stocks list is DF index'
    stocks = set(dframe.index.values.tolist())
    await warmcache(dframe)

    # Add new columns to dataframe with obtained CCL value (ARS/USD ratio
    # for the ticker), and Zacks rank
    for stock in stocks:
        us_stock = df_loc1(dframe, stock, 'US_Ticker')
        price = await get_usd_value(us_stock)
        rank = await get_zacks_rank(us_stock)
        if (price is None or price == 0.0 or rank is None):
            dframe.drop(stock, inplace=True)
            continue

        price_ars = df_loc1(dframe, stock, 'AR_val')
        ratio = df_loc1(dframe, stock, 'Ratio')
        ccl_val = get_ccl_val(price_ars, price, ratio)
        # Add (column and) cell with computed values
        dframe.loc[stock, 'ZRank'] = rank
        dframe.loc[stock, 'CCL_val'] = round(ccl_val, 2)
        dframe.loc[stock, 'AR_tot'] = int(price_ars * ratio)
        dframe.loc[stock, 'USD_val'] = price

    # Use quantile 0.5 as ref value
    ccl_ref = dframe.loc[:, 'CCL_val'].quantile(0.5, interpolation='nearest')
    dframe = dframe.assign(
        CCL_ref=lambda x: round((x['CCL_val'] / ccl_ref - 1) * 100, 2)
    )
    # Sort DF by CCL_ref
    dframe.sort_values(by=['CCL_ref'], inplace=True)
    return dframe


async def get_main_df(args):
    '''
    Main function: pre-filter some stocks (quantile) and call fetch to actually
    get them
    '''
    urllib3.disable_warnings()

    # This 1st part is synchronous, as it's required to build the final dataframe
    ratios = await get_ratios()
    byma_all = await get_byma(ratios)

    tickers_to_include = args.tickers.split(',')
    # Choose only stocks with AR_val > 0 and volume over vol_q
    if args.no_filter:
        dframe = byma_all
    else:
        dframe = byma_all[(byma_all.AR_val > 0) & (
            (byma_all.AR_Vol >= byma_all.AR_Vol.quantile(args.vol_q))
            | (byma_all.AR_Buy >= byma_all.AR_Buy.quantile(args.vol_q))
            | (byma_all.AR_Sel >= byma_all.AR_Sel.quantile(args.vol_q))
            | (byma_all.index.isin(tickers_to_include)))]
    dframe.sort_index(inplace=True)
    if len(dframe) == 0:
        LOGGER.fatal("NO stocks grabbed")
        sys.exit(1)

    LOGGER.info(
        "CEDEARS CCLs: filtered {} tickers for q >= {:.2f}, incl={}".format(
            len(dframe), args.vol_q, str(tickers_to_include)))

    dframe = await (fetch(dframe))
    # Sort DF columns
    dframe.round({'CCL_ref': 2})
    dframe = dframe.reindex(sorted(dframe.columns), axis=1)
    return dframe


def main():
    'The main()'
    pd.set_option('display.max_rows', 500)
    pd.set_option('display.max_columns', 500)
    pd.set_option('display.width', 1000)
    args = parseargs()
    LOGGER.info("Choosing CEDEARS with volume >= {:0.2f} quantile".format(
        args.vol_q))
    # Invoke "main" function (which does async url fetching)
    loop = asyncio.get_event_loop()
    #dframe = loop.run_until_complete(fetch(dframe))
    # return get_main_df(args)
    return loop.run_until_complete(get_main_df(args))


if __name__ == '__main__':
    DFRAME = main()
    print(DFRAME)
