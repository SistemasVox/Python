# Importando as bibliotecas necessárias
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import json
from tkinter import font

# --- Função Principal: Lógica para gerar a estrutura do JSON ---
def gerar_estrutura_json(dados):
    """
    Função que recebe dados JSON (como um dicionário ou lista) e retorna
    uma string formatada representando sua estrutura.
    """
    def percorrer_estrutura(item, nivel=0, max_nivel=10):
        estrutura_texto = ""
        indentacao = "  " * nivel
        
        # Proteção contra recursão muito profunda
        if nivel > max_nivel:
            estrutura_texto += f"{indentacao}... (estrutura muito profunda)\n"
            return estrutura_texto
        
        if isinstance(item, dict):
            if not item:  # Dicionário vazio
                estrutura_texto += f"{indentacao}📂 {{}} (dicionário vazio)\n"
            else:
                for chave, valor in item.items():
                    tipo_valor = type(valor).__name__
                    icone = get_type_icon(tipo_valor)
                    
                    # Informação adicional baseada no tipo
                    info_extra = ""
                    if isinstance(valor, str) and valor:
                        info_extra = f" - exemplo: '{valor[:30]}...'" if len(valor) > 30 else f" - valor: '{valor}'"
                    elif isinstance(valor, (int, float)):
                        info_extra = f" - valor: {valor}"
                    elif isinstance(valor, list):
                        info_extra = f" - {len(valor)} item(s)"
                    elif isinstance(valor, dict):
                        info_extra = f" - {len(valor)} propriedade(s)"
                    
                    estrutura_texto += f"{indentacao}{icone} {chave}: ({tipo_valor}){info_extra}\n"
                    
                    if isinstance(valor, (dict, list)) and valor:
                        estrutura_texto += percorrer_estrutura(valor, nivel + 1, max_nivel)
        
        elif isinstance(item, list):
            if not item:  # Lista vazia
                estrutura_texto += f"{indentacao}📋 [] (lista vazia)\n"
            else:
                estrutura_texto += f"{indentacao}📋 [Lista com {len(item)} item(s)]\n"
                
                # Analisa o primeiro item não vazio
                primeiro_item = None
                for i, elemento in enumerate(item):
                    if elemento is not None:
                        primeiro_item = elemento
                        break
                
                if primeiro_item is not None:
                    estrutura_texto += f"{indentacao}  └─ Estrutura do item {i+1}:\n"
                    estrutura_texto += percorrer_estrutura(primeiro_item, nivel + 2, max_nivel)
                else:
                    estrutura_texto += f"{indentacao}  └─ (todos os itens são nulos/vazios)\n"
        
        elif item is None:
            estrutura_texto += f"{indentacao}❌ null\n"
        
        else:
            # Para tipos primitivos (quando chamado diretamente)
            tipo_item = type(item).__name__
            icone = get_type_icon(tipo_item)
            estrutura_texto += f"{indentacao}{icone} ({tipo_item}) - valor: {item}\n"
                
        return estrutura_texto

    return percorrer_estrutura(dados)

def get_type_icon(tipo):
    """Retorna um ícone para cada tipo de dado"""
    icons = {
        'str': '📝',
        'int': '🔢',
        'float': '🔢',
        'bool': '✅',
        'dict': '📂',
        'list': '📋',
        'NoneType': '❌'
    }
    return icons.get(tipo, '📄')

def gerar_estatisticas_json(dados):
    """Gera estatísticas resumidas do JSON"""
    stats = {
        'total_objetos': 0,
        'total_listas': 0,
        'total_strings': 0,
        'total_numeros': 0,
        'total_booleans': 0,
        'total_nulls': 0,
        'profundidade_maxima': 0
    }
    
    def contar_elementos(item, nivel=0):
        stats['profundidade_maxima'] = max(stats['profundidade_maxima'], nivel)
        
        if isinstance(item, dict):
            stats['total_objetos'] += 1
            for valor in item.values():
                contar_elementos(valor, nivel + 1)
        elif isinstance(item, list):
            stats['total_listas'] += 1
            for elemento in item:
                contar_elementos(elemento, nivel + 1)
        elif isinstance(item, str):
            stats['total_strings'] += 1
        elif isinstance(item, (int, float)):
            stats['total_numeros'] += 1
        elif isinstance(item, bool):
            stats['total_booleans'] += 1
        elif item is None:
            stats['total_nulls'] += 1
    
    contar_elementos(dados)
    
    # Formata as estatísticas
    estatisticas = f"""📊 Estatísticas do JSON:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📂 Objetos (dicionários): {stats['total_objetos']:,}
📋 Listas (arrays): {stats['total_listas']:,}
📝 Strings: {stats['total_strings']:,}
🔢 Números: {stats['total_numeros']:,}
✅ Booleanos: {stats['total_booleans']:,}
❌ Valores nulos: {stats['total_nulls']:,}
📏 Profundidade máxima: {stats['profundidade_maxima']} níveis
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

"""
    return estatisticas

