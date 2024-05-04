import os
import re
from tkinter import filedialog
from tkinter import Tk, simpledialog
from unidecode import unidecode

def filter_file():
    # Crie uma janela raiz Tk e oculte-a
    root = Tk()
    root.withdraw()

    # Abra uma caixa de diálogo de arquivo e obtenha o caminho do arquivo
    file_path = filedialog.askopenfilename()

    # Verifique se um arquivo foi selecionado
    if not file_path:
        print("Nenhum arquivo selecionado.")
        return

    # Solicite ao usuário a palavra-chave para filtrar
    keyword = simpledialog.askstring("Entrada", "Digite a palavra-chave para filtrar:")

    # Converta a palavra-chave para minúsculas e remova os acentos
    keyword = unidecode(keyword.lower())

    # Abra o arquivo original com a codificação correta
    with open(file_path, 'r', encoding='utf-8') as file:
        lines = file.readlines()

    # Inicialize a lista de linhas filtradas
    filtered_lines = []

    # Percorra cada linha no arquivo
    for i in range(len(lines)):
        # Se a linha começa com '#EXTINF', ela contém o nome do canal
        if lines[i].startswith('#EXTINF'):
            # Extraia o nome do canal da linha
            start = lines[i].find('tvg-name=\"') + len('tvg-name=\"')
            end = lines[i].find('\"', start)
            name = lines[i][start:end]

            # Converta o nome do canal para minúsculas e remova os acentos
            name = unidecode(name.lower())

            # Se a palavra-chave estiver no nome do canal, adicione a linha e a próxima linha à lista filtrada
            if keyword in name:
                filtered_lines.append(lines[i])
                if i+1 < len(lines):
                    filtered_lines.append(lines[i+1])

    # Verifique se a palavra-chave foi encontrada
    if not filtered_lines:
        print(f"Nenhum canal encontrado com a palavra-chave '{keyword}'.")
        return

    # Salve as linhas filtradas
    # Remova caracteres especiais e substitua caracteres acentuados na palavra-chave
    filename = re.sub(r'\W+', '_', unidecode(keyword))
    with open(f'{os.path.splitext(file_path)[0]}_{filename}.m3u', 'w', encoding='utf-8') as new_file:
        new_file.write("#EXTM3U\n")  # Adicione a linha "#EXTM3U" no início do arquivo
        new_file.writelines(filtered_lines)

    # Imprima uma mensagem de sucesso
    print("O arquivo foi filtrado com sucesso.")

# Chame a função para filtrar o arquivo
filter_file()
