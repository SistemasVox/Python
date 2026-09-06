# -*- coding: utf-8 -*-

import pyzipper
import itertools
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
import json
import os
from datetime import datetime, timedelta

CHECKPOINT_FILE = "zip_attack_checkpoint.json"

class ZipPasswordRecovery:
    def __init__(self, root):
        self.root = root
        self.root.title("ZIP Password Recovery - Profissional")
        self.root.geometry("750x900")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        self.zip_path = ""
        self.running = False
        self.stop_flag = False
        self.attempts = 0
        self.start_time = 0
        self.total_combinations = 0
        self.rate = 0.0
        self.remaining_seconds = 0

        self.current_length = None
        self.current_index = None
        self.charset = ""
        self.min_len = 3
        self.max_len = 10

        self.char_sets = {
            "lower": "abcdefghijklmnopqrstuvwxyz",
            "upper": "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
            "digits": "0123456789",
            "space": " ",
            "symbols": "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~",
            "accents": "àáâãäèéêëìíîïòóôõöùúûüçñ",
            "extended": "§±÷€£¥©®°•¶†‡µ¡¿"
        }

        self.attack_type = tk.StringVar(value="bruteforce")
        self.build_ui()

    def build_ui(self):
        # ---------- Arquivo ZIP ----------
        file_frame = tk.LabelFrame(self.root, text="Arquivo ZIP", padx=5, pady=5)
        file_frame.pack(fill="x", padx=10, pady=5)
        self.file_label = tk.Label(file_frame, text="Nenhum arquivo selecionado", fg="gray", anchor="w")
        self.file_label.pack(side="left", fill="x", expand=True, padx=5)
        btn_browse = tk.Button(file_frame, text="Procurar...", command=self.select_zip)
        btn_browse.pack(side="right", padx=5)

        # ---------- Tipo de ataque ----------
        attack_frame = tk.LabelFrame(self.root, text="Tipo de Ataque", padx=5, pady=5)
        attack_frame.pack(fill="x", padx=10, pady=5)
        tk.Radiobutton(attack_frame, text="Força Bruta", variable=self.attack_type, value="bruteforce", command=self.toggle_attack_mode).pack(side="left", padx=10)
        tk.Radiobutton(attack_frame, text="Dicionário", variable=self.attack_type, value="dictionary", command=self.toggle_attack_mode).pack(side="left", padx=10)

        # ---------- Força bruta frame ----------
        self.bruteforce_frame = tk.LabelFrame(self.root, text="Configurações da Força Bruta", padx=5, pady=5)

        # Comprimentos
        len_frame = tk.Frame(self.bruteforce_frame)
        len_frame.pack(fill="x", pady=5)
        tk.Label(len_frame, text="Comprimento Mínimo:").pack(side="left", padx=5)
        self.min_len_var = tk.IntVar(value=3)
        tk.Spinbox(len_frame, from_=1, to=12, width=5, textvariable=self.min_len_var).pack(side="left", padx=5)
        tk.Label(len_frame, text="Comprimento Máximo:").pack(side="left", padx=5)
        self.max_len_var = tk.IntVar(value=10)
        tk.Spinbox(len_frame, from_=1, to=12, width=5, textvariable=self.max_len_var).pack(side="left", padx=5)

        # Conjunto de caracteres
        chars_frame = tk.LabelFrame(self.bruteforce_frame, text="Conjunto de Caracteres", padx=5, pady=5)
        chars_frame.pack(fill="x", pady=5)

        self.char_vars = {
            "lower": tk.BooleanVar(value=True),
            "upper": tk.BooleanVar(value=False),
            "digits": tk.BooleanVar(value=True),
            "space": tk.BooleanVar(value=False),
            "symbols": tk.BooleanVar(value=False),
            "accents": tk.BooleanVar(value=False),
            "extended": tk.BooleanVar(value=False)
        }

        # Linha 0
        tk.Checkbutton(chars_frame, text="abc (minúsculas)", variable=self.char_vars["lower"]).grid(row=0, column=0, sticky="w", padx=5)
        tk.Checkbutton(chars_frame, text="ABC (maiúsculas)", variable=self.char_vars["upper"]).grid(row=0, column=1, sticky="w", padx=5)
        tk.Checkbutton(chars_frame, text="123 (dígitos)", variable=self.char_vars["digits"]).grid(row=0, column=2, sticky="w", padx=5)

        # Linha 1
        tk.Checkbutton(chars_frame, text="espaço", variable=self.char_vars["space"]).grid(row=1, column=0, sticky="w", padx=5)
        tk.Checkbutton(chars_frame, text="símbolos (!@#)", variable=self.char_vars["symbols"]).grid(row=1, column=1, sticky="w", padx=5)
        tk.Checkbutton(chars_frame, text="acentuados (àáâ)", variable=self.char_vars["accents"]).grid(row=1, column=2, sticky="w", padx=5)

        # Linha 2
        tk.Checkbutton(chars_frame, text="estendidos (§±÷)", variable=self.char_vars["extended"]).grid(row=2, column=0, sticky="w", padx=5)

        # Campo personalizado (visível)
        tk.Label(chars_frame, text="Caracteres personalizados:", font=("Arial", 9, "bold")).grid(row=3, column=0, sticky="w", padx=5, pady=(10,0))
        self.custom_chars = tk.StringVar(value="")
        ent_custom = tk.Entry(chars_frame, textvariable=self.custom_chars, width=40, bg="lightyellow")
        ent_custom.grid(row=4, column=0, columnspan=3, sticky="ew", padx=5, pady=(2,5))
        tk.Label(chars_frame, text="Ex: @#$%&*", fg="gray", font=("Arial", 8)).grid(row=5, column=0, columnspan=3, sticky="w", padx=5, pady=(0,5))

        chars_frame.columnconfigure(0, weight=1)
        chars_frame.columnconfigure(1, weight=1)
        chars_frame.columnconfigure(2, weight=1)

        # ---------- Dicionário frame ----------
        self.dictionary_frame = tk.LabelFrame(self.root, text="Configurações do Dicionário", padx=5, pady=5)
        dict_inner = tk.Frame(self.dictionary_frame)
        dict_inner.pack(fill="x")
        tk.Label(dict_inner, text="Arquivo de dicionário:").pack(side="left", padx=5)
        self.dict_path_label = tk.Label(dict_inner, text="Nenhum", fg="gray", anchor="w", relief="sunken", width=40)
        self.dict_path_label.pack(side="left", fill="x", expand=True, padx=5)
        btn_browse_dict = tk.Button(dict_inner, text="Procurar...", command=self.select_dictionary)
        btn_browse_dict.pack(side="right", padx=5)

        # ---------- Botões de controle ----------
        self.ctrl_frame = tk.Frame(self.root)
        self.btn_start = tk.Button(self.ctrl_frame, text="Iniciar", command=self.start_attack, bg="green", fg="white")
        self.btn_start.pack(side="left", padx=5)
        self.btn_stop = tk.Button(self.ctrl_frame, text="Parar", command=self.stop_attack, state="disabled", bg="red", fg="white")
        self.btn_stop.pack(side="left", padx=5)
        self.btn_clear = tk.Button(self.ctrl_frame, text="Limpar estado", command=self.clear_checkpoint, bg="orange")
        self.btn_clear.pack(side="left", padx=5)
        self.btn_exit = tk.Button(self.ctrl_frame, text="Sair", command=self.on_closing, bg="gray", fg="white")
        self.btn_exit.pack(side="right", padx=5)

        # ---------- Estatísticas ----------
        self.stats_frame = tk.LabelFrame(self.root, text="Estatísticas do Ataque", padx=5, pady=5)
        self.attempts_label = tk.Label(self.stats_frame, text="Tentativas: 0", anchor="w")
        self.attempts_label.pack(fill="x")
        self.rate_label = tk.Label(self.stats_frame, text="Velocidade: 0 tentativas/s", anchor="w")
        self.rate_label.pack(fill="x")
        self.percent_label = tk.Label(self.stats_frame, text="Progresso: 0.00%", anchor="w")
        self.percent_label.pack(fill="x")
        self.elapsed_label = tk.Label(self.stats_frame, text="Tempo decorrido: 0s", anchor="w")
        self.elapsed_label.pack(fill="x")
        self.remaining_label = tk.Label(self.stats_frame, text="Tempo restante: calculando...", anchor="w")
        self.remaining_label.pack(fill="x")
        self.eta_label = tk.Label(self.stats_frame, text="Previsão de término: --", anchor="w")
        self.eta_label.pack(fill="x")

        # ---------- Barra de progresso ----------
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(self.root, orient="horizontal", variable=self.progress_var, maximum=100)

        # ---------- Log ----------
        self.log_frame = tk.LabelFrame(self.root, text="Log", padx=5, pady=5)
        self.log_area = ScrolledText(self.log_frame, height=15, state="disabled")
        self.log_area.pack(fill="both", expand=True)

        # Empacotamento final
        if self.attack_type.get() == "bruteforce":
            self.bruteforce_frame.pack(fill="x", padx=10, pady=5)
        else:
            self.dictionary_frame.pack(fill="x", padx=10, pady=5)
        self.ctrl_frame.pack(fill="x", padx=10, pady=10)
        self.stats_frame.pack(fill="x", padx=10, pady=5)
        self.progress_bar.pack(fill="x", padx=10, pady=5)
        self.log_frame.pack(fill="both", expand=True, padx=10, pady=5)

        self.dict_path = ""

    def select_zip(self):
        path = filedialog.askopenfilename(filetypes=[("ZIP files", "*.zip")])
        if path:
            self.zip_path = path
            self.file_label.config(text=path, fg="black")

    def select_dictionary(self):
        path = filedialog.askopenfilename(filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if path:
            self.dict_path = path
            self.dict_path_label.config(text=path, fg="black")

    def toggle_attack_mode(self):
        for frame in (self.bruteforce_frame, self.dictionary_frame):
            if frame.winfo_ismapped():
                frame.pack_forget()
        if self.attack_type.get() == "bruteforce":
            self.bruteforce_frame.pack(fill="x", padx=10, pady=5, before=self.ctrl_frame)
        else:
            self.dictionary_frame.pack(fill="x", padx=10, pady=5, before=self.ctrl_frame)

    def log(self, message):
        def _log():
            self.log_area.config(state="normal")
            self.log_area.insert(tk.END, message + "\n")
            self.log_area.see(tk.END)
            self.log_area.config(state="disabled")
        self.root.after(0, _log)

    def build_charset(self):
        chars = ""
        if self.char_vars["lower"].get():
            chars += self.char_sets["lower"]
        if self.char_vars["upper"].get():
            chars += self.char_sets["upper"]
        if self.char_vars["digits"].get():
            chars += self.char_sets["digits"]
        if self.char_vars["space"].get():
            chars += self.char_sets["space"]
        if self.char_vars["symbols"].get():
            chars += self.char_sets["symbols"]
        if self.char_vars["accents"].get():
            chars += self.char_sets["accents"]
        if self.char_vars["extended"].get():
            chars += self.char_sets["extended"]
        custom = self.custom_chars.get().strip()
        if custom:
            chars += custom
        # Remove duplicatas
        seen = set()
        unique = []
        for c in chars:
            if c not in seen:
                seen.add(c)
                unique.append(c)
        return ''.join(unique)

    def test_password(self, password_str):
        if not self.zip_path:
            return False
        pwd_bytes = password_str.encode('utf-8')
        try:
            with pyzipper.AESZipFile(self.zip_path, 'r') as zf:
                zf.setpassword(pwd_bytes)
                for info in zf.infolist():
                    if not info.is_dir():
                        zf.read(info)
                        break
                return True
        except:
            return False

    def format_time_human(self, seconds):
        if seconds < 0:
            seconds = 0
        days = int(seconds // 86400)
        hours = int((seconds % 86400) // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        parts = []
        if days > 0:
            parts.append(f"{days} dia{'s' if days > 1 else ''}")
        if hours > 0:
            parts.append(f"{hours} hora{'s' if hours > 1 else ''}")
        if minutes > 0:
            parts.append(f"{minutes} minuto{'s' if minutes > 1 else ''}")
        if secs > 0 or not parts:
            parts.append(f"{secs} segundo{'s' if secs != 1 else ''}")
        return ", ".join(parts)

    def save_checkpoint(self, length, index, attempts):
        checkpoint = {
            "zip_path": self.zip_path,
            "attack_type": self.attack_type.get(),
            "min_len": self.min_len,
            "max_len": self.max_len,
            "charset": self.charset,
            "current_length": length,
            "current_index": index,
            "attempts": attempts,
            "timestamp": time.time()
        }
        try:
            with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
                json.dump(checkpoint, f, indent=2)
            self.log(f"[Checkpoint] Salvo: comprimento {length}, índice {index}")
        except Exception as e:
            self.log(f"Erro ao salvar checkpoint: {e}")

    def load_checkpoint(self):
        if not os.path.exists(CHECKPOINT_FILE):
            return None
        try:
            with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                cp = json.load(f)
            if cp.get("zip_path") != self.zip_path:
                return None
            if cp.get("attack_type") != self.attack_type.get():
                return None
            if self.attack_type.get() == "bruteforce":
                current_charset = self.build_charset()
                if cp.get("charset") != current_charset:
                    self.log("Charset diferente. Checkpoint ignorado.")
                    return None
                if cp.get("min_len") != self.min_len_var.get() or cp.get("max_len") != self.max_len_var.get():
                    self.log("Limites de comprimento alterados. Checkpoint ignorado.")
                    return None
            return cp
        except:
            return None

    def clear_checkpoint(self):
        if os.path.exists(CHECKPOINT_FILE):
            os.remove(CHECKPOINT_FILE)
            self.log("Estado limpo.")
        else:
            self.log("Nenhum estado salvo.")

    def compute_total_combinations(self, charset, min_len, max_len):
        total = 0
        for l in range(min_len, max_len+1):
            total += len(charset) ** l
        return total

    def update_stats(self):
        if self.start_time == 0 or self.total_combinations == 0:
            return
        elapsed = time.time() - self.start_time
        percent = (self.attempts / self.total_combinations) * 100
        self.rate = self.attempts / elapsed if elapsed > 0 else 0
        remaining_attempts = self.total_combinations - self.attempts
        if self.rate > 0:
            self.remaining_seconds = remaining_attempts / self.rate
            # Limita a um valor máximo razoável (10 anos)
            if self.remaining_seconds > 315360000:
                self.remaining_seconds = 315360000
        else:
            self.remaining_seconds = 0
        # Evita overflow no timedelta
        try:
            if self.remaining_seconds > 0 and self.remaining_seconds < 1e9:
                eta = datetime.now() + timedelta(seconds=self.remaining_seconds)
                eta_str = eta.strftime("%d/%m/%Y %H:%M:%S")
            else:
                eta_str = "muito distante"
        except (OverflowError, ValueError):
            eta_str = "data inválida"

        def update_ui():
            self.attempts_label.config(text=f"Tentativas: {self.attempts:,}")
            self.rate_label.config(text=f"Velocidade: {self.rate:.1f} tentativas/s")
            self.percent_label.config(text=f"Progresso: {percent:.2f}%")
            elapsed_str = self.format_time_human(elapsed)
            self.elapsed_label.config(text=f"Tempo decorrido: {elapsed_str}")
            if self.rate > 0 and remaining_attempts > 0 and self.remaining_seconds > 0:
                remaining_str = self.format_time_human(self.remaining_seconds)
                self.remaining_label.config(text=f"Tempo restante: {remaining_str}")
                self.eta_label.config(text=f"Previsão de término: {eta_str}")
            else:
                self.remaining_label.config(text="Tempo restante: calculando...")
                self.eta_label.config(text="Previsão de término: --")
            self.progress_var.set(percent)
        self.root.after(0, update_ui)

    def save_password_to_file(self, password):
        base_name = os.path.splitext(os.path.basename(self.zip_path))[0]
        result_file = f"senha_{base_name}.txt"
        try:
            with open(result_file, "w", encoding="utf-8") as f:
                f.write(f"Arquivo ZIP: {self.zip_path}\n")
                f.write(f"Senha encontrada: {password}\n")
                f.write(f"Data: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n")
                f.write(f"Tentativas: {self.attempts:,}\n")
                elapsed = time.time() - self.start_time
                f.write(f"Tempo gasto: {self.format_time_human(elapsed)}\n")
            self.log(f"Senha salva em '{result_file}'")
        except Exception as e:
            self.log(f"Erro ao salvar senha: {e}")

    def index_to_combination(self, idx, charset, length):
        result = []
        base = len(charset)
        for _ in range(length):
            idx, rem = divmod(idx, base)
            result.append(charset[rem])
        return ''.join(reversed(result))

    def brute_force_attack(self, charset, min_len, max_len, resume_from=None):
        self.total_combinations = self.compute_total_combinations(charset, min_len, max_len)
        self.log(f"Total de combinações: {self.total_combinations:,}")
        start_length = resume_from[0] if resume_from else min_len
        for length in range(start_length, max_len+1):
            if self.stop_flag:
                break
            total_for_length = len(charset) ** length
            start_idx = resume_from[1] if resume_from and length == start_length else 0
            for idx in range(start_idx, total_for_length):
                if self.stop_flag:
                    self.save_checkpoint(length, idx, self.attempts)
                    break
                self.attempts += 1
                if self.attempts % 100 == 0:
                    self.update_stats()
                guess = self.index_to_combination(idx, charset, length)
                if self.test_password(guess):
                    if os.path.exists(CHECKPOINT_FILE):
                        os.remove(CHECKPOINT_FILE)
                    return guess
            resume_from = None
            if not self.stop_flag:
                self.save_checkpoint(length+1, 0, self.attempts)
        return None

    def dictionary_attack(self, dict_path):
        try:
            with open(dict_path, 'r', encoding='utf-8', errors='ignore') as f:
                passwords = [line.strip() for line in f if line.strip()]
        except Exception as e:
            self.log(f"Erro ao ler dicionário: {e}")
            return None
        total = len(passwords)
        self.total_combinations = total
        self.log(f"Total de senhas no dicionário: {total:,}")
        for i, pwd in enumerate(passwords):
            if self.stop_flag:
                break
            self.attempts = i+1
            if self.attempts % 100 == 0:
                self.update_stats()
            if self.test_password(pwd):
                if os.path.exists(CHECKPOINT_FILE):
                    os.remove(CHECKPOINT_FILE)
                return pwd
        return None

    def attack_thread(self):
        try:
            if not self.zip_path:
                self.root.after(0, lambda: messagebox.showerror("Erro", "Selecione um ZIP"))
                self.stop_attack()
                return
            if self.attack_type.get() == "dictionary" and not self.dict_path:
                self.root.after(0, lambda: messagebox.showerror("Erro", "Selecione um dicionário"))
                self.stop_attack()
                return
            if self.attack_type.get() == "bruteforce":
                self.min_len = self.min_len_var.get()
                self.max_len = self.max_len_var.get()
                if self.min_len > self.max_len:
                    self.root.after(0, lambda: messagebox.showerror("Erro", "Mínimo > Máximo"))
                    self.stop_attack()
                    return
                self.charset = self.build_charset()
                if not self.charset:
                    self.root.after(0, lambda: messagebox.showerror("Erro", "Selecione caracteres ou use o campo personalizado"))
                    self.stop_attack()
                    return
                cp = self.load_checkpoint()
                resume = None
                if cp:
                    ans = messagebox.askyesno("Retomar", f"Progresso salvo encontrado (comprimento {cp['current_length']}, {cp['attempts']:,} tentativas). Retomar?")
                    if ans:
                        resume = (cp['current_length'], cp['current_index'])
                        self.attempts = cp['attempts']
                        self.log(f"Retomando do checkpoint...")
                    else:
                        self.clear_checkpoint()
                self.start_time = time.time()
                found = self.brute_force_attack(self.charset, self.min_len, self.max_len, resume)
            else:
                self.start_time = time.time()
                found = self.dictionary_attack(self.dict_path)

            if found:
                elapsed = time.time() - self.start_time
                self.log(f"\n✅ SENHA ENCONTRADA: '{found}' após {self.attempts:,} tentativas")
                self.log(f"Tempo total: {self.format_time_human(elapsed)}")
                self.save_password_to_file(found)
                self.root.after(0, lambda: messagebox.showinfo("Sucesso", f"Senha: {found}\nSalva em arquivo com nome baseado no ZIP"))
            else:
                if self.stop_flag:
                    self.log("Ataque interrompido.")
                else:
                    self.log("\n❌ Senha não encontrada.")
                    self.root.after(0, lambda: messagebox.showwarning("Falha", "Nenhuma senha válida."))
        except Exception as e:
            self.log(f"ERRO: {e}")
        finally:
            self.running = False
            self.stop_flag = False
            self.root.after(0, self.enable_ui)

    def start_attack(self):
        if self.running:
            return
        if not self.zip_path:
            messagebox.showerror("Erro", "Selecione um ZIP")
            return
        if self.attack_type.get() == "dictionary" and not self.dict_path:
            messagebox.showerror("Erro", "Selecione um dicionário")
            return
        if self.attack_type.get() == "bruteforce":
            if self.min_len_var.get() > self.max_len_var.get():
                messagebox.showerror("Erro", "Mínimo > Máximo")
                return
            if not self.build_charset():
                messagebox.showerror("Erro", "Selecione pelo menos um tipo de caractere ou use o campo personalizado")
                return

        self.running = True
        self.stop_flag = False
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.log_area.config(state="normal")
        self.log_area.delete(1.0, tk.END)
        self.log_area.config(state="disabled")
        self.attempts = 0
        self.start_time = 0
        threading.Thread(target=self.attack_thread, daemon=True).start()

    def stop_attack(self):
        if self.running:
            self.stop_flag = True
            self.log("Parando e salvando estado...")

    def enable_ui(self):
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.update_stats()

    def on_closing(self):
        if self.running:
            if messagebox.askyesno("Ataque em andamento", "Deseja parar e salvar estado antes de sair?"):
                self.stop_attack()
                self.root.after(1000, self.root.destroy)
        else:
            if messagebox.askyesno("Sair", "Tem certeza?"):
                self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = ZipPasswordRecovery(root)
    root.mainloop()