import os
import unicodedata
from datetime import datetime

def remove_accents(input_str):
    nfkd_form = unicodedata.normalize('NFKD', input_str)
    return u"".join([c for c in nfkd_form if not unicodedata.combining(c)])

def rename_files_and_directories(root_dir):
    print(f"Iniciando processo de renomeação em: {root_dir}")
    print(f"Hora início: {datetime.now().strftime('%H:%M:%S')}")
    
    try:
        total_renamed = 0
        # Percorre o diretório de baixo para cima (bottom-up)
        for root, dirs, files in os.walk(root_dir, topdown=False):
            # Renomeia todos os arquivos
            for name in files:
                new_name = remove_accents(name).replace(' ', '_')
                if new_name != name:
                    old_path = os.path.join(root, name)
                    new_path = os.path.join(root, new_name)
                    os.rename(old_path, new_path)
                    total_renamed += 1
            
            # Renomeia todos os diretórios
            for name in dirs:
                new_name = remove_accents(name).replace(' ', '_')
                if new_name != name:
                    old_path = os.path.join(root, name)
                    new_path = os.path.join(root, new_name)
                    os.rename(old_path, new_path)
                    total_renamed += 1
        
        print(f"\nProcesso concluído com sucesso!")
        print(f"Total de itens renomeados: {total_renamed}")
        print(f"Hora término: {datetime.now().strftime('%H:%M:%S')}")
        
    except Exception as e:
        print(f"\nErro durante o processo: {str(e)}")
        print(f"Hora do erro: {datetime.now().strftime('%H:%M:%S')}")

if __name__ == '__main__':
    root_directory = os.path.dirname(os.path.abspath(__file__))
    rename_files_and_directories(root_directory)