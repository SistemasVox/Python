# C:\Users\Marcelo\Documents\Python\Lotofacil\views\tela_principal.py

import tkinter as tk
from threading import Thread, Event
import tkinter.messagebox as mb
import tkinter.simpledialog as sd
from collections import Counter
import re

from models.banco_de_dados import BancoDeDados
from controllers.ui_controller import (
    atualizar_jogo,
    resetar_jogo,
    carregar_jogo_do_historico,
    copiar_para_area_transferencia,
    exibir_info_banco_de_dados
)
from models.jogo import Jogo

thread_running = Event()

def atualizar_status(status_text, message):
    status_text.insert(tk.END, message + "\n")
    status_text.see(tk.END)

def limpar_e_formatar_entrada(entrada):
    """
    Remove caracteres não numéricos e separa os números em pares de dois dígitos.
    """
    entrada_limpa = re.sub(r'\D', '', entrada)
    numeros = [entrada_limpa[i:i+2] for i in range(0, len(entrada_limpa), 2)]
    return numeros

def conferir_jogos_dialog(status_text, root):
    jogo_campeao = sd.askstring(
        "Conferir Jogos",
        "Digite os números do Jogo Campeão (podem ser separados por qualquer caractere):",
        parent=root
    )
    if not jogo_campeao:
        atualizar_status(status_text, "Operação cancelada pelo usuário.\n")
        return

    jogo_campeao = limpar_e_formatar_entrada(jogo_campeao)
    if len(jogo_campeao) == 0:
        mb.showerror("Erro", "Nenhum número válido encontrado no Jogo Campeão.", parent=root)
        return

    atualizar_status(status_text, f"Jogo Campeão: {' '.join(jogo_campeao)}\n")

    try:
        qtd_jogos = sd.askinteger(
            "Conferir Jogos",
            "Quantos jogos deseja conferir?",
            minvalue=1,
            parent=root
        )
        if not qtd_jogos:
            atualizar_status(status_text, "Operação cancelada pelo usuário.\n")
            return
    except ValueError:
        mb.showerror("Erro", "Quantidade de jogos inválida.", parent=root)
        return

    atualizar_status(status_text, f"Quantidade de jogos a conferir: {qtd_jogos}\n")

    jogos_a_conferir = []
    for i in range(1, qtd_jogos + 1):
        jogo_atual = sd.askstring(
            "Conferir Jogos",
            f"Digite os números do Jogo {i} (podem ser separados por qualquer caractere):",
            parent=root
        )
        if not jogo_atual:
            mb.showerror("Erro", f"Jogo {i} não informado.", parent=root)
            return

        jogo_atual = limpar_e_formatar_entrada(jogo_atual)
        if len(jogo_atual) == 0:
            mb.showerror("Erro", f"Nenhum número válido encontrado no Jogo {i}.", parent=root)
            return

        jogos_a_conferir.append(jogo_atual)
        atualizar_status(status_text, f"Jogo {i}: {' '.join(jogo_atual)}\n")

    atualizar_status(status_text, "=== Resultado da Conferência ===\n")
    for idx, jogo in enumerate(jogos_a_conferir, start=1):
        acertos = len(set(jogo) & set(jogo_campeao))
        atualizar_status(status_text, f"Jogo {idx}: {' '.join(jogo)} -> Acertos: {acertos}\n")

def atualizar_banco_thread(status_text, db, info_label):
    try:
        thread_running.set()
        atualizar_status(status_text, "Atualizando...")

        def status_callback(msg):
            atualizar_status(status_text, msg)

        def progresso_callback(progresso, concurso):
            atualizar_status(status_text, f"Progresso: {progresso:.2f}% - Inserindo concurso {concurso}")

        db.criar_atualizar_banco_de_dados(
            status_callback=status_callback,
            progresso_callback=progresso_callback
        )
        exibir_info_banco_de_dados(info_label, db)
    finally:
        thread_running.clear()

def gerar_jogo_com_restricoes(desejado_gap=None, desejado_std=None, max_tentativas=10000):
    """
    Gera jogos aleatórios até encontrar um cuja 'avg_gap' e/ou 'std_dev'
    estejam dentro da tolerância dos valores desejados.
    """
    for _ in range(max_tentativas):
        jogo = Jogo()
        avg_gap = jogo.avaliar_distribuicao_avg_gap()
        std_dev = jogo.avaliar_distribuicao_std()

        ok_gap = desejado_gap is None or abs(avg_gap - desejado_gap) <= 0.1
        ok_std = desejado_std is None or abs(std_dev - desejado_std) <= 0.3

        if ok_gap and ok_std:
            return jogo
    return None

