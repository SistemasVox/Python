import requests
import os
import platform
from tqdm import tqdm  # Biblioteca para mostrar progresso de tarefas
import urllib3
import mysql.connector
from datetime import datetime

# Remover avisos de SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configurações da conexão
session = requests.Session()
session.mount(
    "https://", requests.adapters.HTTPAdapter(pool_connections=100, pool_maxsize=100)
)
session.mount(
    "http://", requests.adapters.HTTPAdapter(pool_connections=100, pool_maxsize=100)
)
session.headers.update({"Accept-Encoding": "gzip, deflate"})
session.keep_alive = False

# Limpar tela
operating_system = platform.system()
if operating_system == "Windows":
    os.system("cls")
else:
    os.system("clear")

url_base = "https://servicebus2.caixa.gov.br/portaldeloterias/api/lotofacil/"

# Número do último concurso
try:
    # Faz a requisição para a API e pega o número do último concurso e a data de apuração
    ultimo_concurso = session.get(url_base, verify=False).json()["numero"]
    data_apuracao = session.get(url_base, verify=False).json()["dataApuracao"]
    print(f"Último concurso encontrado: {ultimo_concurso}. Data: {data_apuracao}.")
except requests.exceptions.RequestException as e:
    print(f"Erro ao buscar o último concurso: {e}.")
    exit()

# Configurações do banco de dados
config = {
    "user": "marcelo_marcelo",
    "password": "pJ6xIvDM4iqzv",
    "host": "158.69.109.147",
    "port": "3306",
    "database": "marcelo_loteria",
}

# Conecta-se ao banco de dados
try:
    conn = mysql.connector.connect(**config)
    cursor = conn.cursor()
    print("Conexão bem sucedida ao banco de dados!")
except mysql.connector.Error as e:
    print(f"Erro ao conectar ao banco de dados: {e}")
    exit()

# Obter a lista de todos os concursos já inseridos no banco de dados
cursor.execute("SELECT concurso FROM loto")
resultados = cursor.fetchall()
concursos_inseridos = set([r[0] for r in resultados])

# Crie um conjunto com todos os concursos na sequência completa
sequencia_completa = set(range(1, ultimo_concurso + 1))

# Determine quais concursos estão ausentes na sequência completa
concursos_ausentes = sorted(list(sequencia_completa - concursos_inseridos))

# Exiba a lista de concursos ausentes
if len(concursos_ausentes) > 0:
    print(f"Os seguintes concursos estão ausentes: {concursos_ausentes}")
else:
    print("Todos os concursos estão presentes no banco de dados.")

# Loop pelos concursos ausentes
if len(concursos_ausentes) > 0:
    sorteios = []  # cria uma lista vazia para armazenar os resultados baixados
    for concurso in tqdm(concursos_ausentes, desc="Baixando resultados da lotofacil"):
        url = url_base + str(concurso)  # define a URL para baixar o resultado do concurso atual
        try:
            # Faz a requisição para a API e pega a data do sorteio, número do concurso e dezenas sorteadas
            response = session.get(url, stream=True, verify=False)
            response.raise_for_status()  # Levanta uma exceção para erros de conexão
            data = response.json()
            # Converte a data de string para objeto datetime e formata para o padrão aaaa/mm/dd
            data_sorteio = datetime.strptime(data["dataApuracao"], '%d/%m/%Y').strftime('%Y/%m/%d')
            numero = data["numero"]
            dezenas = ",".join(data["listaDezenas"])
            sorteios.append((data_sorteio, numero, dezenas))  # adiciona os resultados à lista sorteios
            sql = f"INSERT INTO loto (data_concurso, concurso, dezenas) VALUES ('{data_sorteio}', {numero}, '{dezenas}')"
            cursor.execute(sql)  # executa a query SQL para inserir os resultados no banco de dados
            conn.commit()  # confirma a transação
            tqdm.write(f"Concurso {concurso} baixado e inserido com sucesso no banco de dados!")
        except (requests.exceptions.RequestException, ValueError, mysql.connector.Error) as e:
            tqdm.write(f"Erro ao buscar o concurso {concurso} ou inserir no banco de dados: {e}")
    # Encerra a conexão com o banco de dados
    cursor.close()
    conn.close()

# Informa que o download e a inserção no banco de dados foram concluídos com sucesso
tqdm.write("Download e inserção no banco de dados concluídos com sucesso!")
