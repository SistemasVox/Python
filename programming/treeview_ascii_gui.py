#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Nome do Script: treeview_ascii_gui.py
Descrição: Gera árvore de diretórios em ASCII com interface gráfica moderna usando CustomTkinter.
Autor: Marcelo José Vieira
Data: 2025-04-08
Versão: 1.0

Uso:
    python treeview_ascii_gui.py

Requisitos:
    - Python 3.x
    - customtkinter (pip install customtkinter)

Licença:
    MIT License
"""

import os
import customtkinter as ctk
from tkinter import filedialog, messagebox

# Configuração do tema
ctk.set_appearance_mode("System")   # "Light", "Dark" ou "System"
ctk.set_default_color_theme("blue") # temas: "blue", "green", "dark-blue"

def gerar_estrutura(path, prefix=''):
    tree_str = ''
    try:
        entries = sorted(os.listdir(path))
    except PermissionError:
        return prefix + '└── [sem permição]\n'
    for idx, item in enumerate(entries):
        full = os.path.join(path, item)
        last = (idx == len(entries) - 1)
        branch = '└── ' if last else '├── '
        tree_str += prefix + branch + item + ('/' if os.path.isdir(full) else '') + '\n'
        if os.path.isdir(full):
            ext = '    ' if last else '│   '
            tree_str += gerar_estrutura(full, prefix + ext)
    return tree_str

def selecionar_e_mostrar():
    path = filedialog.askdirectory()
    if not path:
        return
    entry_path.delete(0, ctk.END)
    entry_path.insert(0, path)
    texto = os.path.basename(path) + '/\n' + gerar_estrutura(path)
    text_area.delete("0.0", ctk.END)
    text_area.insert(ctk.END, texto)

def salvar_em_arquivo():
    txt = text_area.get("0.0", ctk.END).strip()
    if not txt:
        messagebox.showwarning('Aviso', 'Nada pra salvar 😉')
        return
    file = filedialog.asksaveasfilename(
        defaultextension='.txt',
        filetypes=[('Texto','*.txt')],
        title='Salvar árvore como...'
    )
    if not file:
        return
    with open(file, 'w', encoding='utf-8') as f:
        f.write(txt)
    messagebox.showinfo('Sucesso', f'Salvo em:\n{file}')

def copiar_para_clipboard():
    txt = text_area.get("0.0", ctk.END).strip()
    if not txt:
        messagebox.showwarning('Aviso', 'Nada pra copiar 😉')
        return
    app.clipboard_clear()
    app.clipboard_append(txt)
    messagebox.showinfo('Copiado', 'Copiado para o clipboard 📋')

def limpar_tudo():
    entry_path.delete(0, ctk.END)
    text_area.delete("0.0", ctk.END)

# --- Interface ---
app = ctk.CTk()
app.title("🌳 Árvore de Diretórios (Moderno)")
app.geometry("800x600")
app.minsize(600, 400)

# Frame topo com sombra e borda arredondada
frame_top = ctk.CTkFrame(app, corner_radius=10, border_width=2, border_color="#888888")
frame_top.pack(padx=20, pady=20, fill="x")

entry_path = ctk.CTkEntry(frame_top, placeholder_text="Caminho do diretório...", corner_radius=8, border_width=1)
entry_path.pack(side="left", expand=True, fill="x", padx=(10,5), pady=10)

btn_sel = ctk.CTkButton(frame_top, text="📁 Selecionar Pasta", corner_radius=8, command=selecionar_e_mostrar)
btn_sel.pack(side="right", padx=(5,10), pady=10)

# Text area estilizada
text_area = ctk.CTkTextbox(app, corner_radius=10, border_width=1)
text_area.pack(padx=20, pady=(0,10), fill="both", expand=True)

# Frame de botões com sombras e cantos arredondados
frame_bot = ctk.CTkFrame(app, corner_radius=10, border_width=2, border_color="#888888")
frame_bot.pack(padx=20, pady=(0,20), fill="x")

btn_salvar = ctk.CTkButton(frame_bot, text="💾 Salvar", corner_radius=8, command=salvar_em_arquivo)
btn_salvar.grid(row=0, column=0, padx=10, pady=10, sticky="ew")

btn_copiar = ctk.CTkButton(frame_bot, text="📋 Copiar", corner_radius=8, command=copiar_para_clipboard)
btn_copiar.grid(row=0, column=1, padx=10, pady=10, sticky="ew")

btn_limpar = ctk.CTkButton(frame_bot, text="🧹 Limpar", corner_radius=8, command=limpar_tudo)
btn_limpar.grid(row=0, column=2, padx=10, pady=10, sticky="ew")

btn_sair = ctk.CTkButton(frame_bot, text="❌ Sair", corner_radius=8, fg_color="#D32F2F", hover_color="#B71C1C", command=app.quit)
btn_sair.grid(row=0, column=3, padx=10, pady=10, sticky="ew")

# Configura grid equally
frame_bot.grid_columnconfigure((0,1,2,3), weight=1)

app.mainloop()
