import json
import requests

def find_key(json_data, search_key, path=''):
    """
    Procura uma chave em um JSON, retornando o caminho e o valor da chave se ela for encontrada.
    """
    # Verifica se o objeto é um dicionário
    if isinstance(json_data, dict):
        # Percorre todas as chaves e valores do dicionário
        for key, value in json_data.items():
            # Se a chave buscada foi encontrada, retorna o caminho e o valor
            if key == search_key:
                return f"{path}/{key}", value
            # Caso contrário, procura a chave no valor correspondente
            result = find_key(value, search_key, f"{path}/{key}")
            if result:
                return result
    # Verifica se o objeto é uma lista
    elif isinstance(json_data, list):
        # Percorre todos os itens da lista
        for i, item in enumerate(json_data):
            # Procura a chave em cada item da lista
            result = find_key(item, search_key, f"{path}/{i}")
            if result:
                return result
    # Se o objeto não é nem dicionário nem lista, retorna None
    return None

# URL do arquivo JSON a ser pesquisado
url = f"http://192.168.1.1/api/model.json"

# Faz uma requisição GET na URL e armazena a resposta
session = requests.Session()
response = session.get(url)

# Converte o conteúdo da resposta em um objeto JSON
parsed_json = json.loads(response.text)

# Chave a ser procurada no JSON
key = 'rsrq'

# Procura a chave no JSON e armazena o resultado
result = find_key(parsed_json, key)

# Verifica se a chave foi encontrada
if result:
    # Extrai o caminho e o valor da chave do resultado
    path, value = result
    
    # Imprime o caminho e o valor da chave encontrada
    print(f"A chave '{key}' foi encontrada no caminho '{path}' com o valor '{value}'.")
    
    # Transforma o caminho da chave em um formato de acesso aos dados
    data_path = path.split('/')[1:]
    data_path = "['" + "']['".join(data_path) + "']"
    
    # Imprime o comando que pode ser usado para acessar a chave encontrada
    print(f"Comando: data{data_path} = '{value}'")
else:
    # Imprime uma mensagem indicando que a chave não foi encontrada
    print(f"A chave '{key}' não foi encontrada.")
