import json
import requests
import os
import pyperclip

def clear_screen():
    """
    Limpa a tela do terminal de acordo com o sistema operacional.
    """
    os.system('cls' if os.name == 'nt' else 'clear')

def find_key(json_data, search_key, path=''):
    """
    Procura uma chave em um JSON, retornando o caminho e o valor da chave se ela for encontrada.

    Args:
        json_data (dict or list): O JSON no qual procurar a chave.
        search_key (str): A chave a ser procurada.
        path (str, optional): Caminho atual no JSON. Default: ''.

    Returns:
        tuple: Retorna uma tupla contendo o caminho e o valor da chave, se encontrada.
        None: Retorna None se a chave não for encontrada.
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

def get_json_data():
    """
    Pergunta ao usuário se deseja inserir a URL do JSON ou o JSON manualmente e retorna o JSON.

    Returns:
        dict: Retorna o JSON obtido através da URL ou inserido manualmente.
    """
    while True:
        try:
            choice = input("Digite 1 para inserir a URL do JSON ou 2 para inserir o JSON manualmente: ")

            if choice == '1':
                url = input("Digite a URL do JSON: ")
                session = requests.Session()
                response = session.get(url, timeout=10)
                response.raise_for_status()
                return json.loads(response.text)
            elif choice == '2':
                json_input = input("Digite o JSON: ")
                return json.loads(json_input)
            else:
                print("Opção inválida. Tente novamente.")
        except requests.exceptions.RequestException as e:
            print(f"Erro ao buscar o JSON da URL: {e}")
        except requests.exceptions.Timeout as e:
            print(f"Tempo limite atingido ao buscar o JSON da URL: {e}")
        except json.JSONDecodeError as e:
            print(f"Erro ao decodificar o JSON: {e}")
        except Exception as e:
            print(f"Erro inesperado: {e}")

# Limpa a tela do terminal antes de começar
clear_screen()

# Obtém o JSON do usuário (URL ou manualmente)
parsed_json = get_json_data()

# Pergunta ao usuário a chave que deseja procurar
key = input("Digite a chave que deseja procurar: ")

# Procura a chave no JSON
result = find_key(parsed_json, key)

# Exibe os resultados
if result:
    path, value = result
    print(f"A chave '{key}' foi encontrada no caminho '{path}' com o valor '{value}'.")
    data_path = path.split('/')[1:]
    data_path = "['" + "']['".join(data_path) + "']"
    command = f"data{data_path}"
    print(f"Comando: {command} = '{value}'")
    
    # Copia o comando para a área de transferência
    try:
        pyperclip.copy(command)
        print("O comando foi copiado para a área de transferência.")
    except pyperclip.PyperclipException as e:
        print(f"Erro ao copiar para a área de transferência: {e}")
else:
    print(f"A chave '{key}' não foi encontrada.")