IMAGE=xjjo/cedears
#
# Only for testdata saving:
USER_AGENT='Mozilla/5.0 (iPhone; U; CPU iPhone OS 4_3_3 like Mac OS X; en-us) AppleWebKit/533.17.9 (KHTML, like Gecko) Version/5.0.2 Mobile/8J2 Safari/6533.18.5'
CURL=curl -A $(USER_AGENT)

all: build

build:
	docker build -t $(IMAGE) .
push: build
	docker push $(IMAGE)
run:
	docker run -u 1000 -it $(IMAGE)
test:
	nose2-3 -C

testdata-refresh:
	$(CURL) -so testdata/CEDEARS-SHARES.html https://www.comafi.com.ar/2254-CEADEAR-SHARES.note.aspx
	$(CURL) -sko testdata/CEDEARS-quotes.json 'https://www.byma.com.ar/wp-admin/admin-ajax.php?action=get_panel&panel_id=5'
	$(CURL) -so testdata/yfin-BBD.json https://query1.finance.yahoo.com/v10/finance/quoteSummary/BBD?modules=financialData
	$(CURL) -so testdata/yfin-PBR.json https://query1.finance.yahoo.com/v10/finance/quoteSummary/PBR?modules=financialData
	$(CURL) -so testdata/yfin-XOM.json https://query1.finance.yahoo.com/v10/finance/quoteSummary/XOM?modules=financialData
	$(CURL) -so testdata/zacks-PBR.html https://www.zacks.com/stock/quote/PBR
	curl -o testdata/zacks-BBD.html https://www.zacks.com/stock/quote/BBD
	curl -o testdata/zacks-XOM.html https://www.zacks.com/stock/quote/XOM

.PHONY: all build push run test testdata-refres
