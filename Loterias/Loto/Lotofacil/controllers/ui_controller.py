import tkinter as tk
from models.jogo import Jogo, GeradorDeJogos

def atualizar_jogo(jogo_labels, jogo=None, historico_listbox=None):
    gerador = GeradorDeJogos()
    novo_jogo = jogo if jogo else gerador.gerar_jogo()
    dezenas = sorted(novo_jogo.dezenas)

    # Atualizar os números exibidos no layout
    for i in range(3):  # Exibir em 3 linhas e 5 colunas
        for j in range(5):
            index = i * 5 + j
            jogo_labels[index].config(text=str(dezenas[index]).zfill(2) if index < len(dezenas) else "")

    # Atualizar o histórico de jogos
    if historico_listbox is not None and jogo is None:
        jogo_str = " ".join(map(str, dezenas))
        if not any(jogo_str == historico_listbox.get(idx) for idx in range(historico_listbox.size())):
            historico_listbox.insert(tk.END, jogo_str)

def resetar_jogo(jogo_labels):
    for label in jogo_labels:
        label.config(text="")

def carregar_jogo_do_historico(event, jogo_labels, historico_listbox):
    selecao = historico_listbox.curselection()
    if selecao:
        dezenas = list(map(int, historico_listbox.get(selecao).split()))
        atualizar_jogo(jogo_labels, Jogo(dezenas=dezenas))

def copiar_para_area_transferencia(root, jogo_labels):
    jogo_texto = "\n".join(
        " ".join(label.cget("text") for label in jogo_labels[i:i+5])
        for i in range(0, len(jogo_labels), 5)
    )
    root.clipboard_clear()
    root.clipboard_append(jogo_texto)
    root.update()

def exibir_info_banco_de_dados(info_label, banco_controller):
    total_concursos, ultimo_concurso = banco_controller.obter_info_banco()
    info_text = f"Total de Concursos: {total_concursos}\nÚltimo Concurso: {ultimo_concurso or 'Nenhum'}"
    info_label.config(text=info_text)
