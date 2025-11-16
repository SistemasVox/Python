import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import sqlite3
import calendar
import math
import os
import configparser

# --- CONSTANTES DAS QUERIES ---
DOWNTIME_CTES = """
WITH DowntimeEvents AS (
    SELECT
        data_hora AS inicio_queda,
        (SELECT MIN(p2.data_hora) 
         FROM ping_logs p2 
         WHERE p2.data_hora > p1.data_hora AND p2.status = 'reconnection') AS fim_queda
    FROM
        ping_logs p1
    WHERE
        status = 'internet_fall'
),
Duracoes AS (
    SELECT
        inicio_queda,
        fim_queda,
        STRFTIME('%s', fim_queda) - STRFTIME('%s', inicio_queda) AS duracao_segundos
    FROM
        DowntimeEvents
    WHERE
        fim_queda IS NOT NULL
)
"""

# --- FUNÇÃO DE FORMATAÇÃO DE TEMPO ---
def formatar_segundos_dhms(segundos_totais):
    """Converte um total de segundos para o formato 'Xd HH:MM:SS'."""
    if segundos_totais is None: return "N/A"
    try:
        segundos_totais = int(float(segundos_totais))
    except ValueError:
        return "N/A"

    dias = segundos_totais // 86400
    segundos_restantes = segundos_totais % 86400
    horas = segundos_restantes // 3600
    segundos_restantes %= 3600
    minutos = segundos_restantes // 60
    segundos = segundos_restantes % 60
    
    if dias > 0:
        return f"{dias}d {horas:02}:{minutos:02}:{segundos:02}"
    else:
        return f"{horas:02}:{minutos:02}:{segundos:02}"

# --- CLASSE PRINCIPAL DA APLICAÇÃO ---

