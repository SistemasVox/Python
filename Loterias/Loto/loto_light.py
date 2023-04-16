import requests
import json
import os
import certifi
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

url_base = 'https://servicebus2.caixa.gov.br/portaldeloterias/api/lotofacil/'

# Número do último concurso
try:
    ultimo_concurso = requests.get(url_base, verify=False).json()['numero']
except requests.exceptions.RequestException as e:
    print(f"Erro ao buscar o último concurso: {e}")
    exit()

# Lista para armazenar as informações dos sorteios
sorteios = []

# Loop pelos concursos anteriores ao último sorteado
for concurso in range(ultimo_concurso, 0, -1):
    url = url_base + str(concurso)
    try:
        response = requests.get(url, verify=False)
    except requests.exceptions.RequestException as e:
        print(f"Erro ao buscar o concurso {concurso}: {e}")
        continue
    if response.status_code == 200:
        data = response.json()
        data_sorteio = data['dataApuracao']
        numero = data['numero']
        dezenas = ', '.join(data['listaDezenas'])
        sorteios.append((data_sorteio, numero, dezenas))

# Armazena as informações em um arquivo loto.sql
try:
    if os.path.exists('loto.sql'):
        os.remove('loto.sql')
    with open('loto.sql', 'w') as f:
        for sorteio in sorteios:
            f.write(f"INSERT INTO loto (data_sorteio, concurso, jogo) VALUES ('{sorteio[0]}', {sorteio[1]}, '{sorteio[2]}');\n")
except OSError as e:
    print(f"Erro ao criar o arquivo loto.sql: {e}")
    exit()
