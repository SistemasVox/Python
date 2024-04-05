import os
import hashlib
import platform
from datetime import datetime

# Limpe a tela com base no sistema operacional
def clear_screen():
    if platform.system() == 'Windows':
        os.system('cls')
    else:
        os.system('clear')

clear_screen()

diretorio = 'C:/Users/Marcelo/Pictures/SGs'

def exibir_mensagem(mensagem):
    now = datetime.now()
    current_time = now.strftime("%H:%M:%S")
    print(f"[{current_time}] {mensagem}")

exibir_mensagem("Iniciando o aplicativo...")
exibir_mensagem(f"Buscando por arquivos duplicados em {diretorio}...")

# Lista todos os arquivos no diretório
arquivos = os.listdir(diretorio)

# Cria um dicionário para armazenar os hashes dos arquivos
hashes = {}

# Lista para armazenar os nomes dos arquivos excluídos
arquivos_excluidos = []

# Percorre os arquivos e calcula o hash MD5 de cada um
for arquivo in arquivos:
    with open(os.path.join(diretorio, arquivo), "rb") as f:
        conteudo = f.read()
        hash_arquivo = hashlib.md5(conteudo).hexdigest()
        if hash_arquivo not in hashes:
            hashes[hash_arquivo] = []
        hashes[hash_arquivo].append(arquivo)

exibir_mensagem("Verificação concluída. Removendo arquivos duplicados...")

# Remove os arquivos duplicados
for hash_arquivo, arquivos_duplicados in hashes.items():
    if len(arquivos_duplicados) > 1:
        exibir_mensagem(f"Arquivos duplicados para o hash '{hash_arquivo}':")
        for idx, arquivo in enumerate(arquivos_duplicados, start=1):
            exibir_mensagem(f"{idx}. {arquivo}")
        exibir_mensagem("")
        
        escolha = int(input("Digite o número do arquivo que deseja manter: "))
        arquivos_a_manter = arquivos_duplicados[escolha - 1]
        
        for arquivo in arquivos_duplicados:
            if arquivo != arquivos_a_manter:
                os.remove(os.path.join(diretorio, arquivo))
                arquivos_excluidos.append(arquivo)  # Adiciona o arquivo excluído à lista
                exibir_mensagem(f"Arquivo '{arquivo}' removido.")
        clear_screen()  # Limpa a tela após cada interação com o usuário

# Exibe um resumo dos arquivos excluídos
exibir_mensagem("\n\nResumo dos arquivos excluídos:")
if arquivos_excluidos:
    for arquivo_excluido in arquivos_excluidos:
        exibir_mensagem(arquivo_excluido)
    exibir_mensagem(f"\nTotal de arquivos excluídos: {len(arquivos_excluidos)}")
else:
    exibir_mensagem("Nenhum arquivo duplicado foi encontrado e nenhum arquivo foi excluído.")