# --- Funções dos Botões ---
def ler_arquivo():
    """Abre arquivo JSON e exibe sua estrutura"""
    caminho_arquivo = filedialog.askopenfilename(
        title="Selecione um arquivo JSON",
        filetypes=(("Arquivos JSON", "*.json"), ("Todos os arquivos", "*.*"))
    )
    if not caminho_arquivo:
        return

    try:
        with open(caminho_arquivo, 'r', encoding='utf-8') as f:
            dados_json = json.load(f)
        
        # Gera estatísticas e estrutura
        estatisticas = gerar_estatisticas_json(dados_json)
        estrutura = gerar_estrutura_json(dados_json)
        
        # Atualiza o display
        area_texto.config(state='normal')
        area_texto.delete(1.0, tk.END)
        area_texto.insert(tk.END, f"📁 Arquivo: {caminho_arquivo.split('/')[-1]}\n")
        area_texto.insert(tk.END, "="*60 + "\n\n")
        area_texto.insert(tk.END, estatisticas)
        area_texto.insert(tk.END, "🏗️ Estrutura detalhada:\n")
        area_texto.insert(tk.END, "─" * 30 + "\n")
        area_texto.insert(tk.END, estrutura)
        area_texto.config(state='disabled')
        
        # Atualiza status
        total_linhas = len(estatisticas.splitlines()) + len(estrutura.splitlines())
        status_var.set(f"✅ Arquivo processado: {total_linhas} linhas de análise geradas")
        
    except json.JSONDecodeError as e:
        messagebox.showerror("Erro de JSON", f"Arquivo JSON inválido:\n{str(e)}")
        status_var.set("❌ Erro: JSON inválido")
    except Exception as e:
        messagebox.showerror("Erro", f"Erro inesperado:\n{str(e)}")
        status_var.set(f"❌ Erro: {str(e)}")

def processar_texto_colado():
    """Processa texto JSON colado na área de texto"""
    area_texto.config(state='normal')
    texto_json = area_texto.get(1.0, tk.END).strip()

    if not texto_json or texto_json == mensagem_inicial.strip():
        messagebox.showwarning("Aviso", "Cole um JSON na área de texto para processar.")
        return

    try:
        dados_json = json.loads(texto_json)
        
        # Gera estatísticas e estrutura
        estatisticas = gerar_estatisticas_json(dados_json)
        estrutura = gerar_estrutura_json(dados_json)

        area_texto.delete(1.0, tk.END)
        area_texto.insert(tk.END, "📋 Texto JSON processado\n")
        area_texto.insert(tk.END, "="*60 + "\n\n")
        area_texto.insert(tk.END, estatisticas)
        area_texto.insert(tk.END, "🏗️ Estrutura detalhada:\n")
        area_texto.insert(tk.END, "─" * 30 + "\n")
        area_texto.insert(tk.END, estrutura)
        
        total_linhas = len(estatisticas.splitlines()) + len(estrutura.splitlines())
        status_var.set(f"✅ Texto processado: {total_linhas} linhas de análise geradas")
    
    except json.JSONDecodeError as e:
        messagebox.showerror("Erro de JSON", f"JSON inválido:\n{str(e)}")
        status_var.set("❌ Erro: JSON inválido")

def copiar_para_clipboard():
    """Copia conteúdo para área de transferência"""
    estado_anterior = area_texto.cget('state')
    area_texto.config(state='normal')
    
    conteudo = area_texto.get(1.0, tk.END).strip()
    if conteudo and conteudo != mensagem_inicial.strip():
        janela.clipboard_clear()
        janela.clipboard_append(conteudo)
        status_var.set("📋 Estrutura copiada para área de transferência!")
    else:
        messagebox.showwarning("Aviso", "Não há conteúdo para copiar.")
        
    area_texto.config(state=estado_anterior)

def limpar_area():
    """Limpa a área de texto"""
    area_texto.config(state='normal')
    area_texto.delete(1.0, tk.END)
    area_texto.insert(tk.END, mensagem_inicial)
    status_var.set("🧹 Área de texto limpa")

def sobre():
    """Exibe informações sobre o programa"""
    messagebox.showinfo("Sobre", 
        "🔍 JSON Structure Analyzer v2.0\n\n"
        "Desenvolvido para análise e visualização\n"
        "da estrutura de arquivos JSON.\n\n"
        "Recursos:\n"
        "• Análise de arquivos JSON\n"
        "• Processamento de texto colado\n"
        "• Interface moderna e intuitiva\n"
        "• Ícones para diferentes tipos de dados")

# --- Interface Gráfica Moderna ---

# Configuração da janela principal
janela = tk.Tk()
janela.title("🔍 JSON Structure Analyzer")
janela.geometry("900x700")
janela.configure(bg='#2C3E50')

# Configuração de fonte personalizada
fonte_titulo = font.Font(family="Segoe UI", size=12, weight="bold")
fonte_botao = font.Font(family="Segoe UI", size=10)
fonte_texto = font.Font(family="Consolas", size=10)

