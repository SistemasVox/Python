#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script.....: treeview_ascii_gui.py
Descrição..: Gera árvore de diretórios em ASCII mostrando apenas pastas
             usando CustomTkinter. Permite ignorar entradas segundo .gitignore
             (incluindo .gitignore em subpastas) e sempre pula diretórios .git.
Autor......: Marcelo José Vieira
Versão.....: 1.6 – 2025-08-06 (Corrige lógica de .gitignore para pastas com “/”)
Licença....: MIT
"""

# ===== Imports =====
import os
import customtkinter as ctk
from tkinter import filedialog, messagebox

# ===== Configurações globais =====
DEBUG = False  # defina True para ver logs no console ☺

# ===== Função de debug =====
def debug_print(msg: str):
    if DEBUG:
        print(f"[DEBUG] {msg}")

# ===== Aparência da janela =====
ctk.set_appearance_mode("System")   # "Light", "Dark" ou "System"
ctk.set_default_color_theme("blue") # "blue", "green", "dark-blue"

# ===== Utilitários de .gitignore =====
def read_gitignore_lines(dir_path: str) -> list[str]:
    gitignore_file = os.path.join(dir_path, '.gitignore')
    if not os.path.isfile(gitignore_file):
        return []
    patterns: list[str] = []
    try:
        with open(gitignore_file, 'r', encoding='utf-8') as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith('#'):
                    patterns.append(stripped)
        debug_print(f"Linhas lidas em {gitignore_file}: {len(patterns)} padrões")
    except Exception as e:
        debug_print(f"Falha lendo {gitignore_file}: {e}")
    return patterns

def make_pathspec(patterns: list[str]):
    if not patterns:
        return None
    try:
        from pathspec import PathSpec
    except ImportError:
        messagebox.showwarning(
            'Aviso',
            'Para aplicar .gitignore, instale: pip install pathspec\n'
            'Continuando sem ignorar...'
        )
        return None
    try:
        return PathSpec.from_lines('gitwildmatch', patterns)
    except Exception as e:
        debug_print(f"Erro criando PathSpec: {e}")
        return None

# ===== Geração recursiva da árvore =====
def generate_structure(path: str,
                       prefix: str = '',
                       root_path: str | None = None,
                       accumulated_patterns: list[str] | None = None,
                       apply_gitignore: bool = True) -> str:
    if root_path is None:
        root_path = path
    if accumulated_patterns is None:
        accumulated_patterns = []

    # Carrega padrões locais de .gitignore e combina
    spec = None
    combined = accumulated_patterns.copy()
    if apply_gitignore:
        rel_dir = os.path.relpath(path, root_path)
        if rel_dir == '.':
            rel_dir = ''
        # lê padrões neste nível
        for pat in read_gitignore_lines(path):
            pat_clean = pat.lstrip('/')
            new_pat = (os.path.join(rel_dir, pat_clean) if rel_dir else pat_clean).replace(os.sep, '/')
            combined.append(new_pat)
        spec = make_pathspec(combined)

    # Tenta listar itens
    try:
        entries = sorted(os.listdir(path))
    except PermissionError:
        return prefix + '└── [sem permissão]\n'
    except Exception as e:
        debug_print(f"Erro listando {path}: {e}")
        return prefix + '└── [erro ao acessar]\n'

    # Filtra apenas pastas válidas
    valid_entries: list[str] = []
    for item in entries:
        full_item = os.path.join(path, item)
        if not os.path.isdir(full_item):
            continue
        if item == '.git':
            debug_print(f"Pulei .git em {path}")
            continue

        if apply_gitignore and spec:
            rel_path = os.path.relpath(full_item, root_path).replace(os.sep, '/')
            # ===== Correção: ignora matching com e sem "/" =====
            if spec.match_file(rel_path) or spec.match_file(rel_path + '/'):
                debug_print(f"Ignorado via .gitignore -> {rel_path}")
                continue

        valid_entries.append(item)

    # Monta árvore ASCII
    tree_str = ''
    for idx, item in enumerate(valid_entries):
        full = os.path.join(path, item)
        last = (idx == len(valid_entries) - 1)
        branch = '└── ' if last else '├── '
        tree_str += f"{prefix}{branch}{item}/\n"
        ext = '    ' if last else '│   '
        tree_str += generate_structure(
            full,
            prefix=prefix + ext,
            root_path=root_path,
            accumulated_patterns=combined if apply_gitignore else [],
            apply_gitignore=apply_gitignore
        )
    return tree_str

# ===== Ações de UI =====
def select_and_show():
    path = filedialog.askdirectory()
    if not path:
        return
    entry_path.delete(0, ctk.END)
    entry_path.insert(0, path)
    apply = messagebox.askyesno(
        'Aplicar .gitignore?',
        'Ocultar pastas ignoradas pelo .gitignore?'
    )
    nome = os.path.basename(path.rstrip(os.sep))
    tree = nome + '/\n' + generate_structure(path, root_path=path, accumulated_patterns=[], apply_gitignore=apply)
    text_area.delete("0.0", ctk.END)
    text_area.insert(ctk.END, tree)

def save_to_file():
    content = text_area.get("0.0", ctk.END).strip()
    if not content:
        messagebox.showwarning('Aviso', 'Nada para salvar 😉')
        return
    fname = filedialog.asksaveasfilename(defaultextension='.txt', filetypes=[('Texto','*.txt')])
    if not fname:
        return
    try:
        with open(fname, 'w', encoding='utf-8') as f:
            f.write(content)
        messagebox.showinfo('Sucesso', f'Salvo em:\n{fname}')
    except Exception as e:
        debug_print(f"Erro salvando: {e}")
        messagebox.showerror('Erro', f'Falha ao salvar:\n{e}')

def copy_to_clipboard():
    content = text_area.get("0.0", ctk.END).strip()
    if not content:
        messagebox.showwarning('Aviso', 'Nada para copiar 😉')
        return
    try:
        app.clipboard_clear()
        app.clipboard_append(content)
        messagebox.showinfo('Copiado', 'Copiado para o clipboard 📋')
    except Exception as e:
        debug_print(f"Erro copiando: {e}")
        messagebox.showerror('Erro', f'Falha ao copiar:\n{e}')

def clear_all():
    entry_path.delete(0, ctk.END)
    text_area.delete("0.0", ctk.END)

# ===== Interface gráfica =====
app = ctk.CTk()
app.title("🌳 Árvore de Pastas (Moderno)")
app.geometry("800x600")
app.minsize(600, 400)

frame_top = ctk.CTkFrame(app, corner_radius=10, border_width=2, border_color="#888")
frame_top.pack(padx=20, pady=20, fill="x")

entry_path = ctk.CTkEntry(frame_top, placeholder_text="Caminho do diretório...", corner_radius=8, border_width=1)
entry_path.pack(side="left", expand=True, fill="x", padx=(10,5), pady=10)

btn_select = ctk.CTkButton(frame_top, text="📁 Selecionar Pasta", corner_radius=8, command=select_and_show)
btn_select.pack(side="right", padx=(5,10), pady=10)

text_area = ctk.CTkTextbox(app, corner_radius=10, border_width=1)
text_area.pack(padx=20, pady=(0,10), fill="both", expand=True)

frame_bot = ctk.CTkFrame(app, corner_radius=10, border_width=2, border_color="#888")
frame_bot.pack(padx=20, pady=(0,20), fill="x")
btn_save = ctk.CTkButton(frame_bot, text="💾 Salvar", corner_radius=8, command=save_to_file)
btn_copy = ctk.CTkButton(frame_bot, text="📋 Copiar", corner_radius=8, command=copy_to_clipboard)
btn_clear = ctk.CTkButton(frame_bot, text="🧹 Limpar", corner_radius=8, command=clear_all)
btn_exit = ctk.CTkButton(frame_bot, text="❌ Sair", corner_radius=8, fg_color="#D32F2F", hover_color="#B71C1C", command=app.quit)

btn_save.grid(row=0, column=0, padx=10, pady=10, sticky="ew")
btn_copy.grid(row=0, column=1, padx=10, pady=10, sticky="ew")
btn_clear.grid(row=0, column=2, padx=10, pady=10, sticky="ew")
btn_exit.grid(row=0, column=3, padx=10, pady=10, sticky="ew")
frame_bot.grid_columnconfigure((0,1,2,3), weight=1)

# ===== Start =====
if __name__ == "__main__":
    app.mainloop()
