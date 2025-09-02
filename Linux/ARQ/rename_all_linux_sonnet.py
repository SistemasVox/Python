#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

def remove_accents(input_str):
    """Remove acentos de uma string."""
    nfkd_form = unicodedata.normalize('NFKD', input_str)
    return "".join([c for c in nfkd_form if not unicodedata.combining(c)])

def sanitize_name(name):
    """Sanitiza um nome removendo acentos e substituindo espaços."""
    # Remove acentos
    clean_name = remove_accents(name)
    # Substitui espaços por underscore
    clean_name = clean_name.replace(' ', '_')
    # Remove caracteres problemáticos adicionais se necessário
    # clean_name = re.sub(r'[<>:"/\\|?*]', '_', clean_name)
    return clean_name

def get_unique_name(directory, desired_name):
    """Garante que o nome seja único no diretório."""
    base_path = Path(directory)
    new_path = base_path / desired_name
    
    if not new_path.exists():
        return desired_name
    
    # Se existe, adiciona um número
    name_parts = desired_name.rsplit('.', 1) if '.' in desired_name else [desired_name]
    base_name = name_parts[0]
    extension = f".{name_parts[1]}" if len(name_parts) > 1 else ""
    
    counter = 1
    while True:
        new_name = f"{base_name}_{counter}{extension}"
        if not (base_path / new_name).exists():
            return new_name
        counter += 1

def preview_changes(root_dir):
    """Mostra uma prévia das mudanças que serão feitas."""
    changes = []
    
    for root, dirs, files in os.walk(root_dir, topdown=False):
        # Verifica arquivos
        for name in files:
            new_name = sanitize_name(name)
            if new_name != name:
                unique_name = get_unique_name(root, new_name)
                if unique_name != new_name:
                    print(f"⚠️  Conflito detectado: {new_name} → {unique_name}")
                changes.append((os.path.join(root, name), os.path.join(root, unique_name), 'file'))
        
        # Verifica diretórios
        for name in dirs:
            new_name = sanitize_name(name)
            if new_name != name:
                unique_name = get_unique_name(root, new_name)
                if unique_name != new_name:
                    print(f"⚠️  Conflito detectado: {new_name} → {unique_name}")
                changes.append((os.path.join(root, name), os.path.join(root, unique_name), 'directory'))
    
    return changes

def rename_files_and_directories(root_dir, dry_run=False):
    """
    Renomeia arquivos e diretórios removendo acentos e espaços.
    
    Args:
        root_dir (str): Diretório raiz para começar a renomeação
        dry_run (bool): Se True, apenas mostra o que seria feito
    """
    print(f"{'[SIMULAÇÃO] ' if dry_run else ''}Iniciando processo de renomeação em: {root_dir}")
    print(f"Hora início: {datetime.now().strftime('%H:%M:%S')}")
    
    # Verifica se o diretório existe
    if not os.path.exists(root_dir):
        print(f"❌ Erro: Diretório '{root_dir}' não existe!")
        return
    
    # Verifica permissões
    if not os.access(root_dir, os.W_OK):
        print(f"❌ Erro: Sem permissão de escrita em '{root_dir}'!")
        return
    
    try:
        renamed_count = 0
        error_count = 0
        
        # Percorre o diretório de baixo para cima
        for root, dirs, files in os.walk(root_dir, topdown=False):
            # Renomeia arquivos
            for name in files:
                new_name = sanitize_name(name)
                if new_name != name:
                    old_path = os.path.join(root, name)
                    unique_name = get_unique_name(root, new_name)
                    new_path = os.path.join(root, unique_name)
                    
                    try:
                        if not dry_run:
                            os.rename(old_path, new_path)
                        print(f"📄 {name} → {unique_name}")
                        renamed_count += 1
                    except Exception as e:
                        print(f"❌ Erro ao renomear arquivo '{name}': {e}")
                        error_count += 1
            
            # Renomeia diretórios
            for name in dirs:
                new_name = sanitize_name(name)
                if new_name != name:
                    old_path = os.path.join(root, name)
                    unique_name = get_unique_name(root, new_name)
                    new_path = os.path.join(root, unique_name)
                    
                    try:
                        if not dry_run:
                            os.rename(old_path, new_path)
                        print(f"📁 {name} → {unique_name}")
                        renamed_count += 1
                    except Exception as e:
                        print(f"❌ Erro ao renomear diretório '{name}': {e}")
                        error_count += 1
        
        print(f"\n{'[SIMULAÇÃO] ' if dry_run else ''}Processo concluído!")
        print(f"✅ Total de itens {'que seriam ' if dry_run else ''}renomeados: {renamed_count}")
        if error_count > 0:
            print(f"❌ Total de erros: {error_count}")
        print(f"Hora término: {datetime.now().strftime('%H:%M:%S')}")
        
    except KeyboardInterrupt:
        print(f"\n⏹️  Processo interrompido pelo usuário!")
    except Exception as e:
        print(f"\n❌ Erro geral durante o processo: {e}")

def main():
    """Função principal com interface de linha de comando."""
    if len(sys.argv) < 2:
        print("📋 Uso:")
        print(f"  {sys.argv[0]} <diretório> [--preview|--dry-run]")
        print(f"  {sys.argv[0]} . --preview  # Mostra o que seria alterado")
        print(f"  {sys.argv[0]} /caminho/para/pasta  # Executa a renomeação")
        return
    
    target_dir = sys.argv[1]
    
    # Resolve o caminho
    if target_dir == '.':
        target_dir = os.getcwd()
    else:
        target_dir = os.path.abspath(target_dir)
    
    # Verifica flags
    preview_mode = '--preview' in sys.argv or '--dry-run' in sys.argv
    
    if preview_mode:
        print("🔍 Modo prévia - mostrando mudanças que seriam feitas:")
        changes = preview_changes(target_dir)
        if not changes:
            print("✅ Nenhuma mudança necessária!")
        else:
            print(f"\n📊 Total de mudanças: {len(changes)}")
            confirm = input("\n❓ Deseja executar essas mudanças? (s/N): ").lower()
            if confirm in ['s', 'sim', 'y', 'yes']:
                rename_files_and_directories(target_dir, dry_run=False)
    else:
        # Confirmação de segurança
        print(f"⚠️  ATENÇÃO: Isso vai renomear TODOS os arquivos e pastas em: {target_dir}")
        confirm = input("❓ Tem certeza que deseja continuar? (digite 'CONFIRMO'): ")
        if confirm == 'CONFIRMO':
            rename_files_and_directories(target_dir)
        else:
            print("❌ Operação cancelada.")

if __name__ == '__main__':
    main()