#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Nome do Script: treeview_ascii_gui.py
Descrição: Gera árvore de diretórios em ASCII com interface gráfica moderna usando CustomTkinter,
           perguntando se deve ignorar arquivos/pastas conforme .gitignore, incluindo .gitignore aninhados,
           e pulando automaticamente qualquer pasta chamada .git.
Autor: Marcelo Vieira
Data: 2025-04-08 (adaptado em 2025-06-15)
Versão: 1.3

Uso:
    python3 treeview_ascii_gui.py

Requisitos:
    - Python 3.x
    - customtkinter (pip install customtkinter)
    - pathspec      (pip install pathspec)

Licença:
    MIT License
"""

# ===== Imports =====
import os
import customtkinter as ctk
from tkinter import filedialog, messagebox

# ===== Configurações globais =====
DEBUG = False  # defina True para ver logs no console ☺

def debug_print(msg: str):
    if DEBUG:
        print(f"[DEBUG] {msg}")

# Aparência da janela
ctk.set_appearance_mode("System")   # "Light", "Dark" ou "System"
ctk.set_default_color_theme("blue") # "blue", "green", "dark-blue"

# ===== Utilidades para .gitignore =====
def read_gitignore_lines(dir_path: str) -> list[str]:
    """
    Lê .gitignore dentro de dir_path e devolve somente linhas de padrão válidas.
    """
    gitignore_file = os.path.join(dir_path, '.gitignore')
    if not os.path.isfile(gitignore_file):
        return []
    try:
        with open(gitignore_file, 'r', encoding='utf-8') as f:
            raw = f.readlines()
    except Exception as exc:
        debug_print(f"Falha lendo {gitignore_file}: {exc}")
        return []

    patterns = []
    for line in raw:
        stripped = line.strip()
        if stripped and not stripped.startswith('#'):
            patterns.append(stripped)
    debug_print(f"{len(patterns)} padrões lidos de {gitignore_file}")
    return patterns

def make_pathspec(patterns: list[str]):
    """
    Cria objeto PathSpec a partir de padrões gitwildmatch. Retorna None se falhar.
    """
    if not patterns:
        return None
    try:
        import pathspec
    except ImportError:
        messagebox.showwarning(
            'Aviso',
            'Para aplicar .gitignore, instale a biblioteca pathspec:\n\n'
            'pip install pathspec\n\n'
            'Continuando sem ignorar...'
        )
        return None
    try:
        return pathspec.PathSpec.from_lines('gitwildmatch', patterns)
    except Exception as exc:
        debug_print(f"Erro criando PathSpec: {exc}")
        return None

# ===== Geração recursiva da árvore =====
def generate_structure(path: str,
                       prefix: str = '',
                       root_path: str | None = None,
                       accumulated_patterns: list[str] | None = None) -> str:
    """
    Monta string ASCII da árvore a partir de 'path'.
    * root_path: pasta raiz onde a árvore começou.
    * accumulated_patterns: padrões já coletados de .gitignore em níveis acima.
    """
    if root_path is None:
        root_path = path
    if accumulated_patterns is None:
        accumulated_patterns = []

    tree_str = ''

    # --- carrega padrões do .gitignore deste diretório, se existir ---
    rel_dir = os.path.relpath(path, root_path)
    if rel_dir == '.':
        rel_dir = ''

    local_patterns = []
    for pat in read_gitignore_lines(path):
        # Remove barra inicial e prefixa caminho relativo, se houver
        pat_no_slash = pat.lstrip('/')
        if rel_dir:
            new_pat = os.path.join(rel_dir, pat_no_slash).replace(os.sep, '/')
        else:
            new_pat = pat_no_slash.replace(os.sep, '/')
        local_patterns.append(new_pat)

    combined_patterns = accumulated_patterns + local_patterns
    spec = make_pathspec(combined_patterns)

    # --- lista conteúdos do diretório ---
    try:
        entries = sorted(os.listdir(path))
    except PermissionError:
        return prefix + '└── [sem permissão]\n'
    except Exception as exc:
        debug_print(f"Erro listando {path}: {exc}")
        return prefix + '└── [erro ao acessar]\n'

    for idx, item in enumerate(entries):
        # pular sempre diretórios .git
        if item == '.git':
            debug_print(f"Pulei .git em {path}")
            continue

        full = os.path.join(path, item)
        try:
            rel_path = os.path.relpath(full, root_path).replace(os.sep, '/')
        except Exception:
            rel_path = item

        # pular se bater no spec
        if spec and spec.match_file(rel_path):
            debug_print(f"Ignorado via .gitignore -> {rel_path}")
            continue

        last = (idx == len(entries) - 1)
        branch = '└── ' if last else '├── '
        display_name = item + ('/' if os.path.isdir(full) else '')
        tree_str += prefix + branch + display_name + '\n'

        if os.path.isdir(full):
            ext = '    ' if last else '│   '
            tree_str += generate_structure(
                full,
                prefix=prefix + ext,
                root_path=root_path,
                accumulated_patterns=combined_patterns
            )
    return tree_str

# ===== Ações dos botões =====
def select_and_show():
    path = filedialog.askdirectory()
    if not path:
        return
    entry_path.delete(0, ctk.END)
    entry_path.insert(0, path)

    use_ignore = messagebox.askyesno(
        'Ignorar .gitignore?',
        'Deseja aplicar as regras de .gitignore (incluindo subpastas)?'
    )
    patterns_start = [] if use_ignore else []

    nome_base = os.path.basename(path.rstrip(os.sep))
    tree_text = nome_base + '/\n' + generate_structure(
        path,
        root_path=path,
        accumulated_patterns=patterns_start
    )

    text_area.delete("0.0", ctk.END)
    text_area.insert(ctk.END, tree_text)

def save_to_file():
    content = text_area.get("0.0", ctk.END).strip()
    if not content:
        messagebox.showwarning('Aviso', 'Nada pra salvar 😉')
        return
    fname = filedialog.asksaveasfilename(
        defaultextension='.txt',
        filetypes=[('Texto', '*.txt')],
        title='Salvar árvore como...'
    )
    if not fname:
        return
    try:
        with open(fname, 'w', encoding='utf-8') as fp:
            fp.write(content)
        messagebox.showinfo('Sucesso', f'Salvo em:\n{fname}')
    except Exception as exc:
        debug_print(f"Erro salvando arquivo: {exc}")
        messagebox.showerror('Erro', f'Falha ao salvar:\n{exc}')

def copy_to_clipboard():
    content = text_area.get("0.0", ctk.END).strip()
    if not content:
        messagebox.showwarning('Aviso', 'Nada pra copiar 😉')
        return
    try:
        app.clipboard_clear()
        app.clipboard_append(content)
        messagebox.showinfo('Copiado', 'Copiado para o clipboard 📋')
    except Exception as exc:
        debug_print(f"Erro ao copiar: {exc}")
        messagebox.showerror('Erro', f'Falha ao copiar:\n{exc}')

def clear_all():
    entry_path.delete(0, ctk.END)
    text_area.delete("0.0", ctk.END)

# ===== Interface gráfica =====
app = ctk.CTk()
app.title("🌳 Árvore de Diretórios (Moderno)")
app.geometry("800x600")
app.minsize(600, 400)

frame_top = ctk.CTkFrame(app, corner_radius=10, border_width=2, border_color="#888")
frame_top.pack(padx=20, pady=20, fill="x")

entry_path = ctk.CTkEntry(
    frame_top,
    placeholder_text="Caminho do diretório...",
    corner_radius=8,
    border_width=1
)
entry_path.pack(side="left", expand=True, fill="x", padx=(10, 5), pady=10)

btn_select = ctk.CTkButton(
    frame_top,
    text="📁 Selecionar Pasta",
    corner_radius=8,
    command=select_and_show
)
btn_select.pack(side="right", padx=(5, 10), pady=10)

text_area = ctk.CTkTextbox(app, corner_radius=10, border_width=1)
text_area.pack(padx=20, pady=(0, 10), fill="both", expand=True)

frame_bot = ctk.CTkFrame(app, corner_radius=10, border_width=2, border_color="#888")
frame_bot.pack(padx=20, pady=(0, 20), fill="x")

btn_save = ctk.CTkButton(frame_bot, text="💾 Salvar", corner_radius=8, command=save_to_file)
btn_save.grid(row=0, column=0, padx=10, pady=10, sticky="ew")

btn_copy = ctk.CTkButton(frame_bot, text="📋 Copiar", corner_radius=8, command=copy_to_clipboard)
btn_copy.grid(row=0, column=1, padx=10, pady=10, sticky="ew")

btn_clear = ctk.CTkButton(frame_bot, text="🧹 Limpar", corner_radius=8, command=clear_all)
btn_clear.grid(row=0, column=2, padx=10, pady=10, sticky="ew")

btn_exit = ctk.CTkButton(
    frame_bot,
    text="❌ Sair",
    corner_radius=8,
    fg_color="#D32F2F",
    hover_color="#B71C1C",
    command=app.quit
)
btn_exit.grid(row=0, column=3, padx=10, pady=10, sticky="ew")

frame_bot.grid_columnconfigure((0, 1, 2, 3), weight=1)

# ===== Start =====
if __name__ == "__main__":
    app.mainloop()
