import os
import re
from tkinter import filedialog
from tkinter import Tk, simpledialog
from unidecode import unidecode

def split_file():
    # Crie uma janela raiz Tk e oculte-a
    root = Tk()
    root.withdraw()

    # Abra uma caixa de diálogo de arquivo e obtenha o caminho do arquivo
    file_path = filedialog.askopenfilename()

    # Verifique se um arquivo foi selecionado
    if not file_path:
        print("Nenhum arquivo selecionado.")
        return

    # Abra o arquivo original com a codificação correta
    with open(file_path, 'r', encoding='utf-8') as file:
        lines = file.readlines()

    # Inicialize o grupo atual e a lista de linhas do grupo
    current_group = None
    group_lines = []

    # Dicionário para armazenar os grupos
    groups = {}

    # Percorra cada linha no arquivo
    for line in lines:
        # Se a linha começa com '#EXTINF', ela contém o nome do grupo
        if line.startswith('#EXTINF'):
            # Extraia o nome do grupo da linha
            start = line.find('group-title=\"') + len('group-title=\"')
            end = line.find('\"', start)
            group = line[start:end]

            # Atualize o grupo atual
            current_group = group

        # Adicione a linha ao grupo atual
        if current_group:
            if current_group in groups:
                groups[current_group].append(line)
            else:
                groups[current_group] = [line]

    # Salve os grupos
    for group, lines in groups.items():
        # Remova caracteres especiais e substitua caracteres acentuados
        filename = re.sub(r'\W+', '_', unidecode(group))
        with open(f'{os.path.splitext(file_path)[0]}_{filename}.m3u', 'w', encoding='utf-8') as new_file:
            new_file.write("#EXTM3U\n")  # Adicione a linha "#EXTM3U" no início do arquivo
            new_file.writelines(lines)

    # Imprima uma mensagem de sucesso
    print("O arquivo foi dividido com sucesso.")

# Chame a função para dividir o arquivo
split_file()
