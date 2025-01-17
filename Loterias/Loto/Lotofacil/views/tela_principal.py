# C:\Users\Marcelo\Documents\Python\Lotofacil\views\tela_principal.py
# Nome do arquivo: tela_principal.py

import tkinter as tk
from threading import Thread, Event
import tkinter.messagebox as mb
import tkinter.simpledialog as sd
from collections import Counter
import re
from collections import Counter

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
    # Remove tudo que não for número
    entrada_limpa = re.sub(r'\D', '', entrada)
    # Divide a string em partes de 2 caracteres
    numeros = [entrada_limpa[i:i+2] for i in range(0, len(entrada_limpa), 2)]
    return numeros

def conferir_jogos_dialog(status_text, root):
    # Solicita o jogo campeão
    jogo_campeao = sd.askstring(
        "Conferir Jogos",
        "Digite os números do Jogo Campeão (podem ser separados por qualquer caractere):",
        parent=root
    )
    if not jogo_campeao:
        atualizar_status(status_text, "Operação cancelada pelo usuário.\n")
        return

    # Limpa e formata o jogo campeão
    jogo_campeao = limpar_e_formatar_entrada(jogo_campeao)
    if len(jogo_campeao) == 0:
        mb.showerror("Erro", "Nenhum número válido encontrado no Jogo Campeão.", parent=root)
        return

    atualizar_status(status_text, f"Jogo Campeão: {' '.join(jogo_campeao)}\n")

    # Solicita a quantidade de jogos a conferir
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

    # Loop para receber os jogos
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

        # Limpa e formata o jogo atual
        jogo_atual = limpar_e_formatar_entrada(jogo_atual)
        if len(jogo_atual) == 0:
            mb.showerror("Erro", f"Nenhum número válido encontrado no Jogo {i}.", parent=root)
            return

        jogos_a_conferir.append(jogo_atual)
        atualizar_status(status_text, f"Jogo {i}: {' '.join(jogo_atual)}\n")

    # Resultado da conferência
    atualizar_status(status_text, "=== Resultado da Conferência ===\n")
    for idx, jogo in enumerate(jogos_a_conferir, start=1):
        acertos = len(set(jogo) & set(jogo_campeao))
        msg = f"Jogo {idx}: {' '.join(jogo)} -> Acertos: {acertos}\n"
        atualizar_status(status_text, msg)

def atualizar_banco_thread(status_text, db, info_label):
    try:
        thread_running.set()
        atualizar_status(status_text, "Atualizando...")

        def status_callback(msg):
            atualizar_status(status_text, msg)

        def progresso_callback(progresso, concurso):
            status_message = f"Progresso: {progresso:.2f}% - Inserindo concurso {concurso}"
            atualizar_status(status_text, status_message)

        db.criar_atualizar_banco_de_dados(
            status_callback=status_callback,
            progresso_callback=progresso_callback
        )
        exibir_info_banco_de_dados(info_label, db)
    finally:
        thread_running.clear()

def gerar_jogo_com_restricoes(desejado_gap=None, desejado_std=None, max_tentativas=1000):
    """
    Gera jogos aleatórios até encontrar um cuja 'avg_gap' e/ou 'std_dev'
    estejam próximos dos valores desejados.
    """
    from models.jogo import Jogo
    for _ in range(max_tentativas):
        jogo = Jogo()
        avg_gap = jogo.avaliar_distribuicao_avg_gap()
        std_dev = jogo.avaliar_distribuicao_std()

        ok_gap = True
        ok_std = True

        # Tolerâncias arbitrárias (+/- 0.1 p/ Gap e +/- 0.3 p/ Std Dev)
        if desejado_gap is not None:
            if abs(avg_gap - desejado_gap) > 0.1:
                ok_gap = False
        if desejado_std is not None:
            if abs(std_dev - desejado_std) > 0.3:
                ok_std = False

        if ok_gap and ok_std:
            return jogo
    return None

