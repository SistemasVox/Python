# =============================================================================
#   Monitoramento Contínuo de Firewall e Servidor 🚀
#
#   Descrição:
#     Script que faz ping contínuo em dois dispositivos (firewall e servidor)
#     de várias unidades de rede, atualiza o status em uma GUI Tkinter, gera
#     alertas não-modais e registra histórico de mudanças de status.
#
#   Fluxo Geral:
#     1) Configura variáveis e listas de unidades (101–132) e estados iniciais
#     2) start_monitoring():
#          • start_continuous_ping(): cria uma thread por unidade que executa
#            native_ping em loop (_5 pings + intervalo adaptativo_)
#          • agenda update_aggregated_status() a cada 1s via root.after
#     3) update_aggregated_status():
#          • coleta resultados de ping (sucessos/erros) de cada thread
#          • compara com status anterior e, se mudou:
#              – chama alert_status_change() para tocar sino e criar popup
#              – chama log_status_change() para inserir texto no widget
#          • atualiza botões na GUI com cores (verde/vermelho/cinza)
#          • reagenda a si mesma para continuar o loop
#     4) Controle pelo usuário:
#          • Checkbuttons pra ligar/desligar log e notificações
#          • Botões “Limpar Histórico”, “Reiniciar” e “Sair”
#
#   Principais Funções:
#     • native_ping(): usa ping do SO de forma síncrona
#     • continuous_monitor(): loop de ping e atualização de resultados
#     • update_ui(): aplica cores e horários na interface
#     • show_nonblocking_alert(): alerta “toast” sem bloquear a GUI
#     • copy_to_clipboard(): copia IP pro clipboard com feedback
#
#   Observações:
#     – Thread-safe com Locks (status_lock, continuous_lock)
#     – Uso de root.after() para ciclo de atualização sem travar GUI
#     – Downtime formatado em s/m/h/days para histórico
#
#   Autor: Marcelo Vieira
#   Data:   13/05/2025
# =============================================================================


import tkinter as tk
from tkinter import ttk
import time, threading, random, subprocess, platform
from datetime import datetime

# ------------------ FUNÇÃO native_ping (alternativa nativa) ------------------
def native_ping(ip, timeout=1):
    sistema = platform.system().lower()
    if sistema == "windows":
        comando = ["ping", "-n", "1", "-w", str(timeout * 1000), ip]
        # Ocultar janela do terminal no Windows
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        try:
            saida = subprocess.check_output(comando, startupinfo=startupinfo, universal_newlines=True)
            return "ttl=" in saida.lower()
        except subprocess.CalledProcessError:
            return False
    else:
        comando = ["ping", "-c", "1", "-W", str(timeout), ip]
        try:
            saida = subprocess.check_output(comando, universal_newlines=True)
            return "ttl=" in saida.lower()
        except subprocess.CalledProcessError:
            return False

# ------------------ ALERTAS NÃO MODAIS COM BELL ------------------
def show_nonblocking_alert(title, message, duration=3000, bg="lightyellow"):
    root.bell()
    alert_win = tk.Toplevel(root)
    alert_win.title(title)
    alert_win.wm_overrideredirect(True)
    alert_win.attributes("-topmost", True)
    alert_win.geometry("300x100+500+300")
    tk.Label(alert_win, text=message, bg=bg, fg="black", font=("Arial", 10))\
        .pack(expand=True, fill="both", padx=10, pady=10)
    alert_win.after(duration, alert_win.destroy)

# ------------------ FUNÇÕES AUXILIARES ------------------
def get_color(status):
    return "green" if status == "on" else "red" if status == "off" else "gray"

