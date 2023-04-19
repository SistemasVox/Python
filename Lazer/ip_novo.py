import requests
from bs4 import BeautifulSoup

try:
    # solicita ao usuário que informe o IP
    ip = input('Digite o endereço IP: ')

    # constrói a URL com base no IP informado
    url = f'https://whatismyip.com.br/?query={ip}'

    # faz uma solicitação GET para o URL e pega o conteúdo HTML
    response = requests.get(url)

    # verifica se a solicitação foi bem-sucedida
    if response.status_code == 200:
        html = response.content

        # analisa o HTML com a biblioteca BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')

        # encontra a tabela com as informações do IP
        table = soup.find('table', {'class': 'center'})

        # verifica se a tabela foi encontrada
        if table:
            # encontra todas as linhas da tabela
            rows = table.find_all('tr')

            # itera sobre as linhas e extrai as informações
            for row in rows:
                # encontra as colunas da linha
                cols = row.find_all('td')

                # extrai o rótulo e o valor da informação
                label = cols[0].text.strip()
                value = cols[1].text.strip()

                # escreve o rótulo e o valor na tela
                print(f'{label}: {value}')
        else:
            print('Tabela não encontrada')
    else:
        print('Solicitação falhou com o status:', response.status_code)
except requests.exceptions.RequestException as e:
    print('Ocorreu um erro de solicitação:', e)
except Exception as e:
    print('Ocorreu um erro:', e)

"""
Basic versão:

import requests
from bs4 import BeautifulSoup
ip = input('Digite o endereço IP: ')
url = f'https://whatismyip.com.br/?query={ip}'
response = requests.get(url)
html = response.content
soup = BeautifulSoup(html, 'html.parser')
table = soup.find('table', {'class': 'center'})
rows = table.find_all('tr')
for row in rows:
    cols = row.find_all('td')
    label = cols[0].text.strip()
    value = cols[1].text.strip()
    print(f'{label}: {value}')
"""