def exibir_estatisticas(status_text, db, root):
    usar_todos = mb.askyesno(
        "Estatísticas",
        "Deseja ver estatísticas de TODOS os concursos?\n(Sim = todos, Não = escolher intervalo)",
        parent=root
    )

    todos_jogos = db.recuperar_todos_jogos()
    if not todos_jogos:
        atualizar_status(status_text, "Nenhum jogo encontrado no banco de dados.\n")
        return

    if usar_todos:
        concursos_filtrados = todos_jogos
    else:
        inicio = sd.askinteger("Intervalo", "Concurso inicial:", minvalue=1, parent=root)
        fim    = sd.askinteger("Intervalo", "Concurso final:",  minvalue=1, parent=root)
        if inicio is None or fim is None:
            atualizar_status(status_text, "Operação cancelada pelo usuário.\n")
            return
        concursos_filtrados = [
            (num, dz) for (num, dz) in todos_jogos
            if inicio <= num <= fim
        ]
        if not concursos_filtrados:
            atualizar_status(
                status_text,
                f"Nenhum concurso no intervalo [{inicio}..{fim}].\nVerifique se digitou corretamente."
            )
            return

    # ================================================
    # Bloco: cálculo de avg_gaps e std_devs
    # ================================================
    avg_gaps = []
    std_devs = []
    for (_, dezenas) in concursos_filtrados:
        jogo_obj = Jogo(dezenas=dezenas)
        avg_gaps.append(round(jogo_obj.avaliar_distribuicao_avg_gap(), 2))
        std_devs.append(round(jogo_obj.avaliar_distribuicao_std(), 2))

    quantidade = len(avg_gaps)
    if quantidade == 0:
        atualizar_status(status_text, "Nenhum jogo válido para calcular estatísticas.\n")
        return

    avg_gap_medio = sum(avg_gaps) / quantidade
    std_dev_medio = sum(std_devs) / quantidade
    avg_gap_min, avg_gap_max = min(avg_gaps), max(avg_gaps)
    std_dev_min, std_dev_max = min(std_devs), max(std_devs)

    # ================================================
    # Bloco: estatísticas separadas (Gap e Std)
    # ================================================
    gap_counts     = Counter(avg_gaps).most_common()
    std_dev_counts = Counter(std_devs).most_common()

    msg = (
        "=== Estatísticas do Banco de Dados ===\n"
        f"Jogos analisados: {quantidade}\n\n"
        f"- Média do Average Gap: {avg_gap_medio:.2f}\n"
        f"  (Mín: {avg_gap_min:.2f} | Máx: {avg_gap_max:.2f})\n\n"
        f"- Média do Std Dev: {std_dev_medio:.2f}\n"
        f"  (Mín: {std_dev_min:.2f} | Máx: {std_dev_max:.2f})\n\n"
        "=== Ocorrências de Average Gap ===\n"
    )
    for gap, count in gap_counts:
        msg += f"  Gap {gap:.2f}: {count} jogos\n"

    msg += "\n=== Ocorrências de Std Dev ===\n"
    for std, count in std_dev_counts:
        msg += f"  Std Dev {std:.2f}: {count} jogos\n"

    # ================================================
    # Bloco: combinações de Gap + Std Dev
    # ================================================
    combo_counts = Counter(zip(avg_gaps, std_devs))
    msg += "\n=== Ocorrências de Gap+Std ===\n"
    for (gap_val, std_val), cnt in combo_counts.most_common():
        msg += f"  {{ {gap_val:.2f} , {std_val:.2f} }}: {cnt} jogos\n"

    msg += "-----------------------------------------\n"
    atualizar_status(status_text, msg)

