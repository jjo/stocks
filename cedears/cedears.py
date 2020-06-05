#!/usr/bin/env python3
#
# Copyright Juanjo Ciarlante <http://twitter.com/xjjo>
#
# License: MIT
#
import argparse
import sys
import re
import urllib3
import asyncio
import logging

import numpy as np
import pandas as pd
import requests
import jsonpath_rw as jp
import json
import memcache
import aiohttp
from aiocache import cached, Cache
from aiocache.serializers import PickleSerializer

logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger=logging.getLogger()

CEDEARS_RATIOS_URL = r'https://www.comafi.com.ar/2254-CEADEAR-SHARES.note.aspx'
CEDEARS_LIVE_URL = r'https://www.byma.com.ar/wp-admin/admin-ajax.php'
CEDEARS_LIVE_PARAMS = {'action': 'get_panel', 'panel_id': '5'}
USER_AGENT=('Mozilla/5.0 (iPhone; U; CPU iPhone OS 4_3_3 like Mac OS X; en-us) '
            'AppleWebKit/533.17.9 (KHTML, like Gecko)'
            'Version/5.0.2 Mobile/8J2 Safari/6533.18.5')
ZACKS_URL = r'https://www.zacks.com/stock/quote/{}'
YAHOOFIN_URL = r'https://query1.finance.yahoo.com/v10/finance/quoteSummary/{}'
YAHOOFIN_PARAMS = {'modules': 'financialData'}

VOLUME_QUANTILE = 0.75

ARGS = None
CACHE = {"cache": Cache.MEMORY}

def parseargs(argv=sys.argv):
    parser = argparse.ArgumentParser(description="CEDEARS CCL tool by jjo")
    parser.add_argument('--vol_quantile', type=float, default = VOLUME_QUANTILE,
                        help="min volume quantile to use, default: {}".format(VOLUME_QUANTILE))
    parser.add_argument('--no-filter', action="store_true",
                        help="Get them all(!)")
    parser.add_argument('--tickers', default="",
                        help="comma delimited list of stocks to include (regardless of vol_quantile)")
    parser.add_argument('--cache', default="memory",
                        help="comma delimited list of stocks to include (regardless of vol_quantile)")
    return parser.parse_args()

#if __name__ == '__main__':
if True:
    ARGS = parseargs()
    if ARGS.cache == "memcache":
        logger.info("Using cache={}".format(ARGS.cache))
        CACHE={
            "cache": Cache.MEMCACHED,
            "port": 11211,
            "endpoint": "127.0.0.1",
            "pool_size": 10,
        }

@cached(ttl=60,
        **CACHE,
        serializer=PickleSerializer(),
        namespace="url")
async def url_get(url, **kwargs):
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers={'User-Agent': USER_AGENT}, **kwargs) as r:
            logger.info("url={}".format(r.url))
            return await r.text()

@cached(ttl=3600,
        **CACHE,
        serializer=PickleSerializer(),
        namespace="ratios")
async def get_ratios():
    def _ratio(s):
        ratio_args = s.split(':')
        return float(ratio_args[0]) / float(ratio_args[1])

    logger.info("CEDEARS ratios: fetching from {}".format(CEDEARS_RATIOS_URL))
    # Returns list of all tables on page
    r = await url_get(CEDEARS_RATIOS_URL)
    tables = pd.read_html(r)

    # Single table in page
    table = tables[0]
    # Translate field names ES -> EN
    table.columns = [col.split(" ")[0] for col in table.columns]
    table.rename(columns={
        'Simbolo': 'Ticker',
        'Trading': 'US_Ticker',
    }, inplace=True)
    # Transform X:Y string ratio to X/Y float
    table['Ratio'] = table['Ratio'].apply(lambda x: _ratio(x))
    table = table.set_index('Ticker', drop = False)
    #logger.info("CEDEARS ratios: got {} entries from {}".format(len(table), r.url))
    logger.info("CEDEARS ratios: got {} entries".format(len(table)))
    return table

@cached(ttl=60,
        **CACHE,
        serializer=PickleSerializer(),
        key="byma",
        namespace="byma")
