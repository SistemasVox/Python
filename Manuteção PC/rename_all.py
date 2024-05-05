import os
import random
import string
from datetime import datetime

def get_current_time():
    return datetime.now().strftime('%H:%M:%S')

def rename_files(directory):
    os.system('cls' if os.name == 'nt' else 'clear')
    print(f"{get_current_time()}: Iniciando a renomeação dos arquivos em {directory}...")

    for filename in os.listdir(directory):
        extension = os.path.splitext(filename)[1]
        new_name = ''.join(random.choices(string.ascii_letters + string.digits, k=16)) + extension
        source = os.path.join(directory, filename)
        destination = os.path.join(directory, new_name)

        os.rename(source, destination)

    print(f"{get_current_time()}: Renomeação dos arquivos concluída.")

rename_files('C:/Users/Marcelo/Pictures/SGs')
