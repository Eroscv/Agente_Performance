from dotenv import load_dotenv
load_dotenv()
import requests, time
from meta_ads.loader import FIELDS, _normalizar, _get_token, API_BASE

token = _get_token('META_POD_ACCESS_TOKEN')
all_rows = []
page = 0
url = f'{API_BASE}/act_324906945710366/insights'
params = {
    'access_token': token,
    'level': 'ad',
    'fields': FIELDS,
    'time_range': '{"since":"2026-09-14","until":"2026-09-20"}',
    'time_increment': 1,
    'limit': 50,
    'filtering': '[{"field":"spend","operator":"GREATER_THAN","value":"0"}]',
    'action_attribution_windows': '["7d_click","1d_view"]',
}

while url and page < 30:
    for attempt in range(3):
        try:
            r = requests.get(url, params=params if page==0 else {}, timeout=25)
            data = r.json()
            if 'error' in data:
                print(f'Erro p{page} t{attempt}:', data['error'].get('message'), flush=True)
                time.sleep(3)
                continue
            break
        except Exception as e:
            print(f'Ex p{page} t{attempt}: {e}', flush=True)
            time.sleep(3)
    else:
        print(f'Desistindo p{page}')
        break

    batch = data.get('data', [])
    all_rows.extend([_normalizar(x) for x in batch])
    page += 1
    print(f'p{page}: +{len(batch)} (total={len(all_rows)})', flush=True)
    url = data.get('paging',{}).get('next')
    if url:
        time.sleep(0.5)

receita = sum(r.get('valor_compras',0) or 0 for r in all_rows)
invest  = sum(r.get('investimento',0) or 0 for r in all_rows)
compras = sum(r.get('compras',0) or 0 for r in all_rows)
print(f'\nTotal: {len(all_rows)} linhas | {page} páginas')
print(f'Receita Meta Sem38 POD (7d_click+1d_view): R$ {receita:,.2f}')
print(f'Investimento: R$ {invest:,.2f}')
print(f'Compras: {compras:.0f}')
print(f'ROAS: {receita/invest:.2f}x' if invest else '')