async def get_byma(ratios):
    logger.info("CEDEARS quotes: fetching from {}".format(CEDEARS_LIVE_URL))
    # WTF CEDEARS_LIVE_URL doesn't have a proper TLS cert(?)
    r = await url_get(CEDEARS_LIVE_URL, params=CEDEARS_LIVE_PARAMS, verify_ssl=False, timeout=30)
    # Parse JSON into DF
    df = pd.DataFrame(columns=['Ticker', 'ARS_value', 'Ratio', 'ARS_Volume', 'ARS_OrdBuy', 'ARS_OrdSel', 'ARS_delta', 'US_Ticker'])
    for x in json.loads(r)["Cotizaciones"]:
        # - skip tickers w/o value
        # - only delayed quotes, for better volume
        # - skip tickers w/no Volume
        # - only ARS tickers
        if (
            x['Ultimo'] == 0 or
            x['Vencimiento'] not in ("48hs", "24hs") or
            x['Tipo_Liquidacion'] != "Pesos"
        ):
            continue
        ticker = x['Simbolo']
        ars_value = x['Ultimo']
        period = x['Vencimiento']
        volume = x['Volumen_Nominal']
        ars_buy = x['Cantidad_Nominal_Compra']
        ars_sell = x['Cantidad_Nominal_Venta']
        ars_delta = x['Variacion']
        #logger.info('ticker={} ars_value={}'.format(ticker, ars_value))
        try:
            ratio = ratios.loc[ticker, 'Ratio']
        except KeyError:
            continue
        us_ticker = ratios.loc[ticker, 'US_Ticker']
        df = df.append({
            'Ticker': ticker,
            'US_Ticker': us_ticker,
            'ARS_value': ars_value,
            'Ratio': ratio,
            'ARS_Volume': volume,
            'ARS_OrdBuy': ars_buy,
            'ARS_OrdSel': ars_sell,
            'ARS_period': period,
            'ARS_delta': ars_delta,
        }, ignore_index=True)
    # Index the DF by ticker
    df = df.set_index("Ticker")
    #logger.info("CEDEARS quotes: got {} entries from {}".format(len(df), r.url))
    logger.info("CEDEARS quotes: got {} entries".format(len(df)))
    if len(df) == 0:
        logger.warning("CEDEARS quotes: ZERO -- Market not yet open ?".format(len(df)))
    return df

@cached(ttl=1800,
        **CACHE,
        serializer=PickleSerializer(),
        namespace="zrank")
async def get_zacks_rank(stock):

    url = ZACKS_URL.format(stock)
    rank = "N/A"
    try:
        r = await url_get(url, timeout=30)
        rank_match = re.search(
            r'\n\s*([^<]+).+rank_chip.rankrect_1.*rank_chip.rankrect_2',
            r
        )
    except (aiohttp.client_exceptions.ClientOSError,
            aiohttp.client_exceptions.ServerDisconnectedError,
            asyncio.exceptions.TimeoutError):
        return rank
    try:
        rank = rank_match.groups(1)[0]
    except AttributeError:
        return rank

    # Save found rank into cache
    rank = "{:8}".format(rank.replace("Strong ", "S"))
    logger.debug("stock={:8} rank={:8}".format(stock, rank))
    return rank

@cached(ttl=60,
        **CACHE,
        serializer=PickleSerializer(),
        namespace="zrank")
async def get_usd_value(stock):
    url = YAHOOFIN_URL.format(stock)
    r = await url_get(url, params=YAHOOFIN_PARAMS, timeout=30)
    # Use jsonpath to traverse it down to the data we want
    jp_exp = jp.parse('$.quoteSummary.result..financialData.currentPrice.raw')
    try:
        price = jp_exp.find(json.loads(r))[0].value
    except IndexError:
        return
    if r == "":
        return
    # Save found price into cache
    logger.debug("stock={:8} price={:0.2f}".format(stock, price))
    return price

# Just a convenient function that's called couple times below
def get_ccl_val(price_ars, price, ratio):
    return price_ars / price * ratio

def df_loc1(df, index, col):
    return df.loc[df.index == index, col].head(1).iloc[0]