# Estilo para widgets ttk
style = ttk.Style()
style.theme_use('clam')

# Configurar cores do tema
style.configure('Title.TLabel', 
               background='#2C3E50', 
               foreground='#ECF0F1', 
               font=fonte_titulo)

style.configure('Modern.TButton',
               background='#3498DB',
               foreground='white',
               borderwidth=0,
               focuscolor='none',
               font=fonte_botao)

style.map('Modern.TButton',
         background=[('active', '#2980B9'),
                    ('pressed', '#21618C')])

# Header com título
header_frame = tk.Frame(janela, bg='#34495E', height=60)
header_frame.pack(fill=tk.X, padx=0, pady=0)
header_frame.pack_propagate(False)

titulo_label = ttk.Label(header_frame, 
                        text="🔍 JSON Structure Analyzer", 
                        style='Title.TLabel')
titulo_label.pack(pady=15)

# Frame principal para conteúdo
main_frame = tk.Frame(janela, bg='#2C3E50')
main_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)

# Frame para botões - organizado em duas linhas
button_frame = tk.Frame(main_frame, bg='#2C3E50')
button_frame.pack(fill=tk.X, pady=(0, 15))

# Primeira linha de botões
button_row1 = tk.Frame(button_frame, bg='#2C3E50')
button_row1.pack(fill=tk.X, pady=(0, 5))

btn_ler = ttk.Button(button_row1, text="📁 Abrir Arquivo", 
                    command=ler_arquivo, style='Modern.TButton')
btn_ler.pack(side=tk.LEFT, padx=(0, 10))

btn_processar = ttk.Button(button_row1, text="⚡ Processar Texto", 
                          command=processar_texto_colado, style='Modern.TButton')
btn_processar.pack(side=tk.LEFT, padx=(0, 10))

btn_copiar = ttk.Button(button_row1, text="📋 Copiar", 
                       command=copiar_para_clipboard, style='Modern.TButton')
btn_copiar.pack(side=tk.LEFT, padx=(0, 10))

# Segunda linha de botões
button_row2 = tk.Frame(button_frame, bg='#2C3E50')
button_row2.pack(fill=tk.X)

btn_limpar = ttk.Button(button_row2, text="🧹 Limpar", 
                       command=limpar_area, style='Modern.TButton')
btn_limpar.pack(side=tk.LEFT, padx=(0, 10))

btn_sobre = ttk.Button(button_row2, text="ℹ️ Sobre", 
                      command=sobre, style='Modern.TButton')
btn_sobre.pack(side=tk.LEFT, padx=(0, 10))

btn_sair = ttk.Button(button_row2, text="❌ Sair", 
                     command=janela.quit, style='Modern.TButton')
btn_sair.pack(side=tk.RIGHT)

# Área de texto com design moderno
text_frame = tk.Frame(main_frame, bg='#34495E', relief='solid', bd=1)
text_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

# Scrollbar personalizada
scrollbar = tk.Scrollbar(text_frame, bg='#34495E', troughcolor='#2C3E50')
scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

area_texto = tk.Text(text_frame, 
                    wrap=tk.WORD, 
                    font=fonte_texto,
                    bg='#ECF0F1',
                    fg='#2C3E50',
                    insertbackground='#3498DB',
                    selectbackground='#3498DB',
                    selectforeground='white',
                    padx=15,
                    pady=15,
                    yscrollcommand=scrollbar.set,
                    relief='flat',
                    bd=0)
area_texto.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
scrollbar.config(command=area_texto.yview)

# Barra de status
status_frame = tk.Frame(janela, bg='#34495E', height=30)
status_frame.pack(fill=tk.X, side=tk.BOTTOM)
status_frame.pack_propagate(False)

status_var = tk.StringVar()
status_var.set("🚀 Pronto para analisar arquivos JSON")

status_label = tk.Label(status_frame, 
                       textvariable=status_var,
                       bg='#34495E',
                       fg='#BDC3C7',
                       font=('Segoe UI', 9))
status_label.pack(side=tk.LEFT, padx=10, pady=5)

# Mensagem inicial
mensagem_inicial = """🎉 Bem-vindo ao JSON Structure Analyzer!

📋 Como usar:
1. 📁 Clique em "Abrir Arquivo" para carregar um arquivo JSON
2. ⚡ Ou cole seu JSON aqui e clique em "Processar Texto"
3. 📋 Use "Copiar" para copiar a estrutura analisada

✨ Recursos:
• 🔍 Análise detalhada da estrutura JSON
• 🎨 Ícones coloridos para diferentes tipos de dados
• 📊 Contagem de itens em listas
• 🚀 Interface moderna e intuitiva

Cole seu JSON aqui e clique em "Processar Texto" para começar!"""

area_texto.insert(tk.END, mensagem_inicial)

# Configurar redimensionamento
janela.rowconfigure(1, weight=1)
janela.columnconfigure(0, weight=1)

# Loop principal
if __name__ == "__main__":
    janela.mainloop()