class DowntimeCalculatorApp:
    
    def __init__(self, root):
        """Inicializa a aplicação."""
        self.root = root
        self.db_path = None
        self.last_directory = os.path.expanduser("~")
        self.config_file = 'config.ini'
        self.report_buttons = []

        # --- Configuração de Estilo Cyberpunk ---
        self.BG_COLOR = '#1a001a'
        self.FG_COLOR = '#00ffff'
        self.HL_COLOR = '#00ffff'
        self.BTN_COLOR = '#ff00ff'
        self.BTN_TEXT = '#1a001a'
        self.ENTRY_BG = '#330033'
        
        self.FONT_FAMILY = "Rajdhani"
        self.FONT_NORMAL = (self.FONT_FAMILY, 10)
        self.FONT_BOLD = (self.FONT_FAMILY, 10, "bold")
        self.FONT_TITLE = (self.FONT_FAMILY, 11, "bold")

        self.setup_style()
        self.create_widgets()
        self.load_config()
        self.update_button_states() # Heurística: Prevenção de Erros

    def setup_style(self):
        """Aplica o tema cyberpunk global."""
        style = ttk.Style()
        style.theme_use('clam')

        style.configure('.',
            background=self.BG_COLOR,
            foreground=self.FG_COLOR,
            font=self.FONT_NORMAL,
            fieldbackground=self.ENTRY_BG,
            bordercolor=self.HL_COLOR)
        
        self.root.configure(background=self.BG_COLOR)
        style.configure('TFrame', background=self.BG_COLOR)
        style.configure('TLabel', background=self.BG_COLOR, foreground=self.FG_COLOR)
        style.configure('Status.TLabel', foreground=self.HL_COLOR, font=(self.FONT_FAMILY, 9, "italic"))

        # LabelFrames (Caixas de Grupo "Holográficas")
        style.configure('TLabelFrame',
            background=self.BG_COLOR,
            bordercolor=self.HL_COLOR,
            relief='solid')
        style.configure('TLabelFrame.Label',
            background=self.BG_COLOR,
            foreground=self.HL_COLOR,
            font=self.FONT_TITLE)

        style.configure('TButton',
            font=self.FONT_BOLD,
            background=self.BTN_COLOR,
            foreground=self.BTN_TEXT,
            bordercolor=self.BTN_COLOR,
            relief='raised')
        style.map('TButton',
            background=[('active', self.HL_COLOR), ('disabled', '#550055')],
            foreground=[('active', self.BTN_TEXT), ('disabled', '#999999')])

        style.configure('TEntry',
            foreground=self.FG_COLOR,
            fieldbackground=self.ENTRY_BG,
            insertcolor=self.FG_COLOR,
            bordercolor=self.HL_COLOR,
            borderwidth=1,
            relief='solid')

    def create_widgets(self):
        """Cria e organiza todos os widgets na janela."""
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill='both', expand=True)

        # --- [ 1 ] Seção de Inputs ---
        frame_inputs = ttk.LabelFrame(main_frame, text=" [ 1 ] CONFIGURAÇÃO ", padding="10")
        frame_inputs.pack(fill='x', expand=False, pady=5)
        
        ttk.Label(frame_inputs, text="Arquivo de Log (BD):").grid(row=0, column=0, sticky='w', padx=5, pady=5)
        self.label_db_name = ttk.Label(frame_inputs, text="Nenhum arquivo carregado", font=(self.FONT_FAMILY, 9, "italic"), width=50)
        self.label_db_name.grid(row=1, column=1, sticky='w', padx=5, pady=2)
        
        self.btn_browse = ttk.Button(frame_inputs, text="Procurar...", width=15, command=self.on_browse)
        self.btn_browse.grid(row=1, column=0, sticky='w', padx=5, pady=2)

        ttk.Label(frame_inputs, text="Valor Fatura Mensal (R$):").grid(row=2, column=0, sticky='w', padx=5, pady=10)
        self.entry_valor_mensal = ttk.Entry(frame_inputs, width=15)
        self.entry_valor_mensal.grid(row=2, column=1, sticky='w', padx=5, pady=10)
        frame_inputs.grid_columnconfigure(1, weight=1)

        # --- [ 2 ] Seção de Relatórios Rápidos ---
        frame_relatorios = ttk.LabelFrame(main_frame, text=" [ 2 ] RELATÓRIOS RÁPIDOS ", padding="10")
        frame_relatorios.pack(fill='x', expand=False, pady=5)
        frame_relatorios.grid_columnconfigure((0,1,2), weight=1)

        btn_map = [
            ("Offline por Dia", self.show_minutos_por_dia),
            ("Falhas por Dia (Semana)", self.show_falhas_por_semana),
            ("MTBF (Tempo entre Falhas)", self.show_mtbf),
            ("Duração de Cada Queda", self.show_duracao_quedas),
            ("Última Ocorrência (Status)", self.show_ultima_ocorrencia),
            ("Resumo Diário (Pivot)", self.show_resumo_diario_pivot),
            ("Eventos (Últimas 24h)", self.show_eventos_24h),
            ("Horário de Maior Incidência", self.show_horario_incidencia),
            ("Quedas Graves por Dia", self.show_quedas_graves_dia)
        ]

        row, col = 0, 0
        for text, command in btn_map:
            btn = ttk.Button(frame_relatorios, text=text, command=command)
            btn.grid(row=row, column=col, sticky='ew', padx=2, pady=2)
            self.report_buttons.append(btn)
            col += 1
            if col > 2:
                col = 0
                row += 1
        
        # --- [ 3 ] Seção de Cálculo de Desconto ---
        frame_desconto = ttk.LabelFrame(main_frame, text=" [ 3 ] CÁLCULO DE DESCONTO MENSAL ", padding="10")
        frame_desconto.pack(fill='x', expand=False, pady=5)
        
        self.btn_calcular_desconto = ttk.Button(frame_desconto, text=">>> CALCULAR DESCONTO MENSAL <<<", command=self.on_calculate_desconto)
        self.btn_calcular_desconto.pack(fill='x', expand=True, ipady=5)

        # --- [ 4 ] Seção de Resultados ---
        frame_resultados = ttk.LabelFrame(main_frame, text=" [ 4 ] RESULTADOS ", padding="10")
        frame_resultados.pack(fill='both', expand=True, pady=5)

        self.text_resultados = scrolledtext.ScrolledText(frame_resultados, wrap=tk.WORD, 
                                                         font=("Courier New", 10),
                                                         bg=self.ENTRY_BG,
                                                         fg=self.FG_COLOR,
                                                         insertbackground=self.FG_COLOR,
                                                         borderwidth=1,
                                                         relief='solid')
        self.text_resultados.pack(fill='both', expand=True, pady=(0, 5))
        self.text_resultados.config(state='disabled')

        self.btn_copy_report = ttk.Button(frame_resultados, text="Copiar Relatório", command=self.copy_to_clipboard)
        self.btn_copy_report.pack(fill='x', expand=False, pady=(0, 5))
        
        # --- Botões de Ação Inferiores ---
        frame_bottom_buttons = ttk.Frame(frame_resultados)
        frame_bottom_buttons.pack(fill='x', expand=False)
        frame_bottom_buttons.grid_columnconfigure((0, 1), weight=1)

        self.btn_about = ttk.Button(frame_bottom_buttons, text="Sobre", command=self.show_about)
        self.btn_about.grid(row=0, column=0, sticky='ew', padx=2, pady=2)
        
        self.btn_exit = ttk.Button(frame_bottom_buttons, text="Sair", command=self.exit_app)
        self.btn_exit.grid(row=0, column=1, sticky='ew', padx=2, pady=2)
        
        # --- [ 5 ] Barra de Status ---
        self.status_bar = ttk.Label(main_frame, text="Carregue um arquivo .db para começar.", style='Status.TLabel', anchor='w')
        self.status_bar.pack(side='bottom', fill='x', pady=(5, 0))

    # --- Funções de Lógica e Heurísticas ---

    def update_status(self, message):
        """Heurística: Visibilidade do status do sistema."""
        self.status_bar.config(text=message)

    def update_button_states(self):
        """Heurística: Prevenção de erros. Ativa/Desativa botões."""
        state = 'normal' if self.db_path else 'disabled'
        for btn in self.report_buttons:
            btn.config(state=state)
        self.btn_calcular_desconto.config(state=state)
        self.btn_copy_report.config(state=state)

    def load_config(self):
        """Heurística: Reconhecimento. Carrega último valor da fatura."""
        config = configparser.ConfigParser()
        valor_default = "149.90"
        if os.path.exists(self.config_file):
            try:
                config.read(self.config_file)
                valor = config.get('DEFAULT', 'valor_fatura', fallback=valor_default)
            except Exception:
                valor = valor_default
        else:
            valor = valor_default
        
        self.entry_valor_mensal.insert(0, valor)

    def save_config(self, valor):
        """Heurística: Reconhecimento. Salva último valor da fatura."""
        config = configparser.ConfigParser()
        config['DEFAULT'] = {'valor_fatura': valor}
        try:
            with open(self.config_file, 'w') as f:
                config.write(f)
        except Exception as e:
            self.update_status(f"Erro ao salvar config: {e}")

    def copy_to_clipboard(self):
        """Heurística: Flexibilidade e eficiência."""
        content = self.text_resultados.get('1.0', tk.END)
        if content.strip():
            self.root.clipboard_clear()
            self.root.clipboard_append(content)
            self.update_status("Relatório copiado para a área de transferências!")
        else:
            self.update_status("Nada para copiar.")

    def on_browse(self):
        """Abre o seletor de arquivos e ativa os botões."""
        filepath = filedialog.askopenfilename(
            title="Selecione o banco de dados",
            initialdir=self.last_directory,
            filetypes=[("Arquivos de Banco de Dados", "*.db"), ("SQLite", "*.sqlite"), ("Todos os arquivos", "*.*")]
        )
        if filepath:
            self.db_path = filepath
            self.last_directory = os.path.dirname(filepath)
            self.label_db_name.config(text=f"{os.path.basename(filepath)}")
            self.update_button_states()
            self.update_status(f"Arquivo carregado: {os.path.basename(filepath)}")

    def run_db_query(self, sql, params=()):
        """Função centralizada para executar consultas no banco de dados."""
        if not self.db_path:
            self.update_status("Erro: Nenhum banco de dados selecionado.")
            return None
        
        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            cursor = conn.cursor()
            cursor.execute(sql, params)
            results = cursor.fetchall()
            conn.close()
            return results
        except sqlite3.OperationalError as e:
            if "read-only" in str(e):
                 self.update_status("Erro de Acesso (read-only). Tentando modo normal...")
                 try:
                     conn = sqlite3.connect(self.db_path)
                     cursor = conn.cursor()
                     cursor.execute(sql, params)
                     results = cursor.fetchall()
                     conn.close()
                     return results
                 except Exception as e_norm:
                     messagebox.showerror("Erro de SQL", f"Falha ao tentar novamente: {e_norm}")
                     self.update_status(f"Erro: {e_norm}")
                     return None
            else:
                 messagebox.showerror("Erro de SQL", f"Ocorreu um erro: {e}")
                 self.update_status(f"Erro: {e}")
                 return None
        except Exception as e:
            messagebox.showerror("Erro de SQL", f"Ocorreu um erro: {e}")
            self.update_status(f"Erro: {e}")
            return None

    def update_report_text(self, title, content):
        """Limpa e atualiza a caixa de texto de resultados."""
        self.text_resultados.config(state='normal')
        self.text_resultados.delete('1.0', tk.END)
        self.text_resultados.insert(tk.END, f"RELATÓRIO: {title}\n")
        self.text_resultados.insert(tk.END, "="*60 + "\n\n")
        
        if not content:
            self.text_resultados.insert(tk.END, "Nenhum dado encontrado para este relatório.")
        else:
            self.text_resultados.insert(tk.END, content)
            
        self.text_resultados.config(state='disabled')
        
    def show_about(self):
        """Exibe a janela 'Sobre'."""
        messagebox.showinfo("Sobre - Downtime Calculator",
                            "Criado por SistemasVox com Auxílio do Gemini 2.5 Pro.")

    def exit_app(self):
        """Fecha a aplicação."""
        self.root.destroy()

    # --- Funções dos Botões de Relatório ---
    
    def show_minutos_por_dia(self):
        sql = DOWNTIME_CTES + """
        SELECT STRFTIME('%Y-%m-%d', inicio_queda) AS dia, SUM(duracao_segundos)
        FROM Duracoes GROUP BY dia ORDER BY dia DESC;
        """
        results = self.run_db_query(sql)
        if results is None: return

        content = ""
        for dia, total_segundos in results:
            formatted_time = formatar_segundos_dhms(total_segundos)
            content += f"Dia: {dia}  |  Tempo Offline: {formatted_time}\n"
        self.update_report_text("Total de Minutos Offline por Dia", content)
        self.update_status("Relatório 'Offline por Dia' gerado.")

    def show_falhas_por_semana(self):
        sql = """
        SELECT
            CASE STRFTIME('%w', data_hora)
                WHEN '0' THEN 'Domingo' WHEN '1' THEN 'Segunda-feira'
                WHEN '2' THEN 'Terça-feira' WHEN '3' THEN 'Quarta-feira'
                WHEN '4' THEN 'Quinta-feira' WHEN '5' THEN 'Sexta-feira'
                WHEN '6' THEN 'Sábado'
            END AS dia_da_semana, COUNT(id) AS total_falhas
        FROM ping_logs WHERE status IN ('internet_fall', 'ping_fall')
        GROUP BY dia_da_semana ORDER BY STRFTIME('%w', data_hora) ASC;
        """
        results = self.run_db_query(sql)
        if results is None: return
        
        content = ""
        for dia, total in results:
            dia_str = dia if dia is not None else "Desconhecido" 
            content += f"{dia_str:<15} | {total} falhas\n"
        self.update_report_text("Falhas por Dia da Semana", content)
        self.update_status("Relatório 'Falhas por Semana' gerado.")

    def show_mtbf(self):
        sql = """
        WITH Falhas AS (
            SELECT data_hora, LAG(data_hora, 1) OVER (ORDER BY data_hora) AS data_falha_anterior
            FROM ping_logs WHERE status = 'internet_fall'
        )
        SELECT AVG(STRFTIME('%s', data_hora) - STRFTIME('%s', data_falha_anterior))
        FROM Falhas WHERE data_falha_anterior IS NOT NULL;
        """
        results = self.run_db_query(sql)
        if results is None or not results: return
        
        avg_seconds = results[0][0]
        formatted_mtbf = formatar_segundos_dhms(avg_seconds)
        content = (f"Tempo Média Entre Falhas (MTBF) para 'internet_fall':\n\n"
                   f"{formatted_mtbf}\n\n"
                   f"(Isso representa o tempo médio de estabilidade *entre* duas quedas totais)")
        self.update_report_text("Tempo Média Entre Falhas (MTBF)", content)
        self.update_status("Relatório 'MTBF' gerado.")

    def show_duracao_quedas(self):
        sql = DOWNTIME_CTES + """
        SELECT inicio_queda, fim_queda, duracao_segundos
        FROM Duracoes ORDER BY inicio_queda DESC LIMIT 50;
        """
        results = self.run_db_query(sql)
        if results is None: return
        
        content = "Mostrando as 50 quedas de 'internet_fall' mais recentes:\n\n"
        for inicio, fim, duracao_s in results:
            formatted_duracao = formatar_segundos_dhms(duracao_s)
            content += f"Início: {inicio}\n"
            content += f"Fim:    {fim}\n"
            content += f"Duração: {formatted_duracao}\n"
            content += "-"*40 + "\n"
        
        self.update_report_text("Duração de Cada Queda de Internet (Downtime)", content)
        self.update_status("Relatório 'Duração de Quedas' gerado.")

    def show_ultima_ocorrencia(self):
        sql = """
        SELECT status, MAX(data_hora) AS ultima_ocorrencia
        FROM ping_logs GROUP BY status ORDER BY ultima_ocorrencia DESC;
        """
        results = self.run_db_query(sql)
        if results is None: return
        
        content = ""
        for status, ultima in results:
            content += f"{status:<15} | {ultima}\n"
        self.update_report_text("Última Ocorrência de Cada Status", content)
        self.update_status("Relatório 'Última Ocorrência' gerado.")

    def show_resumo_diario_pivot(self):
        sql = """
        SELECT
            STRFTIME('%Y-%m-%d', data_hora) AS dia,
            COUNT(CASE WHEN status = 'internet_fall' THEN 1 END) AS quedas_completas,
            COUNT(CASE WHEN status = 'ping_fall' THEN 1 END) AS quedas_de_ping,
            COUNT(CASE WHEN status = 'reconnection' THEN 1 END) AS reconexoes,
            AVG(CASE WHEN ping_anterior IS NOT NULL THEN CAST(ping_anterior AS INTEGER) END) AS media_ping_dia
        FROM ping_logs GROUP BY dia ORDER BY dia DESC LIMIT 30;
        """
        results = self.run_db_query(sql)
        if results is None: return
        
        content = f"{'Dia':<12} | {'Quedas':<8} | {'PingFall':<10} | {'Conexões':<10} | {'Ping Médio'}\n"
        content += "="*60 + "\n"
        for dia, quedas, ping_fall, reconexoes, media_ping in results:
            ping_str = f"{media_ping:.1f} ms" if media_ping else "N/A"
            content += f"{dia:<12} | {quedas:<8} | {ping_fall:<10} | {reconexoes:<10} | {ping_str}\n"
        self.update_report_text("Resumo Diário de Eventos (Pivot)", content)
        self.update_status("Relatório 'Resumo Diário' gerado.")

    def show_eventos_24h(self):
        sql = """
        SELECT data_hora, status, ping_anterior FROM ping_logs
        WHERE data_hora >= STRFTIME('%Y-%m-%d %H:%M:%S', 'now', '-1 day')
        ORDER BY data_hora DESC;
        """
        results = self.run_db_query(sql)
        if results is None: return
        
        content = ""
        for data_hora, status, ping in results:
            ping_str = f"{ping} ms" if ping else "NULL"
            content += f"{data_hora} | {status:<15} | {ping_str}\n"
        self.update_report_text("Eventos nas Últimas 24 Horas", content)
        self.update_status("Relatório 'Eventos 24h' gerado.")

    def show_horario_incidencia(self):
        sql = """
        SELECT STRFTIME('%H', data_hora) AS hora_do_dia, COUNT(id) AS total_falhas
        FROM ping_logs WHERE status = 'internet_fall' OR status = 'ping_fall'
        GROUP BY hora_do_dia ORDER BY total_falhas DESC;
        """
        results = self.run_db_query(sql)
        if results is None: return
        
        content = "Hora do Dia | Total de Falhas (internet_fall + ping_fall)\n"
        content += "-"*50 + "\n"
        for hora, total in results:
            content += f" {hora:02}h - {int(hora)+1:02}h   | {total} falhas\n"
        self.update_report_text("Horário de Maior Incidência de Falhas", content)
        self.update_status("Relatório 'Horário de Incidência' gerado.")

    def show_quedas_graves_dia(self):
        sql = """
        SELECT STRFTIME('%Y-%m-%d', data_hora) AS dia, COUNT(id) AS total_quedas
        FROM ping_logs WHERE status = 'internet_fall'
        GROUP BY dia ORDER BY dia DESC;
        """
        results = self.run_db_query(sql)
        if results is None: return
        
        content = ""
        for dia, total in results:
            content += f"Dia: {dia} | {total} quedas graves ('internet_fall')\n"
        self.update_report_text("Quedas Graves ('internet_fall') por Dia", content)
        self.update_status("Relatório 'Quedas Graves' gerado.")

    def on_calculate_desconto(self):
        """Calcula o desconto mensal com base na fatura."""
        valor_mensal_str = self.entry_valor_mensal.get()

        try:
            valor_mensal = float(valor_mensal_str.replace(',', '.'))
            if valor_mensal <= 0: raise ValueError("O valor deve ser positivo.")
        except ValueError:
            messagebox.showerror("Erro no Valor", "Por favor, insira um valor mensal válido (ex: 99.90).")
            self.update_status("Erro: Valor da fatura inválido.")
            return

        sql_query = DOWNTIME_CTES + """
        SELECT STRFTIME('%Y-%m', inicio_queda) AS mes, SUM(duracao_segundos)
        FROM Duracoes GROUP BY mes ORDER BY mes DESC;
        """
        resultados_sql = self.run_db_query(sql_query)
        if resultados_sql is None: return

        if not resultados_sql:
            self.update_report_text("Cálculo de Desconto", "Nenhum dado de 'internet_fall' encontrado para calcular.")
            self.update_status("Cálculo de desconto: Nenhum dado.")
            return

        self.save_config(valor_mensal_str) # Salva o valor da fatura

        relatorio_final = f"Cálculo de Desconto - Fatura Mensal: R$ {valor_mensal:.2f}\n"
        relatorio_final += "="*60 + "\n\n"

        for linha in resultados_sql:
            mes_str, total_segundos_offline = linha
            if total_segundos_offline is None or total_segundos_offline == 0: continue

            total_minutos_offline_float = total_segundos_offline / 60.0
            minutos_offline_arredondados = math.ceil(total_minutos_offline_float)
            
            ano, mes_int = map(int, mes_str.split('-'))
            dias_no_mes = calendar.monthrange(ano, mes_int)[1]
            minutos_totais_no_mes = dias_no_mes * 24 * 60

            percentual_offline = minutos_offline_arredondados / minutos_totais_no_mes
            desconto_bruto = percentual_offline * valor_mensal
            desconto_final_arredondado = math.ceil(desconto_bruto * 100) / 100

            relatorio_final += f"Mês: {mes_str}\n"
            relatorio_final += f"  - Tempo Offline (bruto):   {formatar_segundos_dhms(total_segundos_offline)}\n"
            relatorio_final += f"  - Minutos p/ Cálculo (teto): {minutos_offline_arredondados} min\n"
            relatorio_final += f"  - Total de Minutos no Mês:   {minutos_totais_no_mes} min ({dias_no_mes} dias)\n"
            relatorio_final += f"  - Percentual Offline:        {percentual_offline * 100:.4f}%\n"
            relatorio_final += f"  - DESCONTO DEVIDO (R$):    {desconto_final_arredondado:.2f}\n\n"

        self.update_report_text("Cálculo de Desconto Mensal", relatorio_final)
        self.update_status("Relatório de desconto gerado com sucesso.")


# --- INICIALIZAÇÃO DA APLICAÇÃO ---
if __name__ == "__main__":
    root = tk.Tk()
    root.title("Calculadora de Desconto por Downtime v3.2 - Cyberpunk Edition")
    root.geometry("750x968")        
    app = DowntimeCalculatorApp(root)
    root.mainloop()