async def fetch(df):
    # Stocks list is DF index
    stocks = set(df.index.values.tolist())
    logger.info("jjo: stocks={}".format(stocks))
    # Async stanza, to concurrently fetch stocks' price and zacks rank
    futures = []
    # Warm caches
    for stock in stocks:
        # We may have several entries for (AR)stock, just choose one:
        us_stock = df_loc1(df, stock, 'US_Ticker')
        assert type(us_stock) == str, "stock={} returned type(us_stock)={}".format(
            stock, type(us_stock)
        )
        futures.append(get_usd_value(us_stock))
        futures.append(get_zacks_rank(us_stock))

    # Called functions already save data into caches, they don't return values
    for _ in await asyncio.gather(*futures):
        pass
    logger.info("jjo: DONE stocks={}".format(stocks))

    # Add new columns to dataframe with obtained CCL value (ARS/USD ratio
    # for the ticker), and Zacks rank
    for stock in stocks:
        us_stock = df_loc1(df, stock, 'US_Ticker')
        price = await get_usd_value(us_stock)
        rank = await get_zacks_rank(us_stock)
        if (price == None or price == 0.0 or rank == None):
            df.drop(stock, inplace=True)
            continue

        price_ars = df_loc1(df, stock, 'ARS_value')
        ratio = df_loc1(df, stock, 'Ratio')
        ccl_val = get_ccl_val(price_ars, price, ratio)
        # Add (column and) cell with computed values
        df.loc[stock, 'ZRank'] = rank
        df.loc[stock, 'CCL_val'] = ccl_val
        df.loc[stock, 'ARS_tot'] = int(price_ars * ratio)
        df.loc[stock, 'USD_val'] = price

    # Use quantile 0.5 as ref value
    ccl_ref = df.loc[:,'CCL_val'].quantile(0.5, interpolation='nearest')
    df = df.assign(CCL_ratio = lambda x: (x['CCL_val'] / ccl_ref - 1) * 100)
    # Sort DF by CCL_ratio
    df.sort_values(by=['CCL_ratio'], inplace=True)
    return df

async def get_main_df(args):
    urllib3.disable_warnings()

    # This 1st part is synchronous, as it's required to build the final dataframe
    ratios = await get_ratios()
    byma_all = await get_byma(ratios)

    tickers_to_include = args.tickers.split(',')
    # Choose only stocks with ARS_value > 0 and volume over vol_quantile
    if args.no_filter:
        df = byma_all
    else:
        df = byma_all[
            (byma_all.ARS_value > 0) &
            (
                (byma_all.ARS_Volume >= byma_all.ARS_Volume.quantile(args.vol_quantile)) |
                (byma_all.ARS_OrdBuy >= byma_all.ARS_OrdBuy.quantile(args.vol_quantile)) |
                (byma_all.ARS_OrdSel >= byma_all.ARS_OrdSel.quantile(args.vol_quantile)) |
                (byma_all.index.isin(tickers_to_include))
            )
        ]
    df.sort_index(inplace=True)
    if len(df) == 0:
        logger.fatal("NO stocks grabbed")
        sys.exit(1)

    logger.info(
        "CEDEARS CCLs: filtered {} tickers for q >= {:.2f}, incl={}"
        .format(len(df), args.vol_quantile, str(tickers_to_include))
    )

    df = await(fetch(df))
    # Sort DF columns
    df.round({'CCL_ratio': 2})
    df = df.reindex(sorted(df.columns), axis=1)
    return df


def main():
    pd.set_option('display.max_rows', 500)
    pd.set_option('display.max_columns', 500)
    pd.set_option('display.width', 1000)
    args = parseargs()
    global CACHE
    logger.info(
        "Choosing CEDEARS with volume >= {:0.2f} quantile"
        .format(args.vol_quantile)
    )
    # Invoke "main" function (which does async url fetching)
    loop = asyncio.get_event_loop()
    #df = loop.run_until_complete(fetch(df))
    #return get_main_df(args)
    return loop.run_until_complete(get_main_df(args))

if __name__ == '__main__':
    df = main()
    print(df)