def format_downtime(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds//60}m:{seconds%60:02d}s"
    elif seconds < 86400:
        return f"{seconds//3600}h:{(seconds%3600)//60:02d}m:{seconds%60:02d}s"
    else:
        d = seconds // 86400; rem = seconds % 86400
        return f"{d}d:{rem//3600:02d}h:{(rem%3600)//60:02d}m:{rem%60:02d}s"

def alert_status_change(unit, device, old_status, new_status, downtime):
    if new_status == "sd":
        return
    formatted = format_downtime(downtime)
    if old_status in ["off", "sd"] and new_status == "on":
        if notify_online_var.get():
            show_nonblocking_alert("Alerta", f"Unidade {unit} [{device}] voltou a ficar ON (tempo offline: {formatted}).", bg="lightyellow")
    elif old_status == "on" and new_status == "off":
        if notify_offline_var.get():
            show_nonblocking_alert("Alerta", f"Unidade {unit} [{device}] ficou OFF.", bg="lightpink")

def log_status_change(unit, device, old_status, new_status, downtime):
    if new_status == "sd" or not log_changes_var.get():
        return
    formatted = format_downtime(downtime) if (old_status=="off" and new_status=="on") else "0s"
    msg = (f"{datetime.now().strftime('%d/%m/%Y %H:%M:%S')} - Unidade {unit} [{device}]: "
           f"{old_status.upper()} -> {new_status.upper()} (tempo offline: {formatted}).\n\n")
    log_text.insert(tk.END, msg)
    log_text.see(tk.END)

def interruptible_sleep(duration, stop_event):
    end_time = time.time() + duration
    while time.time() < end_time:
        if stop_event.is_set():
            break
        time.sleep(0.1)

# ------------------ CONFIGURAÇÕES INICIAIS ------------------
units = list(range(101, 133))
groups = 3  # Distribuição em 3 grupos

previous_status = {unit: {
    "firewall": {"status": "sd", "color": "gray", "change_time": datetime.now(), "update_time": datetime.now()},
    "server": {"status": "sd", "color": "gray", "change_time": datetime.now(), "update_time": datetime.now()}
} for unit in units}

continuous_results = {unit: {
    "firewall": {"status": "sd", "timestamp": datetime.now(), "sucessos": 0, "falhas": 0, "last_update": datetime.now()},
    "server": {"status": "sd", "timestamp": datetime.now(), "sucessos": 0, "falhas": 0, "last_update": datetime.now()}
} for unit in units}

# Agora, cada unidade terá um único processo contínuo (thread) de monitoramento.
ping_threads = {}
status_lock = threading.Lock()
continuous_lock = threading.Lock()

# ------------------ MONITORAMENTO CONTÍNUO POR MÁQUINA ------------------
def continuous_monitor(unit, stop_event):
    fw_ip = f"10.{unit}.0.1"
    srv_ip = f"10.{unit}.0.10"
    while not stop_event.is_set():
        for device, ip in [("firewall", fw_ip), ("server", srv_ip)]:
            successes = 0
            for _ in range(5):
                if stop_event.is_set():
                    break
                if native_ping(ip, timeout=1):
                    successes += 1
                now = datetime.now()
                with continuous_lock:
                    continuous_results[unit][device]["last_update"] = now
                interruptible_sleep(1, stop_event)
            now = datetime.now()
            new_status = "on" if successes > 0 else "off"
            with continuous_lock:
                continuous_results[unit][device]["status"] = new_status
                continuous_results[unit][device]["timestamp"] = now
                continuous_results[unit][device]["sucessos"] = successes
                continuous_results[unit][device]["falhas"] = 5 - successes
            sleep_interval = 5 if new_status == "on" else 1
            interruptible_sleep(sleep_interval, stop_event)

def start_continuous_ping():
    global ping_threads
    with continuous_lock:
        for unit in units:
            continuous_results[unit]["firewall"] = {"status": "sd", "timestamp": datetime.now(),
                                                    "sucessos": 0, "falhas": 0, "last_update": datetime.now()}
            continuous_results[unit]["server"] = {"status": "sd", "timestamp": datetime.now(),
                                                  "sucessos": 0, "falhas": 0, "last_update": datetime.now()}
    for unit in units:
        stop_event = threading.Event()
        t = threading.Thread(target=continuous_monitor, args=(unit, stop_event), daemon=True)
        ping_threads[unit] = (t, stop_event)
        t.start()
        time.sleep(random.uniform(0.01, 0.1))

def stop_continuous_ping():
    global ping_threads
    for t, stop_event in ping_threads.values():
        stop_event.set()
        t.join(timeout=1)
    ping_threads.clear()

# ------------------ AGREGADOR DE STATUS ------------------
def update_aggregated_status():
    now = datetime.now()
    status_updates = {}
    for unit in units:
        with continuous_lock:
            fw_status = continuous_results[unit]["firewall"]["status"]
            srv_status = continuous_results[unit]["server"]["status"]
            fw_update = continuous_results[unit]["firewall"].get("last_update", now)
            srv_update = continuous_results[unit]["server"].get("last_update", now)
        status_updates[unit] = {
            "firewall": fw_status,
            "server": srv_status,
            "fw_update": fw_update,
            "srv_update": srv_update
        }
    with status_lock:
        for unit in units:
            for device in ["firewall", "server"]:
                old = previous_status[unit][device]
                if device == "firewall":
                    new_status = status_updates[unit]["firewall"]
                    update_time = status_updates[unit]["fw_update"]
                else:
                    new_status = status_updates[unit]["server"]
                    update_time = status_updates[unit]["srv_update"]
                if old["status"] == "sd":
                    previous_status[unit][device] = {
                        "status": new_status,
                        "color": get_color(new_status),
                        "change_time": now,
                        "update_time": update_time
                    }
                elif old["status"] != new_status:
                    if old["status"] == "off" and new_status == "on":
                        downtime = (now - old["change_time"]).total_seconds()
                    else:
                        downtime = 0
                    alert_status_change(unit, device, old["status"], new_status, downtime)
                    log_status_change(unit, device, old["status"], new_status, downtime)
                    previous_status[unit][device] = {
                        "status": new_status,
                        "color": get_color(new_status),
                        "change_time": now,
                        "update_time": update_time
                    }
                else:
                    previous_status[unit][device]["update_time"] = update_time
    update_ui()
    global update_status_job
    update_status_job = root.after(1000, update_aggregated_status)

update_status_job = None

# ------------------ FUNÇÃO DE MONITORAMENTO ------------------
def start_monitoring():
    start_continuous_ping()
    global update_status_job
    update_status_job = root.after(1000, update_aggregated_status)

def restart_monitoring():
    global update_status_job, previous_status, continuous_results
    if update_status_job:
        root.after_cancel(update_status_job)
        update_status_job = None
    stop_continuous_ping()
    previous_status = {unit: {
        "firewall": {"status": "sd", "color": "gray", "change_time": datetime.now(), "update_time": datetime.now()},
        "server": {"status": "sd", "color": "gray", "change_time": datetime.now(), "update_time": datetime.now()}
    } for unit in units}
    continuous_results = {unit: {
        "firewall": {"status": "sd", "timestamp": datetime.now(), "sucessos": 0, "falhas": 0, "last_update": datetime.now()},
        "server": {"status": "sd", "timestamp": datetime.now(), "sucessos": 0, "falhas": 0, "last_update": datetime.now()}
    } for unit in units}
    start_monitoring()

# ------------------ ATUALIZAÇÃO DA INTERFACE ------------------
firewall_buttons = []
server_buttons = []
time_labels = []
unit_labels = []

def update_single_unit_ui(unit):
    idx = units.index(unit)
    with status_lock:
        fw_status = previous_status[unit]["firewall"]["status"]
        srv_status = previous_status[unit]["server"]["status"]
        t_str = previous_status[unit]["firewall"]["update_time"].strftime("%H:%M:%S")
    firewall_buttons[idx].config(bg=get_color(fw_status))
    server_buttons[idx].config(bg=get_color(srv_status))
    time_labels[idx].config(text=t_str)

def update_ui():
    for unit in units:
        update_single_unit_ui(unit)
    root.update_idletasks()

# ------------------ FUNÇÃO PARA COPIAR IP ------------------
def copy_to_clipboard(ip):
    root.clipboard_clear()
    root.clipboard_append(ip)
    show_nonblocking_alert("Copiado", f"IP {ip} copiado para a área de transferência.", duration=1500, bg="lightblue")

# ------------------ TOOLTIP PARA STATUS ------------------
tooltip = None

def show_status_tooltip(event, text):
    global tooltip
    if tooltip:
        tooltip.destroy()
    tooltip = tk.Toplevel(event.widget)
    tooltip.wm_overrideredirect(True)
    x = event.widget.winfo_rootx() + 20
    y = event.widget.winfo_rooty() + 20
    tooltip.wm_geometry(f"+{x}+{y}")
    tk.Label(tooltip, text=text, background="lightyellow", relief="solid", borderwidth=1, font=("Arial", 10))\
        .pack(expand=True, fill="both", padx=10, pady=10)

def hide_status_tooltip(event):
    global tooltip
    if tooltip:
        tooltip.destroy()
        tooltip = None

# ------------------ CRIAÇÃO DA INTERFACE GRÁFICA ------------------
root = tk.Tk()
root.title("Monitoramento Contínuo de Firewall e Servidor")
root.geometry("1080x620")

columns = [
    "Unidade", "Status", "Hora",
    "Unidade", "Status", "Hora",
    "Unidade", "Status", "Hora",
    "Histórico"
]

frame = ttk.Frame(root)
canvas = tk.Canvas(frame)
scrollbar = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
scrollable_frame = ttk.Frame(canvas)
scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
canvas.configure(yscrollcommand=scrollbar.set)

frame.pack(fill="both", expand=True)
canvas.pack(side="left", fill="both", expand=True)
scrollbar.pack(side="right", fill="y")

header_label = ttk.Label(scrollable_frame, text="Monitoramento de Firewall e Servidor", font=('Arial', 16, 'bold'))
header_label.grid(row=0, column=0, columnspan=len(columns), pady=10)

for i, col in enumerate(columns):
    ttk.Label(scrollable_frame, text=col, font=('Arial', 12, 'bold'), relief="solid")\
        .grid(row=1, column=i, padx=5, pady=5, sticky="nsew")

firewall_buttons.clear()
server_buttons.clear()
time_labels.clear()
unit_labels.clear()

for i, unit in enumerate(units):
    grupo = i % groups
    linha = (i // groups) + 2
    col_offset = grupo * 3
    lbl_unit = ttk.Label(scrollable_frame, text=str(unit), font=('Arial', 10, 'bold'),
                         relief="solid", anchor="center")
    lbl_unit.grid(row=linha, column=col_offset, padx=5, pady=5, sticky="nsew")
    unit_labels.append(lbl_unit)
    status_frame = tk.Frame(scrollable_frame, relief="solid", bd=1)
    status_frame.grid(row=linha, column=col_offset+1, padx=5, pady=5, sticky="nsew")
    btn_fw = tk.Button(status_frame, text="", bg="gray", width=4, height=1)
    btn_fw.pack(side="left", fill="both", expand=True)
    btn_srv = tk.Button(status_frame, text="", bg="gray", width=4, height=1)
    btn_srv.pack(side="left", fill="both", expand=True)
    fw_ip = f"10.{unit}.0.1"
    srv_ip = f"10.{unit}.0.10"
    btn_fw.bind("<Button-1>", lambda e, ip=fw_ip: copy_to_clipboard(ip))
    btn_srv.bind("<Button-1>", lambda e, ip=srv_ip: copy_to_clipboard(ip))
    btn_fw.bind("<Enter>", lambda e, u=unit: show_status_tooltip(e, f"Firewall {previous_status[u]['firewall']['status'].upper()}"))
    btn_fw.bind("<Leave>", hide_status_tooltip)
    btn_srv.bind("<Enter>", lambda e, u=unit: show_status_tooltip(e, f"Servidor {previous_status[u]['server']['status'].upper()}"))
    btn_srv.bind("<Leave>", hide_status_tooltip)
    firewall_buttons.append(btn_fw)
    server_buttons.append(btn_srv)
    lbl_time = ttk.Label(scrollable_frame, text="N/A", relief="solid", anchor="center")
    lbl_time.grid(row=linha, column=col_offset+2, padx=5, pady=5, sticky="nsew")
    time_labels.append(lbl_time)

log_text = tk.Text(scrollable_frame, wrap="word", width=40, height=30)
log_text.grid(row=2, column=len(columns)-1, rowspan=100, padx=5, pady=5, sticky="nsew")

control_frame = ttk.Frame(root)
control_frame.pack(pady=5)
log_changes_var = tk.IntVar(value=1)
notify_online_var = tk.IntVar()
notify_offline_var = tk.IntVar()
tk.Checkbutton(control_frame, text="Registrar Histórico", variable=log_changes_var)\
    .pack(side="left", padx=5)
tk.Checkbutton(control_frame, text="Notificar Online", variable=notify_online_var)\
    .pack(side="left", padx=5)
tk.Checkbutton(control_frame, text="Notificar Offline", variable=notify_offline_var)\
    .pack(side="left", padx=5)
tk.Button(control_frame, text="Limpar Histórico", command=lambda: log_text.delete("1.0", tk.END))\
    .pack(side="left", padx=5)
tk.Button(control_frame, text="Reiniciar", command=restart_monitoring)\
    .pack(side="left", padx=5)
tk.Button(control_frame, text="Sair", command=root.quit)\
    .pack(side="left", padx=5)

# Inicia o monitoramento ao iniciar o script
start_monitoring()

root.mainloop()
stop_continuous_ping()
