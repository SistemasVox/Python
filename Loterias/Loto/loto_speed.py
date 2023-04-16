import requests
import os
from tqdm import tqdm
import urllib3

# Remover avisos de SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configurações da conexão
session = requests.Session()
session.mount('https://', requests.adapters.HTTPAdapter(pool_connections=100, pool_maxsize=100))
session.mount('http://', requests.adapters.HTTPAdapter(pool_connections=100, pool_maxsize=100))
session.headers.update({'Accept-Encoding': 'gzip, deflate'})
session.keep_alive = False

url_base = 'https://servicebus2.caixa.gov.br/portaldeloterias/api/lotofacil/'

# Número do último concurso
try:
    ultimo_concurso = session.get(url_base, verify=False).json()['numero']
except requests.exceptions.RequestException as e:
    print(f"Erro ao buscar o último concurso: {e}")
    exit()

# Loop pelos concursos anteriores ao último sorteado
sorteios = []
comandos_insert = []
for concurso in tqdm(range(ultimo_concurso, 0, -1), desc='Baixando resultados da lotofacil'):
    url = url_base + str(concurso)
    try:
        response = session.get(url, stream=True, verify=False)
        response.raise_for_status()  # Levanta uma exceção para erros de conexão
        data = response.json()
        data_sorteio = data['dataApuracao']
        numero = data['numero']
        dezenas = ','.join(data['listaDezenas'])
        sorteios.append((data_sorteio, numero, dezenas))
        comandos_insert.append(f"('{data_sorteio}', {numero}, '{dezenas}')")
        tqdm.write(f"Concurso {concurso} baixado com sucesso!")
    except (requests.exceptions.RequestException, ValueError) as e:
        tqdm.write(f"Erro ao buscar o concurso {concurso}: {e}")

# Verifica se o arquivo já existe e exclui
if os.path.exists('loto.sql'):
    os.remove('loto.sql')

# Cria o arquivo e grava os comandos INSERT INTO
try:
    with open('loto.sql', 'w') as f:
        f.write("INSERT INTO loto (data_concurso, concurso, dezenas) VALUES\n")
        f.write(',\n'.join(comandos_insert))
        f.write(';')
except OSError as e:
    tqdm.write(f"Erro ao escrever no arquivo loto.sql: {e}")

tqdm.write("Download concluído com sucesso!")
