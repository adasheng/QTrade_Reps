import urllib.request as request
url = 'https://raw.githubusercontent.com/FinanceData/fdr_krx_data_cache/master/data/snap/index_list/2026-03-11.csv'
try:
    print(request.urlopen(url).read(100))
except Exception as e:
    print(e)