def exibir_estatisticas(status_text, db, root):
    usar_todos = mb.askyesno(
        "Estatísticas",
        "Deseja ver estatísticas de TODOS os concursos?\n"
        "(Sim = todos, Não = escolher intervalo)",
        parent=root
    )

    todos_jogos = db.recuperar_todos_jogos()
    if not todos_jogos:
        atualizar_status(status_text, "Nenhum jogo encontrado no banco de dados.\n")
        return

    if usar_todos:
        # Define todos os concursos como filtrados
        concursos_filtrados = todos_jogos
    else:
        # Define o intervalo de concursos filtrados
        inicio = sd.askinteger("Intervalo", "Concurso inicial:", minvalue=1, parent=root)
        fim = sd.askinteger("Intervalo", "Concurso final:", minvalue=1, parent=root)

        if inicio is None or fim is None:
            atualizar_status(status_text, "Operação cancelada pelo usuário.\n")
            return

        concursos_filtrados = [
            (num, dz) for (num, dz) in todos_jogos
            if inicio <= num <= fim
        ]
        if not concursos_filtrados:
            msg = (
                f"Nenhum concurso encontrado no intervalo [{inicio}..{fim}].\n"
                "Verifique se digitou corretamente."
            )
            atualizar_status(status_text, msg)
            return

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

    # Contagem de ocorrências
    gap_counts = Counter(avg_gaps).most_common()
    std_dev_counts = Counter(std_devs).most_common()

    # Formatação da mensagem
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
    root.geometry("1200x650")

    title_label = tk.Label(root, text="Simulador de Lotofácil", font=("Arial", 16))
    title_label.grid(row=0, column=0, columnspan=5, pady=10)

    main_frame = tk.Frame(root)
    main_frame.grid(row=1, column=0, columnspan=5, pady=10)

    jogo_labels = []
    for i in range(3):
        for j in range(5):
            label = tk.Label(
                main_frame,
                text="",
                width=4,
                height=2,
                font=("Arial", 12),
                relief="solid",
                borderwidth=1
            )
            label.grid(row=i, column=j, padx=5, pady=5)
            jogo_labels.append(label)

    button_frame = tk.Frame(root)
    button_frame.grid(row=2, column=0, columnspan=5, pady=10)

    # Status frame with title
    status_frame = tk.Frame(root)
    status_frame.grid(row=1, column=5, rowspan=1, padx=20, pady=10)
    
    status_title = tk.Label(status_frame, text="Log de Operações", font=("Arial", 12, "bold"))
    status_title.pack(anchor="w", pady=(0, 5))
    
    status_text = tk.Text(status_frame, height=10, width=60)
    status_text.pack()

    # Historical frame below status
    historico_frame = tk.Frame(root)
    historico_frame.grid(row=2, column=5, rowspan=2, padx=20, pady=10)

    historico_label = tk.Label(historico_frame, text="Histórico de Jogos", font=("Arial", 12, "bold"))
    historico_label.pack(anchor="w", pady=(0, 5))

    scrollbar = tk.Scrollbar(historico_frame)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    historico_listbox = tk.Listbox(historico_frame, width=50, height=15, yscrollcommand=scrollbar.set)
    historico_listbox.pack(side=tk.LEFT, fill=tk.BOTH)
    historico_listbox.bind("<<ListboxSelect>>",
                          lambda event: carregar_jogo_do_historico(event, jogo_labels, historico_listbox))

    scrollbar.config(command=historico_listbox.yview)

    db = BancoDeDados(filename='banco_de_dados.txt')

    # Frame das Restrições ABAIXO dos botões
    restricoes_frame = tk.Frame(root, bd=2, relief="groove", padx=10, pady=10)
    restricoes_frame.grid(row=3, column=0, columnspan=5, pady=10)

    restricoes_label = tk.Label(restricoes_frame, text="Restrições de Gap/Std", font=("Arial", 12, "bold"))
    restricoes_label.grid(row=0, column=0, columnspan=2, pady=5)

    gap_label = tk.Label(restricoes_frame, text="Gap Desejado:", font=("Arial", 10))
    gap_label.grid(row=1, column=0, sticky="e", padx=5)
    gap_entry = tk.Entry(restricoes_frame, width=10)
    gap_entry.grid(row=1, column=1, padx=5, pady=5)

    dev_label = tk.Label(restricoes_frame, text="Std Dev Desejado:", font=("Arial", 10))
    dev_label.grid(row=2, column=0, sticky="e", padx=5)
    dev_entry = tk.Entry(restricoes_frame, width=10)
    dev_entry.grid(row=2, column=1, padx=5, pady=5)

    recomendacoes_label = tk.Label(
        restricoes_frame,
        text="(Ex: Gap ~1.0 a 1.8,  Std Dev ~3.0 a 7.0)",
        font=("Arial", 9),
        fg="gray"
    )
    recomendacoes_label.grid(row=3, column=0, columnspan=2, pady=(5, 5))

    use_custom_values_var = tk.BooleanVar(value=False)
    use_custom_checkbox = tk.Checkbutton(
        restricoes_frame,
        text="Usar valores?",
        variable=use_custom_values_var
    )
    use_custom_checkbox.grid(row=4, column=0, columnspan=2, pady=5)

    def gerar_jogo_exibir_valores():
        if use_custom_values_var.get():
            gap_str = gap_entry.get().strip()
            dev_str = dev_entry.get().strip()
            try:
                desejado_gap = float(gap_str) if gap_str else None
                desejado_std = float(dev_str) if dev_str else None
            except ValueError:
                atualizar_status(status_text, "Valores inválidos para Gap/Std. Gerando jogo normal.\n")
                desejado_gap = None
                desejado_std = None

            jogo_gerado = gerar_jogo_com_restricoes(desejado_gap, desejado_std)
            if jogo_gerado:
                atualizar_jogo(jogo_labels, jogo=jogo_gerado)
                avg_gap = jogo_gerado.avaliar_distribuicao_avg_gap()
                std_dev = jogo_gerado.avaliar_distribuicao_std()
                msg = (
                    "Jogo Gerado (com restrições):\n"
                    f"{jogo_gerado}\n"
                    f"Média das Diferenças (avg_gap): {avg_gap:.2f}\n"
                    f"Desvio Padrão (std_dev): {std_dev:.2f}\n"
                    "----------------------------------------\n"
                )
                atualizar_status(status_text, msg)
                historico_listbox.insert(0, f"{' '.join(map(str, jogo_gerado.dezenas))} | Gap: {avg_gap:.2f}, Std: {std_dev:.2f}")
                return
            else:
                atualizar_status(
                    status_text,
                    "Não foi possível encontrar um jogo que atenda às restrições.\n"
                    "Gerando jogo normal...\n"
                )
        
        jogo_gerado = atualizar_jogo(jogo_labels, jogo=None)
        if jogo_gerado:
            avg_gap = jogo_gerado.avaliar_distribuicao_avg_gap()
            std_dev = jogo_gerado.avaliar_distribuicao_std()
            msg = (
                f"Jogo Gerado: {jogo_gerado}\n"
                f"Média das Diferenças (avg_gap): {avg_gap:.2f}\n"
                f"Desvio Padrão (std_dev): {std_dev:.2f}\n"
                "----------------------------------------\n"
            )
            atualizar_status(status_text, msg)
            historico_listbox.insert(0, f"{' '.join(map(str, jogo_gerado.dezenas))} | Gap: {avg_gap:.2f}, Std: {std_dev:.2f}")
        else:
            atualizar_status(status_text, "Nenhum jogo foi retornado por atualizar_jogo.\n")

    gerar_button = tk.Button(button_frame, text="Gerar Jogo", command=gerar_jogo_exibir_valores)
    gerar_button.pack(side=tk.LEFT, padx=10)

    copiar_button = tk.Button(button_frame, text="Copiar Jogo",
                              command=lambda: copiar_para_area_transferencia(root, jogo_labels))
    copiar_button.pack(side=tk.LEFT, padx=10)

    reset_button = tk.Button(button_frame, text="Resetar Jogo",
                             command=lambda: resetar_jogo(jogo_labels))
    reset_button.pack(side=tk.LEFT, padx=10)

    def conferir_jogos():
        conferir_jogos_dialog(status_text, root)

    conferencia_button = tk.Button(button_frame, text="Conferir Jogos", command=conferir_jogos)
    conferencia_button.pack(side=tk.LEFT, padx=10)

    def iniciar_atualizacao_banco():
        if thread_running.is_set():
            atualizar_status(status_text, "Atualização já está em andamento.")
        else:
            Thread(target=atualizar_banco_thread, args=(status_text, db, info_label)).start()

    atualizar_banco_button = tk.Button(button_frame, text="Atualizar Banco", command=iniciar_atualizacao_banco)
    atualizar_banco_button.pack(side=tk.LEFT, padx=10)

    def ver_estatisticas():
        exibir_estatisticas(status_text, db, root)

    estatisticas_button = tk.Button(button_frame, text="Ver Estatísticas", command=ver_estatisticas)
    estatisticas_button.pack(side=tk.LEFT, padx=10)

    interpretar_button = tk.Button(button_frame, text="Como Interpretar", command=como_interpretar)
    interpretar_button.pack(side=tk.LEFT, padx=10)

    sair_button = tk.Button(button_frame, text="Sair", command=root.quit)
    sair_button.pack(side=tk.LEFT, padx=10)

    info_label = tk.Label(root, text="", font=("Arial", 12))
    info_label.grid(row=4, column=0, columnspan=5, pady=10)

    exibir_info_banco_de_dados(info_label, db)

    root.mainloop()
