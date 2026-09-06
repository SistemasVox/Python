import tkinter as tk
from tkinter import scrolledtext, messagebox
import random
import re
import webbrowser
import os
import string

# ------------------------------------------------------------
# Gerador de Caça-Palavras (sem destaque na grade)
# ------------------------------------------------------------
class WordSearchGenerator:
    def __init__(self, words, size=10):
        self.words = [w.upper() for w in words if w.strip() and len(w) >= 3]
        self.words.sort(key=len, reverse=True)
        self.size = size
        self.grid = None
        self.word_positions = {}
        self.directions = [
            (0, 1), (1, 0), (1, 1), (1, -1),
            (0, -1), (-1, 0), (-1, -1), (-1, 1)
        ]

    def generate(self):
        if not self.words:
            return False

        for _ in range(200):
            self.grid = [[' ' for _ in range(self.size)] for _ in range(self.size)]
            self.word_positions = {}
            success = True

            shuffled = self.words[:]
            random.shuffle(shuffled)

            for word in shuffled:
                placed = False
                for _ in range(100):
                    row = random.randint(0, self.size - 1)
                    col = random.randint(0, self.size - 1)
                    direction = random.choice(self.directions)
                    if self._can_place(word, row, col, direction):
                        self._place_word(word, row, col, direction)
                        placed = True
                        break
                if not placed:
                    success = False
                    break

            if success:
                self._fill_empty()
                return True

        return False

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
# Função para gerar duas grades com subconjunto aleatório
# ------------------------------------------------------------
def generate_two_grids(all_words, size=10, max_words=15):
    filtered = [w for w in all_words if len(w) >= 3]
    if len(filtered) < 2:
        return None, []

    selected = filtered[:]
    random.shuffle(selected)
    for num_words in range(min(max_words, len(selected)), 1, -1):
        subset = selected[:num_words]
        results = []
        seen = set()
        attempts = 0
        max_attempts = 80
        while len(results) < 2 and attempts < max_attempts:
            gen = WordSearchGenerator(subset, size)
            if gen.generate():
                grid = gen.get_grid()
                positions = gen.get_word_positions()
                grid_str = ''.join(''.join(row) for row in grid)
                if grid_str not in seen:
                    seen.add(grid_str)
                    results.append((grid, positions))
            attempts += 1

        if len(results) == 2:
            return subset, results

    return None, []


