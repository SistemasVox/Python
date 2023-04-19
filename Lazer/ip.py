import requests

API_KEY = ''

ip_address = input('Insira o endereço IP: ')

url = f'https://ipinfo.io/{ip_address}?token={API_KEY}'

response = requests.get(url)

if response.status_code == 200:
    data = response.json()
    print(f'Endereço IP: {data["ip"]}')
    print(f'Localização: {data["city"]}, {data["region"]}, {data["country"]}')
    print(f'Provedor de Internet: {data["org"]}')
else:
    print('Não foi possível consultar informações sobre o IP')
