import os
import hashlib
import platform
from datetime import datetime

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

hashes = {}
arquivos_excluidos = []

for entrada in os.scandir(diretorio):
    if entrada.is_file():
        hash_md5 = hashlib.md5()
        with open(entrada.path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        hash_arquivo = hash_md5.hexdigest()
        if hash_arquivo not in hashes:
            hashes[hash_arquivo] = []
        hashes[hash_arquivo].append(entrada.path)

exibir_mensagem("Verificação concluída. Removendo arquivos duplicados...")

for hash_arquivo, caminhos in hashes.items():
    if len(caminhos) > 1:
        caminhos.sort(key=os.path.getmtime)  # Ordena os arquivos por data de criação
        exibir_mensagem(f"Arquivos duplicados para o hash '{hash_arquivo}':")
        for idx, caminho in enumerate(caminhos, start=1):
            exibir_mensagem(f"{idx}. {caminho}")
        
        escolha = int(input("Digite o número do arquivo que deseja manter (ou 0 para manter o mais antigo): "))
        arquivo_a_manter = caminhos[0] if escolha == 0 else caminhos[escolha - 1]
        
        for caminho in caminhos:
            if caminho != arquivo_a_manter:
                try:
                    os.remove(caminho)
                    arquivos_excluidos.append(caminho)
                    exibir_mensagem(f"Arquivo '{caminho}' removido.")
                except OSError as e:
                    exibir_mensagem(f"Erro ao remover '{caminho}': {e.strerror}")
        clear_screen()

exibir_mensagem("Resumo dos arquivos excluídos:")
if arquivos_excluidos:
    for arquivo_excluido in arquivos_excluidos:
        exibir_mensagem(arquivo_excluido)
    exibir_mensagem(f"\nTotal de arquivos excluídos: {len(arquivos_excluidos)}")
else:
    exibir_mensagem("Nenhum arquivo duplicado foi encontrado e nenhum arquivo foi excluído.")
