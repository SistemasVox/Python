import tkinter as tk
from tkinter import scrolledtext, messagebox, filedialog
import random
import re
import webbrowser
import os
import sys
import string
import subprocess
from datetime import datetime

# ------------------------------------------------------------
# TEMA CYBERPUNK RETRÔ
# ------------------------------------------------------------
CORES = {
    "bg": "#0b0014",          # fundo profundo
    "painel": "#160225",      # painéis
    "fundo_texto": "#05010d", # caixas de texto
    "fundo_log": "#02100a",   # console de log
    "magenta": "#ff2a6d",
    "ciano": "#05d9e8",
    "amarelo": "#f9f871",
    "verde": "#05ff9c",
    "roxo": "#7700a6",
    "texto": "#d1f7ff",
    "texto_escuro": "#6c6c93",
}

FONTES = {
    "titulo": ("Courier New", 20, "bold"),
    "sub": ("Courier New", 9, "bold"),
    "corpo": ("Courier New", 10),
    "mono": ("Consolas", 10, "normal"),
    "botao": ("Courier New", 10, "bold"),
}

# ------------------------------------------------------------
# Gerador de Caça-Palavras (sem diagonais por padrão)
# ------------------------------------------------------------
class WordSearchGenerator:
    def __init__(self, words, size=10, allow_diagonals=False):
        self.words = [w.upper() for w in words if w.strip() and len(w) >= 3]
        self.words.sort(key=len, reverse=True)
        self.size = size
        self.grid = None
        self.word_positions = {}

        base = [(0, 1), (1, 0), (0, -1), (-1, 0)]
        diag = [(1, 1), (1, -1), (-1, -1), (-1, 1)]
        self.directions = base + (diag if allow_diagonals else [])

    # ---- monta a grade aproveitando o máximo de palavras (até max_words) ----
    def generate_max(self, max_words, restarts=15):
        limit = min(max_words, len(self.words))
        if limit <= 0:
            return False

        best_count = -1
        best_grid = None
        best_positions = None

        for _ in range(restarts):
            self.grid = [[' ' for _ in range(self.size)] for _ in range(self.size)]
            self.word_positions = {}

            candidates_words = self.words[:]
            random.shuffle(candidates_words)

            for word in candidates_words:
                if len(self.word_positions) >= limit:
                    break

                placed = False

                # 1) posições que cruzam com letras já colocadas
                cross = self._cross_candidates(word)
                random.shuffle(cross)
                for r, c, d in cross:
                    if self._can_place(word, r, c, d):
                        self._place_word(word, r, c, d)
                        placed = True
                        break

                # 2) fallback aleatório (útil no começo, com a grade vazia)
                if not placed:
                    for _ in range(100):
                        row = random.randint(0, self.size - 1)
                        col = random.randint(0, self.size - 1)
                        direction = random.choice(self.directions)
                        if self._can_place(word, row, col, direction):
                            self._place_word(word, row, col, direction)
                            placed = True
                            break

            placed_count = len(self.word_positions)
            if placed_count > best_count:
                best_count = placed_count
                best_grid = [r[:] for r in self.grid]
                best_positions = {w: p[:] for w, p in self.word_positions.items()}

            if best_count >= limit:
                break

        if best_count <= 0:
            return False

        self.grid = best_grid
        self.word_positions = best_positions
        self._fill_empty()

        # 3) aproveita palavras que ficaram "dentro" de outras (ou surgiram na grade)
        for word in self.words:
            if len(self.word_positions) >= limit:
                break
            if word not in self.word_positions:
                pos = self._find_word(word)
                if pos:
                    self.word_positions[word] = pos

        return True

    def _cross_candidates(self, word):
        """Posições (row, col, direction) em que a palavra cruza letras já colocadas."""
        candidates = []
        for r in range(self.size):
            for c in range(self.size):
                ch = self.grid[r][c]
                if ch == ' ':
                    continue
                for i, wc in enumerate(word):
                    if wc != ch:
                        continue
                    col = c - i                      # horizontal
                    if 0 <= col and col + len(word) <= self.size:
                        candidates.append((r, col, (0, 1)))
                    row = r - i                      # vertical
                    if 0 <= row and row + len(word) <= self.size:
                        candidates.append((row, c, (1, 0)))
        return candidates

    def _find_word(self, word):
        """Procura a palavra na grade pronta (nos 4 sentidos); retorna posições ou None."""
        for r in range(self.size):
            for c in range(self.size):
                for dr, dc in ((0, 1), (1, 0), (0, -1), (-1, 0)):
                    ok = True
                    for i, ch in enumerate(word):
                        rr, cc = r + dr * i, c + dc * i
                        if not (0 <= rr < self.size and 0 <= cc < self.size) or self.grid[rr][cc] != ch:
                            ok = False
                            break
                    if ok:
                        return [(r + dr * i, c + dc * i) for i in range(len(word))]
        return None

    def _can_place(self, word, row, col, direction):
        dr, dc = direction
        end_row = row + (len(word) - 1) * dr
        end_col = col + (len(word) - 1) * dc

        if end_row < 0 or end_row >= self.size or end_col < 0 or end_col >= self.size:
            return False

        for i, ch in enumerate(word):
            r = row + i * dr
            c = col + i * dc
            current = self.grid[r][c]

            if current != ' ' and current != ch:
                return False

        return True

    def _place_word(self, word, row, col, direction):
        dr, dc = direction
        positions = []

        for i, ch in enumerate(word):
            r = row + i * dr
            c = col + i * dc
            self.grid[r][c] = ch
            positions.append((r, c))

        self.word_positions[word] = positions

    def _fill_empty(self):
        letters = string.ascii_uppercase

        for r in range(self.size):
            for c in range(self.size):
                if self.grid[r][c] == ' ':
                    self.grid[r][c] = random.choice(letters)

    def get_grid(self):
        return self.grid

    def get_word_positions(self):
        return self.word_positions


