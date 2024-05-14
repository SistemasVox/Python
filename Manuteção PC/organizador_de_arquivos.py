import os
import shutil
import threading

def mover_arquivos():
    try:
        num_arquivos_por_pasta = int(input("Digite o número de arquivos por pasta: "))
        diretorio = os.getcwd()
        arquivos = sorted([entry.path for entry in os.scandir(diretorio) if entry.is_file()], key=os.path.getctime)
        total_arquivos = len(arquivos)
        
        if total_arquivos == 0:
            print("O diretório está vazio ou contém apenas pastas. Não é necessário organizar.")
            return

        num_pastas = total_arquivos // num_arquivos_por_pasta
        if total_arquivos % num_arquivos_por_pasta:
            num_pastas += 1

        print(f"Organizando {total_arquivos} arquivos em {num_pastas} pastas...")

        for i in range(num_pastas):
            subdiretorio = os.path.join(diretorio, f'parte_{i + 1}')
            os.makedirs(subdiretorio, exist_ok=True)
            for arquivo in arquivos[i * num_arquivos_por_pasta:(i + 1) * num_arquivos_por_pasta]:
                # Cria uma nova thread para mover cada arquivo
                threading.Thread(target=shutil.move, args=(arquivo, os.path.join(subdiretorio, os.path.basename(arquivo)))).start()
                print(f"Movendo {arquivo} para {subdiretorio}...")

        print("Todos os arquivos foram organizados com sucesso!")

    except ValueError:
        print("Por favor, insira um número válido.")
    except FileNotFoundError:
        print("Arquivo não encontrado.")
    except PermissionError:
        print("Permissão negada.")
    except OSError as e:
        if e.errno == 28:
            print("Espaço em disco insuficiente.")
        else:
            print("Ocorreu um erro: ", e)

# Uso
mover_arquivos()
