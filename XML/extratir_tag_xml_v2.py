import tkinter as tk
from tkinter import filedialog
import xml.etree.ElementTree as ET
import os
import re

# Limpar a tela
def clear_terminal():
    os.system('cls' if os.name == 'nt' else 'clear')

clear_terminal()

# Cria uma janela principal
root = tk.Tk()
root.withdraw()

# Abre uma janela de seleção de arquivo
file_path = filedialog.askopenfilename()

# Faz o parsing do arquivo XML e armazena a raiz do documento
tree = ET.parse(file_path)
root = tree.getroot()

# Define as tags a serem extraídas do arquivo XML
tags_para_extrair = input('Digite as tags que deseja extrair (separadas por vírgula): ')
tags_para_extrair = tags_para_extrair.split(',')

# Função que percorre a árvore do XML e extrai os dados das tags selecionadas
def extrair_dados(elemento, dados):
    # Verifica se a tag do elemento está na lista de tags para extrair
    if elemento.tag in tags_para_extrair:
        # Adiciona os dados do elemento à lista
        dados.append(elemento.text)

    # Percorre os filhos do elemento
    for filho in elemento:
        # Chama a função recursivamente para cada filho
        extrair_dados(filho, dados)

# Extrai os dados das tags selecionadas
dados = []
extrair_dados(root, dados)

# Cria o arquivo de texto para salvar as informações extraídas
nome_arquivo = os.path.splitext(os.path.basename(file_path))[0]
arquivo_saida = f'{nome_arquivo}_dados.txt'

# Exclui o arquivo de texto de saída, caso ele já exista
if os.path.exists(arquivo_saida):
    os.remove(arquivo_saida)

# Salva as informações extraídas no arquivo de texto
with open(arquivo_saida, 'w') as f:
    for dado in dados:
        # Verifica se o dado é uma imagem no formato JPG, PNG, JPEG, GIF ou BMP
        if re.search(r'\.(jpg|jpeg|png|gif|bmp)$', dado, re.IGNORECASE):
            # Formata a saída com uma tag HTML <img>
            f.write(f'<img src="{dado}" alt="Imagem">\n')
        else:
            f.write(f'{dado}\n')

# Mostra as informações extraídas na saída padrão do console
print('Dados das tags selecionadas:')
print('-' * 30)
for dado in dados:
    if re.search(r'\.(jpg|jpeg|png|gif|bmp)$', dado, re.IGNORECASE):
        print(f'<img src="{dado}" alt="Imagem">')
    else:
        print(dado)

print(f'\nAs informações foram salvas no arquivo "{arquivo_saida}".')
abs_arquivo_saida = os.path.abspath(arquivo_saida)
print(f'\nAs informações foram salvas no arquivo "{abs_arquivo_saida}".')