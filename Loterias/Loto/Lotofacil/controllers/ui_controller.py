# C:\Users\Marcelo\Documents\Python\Lotofacil\controllers\ui_controller.py
# Nome do arquivo: ui_controller.py

import tkinter as tk
from models.jogo import Jogo, GeradorDeJogos

def atualizar_jogo(jogo_labels, jogo=None, historico_listbox=None):
    gerador = GeradorDeJogos()
    novo_jogo = jogo if jogo else gerador.gerar_jogo()
    dezenas = sorted(novo_jogo.dezenas)

    for i in range(3):
        for j in range(5):
            index = i * 5 + j
            if index < len(dezenas):
                jogo_labels[index].config(text=str(dezenas[index]).zfill(2))
            else:
                jogo_labels[index].config(text="")

    if historico_listbox is not None and jogo is None:
        jogo_str = " ".join(map(str, dezenas))
        if not any(jogo_str == historico_listbox.get(idx) for idx in range(historico_listbox.size())):
            historico_listbox.insert(tk.END, jogo_str)

    return novo_jogo

def resetar_jogo(jogo_labels):
    for label in jogo_labels:
        label.config(text="")

def carregar_jogo_do_historico(event, jogo_labels, historico_listbox):
    selecao = historico_listbox.curselection()
    if not selecao:
        return
    
    # Pega apenas a parte dos números (antes do |)
    jogo_texto = historico_listbox.get(selecao).split('|')[0].strip()
    dezenas = list(map(int, jogo_texto.split()))
    
    # Atualiza os labels com as dezenas selecionadas
    for i, label in enumerate(jogo_labels):
        if i < len(dezenas):
            # Adiciona zero à esquerda em números menores que 10
            label.config(text=f"{dezenas[i]:02d}")
        else:
            label.config(text="")

def copiar_para_area_transferencia(root, jogo_labels):
    jogo_texto = "\n".join(
        " ".join(label.cget("text") for label in jogo_labels[i:i+5])
        for i in range(0, len(jogo_labels), 5)
    )
    root.clipboard_clear()
    root.clipboard_append(jogo_texto)
    root.update()

def exibir_info_banco_de_dados(info_label, banco):
    total_concursos, ultimo_concurso = banco.obter_info_banco()
    info_text = (
        f"Total de Concursos: {total_concursos}\n"
        f"Último Concurso: {ultimo_concurso or 'Nenhum'}"
    )
    info_label.config(text=info_text)
