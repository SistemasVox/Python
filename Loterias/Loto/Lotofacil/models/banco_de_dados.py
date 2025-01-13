# C:\Users\Marcelo\Documents\Python\Lotofacil\models\banco_de_dados.py
# Nome do arquivo: banco_de_dados.py

import requests
from concurrent.futures import ThreadPoolExecutor
from tqdm import tqdm
from threading import Lock
import random

class BancoDeDados:
    def __init__(self, filename='banco_de_dados.txt'):
        self.filename = filename
        self.lock = Lock()
        self.headers = {
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                " AppleWebKit/537.36 (KHTML, like Gecko)"
                " Chrome/131.0.0.0 Safari/537.36"
            )
        }

    def buscar_concurso(self, numero):
        """
        Faz a requisição à API da Caixa para obter informações
        de um determinado concurso da Lotofácil.
        Caso 'numero' seja string vazia (""), tenta buscar o concurso mais recente.
        """
        url = f"https://servicebus2.caixa.gov.br/portaldeloterias/api/lotofacil/{numero}"
        try:
            response = requests.get(url, timeout=1, headers=self.headers)
            response.raise_for_status()
            data = response.json()
            if not data:
                raise ValueError(f"Resposta vazia para o concurso {numero}")
            return data
        except requests.exceptions.RequestException:
            return None
        except ValueError:
            return None

    def criar_atualizar_banco_de_dados(self, status_callback=None, progresso_callback=None):
        """
        Cria ou atualiza o banco de dados local (arquivo texto) com todos
        os concursos da Lotofácil, do 1 até o mais recente disponível na API.
        
        - status_callback: função para receber mensagens de status
        - progresso_callback: função para receber percentual de progresso e número do concurso
        """
        try:
            # Carregar concursos existentes do arquivo
            try:
                with open(self.filename, 'r') as file:
                    dados_existentes = {
                        int(line.split(",")[0]): line.strip()
                        for line in file.readlines()
                    }
                    # Só pra saber se tinha algo:
                    ultimo_concurso_local = max(dados_existentes.keys()) if dados_existentes else 0
            except FileNotFoundError:
                dados_existentes = {}
                ultimo_concurso_local = 0

            # Buscar o último concurso disponível na API (string vazia -> concurso mais recente)
            ultimo_dado = self.buscar_concurso("")
            if not ultimo_dado:
                if status_callback:
                    status_callback(
                        "Erro ao buscar o último concurso. "
                        "Verifique sua conexão ou a API."
                    )
                return

            ultimo_numero = int(ultimo_dado["numero"])

            # Identificar quais concursos estão ausentes
            sequencia_completa = set(range(1, ultimo_numero + 1))
            concursos_existentes = set(dados_existentes.keys())
            concursos_ausentes = list(sequencia_completa - concursos_existentes)
            random.shuffle(concursos_ausentes)

            if concursos_ausentes:
                if status_callback:
                    status_callback(
                        f"Concursos ausentes identificados: {len(concursos_ausentes)}"
                    )
            else:
                if status_callback:
                    status_callback("Banco de dados completo. Nenhum concurso ausente.")

            # Baixar concursos ausentes
            novos_dados = []
            with ThreadPoolExecutor(max_workers=4) as executor:
                futuros = {
                    executor.submit(self.buscar_concurso, numero): numero
                    for numero in concursos_ausentes
                }

                for i, futuro in enumerate(tqdm(futuros, desc="Baixando concursos")):
                    numero = futuros[futuro]
                    try:
                        concurso = futuro.result()
                        if concurso is None:
                            if status_callback:
                                status_callback(
                                    f"Concurso {numero} não pôde ser baixado. Pulando."
                                )
                            continue

                        dezenas = concurso.get("dezenasSorteadasOrdemSorteio")
                        if dezenas and len(dezenas) == 15:
                            novos_dados.append((numero, dezenas))
                            if progresso_callback:
                                progresso_callback(
                                    (i + 1) / len(concursos_ausentes) * 100,
                                    numero
                                )
                        else:
                            if status_callback:
                                status_callback(
                                    f"Concurso {numero} com dados inválidos. Pulando."
                                )
                    except Exception as e:
                        if status_callback:
                            status_callback(
                                f"Erro ao processar concurso {numero}: {str(e)}"
                            )

            # Atualizar dados existentes
            for numero, dezenas in novos_dados:
                dados_existentes[numero] = f"{numero},{','.join(dezenas)}"

            # Salvar concursos ordenados
            dados_ordenados = sorted(
                dados_existentes.values(),
                key=lambda x: int(x.split(",")[0])
            )
            with self.lock, open(self.filename, 'w') as file:
                file.write("\n".join(dados_ordenados) + "\n")

            if status_callback:
                status_callback("Banco de dados atualizado com sucesso.")

            # Verificar se todos os concursos estão presentes
            concursos_faltando = sequencia_completa - set(dados_existentes.keys())
            if concursos_faltando:
                if status_callback:
                    status_callback(
                        f"Ainda faltam concursos: {sorted(concursos_faltando)}"
                    )
            else:
                if status_callback:
                    status_callback("Banco de dados completo e consistente.")

        except Exception as e:
            if status_callback:
                status_callback(f"Erro na atualização do banco de dados: {str(e)}")

    def obter_total_concursos(self):
        """
        Retorna quantas linhas (concursos) há no arquivo (banco_de_dados.txt).
        """
        try:
            with open(self.filename, "r") as file:
                return len(file.readlines())
        except FileNotFoundError:
            return 0

    def obter_ultimo_concurso(self):
        """
        Retorna o número do último concurso armazenado no arquivo local,
        ou None se o arquivo estiver vazio ou não existir.
        """
        try:
            with open(self.filename, 'r') as file:
                dados_existentes = file.readlines()
                if dados_existentes:
                    return int(dados_existentes[-1].split(",")[0])
                else:
                    return None
        except FileNotFoundError:
            return None

    def recuperar_todos_jogos(self):
        """
        Lê o arquivo e retorna uma lista de tuplas:
          [(num_concurso, [d1, d2, ..., d15]), ...]
        """
        jogos = []
        try:
            with open(self.filename, 'r') as file:
                lines = file.readlines()

            for line in lines:
                parts = line.strip().split(',')
                # parts[0] = número do concurso
                # parts[1..15] = as dezenas
                if len(parts) == 16:
                    num_concurso = int(parts[0])
                    dezenas = [int(x) for x in parts[1:]]
                    jogos.append((num_concurso, dezenas))
        except FileNotFoundError:
            pass

        return jogos

    def obter_info_banco(self):
        """
        Retorna (total_concursos, ultimo_concurso).
        """
        total = self.obter_total_concursos()
        ultimo = self.obter_ultimo_concurso()
        return (total, ultimo)
