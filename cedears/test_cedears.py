'tests'
import argparse
import os
import unittest
import urllib
import json
import sys
import re
import mock
import cedears

CEDEARS_RATIOS_FILE = "CEDEARS-SHARES.html"
CEDEARS_LIVE_FILE = "CEDEARS-quotes.json"
URL_FILE_RE = {
    b'https://www.zacks.com/stock/quote/(.+)': r"zacks-{}.html",
    b'https://query1.finance.yahoo.com/v10/finance/quoteSummary/(.*)': r"yfin-{}.json"
}

def local_url_to_file(url):
    '...'
    url_to_file = {
        cedears.CEDEARS_RATIOS_URL: CEDEARS_RATIOS_FILE,
        cedears.CEDEARS_LIVE_URL: CEDEARS_LIVE_FILE,
    }
    file_url = url_to_file.get(url)
    if file_url is None:
        for (k, value) in URL_FILE_RE.items():
            match = re.match(k, bytes(url, encoding="utf-8"))
            if match:
                stock = match.groups(1)[0].decode("utf-8")
                file_url = value.format(stock)

    return 'file://{}/testdata/{}'.format(
        os.getcwd(), file_url
    )

def local_get(url, **kwargs):
    "Fetch a stream from local files."
    p_url = local_url_to_file(url)
    p_url = urllib.parse.urlparse(p_url)
    if p_url.scheme != 'file':
        raise ValueError("Expected file scheme")

    filename = urllib.request.url2pathname(p_url.path)
    text = open(filename, 'rb').read().decode("utf-8")
    json_ret = {}
    try:
        json_ret = json.loads(text)
    except json.decoder.JSONDecodeError:
        pass
    return type('testreq', (object,),
                {
                    "text": text,
                    "url": url,
                    "json": lambda x: json_ret,
                    "status_code": 200,
                })()

@mock.patch('cedears.url_get', local_get)
class TestCedears(unittest.TestCase):
    def test_get_ratios(self):
        df = cedears.get_ratios()
        self.assertEqual(len(df), 237)
        self.assertEqual(df.loc['AAPL', 'Ratio'], 10.0)
        self.assertEqual(df.loc['XOM', 'Ratio'], 5.0)
        self.assertEqual(df.loc['DISN', 'US_Ticker'], 'DIS')
        return df

    def test_get_byma(self):
        ratios = self.test_get_ratios()
        df = cedears.get_byma(ratios)
        self.assertEqual(df.loc['AAPL', 'Ratio'], 10.0)
        self.assertEqual(cedears.df_loc1(df, 'DISN', 'US_Ticker'), 'DIS')

    @mock.patch('cedears.argparse.ArgumentParser.parse_args',
                return_value=argparse.Namespace(vol_quantile=0.98))
    def test_get_main_df(self, mock_args):
        df = cedears.main()
        self.assertEqual(df.loc['XOM', 'CCL_ratio'], -0.7198023972132983)

if __name__ == '__main__':
    unittest.main()
