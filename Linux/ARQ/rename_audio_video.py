import os
import unicodedata
from datetime import datetime

# Extensões comuns de arquivos de áudio e vídeo
MEDIA_EXTENSIONS = {
    # Vídeo
    '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.mpg', '.mpeg',
    '.3gp', '.3g2', '.m2ts', '.ts', '.vob', '.ogv', '.rm', '.rmvb', '.asf', '.divx',
    # Áudio
    '.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg', '.wma', '.opus', '.alac', '.aiff',
    '.ape', '.mid', '.midi', '.ac3', '.dts', '.wv', '.dsf', '.mka'
}

def remove_accents(input_str):
    nfkd_form = unicodedata.normalize('NFKD', input_str)
    return u"".join([c for c in nfkd_form if not unicodedata.combining(c)])

def is_media_file(filename):
    """Verifica se o arquivo é um arquivo de mídia baseado na extensão"""
    return os.path.splitext(filename)[1].lower() in MEDIA_EXTENSIONS

def rename_files_and_directories(root_dir):
    print(f"Iniciando processo de renomeação em: {root_dir}")
    print(f"Hora início: {datetime.now().strftime('%H:%M:%S')}")
    
    try:
        total_renamed_files = 0
        total_renamed_dirs = 0
        
        # Percorre o diretório de baixo para cima (bottom-up)
        for root, dirs, files in os.walk(root_dir, topdown=False):
            # Renomeia apenas arquivos de mídia
            for name in files:
                if is_media_file(name):
                    new_name = remove_accents(name).replace(' ', '_')
                    if new_name != name:
                        old_path = os.path.join(root, name)
                        new_path = os.path.join(root, new_name)
                        os.rename(old_path, new_path)
                        total_renamed_files += 1
            
            # Renomeia todos os diretórios
            for name in dirs:
                new_name = remove_accents(name).replace(' ', '_')
                if new_name != name:
                    old_path = os.path.join(root, name)
                    new_path = os.path.join(root, new_name)
                    os.rename(old_path, new_path)
                    total_renamed_dirs += 1
        
        print(f"\nProcesso concluído com sucesso!")
        print(f"Total de arquivos de mídia renomeados: {total_renamed_files}")
        print(f"Total de diretórios renomeados: {total_renamed_dirs}")
        print(f"Hora término: {datetime.now().strftime('%H:%M:%S')}")
        
    except Exception as e:
        print(f"\nErro durante o processo: {str(e)}")
        print(f"Hora do erro: {datetime.now().strftime('%H:%M:%S')}")

if __name__ == '__main__':
    root_directory = os.path.dirname(os.path.abspath(__file__))
    rename_files_and_directories(root_directory)