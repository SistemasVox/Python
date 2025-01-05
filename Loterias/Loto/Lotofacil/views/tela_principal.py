# tela_principal.py
import tkinter as tk
from threading import Thread, Event
from controllers.banco_controller import BancoController
from controllers.ui_controller import atualizar_jogo, resetar_jogo, carregar_jogo_do_historico, copiar_para_area_transferencia, exibir_info_banco_de_dados

thread_running = Event()

def atualizar_status(status_text, message):
    status_text.insert(tk.END, message + "\n")
    status_text.see(tk.END)

def atualizar_banco_thread(status_text, banco_controller, info_label):
    try:
        thread_running.set()
        atualizar_status(status_text, "Atualizando...")

        def status_callback(msg):
            atualizar_status(status_text, msg)

        def progresso_callback(progresso, concurso):
            status_message = f"Progresso: {progresso:.2f}% - Inserindo concurso {concurso}"
            atualizar_status(status_text, status_message)

        banco_controller.atualizar_banco(
            status_callback=status_callback,
            progresso_callback=progresso_callback
        )
        exibir_info_banco_de_dados(info_label, banco_controller)
    finally:
        thread_running.clear()

def create_main_screen():
    root = tk.Tk()
    root.title("Simulador de Lotofácil")
    root.geometry("1200x600")

    title_label = tk.Label(root, text="Simulador de Lotofácil", font=("Arial", 16))
    title_label.grid(row=0, column=0, columnspan=5, pady=10)

    main_frame = tk.Frame(root)
    main_frame.grid(row=1, column=0, columnspan=5, pady=10)

    jogo_labels = []
    for i in range(3):
        for j in range(5):
            label = tk.Label(main_frame, text="", width=4, height=2, font=("Arial", 12), relief="solid", borderwidth=1)
            label.grid(row=i, column=j, padx=5, pady=5)
            jogo_labels.append(label)

    button_frame = tk.Frame(root)
    button_frame.grid(row=2, column=0, columnspan=5, pady=10)

    gerar_button = tk.Button(button_frame, text="Gerar Jogo", command=lambda: atualizar_jogo(jogo_labels, historico_listbox=historico_listbox))
    gerar_button.pack(side=tk.LEFT, padx=10)

    copiar_button = tk.Button(button_frame, text="Copiar Jogo", command=lambda: copiar_para_area_transferencia(root, jogo_labels))
    copiar_button.pack(side=tk.LEFT, padx=10)

    reset_button = tk.Button(button_frame, text="Resetar Jogo", command=lambda: resetar_jogo(jogo_labels))
    reset_button.pack(side=tk.LEFT, padx=10)

    banco_controller = BancoController()

    status_text = tk.Text(root, height=15, width=50)
    status_text.grid(row=1, column=5, rowspan=2, padx=20, pady=10)

    def iniciar_atualizacao_banco():
        if thread_running.is_set():
            atualizar_status(status_text, "Atualização já está em andamento.")
        else:
            Thread(target=atualizar_banco_thread, args=(status_text, banco_controller, info_label)).start()

    atualizar_banco_button = tk.Button(button_frame, text="Atualizar Banco", command=iniciar_atualizacao_banco)
    atualizar_banco_button.pack(side=tk.LEFT, padx=10)

    sair_button = tk.Button(button_frame, text="Sair", command=root.quit)
    sair_button.pack(side=tk.LEFT, padx=10)

    historico_frame = tk.Frame(root)
    historico_frame.grid(row=1, column=6, rowspan=3, padx=20, pady=10)

    historico_label = tk.Label(historico_frame, text="Histórico de Jogos")
    historico_label.pack()

    scrollbar = tk.Scrollbar(historico_frame)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    historico_listbox = tk.Listbox(historico_frame, width=50, height=20, yscrollcommand=scrollbar.set)
    historico_listbox.pack(side=tk.LEFT, fill=tk.BOTH)
    historico_listbox.bind("<<ListboxSelect>>", lambda event: carregar_jogo_do_historico(event, jogo_labels, historico_listbox))

    scrollbar.config(command=historico_listbox.yview)

    info_label = tk.Label(root, text="", font=("Arial", 12))
    info_label.grid(row=3, column=0, columnspan=5, pady=10)

    exibir_info_banco_de_dados(info_label, banco_controller)

    root.mainloop()