# ------------------------------------------------------------
# Duas grades INDEPENDENTES, cada uma com o máximo de palavras
# ------------------------------------------------------------
def generate_two_grids(all_words, size=10, max_words=15, allow_diagonals=False):
    filtered = [w for w in all_words if len(w) >= 3]

    if len(filtered) < 2:
        return None, []

    results = []
    used = set()

    for _ in range(2):
        gen = WordSearchGenerator(filtered, size, allow_diagonals)
        if gen.generate_max(max_words):
            results.append((gen.get_grid(), gen.get_word_positions()))
            used.update(gen.get_word_positions().keys())

    if len(results) < 2:
        return None, []

    return used, results


# ------------------------------------------------------------
# Interface gráfica cyberpunk retrô
# ------------------------------------------------------------
class WordSearchApp:
    def __init__(self, root):
        self.root = root
        self.root.title("CAÇA-PALAVRAS // NEON v2.077")
        self.root.geometry("760x820")
        self.root.configure(bg=CORES["bg"])

        self.last_html = None
        self.last_filename = None
        self.last_words = set()

        # ----- cabeçalho -----
        header = tk.Frame(root, bg=CORES["bg"])
        header.pack(fill=tk.X, padx=12, pady=(12, 4))
        tk.Label(header, text="▚▚ CAÇA-PALAVRAS ▞▞", font=FONTES["titulo"],
                 bg=CORES["bg"], fg=CORES["magenta"]).pack(side=tk.LEFT)
        tk.Label(header, text="// PROTOCOLO NEON v2.077", font=FONTES["sub"],
                 bg=CORES["bg"], fg=CORES["ciano"]).pack(side=tk.LEFT, padx=(10, 0), pady=(10, 0))

        # ----- painel principal -----
        painel = tk.Frame(root, bg=CORES["painel"], bd=1, relief="solid",
                          highlightbackground=CORES["magenta"], highlightthickness=1)
        painel.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)

        tk.Label(painel, text="> INJETE O TEXTO-FONTE (as palavras serão extraídas automaticamente):",
                 font=FONTES["corpo"], bg=CORES["painel"], fg=CORES["amarelo"],
                 anchor="w").pack(fill=tk.X, padx=10, pady=(10, 4))

        self.text_area = scrolledtext.ScrolledText(
            painel, wrap=tk.WORD, height=10, font=FONTES["mono"],
            bg=CORES["fundo_texto"], fg=CORES["ciano"],
            insertbackground=CORES["magenta"],
            selectbackground=CORES["roxo"], selectforeground="#ffffff",
            bd=0, relief="flat", highlightthickness=1,
            highlightbackground=CORES["ciano"])
        self.text_area.pack(fill=tk.BOTH, expand=True, padx=10)
        self.text_area.bind("<KeyRelease>", self._update_stats)

        self.stats_var = tk.StringVar(value=">> 0 PALAVRAS DETECTADAS")
        tk.Label(painel, textvariable=self.stats_var, font=FONTES["sub"],
                 bg=CORES["painel"], fg=CORES["verde"], anchor="w").pack(fill=tk.X, padx=10, pady=(4, 0))

        # ----- opções -----
        opt = tk.Frame(painel, bg=CORES["painel"])
        opt.pack(fill=tk.X, padx=10, pady=8)

        tk.Label(opt, text="GRADE:", font=FONTES["sub"],
                 bg=CORES["painel"], fg=CORES["texto"]).pack(side=tk.LEFT)
        self.size_var = tk.StringVar(value="10")
        tk.Spinbox(opt, from_=5, to=30, width=4, textvariable=self.size_var, font=FONTES["mono"],
                   bg=CORES["fundo_texto"], fg=CORES["ciano"], buttonbackground=CORES["painel"],
                   insertbackground=CORES["magenta"], bd=0,
                   highlightthickness=1, highlightbackground=CORES["magenta"]).pack(side=tk.LEFT, padx=(4, 12))

        tk.Label(opt, text="MAX PALAVRAS:", font=FONTES["sub"],
                 bg=CORES["painel"], fg=CORES["texto"]).pack(side=tk.LEFT)
        self.max_words_var = tk.StringVar(value="15")
        tk.Spinbox(opt, from_=5, to=30, width=4, textvariable=self.max_words_var, font=FONTES["mono"],
                   bg=CORES["fundo_texto"], fg=CORES["ciano"], buttonbackground=CORES["painel"],
                   insertbackground=CORES["magenta"], bd=0,
                   highlightthickness=1, highlightbackground=CORES["magenta"]).pack(side=tk.LEFT, padx=(4, 12))

        self.diag_var = tk.BooleanVar(value=False)
        tk.Checkbutton(opt, text="DIAGONAIS", variable=self.diag_var, font=FONTES["sub"],
                       bg=CORES["painel"], fg=CORES["texto"], selectcolor=CORES["fundo_texto"],
                       activebackground=CORES["painel"], activeforeground=CORES["ciano"],
                       highlightthickness=0,
                       command=lambda: self.log("DIAGONAIS " + ("ATIVADAS" if self.diag_var.get() else "DESATIVADAS"))
                       ).pack(side=tk.LEFT, padx=(0, 12))

        self.browser_var = tk.BooleanVar(value=True)
        tk.Checkbutton(opt, text="ABRIR NAVEGADOR", variable=self.browser_var, font=FONTES["sub"],
                       bg=CORES["painel"], fg=CORES["texto"], selectcolor=CORES["fundo_texto"],
                       activebackground=CORES["painel"], activeforeground=CORES["ciano"],
                       highlightthickness=0).pack(side=tk.LEFT)

        # ----- botões -----
        btns = tk.Frame(painel, bg=CORES["painel"])
        btns.pack(fill=tk.X, padx=10, pady=(0, 10))
        self._btn(btns, "[GERAR]", self.generate, CORES["verde"]).pack(side=tk.LEFT, padx=(0, 6))
        self._btn(btns, "[LIMPAR]", self.clear, CORES["amarelo"]).pack(side=tk.LEFT, padx=(0, 6))
        self._btn(btns, "[SALVAR COMO]", self.save_as, CORES["ciano"]).pack(side=tk.LEFT, padx=(0, 6))
        self._btn(btns, "[COPIAR PALAVRAS]", self.copy_words, CORES["magenta"]).pack(side=tk.LEFT, padx=(0, 6))
        self._btn(btns, "[PASTA]", self.open_folder, CORES["texto"]).pack(side=tk.LEFT, padx=(0, 6))
        self._btn(btns, "[SAIR]", self.root.quit, CORES["magenta"]).pack(side=tk.LEFT)

        # ----- log do sistema -----
        tk.Label(painel, text="LOG DO SISTEMA ▓", font=FONTES["sub"],
                 bg=CORES["painel"], fg=CORES["magenta"], anchor="w").pack(fill=tk.X, padx=10)
        self.log_area = tk.Text(painel, height=6, font=FONTES["mono"], bg=CORES["fundo_log"],
                                fg=CORES["verde"], bd=0, highlightthickness=1,
                                highlightbackground=CORES["verde"], state=tk.DISABLED)
        self.log_area.pack(fill=tk.BOTH, padx=10, pady=(2, 10))

        # ----- barra de status -----
        self.status_var = tk.StringVar(value="PRONTO. AGUARDANDO INPUT_")
        tk.Label(root, textvariable=self.status_var, font=FONTES["sub"], bg=CORES["bg"],
                 fg=CORES["amarelo"], anchor="w", bd=1, relief="solid",
                 padx=8, pady=4).pack(fill=tk.X, padx=12, pady=(0, 12))

        self.log("SISTEMA INICIALIZADO.")

    # ---- utilidades de interface ----
    def _btn(self, parent, text, cmd, cor):
        return tk.Button(parent, text=text, command=cmd, font=FONTES["botao"],
                         bg=CORES["painel"], fg=cor, activebackground=cor,
                         activeforeground=CORES["bg"], bd=1, relief="solid",
                         highlightthickness=1, highlightbackground=cor,
                         padx=8, pady=3, cursor="hand2",
                         disabledforeground=CORES["texto_escuro"])

    def log(self, msg):
        self.log_area.config(state=tk.NORMAL)
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_area.insert(tk.END, f"[{ts}] {msg}\n")
        self.log_area.see(tk.END)
        self.log_area.config(state=tk.DISABLED)

    def _update_stats(self, event=None):
        n = len(self.get_words())
        self.stats_var.set(f">> {n} PALAVRAS DETECTADAS")

    # ---- ações ----
    def get_words(self):
        text = self.text_area.get("1.0", tk.END)
        words = re.findall(r'[A-Za-zÀ-ÖØ-öø-ÿ]+', text)

        seen = set()
        unique = []

        for w in words:
            uw = w.upper()
            if uw not in seen:
                seen.add(uw)
                unique.append(uw)

        return unique

    def clear(self):
        self.text_area.delete("1.0", tk.END)
        self._update_stats()
        self.status_var.set("CAMPOS LIMPOS_")
        self.log("BUFFER DE TEXTO PURGADO.")

    def save_as(self):
        if not self.last_html:
            messagebox.showwarning("Aviso", "Gere uma grade antes de salvar.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".html",
            filetypes=[("Página HTML", "*.html"), ("Todos os arquivos", "*.*")],
            initialfile="caca_palavras.html")
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self.last_html)
                self.last_filename = os.path.abspath(path)
                self.status_var.set(f"SALVO: {os.path.basename(path)}_")
                self.log(f"ARQUIVO SALVO: {self.last_filename}")
            except Exception as e:
                messagebox.showerror("Erro", f"Não foi possível salvar:\n{e}")
                self.log(f"FALHA DE ESCRITA: {e}")

    def copy_words(self):
        if not self.last_words:
            messagebox.showwarning("Aviso", "Gere uma grade antes de copiar as palavras.")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(", ".join(sorted(self.last_words)))
        self.status_var.set(f"{len(self.last_words)} PALAVRAS COPIADAS_")
        self.log("LISTA DE PALAVRAS COPIADA PARA A ÁREA DE TRANSFERÊNCIA.")

    def open_folder(self):
        target = os.path.dirname(self.last_filename or os.path.abspath("caca_palavras.html"))
        try:
            if os.name == "nt":
                os.startfile(target)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", target])
            else:
                subprocess.Popen(["xdg-open", target])
            self.log(f"PASTA ABERTA: {target}")
        except Exception as e:
            messagebox.showerror("Erro", f"Não foi possível abrir a pasta:\n{e}")
            self.log(f"FALHA AO ABRIR PASTA: {e}")

    def generate(self):
        original_text = self.text_area.get("1.0", tk.END).strip()

        if not original_text:
            messagebox.showwarning("Aviso", "Por favor, digite algum texto.")
            self.status_var.set("ERRO: INPUT VAZIO_")
            self.log("FALHA: NENHUM TEXTO INJETADO.")
            return

        all_words = self.get_words()

        if len(all_words) < 2:
            messagebox.showwarning("Aviso", "Não foram encontradas palavras suficientes (mínimo 2).")
            self.status_var.set("ERRO: POUCAS PALAVRAS_")
            self.log("FALHA: MENOS DE 2 PALAVRAS VÁLIDAS.")
            return

        try:
            size = int(self.size_var.get())
            if size < 5:
                raise ValueError
        except:
            messagebox.showwarning("Aviso", "Tamanho inválido. Use um número inteiro >= 5.")
            self.status_var.set("ERRO: TAMANHO INVÁLIDO_")
            return

        try:
            max_words = int(self.max_words_var.get())
            if max_words < 2:
                raise ValueError
        except:
            messagebox.showwarning("Aviso", "Nº máximo inválido. Use um número inteiro >= 2.")
            self.status_var.set("ERRO: LIMITE INVÁLIDO_")
            return

        allow_diag = self.diag_var.get()

        self.status_var.set("PROCESSANDO...")
        self.log(f"EXTRAÇÃO: {len(all_words)} PALAVRAS ÚNICAS.")
        self.log(f"SINTETIZANDO GRADES {size}x{size} | LIMITE {max_words} | DIAGONAIS {'ON' if allow_diag else 'OFF'}...")
        self.root.update()

        subset, results = generate_two_grids(all_words, size, max_words, allow_diag)

        if not results:
            self.status_var.set("FALHA NA SÍNTESE_")
            self.log("ERRO: NÃO FOI POSSÍVEL MONTAR DUAS GRADES.")
            messagebox.showwarning(
                "Sem solução",
                f"Não foi possível criar duas grades {size}x{size}.\n"
                "Tente aumentar o tamanho da grade ou o número máximo de palavras."
            )
            return

        html_content = self._build_html(results, original_text, size, subset)
        self.last_html = html_content
        self.last_words = set(subset)
        filename = "caca_palavras.html"

        try:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(html_content)

            self.last_filename = os.path.abspath(filename)
            counts = [len(pos) for _, pos in results]
            self.log(f"GRADE 1: {counts[0]} PALAVRAS | GRADE 2: {counts[1]} PALAVRAS.")
            self.log(f"HTML COMPILADO: {self.last_filename}")

            if self.browser_var.get():
                webbrowser.open(self.last_filename)
                self.log("NAVEGADOR ACIONADO.")

            self.status_var.set(f"OK: GRADES {size}x{size} | {counts[0]}+{counts[1]} PALAVRAS_")
            messagebox.showinfo(
                "Sucesso",
                f"Página HTML gerada com duas grades {size}x{size} independentes.\n"
                f"Arquivo: {self.last_filename}"
            )

        except Exception as e:
            self.status_var.set("ERRO DE GRAVAÇÃO_")
            self.log(f"FALHA AO SALVAR ARQUIVO: {e}")
            messagebox.showerror("Erro", f"Não foi possível salvar o arquivo:\n{e}")

    def _build_html(self, grids, original_text, size, subset):
        """Gera HTML com duas grades lado a lado e título com palavras em negrito."""
        grid_rows = size
        grid_cols = size

        page_width_px = 720
        page_height_px = 1040   # A4 (~96 dpi) descontando margens de impressão
        margin_px = 40
        gap_px = 30

        usable_width = page_width_px - 2 * margin_px

        # Grades lado a lado (layout original); células limitadas pela largura
        cell_size = min((usable_width - gap_px) / 2 / grid_cols, 45)
        cell_size = round(max(cell_size, 12), 1)
        font_size = int(cell_size * 0.9)

        grid_height = cell_size * grid_rows

        # Título: maior corpo (18→12 pt) que caiba no espaço restante da folha
        avail_for_title = page_height_px - 2 * margin_px - grid_height - gap_px

        def title_height(pt):
            font_px = pt * 4 / 3
            line_h = font_px * 1.2
            cpl = max(10, int(usable_width / (font_px * 0.55)))  # caracteres/linha
            lines = 0
            for raw in original_text.splitlines() or [""]:
                n = len(raw)
                lines += max(1, (n + cpl - 1) // cpl)
            return lines * line_h

        title_pt = 12
        for pt in (18, 16, 14, 12):
            if title_height(pt) <= avail_for_title:
                title_pt = pt
                break

        def bold_selected(match):
            word = match.group(0)
            if word.upper() in subset:
                return f"<strong>{word}</strong>"
            else:
                return word

        import html

        escaped_text = html.escape(original_text)
        bolded_text = re.sub(r'[A-Za-zÀ-ÖØ-öø-ÿ]+', bold_selected, escaped_text)

        html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>Caça-Palavras</title>
<style>
@page {{
size: A4;
margin: 1cm;
}}
body {{
font-family: Arial, sans-serif;
margin: 0;
padding: 20px;
}}
.title {{
font-size: {title_pt}pt;
line-height: 1.2;
text-align: center;
margin-bottom: 20px;
white-space: pre-wrap;
}}
.container {{
display: grid;
grid-template-columns: 1fr 1fr;
gap: {gap_px}px;
justify-items: center;
}}
.grid-box {{
text-align: center;
}}
table {{
border-collapse: collapse;
margin: 0 auto;
}}
td {{
width: {cell_size}px;
height: {cell_size}px;
text-align: center;
vertical-align: middle;
font-size: {font_size}px;
border: 1px solid #333;
background-color: white;
}}
.page-break {{
display: none;
}}
@media print {{
.page-break {{
display: block;
page-break-before: always;
}}
}}
</style>
</head>
<body>
<div class="title">{bolded_text}</div>
<div class="container">
"""

        for idx, (grid, _) in enumerate(grids):
            html += f'        <div class="grid-box">\n'
            html += f'            <table>\n'

            for r, row in enumerate(grid):
                html += f'                <tr>\n'

                for c, ch in enumerate(row):
                    html += f'                    <td>{ch}</td>\n'

                html += f'                </tr>\n'

            html += f'            </table>\n'
            html += f'        </div>\n'

            if (idx + 1) % 2 == 0 and idx < len(grids) - 1:
                html += '    </div>\n    <div class="page-break"></div>\n    <div class="container">\n'

        html += """    </div>
</body>
</html>"""

        return html


# ------------------------------------------------------------
# Execução
# ------------------------------------------------------------
if __name__ == "__main__":
    root = tk.Tk()
    app = WordSearchApp(root)
    root.mainloop()