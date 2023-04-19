import json

def find_key(json_data, search_key, path=''):
    """
    Procura uma chave em um JSON, retornando o caminho e o valor da chave se ela for encontrada.
    """
    if isinstance(json_data, dict):
        for key, value in json_data.items():
            if key == search_key:
                return f"{path}/{key}", value
            result = find_key(value, search_key, f"{path}/{key}")
            if result:
                return result
    elif isinstance(json_data, list):
        for i, item in enumerate(json_data):
            result = find_key(item, search_key, f"{path}/{i}")
            if result:
                return result
    return None

# Exemplo de uso
import requests
url = f"http://192.168.1.1/api/model.json"
session = requests.Session()
response = session.get(url)
parsed_json = json.loads(response.text)
key = 'rsrq'
result = find_key(parsed_json, key)
if result:
    path, value = result
    print(f"A chave '{key}' foi encontrada no caminho '{path}' com o valor '{value}'.")
    data_path = path.split('/')[1:]
    data_path = "['" + "']['".join(data_path) + "']"
    print(f"Comando: data{data_path} = '{value}'")
else:
    print(f"A chave '{key}' não foi encontrada.")