def como_interpretar():
    texto = (
        "=== Como interpretar o Average Gap e o Desvio Padrão (Std Dev)? ===\n\n"
        "1) Average Gap:\n"
        "   - Mede a distância média entre números consecutivos do jogo.\n"
        "   - Quanto MENOR o Average Gap, mais 'próximos' estão os números.\n"
        "   - Quanto MAIOR o Average Gap, mais 'espalhados' estão.\n"
        "   - Em geral, ele varia ~entre 1.0 e 1.8.\n\n"
        "2) Desvio Padrão (Std Dev):\n"
        "   - Mede como os números estão afastados da média.\n"
        "   - Quanto MENOR o Std Dev, mais próximos da média geral.\n"
        "   - Quanto MAIOR, mais dispersos.\n"
        "   - Em geral, ele varia ~entre 3.0 e 7.0.\n\n"
        "Use essas métricas para analisar se os números de um concurso (ou intervalo)\n"
        "tendem a ficar mais próximos uns dos outros ou muito espalhados."
    )
    mb.showinfo("Como interpretar estatísticas", texto)

def create_main_screen():
    root = tk.Tk()
    root.title("Simulador de Lotofácil")
    root.geometry("1350x650")

    # Título
    title_label = tk.Label(root, text="Simulador de Lotofácil", font=("Arial", 16))
    title_label.grid(row=0, column=0, columnspan=5, pady=10)

    # Frame dos números
    main_frame = tk.Frame(root)
    main_frame.grid(row=1, column=0, columnspan=5, pady=10)
    jogo_labels = []
    for i in range(3):
        for j in range(5):
            lbl = tk.Label(
                main_frame, text="", width=4, height=2,
                font=("Arial", 12), relief="solid", borderwidth=1
            )
            lbl.grid(row=i, column=j, padx=5, pady=5)
            jogo_labels.append(lbl)

    # Frame dos botões
    button_frame = tk.Frame(root)
    button_frame.grid(row=2, column=0, columnspan=5, pady=10)

    # Frame do log de operações
    status_frame = tk.Frame(root)
    status_frame.grid(row=1, column=5, padx=20, pady=10)
    tk.Label(status_frame, text="Log de Operações", font=("Arial", 12, "bold")).pack(anchor="w", pady=(0,5))
    status_text = tk.Text(status_frame, height=10, width=60)
    status_text.pack()

    # Frame do histórico
    historico_frame = tk.Frame(root)
    historico_frame.grid(row=2, column=5, rowspan=2, padx=20, pady=10)
    tk.Label(historico_frame, text="Histórico de Jogos", font=("Arial", 12, "bold")).pack(anchor="w", pady=(0,5))
    scrollbar = tk.Scrollbar(historico_frame)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    historico_listbox = tk.Listbox(
        historico_frame, width=50, height=15, yscrollcommand=scrollbar.set
    )
    historico_listbox.pack(side=tk.LEFT, fill=tk.BOTH)
    historico_listbox.bind(
        "<<ListboxSelect>>",
        lambda e: carregar_jogo_do_historico(e, jogo_labels, historico_listbox)
    )
    scrollbar.config(command=historico_listbox.yview)

    # Banco de dados
    db = BancoDeDados(filename='banco_de_dados.txt')

    # Frame de restrições de Gap/Std
    restr_frame = tk.Frame(root, bd=2, relief="groove", padx=10, pady=10)
    restr_frame.grid(row=3, column=0, columnspan=5, pady=10)
    tk.Label(restr_frame, text="Restrições de Gap/Std", font=("Arial",12,"bold")).grid(row=0, column=0, columnspan=2, pady=5)
    tk.Label(restr_frame, text="Gap Desejado:", font=("Arial",10)).grid(row=1, column=0, sticky="e", padx=5)
    gap_entry = tk.Entry(restr_frame, width=10)
    gap_entry.grid(row=1, column=1, padx=5, pady=5)
    tk.Label(restr_frame, text="Std Dev Desejado:", font=("Arial",10)).grid(row=2, column=0, sticky="e", padx=5)
    dev_entry = tk.Entry(restr_frame, width=10)
    dev_entry.grid(row=2, column=1, padx=5, pady=5)
    tk.Label(
        restr_frame,
        text="(Ex: Gap ~1.0 a 1.8, Std Dev ~3.0 a 7.0)",
        font=("Arial",9), fg="gray"
    ).grid(row=3, column=0, columnspan=2, pady=(5,5))
    use_custom_values_var = tk.BooleanVar(value=False)
    tk.Checkbutton(
        restr_frame, text="Usar valores?", variable=use_custom_values_var
    ).grid(row=4, column=0, columnspan=2, pady=5)

    # ================================================
    # Função interna: gera jogo e exibe valores + contagem
    # ================================================
    def gerar_jogo_exibir_valores():
        # Bloco: geração com restrições
        if use_custom_values_var.get():
            try:
                desejado_gap = float(gap_entry.get().strip()) if gap_entry.get().strip() else None
                desejado_std = float(dev_entry.get().strip()) if dev_entry.get().strip() else None
            except ValueError:
                atualizar_status(status_text, "Valores inválidos para Gap/Std. Gerando jogo normal.")
                desejado_gap = desejado_std = None

            jogo = gerar_jogo_com_restricoes(desejado_gap, desejado_std)
            if jogo:
                atualizar_jogo(jogo_labels, jogo=jogo)
                avg_gap = jogo.avaliar_distribuicao_avg_gap()
                std_dev = jogo.avaliar_distribuicao_std()
                atualizar_status(
                    status_text,
                    "Jogo Gerado (com restrições):\n"
                    f"{jogo}\n"
                    f"Gap: {avg_gap:.2f} | Std: {std_dev:.2f}\n"
                    "----------------------------------------"
                )
                historico_listbox.insert(
                    0,
                    f"{' '.join(map(str, jogo.dezenas))} | Gap: {avg_gap:.2f}, Std: {std_dev:.2f}"
                )
                return  # evita fallback
            else:
                atualizar_status(
                    status_text,
                    "Não foi possível encontrar jogo com essas restrições. Gerando normal..."
                )

        # Bloco: geração sem restrições (fallback)
        jogo = atualizar_jogo(jogo_labels, jogo=None)
        if jogo:
            avg_gap = jogo.avaliar_distribuicao_avg_gap()
            std_dev = jogo.avaliar_distribuicao_std()
            atualizar_status(
                status_text,
                f"Jogo Gerado: {jogo}\n"
                f"Gap: {avg_gap:.2f} | Std: {std_dev:.2f}\n"
                "----------------------------------------"
            )
            historico_listbox.insert(
                0,
                f"{' '.join(map(str, jogo.dezenas))} | Gap: {avg_gap:.2f}, Std: {std_dev:.2f}"
            )
        else:
            atualizar_status(status_text, "Nenhum jogo foi retornado por atualizar_jogo.")

        # Bloco: contar ocorrências de Gap+Std no histórico
        combo = Counter()
        for item in historico_listbox.get(0, tk.END):
            try:
                partes = item.split('|')[1].strip()
                gap_p, std_p = partes.split(',')
                g = float(gap_p.split(':')[1]); s = float(std_p.split(':')[1])
                combo[(round(g,2), round(s,2))] += 1
            except:
                continue

        atualizar_status(status_text, "=== Ocorrências de Gap+Std ===")
        for (g, s), c in combo.most_common():
            atualizar_status(status_text, f"{{ {g:.2f} , {s:.2f} }} = {c}x")
        atualizar_status(status_text, "----------------------------------------\n")

    # Botões de ação
    tk.Button(button_frame, text="Gerar Jogo", command=gerar_jogo_exibir_valores).pack(side=tk.LEFT, padx=10)
    tk.Button(button_frame, text="Copiar Jogo", command=lambda: copiar_para_area_transferencia(root, jogo_labels)).pack(side=tk.LEFT, padx=10)
    tk.Button(button_frame, text="Resetar Jogo", command=lambda: resetar_jogo(jogo_labels)).pack(side=tk.LEFT, padx=10)
    tk.Button(button_frame, text="Conferir Jogos", command=lambda: conferir_jogos_dialog(status_text, root)).pack(side=tk.LEFT, padx=10)
    tk.Button(button_frame, text="Atualizar Banco", command=lambda: Thread(target=atualizar_banco_thread, args=(status_text, db, info_label)).start()).pack(side=tk.LEFT, padx=10)
    tk.Button(button_frame, text="Ver Estatísticas", command=lambda: exibir_estatisticas(status_text, db, root)).pack(side=tk.LEFT, padx=10)
    tk.Button(button_frame, text="Como Interpretar", command=como_interpretar).pack(side=tk.LEFT, padx=10)
    tk.Button(button_frame, text="Sair", command=root.quit).pack(side=tk.LEFT, padx=10)

    # Label de info do BD
    info_label = tk.Label(root, text="", font=("Arial", 12))
    info_label.grid(row=4, column=0, columnspan=5, pady=10)
    exibir_info_banco_de_dados(info_label, db)

    root.mainloop()
