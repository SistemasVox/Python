#!/usr/bin/env python3
"""
rename_media.py
Renomeia arquivos de mídia (áudio/vídeo) e diretórios removendo acentos
e substituindo espaços por "_".
Uso:
    python rename_media.py /caminho/para/pasta [--dry-run]
"""
import argparse
import logging
import os
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path

# Extensões reconhecidas
MEDIA_EXTENSIONS = {
    '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.mpg', '.mpeg',
    '.3gp', '.3g2', '.m2ts', '.ts', '.vob', '.ogv', '.rm', '.rmvb', '.asf', '.divx',
    '.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg', '.wma', '.opus', '.alac', '.aiff',
    '.ape', '.mid', '.midi', '.ac3', '.dts', '.wv', '.dsf', '.mka'
}

LOG_FILE = "renamer.log"

# -----------------------------------------------------------
# Configuração do logging
# -----------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)

def remove_accents(text: str) -> str:
    nfkd = unicodedata.normalize('NFKD', text)
    return ''.join(c for c in nfkd if not unicodedata.combining(c))

def is_media_file(fname: str) -> bool:
    return Path(fname).suffix.lower() in MEDIA_EXTENSIONS

def safe_rename(src: Path, dst: Path, dry_run: bool = False) -> None:
    """Renomeia src para dst, garantindo unicidade de nome."""
    if src.resolve() == dst.resolve():
        return  # já está correto

    counter = 1
    final_dst = dst
    while final_dst.exists():
        final_dst = dst.with_stem(f"{dst.stem}_{counter}")
        counter += 1

    logging.info(f"{'[DRY-RUN] ' if dry_run else ''}{src} -> {final_dst}")
    if not dry_run:
        shutil.move(str(src), str(final_dst))

def rename_tree(root_dir: Path, dry_run: bool) -> None:
    logging.info("Iniciando renomeação em: %s", root_dir)
    logging.info("Modo dry-run: %s", dry_run)

    renamed_files = 0
    renamed_dirs = 0

    # bottom-up para evitar renomear pastas antes de arquivos internos
    for root, dirs, files in os.walk(root_dir, topdown=False):
        # Arquivos
        for fname in files:
            if not is_media_file(fname):
                continue
            src = Path(root) / fname
            new_name = remove_accents(fname).replace(' ', '_')
            dst = src.with_name(new_name)
            if src.name != new_name:
                safe_rename(src, dst, dry_run)
                renamed_files += 1

        # Diretórios
        for dname in dirs:
            src = Path(root) / dname
            new_name = remove_accents(dname).replace(' ', '_')
            dst = src.with_name(new_name)
            if src.name != new_name:
                safe_rename(src, dst, dry_run)
                renamed_dirs += 1

    logging.info("Processo finalizado. Arquivos renomeados: %d | Pastas renomeadas: %d",
                 renamed_files, renamed_dirs)

def main():
    parser = argparse.ArgumentParser(description="Renomeia arquivos de mídia e pastas.")
    parser.add_argument("directory", nargs="?", default=".",
                        help="Diretório-alvo (padrão: pasta do script)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Simula as mudanças sem executar")
    args = parser.parse_args()

    target = Path(args.directory).expanduser().resolve()
    if not target.is_dir():
        logging.error("Caminho inválido: %s", target)
        return

    rename_tree(target, args.dry_run)

if __name__ == "__main__":
    main()