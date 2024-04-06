import os
import random
import shutil
import platform
import string
import secrets
import time

# Constantes
DEFAULT_NUM_PHOTOS = 1000
RANDOM_NAME_LENGTH = 8

# Limpe a tela com base no sistema operacional
def clear_screen():
    if platform.system() == 'Windows':
        os.system('cls')
    else:
        os.system('clear')

def select_files(file_info, num_photos):
    # Embaralhe a lista de arquivos com random
    for _ in range(10):  # Embaralha a lista 10 vezes
        random.shuffle(file_info)

    # Embaralhe a lista de arquivos de forma mais segura com secrets
    file_info = sorted(file_info, key=lambda x: secrets.randbelow(len(file_info)))

    # Calcula o peso de cada arquivo com base na diferença entre a data atual e a data de último acesso
    current_time = time.time()
    weights = [current_time - atime for _, atime in file_info]

    # Seleciona os arquivos com base nos pesos
    selected_files = random.choices([f for f, _ in file_info], weights=weights, k=num_photos)
    
    return selected_files

clear_screen()

# Obtenha o diretório base (diretório atual)
base_dir = os.getcwd()
print(f'Diretório base: {base_dir}')

# Defina os diretórios de origem e destino
source_dir = os.path.join(base_dir, 'SGs')
destination_dir = os.path.join(base_dir, 'SGs_temp')

# Arquivo para armazenar os últimos arquivos selecionados
last_selected_file = os.path.join(base_dir, 'last_selected.txt')

# Obtenha o número de fotos a serem copiadas do usuário
num_photos_input = input('Digite o número de fotos a serem copiadas (padrão 1000): ').strip()
num_photos = int(num_photos_input) if num_photos_input.isdigit() else DEFAULT_NUM_PHOTOS

# Liste todos os arquivos no diretório de origem e obtenha a data de último acesso
print(f'Listando arquivos no diretório de origem: {source_dir}')
file_info = [(f, os.path.getatime(os.path.join(source_dir, f))) for f in os.listdir(source_dir) if os.path.isfile(os.path.join(source_dir, f))]

# Seleciona os arquivos
selected_files = select_files(file_info, num_photos)

# Remova os arquivos selecionados anteriormente, se existirem
if os.path.exists(last_selected_file):
    with open(last_selected_file, 'r') as file:
        last_selected = file.read().splitlines()
    selected_files = [f for f in selected_files if f not in last_selected]

# Verifique se há arquivos suficientes para a seleção
if len(selected_files) < num_photos:
    print(f'Aviso: Apenas {len(selected_files)} arquivos estão disponíveis para seleção após remover os selecionados anteriormente.')
    num_photos = len(selected_files)

# Salve os nomes dos arquivos selecionados
with open(last_selected_file, 'w') as file:
    file.write('\n'.join(selected_files))

# Remova o diretório destino se ele existir e crie um novo
if os.path.exists(destination_dir):
    print(f'Removendo o diretório existente: {destination_dir}')
    try:
        shutil.rmtree(destination_dir)
    except OSError as e:
        print(f'Erro: {e.filename} - {e.strerror}.')
print(f'Criando o diretório de destino: {destination_dir}')
os.makedirs(destination_dir)

# Copie os arquivos selecionados para o diretório destino com nomes randômicos
print(f'Copiando {len(selected_files)} arquivos para o diretório de destino com nomes randômicos...')
for file in selected_files:
    # Gere um nome randômico de 8 dígitos contendo números de 0 a 9 e letras de A a Z
    random_name = ''.join(random.choices(string.ascii_letters + string.digits, k=RANDOM_NAME_LENGTH))
    # Adicione a extensão do arquivo ao nome randômico
    file_name, file_extension = os.path.splitext(file)
    random_name += file_extension
    # Copie o arquivo com o nome randômico
    shutil.copy(os.path.join(source_dir, file), os.path.join(destination_dir, random_name))

print(f'{len(selected_files)} fotos foram copiadas para {destination_dir}')