# ------------------------------------------------------------
# Interface gráfica
# ------------------------------------------------------------
class WordSearchApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Gerador de Caça-Palavras")
        self.root.geometry("600x520")

        lbl = tk.Label(root, text="Digite o texto (as palavras serão extraídas automaticamente):")
        lbl.pack(pady=(10, 0))

        self.text_area = scrolledtext.ScrolledText(root, wrap=tk.WORD, width=60, height=15)
        self.text_area.pack(padx=10, pady=10, fill=tk.BOTH, expand=True)

        opt_frame = tk.Frame(root)
        opt_frame.pack(pady=5)

        tk.Label(opt_frame, text="Tamanho da grade:").pack(side=tk.LEFT, padx=5)
        self.size_var = tk.StringVar(value="10")
        size_spin = tk.Spinbox(opt_frame, from_=5, to=30, width=5, textvariable=self.size_var)
        size_spin.pack(side=tk.LEFT, padx=5)

        tk.Label(opt_frame, text="Nº máximo de palavras na grade:").pack(side=tk.LEFT, padx=5)
        self.max_words_var = tk.StringVar(value="15")
        max_spin = tk.Spinbox(opt_frame, from_=5, to=30, width=5, textvariable=self.max_words_var)
        max_spin.pack(side=tk.LEFT, padx=5)

        btn_frame = tk.Frame(root)
        btn_frame.pack(pady=10)

        self.btn_generate = tk.Button(btn_frame, text="Gerar", command=self.generate, width=12)
        self.btn_generate.pack(side=tk.LEFT, padx=5)

        self.btn_clear = tk.Button(btn_frame, text="Limpar", command=self.clear, width=12)
        self.btn_clear.pack(side=tk.LEFT, padx=5)

        self.btn_exit = tk.Button(btn_frame, text="Sair", command=self.root.quit, width=12)
        self.btn_exit.pack(side=tk.LEFT, padx=5)

        self.status = tk.Label(root, text="Pronto.", fg="gray")
        self.status.pack(pady=(5, 10))

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
        self.status.config(text="Campos limpos.", fg="gray")

    def generate(self):
        original_text = self.text_area.get("1.0", tk.END).strip()
        if not original_text:
            messagebox.showwarning("Aviso", "Por favor, digite algum texto.")
            self.status.config(text="Erro: texto vazio.", fg="red")
            return

        all_words = self.get_words()
        if len(all_words) < 2:
            messagebox.showwarning("Aviso", "Não foram encontradas palavras suficientes (mínimo 2).")
            self.status.config(text="Erro: poucas palavras.", fg="red")
            return

        try:
            size = int(self.size_var.get())
            if size < 5:
                raise ValueError
        except:
            messagebox.showwarning("Aviso", "Tamanho inválido. Use um número inteiro >= 5.")
            self.status.config(text="Tamanho inválido.", fg="red")
            return

        try:
            max_words = int(self.max_words_var.get())
            if max_words < 2:
                raise ValueError
        except:
            messagebox.showwarning("Aviso", "Nº máximo inválido. Use um número inteiro >= 2.")
            self.status.config(text="Valor inválido.", fg="red")
            return

        self.status.config(text=f"Extraídas {len(all_words)} palavras. Selecionando subconjunto...", fg="blue")
        self.root.update()

        subset, results = generate_two_grids(all_words, size, max_words)

        if not results:
            self.status.config(text="Falha: não foi possível montar duas grades.", fg="red")
            messagebox.showwarning(
                "Sem solução",
                f"Não foi possível criar duas grades {size}x{size} com até {max_words} palavras.\n"
                "Tente aumentar o tamanho da grade ou o número máximo de palavras."
            )
            return

        html_content = self._build_html(results, original_text, size, subset)

        filename = "caca_palavras.html"
        try:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(html_content)
            webbrowser.open(filename)
            self.status.config(text=f"Grade {size}x{size} com {len(subset)} palavras gerada!", fg="green")
            messagebox.showinfo("Sucesso", f"Página HTML gerada com grade {size}x{size}.\nArquivo: {os.path.abspath(filename)}")
        except Exception as e:
            self.status.config(text="Erro ao salvar arquivo.", fg="red")
            messagebox.showerror("Erro", f"Não foi possível salvar o arquivo:\n{e}")

    def _build_html(self, grids, original_text, size, subset):
        """Gera HTML com duas grades, título com palavras em negrito, sem lista adicional."""
        grid_rows = size
        grid_cols = size

        page_width_px = 720
        margin_px = 40
        gap_px = 30
        avail_width = (page_width_px - 2 * margin_px - gap_px) / 2
        avail_height = (page_width_px * 1.414 - 2 * margin_px - gap_px) / 2

        cell_size = min(avail_width / grid_cols, avail_height / grid_rows, 35)
        cell_size = max(cell_size, 12)
        font_size = int(cell_size * 0.7)

        # Processa o texto original para colocar em negrito as palavras do subset
        # Vamos usar regex para encontrar palavras no texto e substituir apenas as que estão no subset (case insensitive)
        # Preservar a formatação (quebras de linha, espaços, pontuação)
        # Usamos re.sub com função de substituição
        def bold_selected(match):
            word = match.group(0)
            # Verifica se a palavra (em maiúsculas) está no subset
            if word.upper() in subset:
                return f"<strong>{word}</strong>"
            else:
                return word

        # Para preservar quebras de linha, vamos colocar o texto em uma div com white-space: pre-wrap
        # e aplicar a substituição em todo o texto, mantendo caracteres especiais.
        # Mas cuidado: o texto pode ter caracteres como <, >, & que devem ser escapados.
        # Vamos escapar o texto antes de aplicar a substituição.
        import html
        escaped_text = html.escape(original_text)
        # Agora aplicamos a substituição nas palavras (usando regex que captura apenas letras)
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
            font-size: 18pt;
            text-align: center;
            margin-bottom: 20px;
            white-space: pre-wrap;
        }}
        .container {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 30px;
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