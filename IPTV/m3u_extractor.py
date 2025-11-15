import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import pyperclip
import re
from pathlib import Path

# --- Funções de Extração ---
def extrair_links_m3u(caminho_arquivo):
    """Processa um arquivo .m3u, extraindo pares de (nome, url)."""
    entradas_lista = []
    try:
        with open(caminho_arquivo, 'r', encoding='utf-8') as f:
            linhas = f.readlines()

        info_atual = "Nome não disponível"
        
        for i, linha in enumerate(linhas):
            linha = linha.strip()
            
            if linha.startswith('#EXTINF'):
                partes = linha.split(',')
                if len(partes) > 1:
                    info_atual = partes[-1].strip()
                else:
                    info_atual = "Nome não disponível"
            
            elif not linha.startswith('#') and linha:
                url = linha
                entradas_lista.append((info_atual, url))
                info_atual = "Nome não disponível"
                
    except Exception as e:
        messagebox.showerror("Erro de Leitura", f"Erro ao ler o arquivo:\n{e}")
        return []

    return entradas_lista

# --- Interface Gráfica ---
class ExtratorM3U:
    def __init__(self, root):
        self.root = root
        self.dados_carregados = []
        self.indice_atual = -1
        self.arquivo_atual = None
        
        self.configurar_janela()
        self.criar_interface()
        self.configurar_atalhos()
        
    def configurar_janela(self):
        """Configura a janela principal."""
        self.root.title("Extrator de Links M3U - SistemasVox")
        self.root.geometry("1024x768")
        self.root.minsize(1024, 768)
        
        # Cores futuristas
        self.cores = {
            'bg_escuro': '#0a0e27',
            'bg_medio': '#151b3d',
            'bg_claro': '#1e2749',
            'acento': '#00d9ff',
            'acento2': '#7b2fff',
            'acento3': '#ff2e97',
            'texto': '#e0e6ff',
            'texto_dim': '#8892b0',
            'sucesso': '#00ff88',
            'erro': '#ff3366'
        }
        
        # Configura background
        self.root.configure(bg=self.cores['bg_escuro'])
        
        # Tema moderno
        style = ttk.Style()
        style.theme_use('clam')
        
        # Estilos personalizados futuristas
        style.configure('Header.TLabel', 
                       font=('Rajdhani', 13, 'bold'),
                       foreground=self.cores['acento'],
                       background=self.cores['bg_escuro'])
        
        style.configure('Status.TLabel', 
                       font=('Rajdhani', 9),
                       foreground=self.cores['texto'],
                       background=self.cores['bg_medio'])
        
        style.configure('Action.TButton', 
                       padding=10,
                       font=('Rajdhani', 10, 'bold'))
        
        style.configure('TFrame', background=self.cores['bg_escuro'])
        style.configure('TLabel', 
                       background=self.cores['bg_escuro'],
                       foreground=self.cores['texto'])
        
        style.configure('TLabelframe', 
                       background=self.cores['bg_escuro'],
                       foreground=self.cores['acento'],
                       bordercolor=self.cores['acento2'])
        
        style.configure('TLabelframe.Label', 
                       font=('Rajdhani', 10, 'bold'),
                       foreground=self.cores['acento'],
                       background=self.cores['bg_escuro'])
        
        # Botões com gradiente visual
        style.map('Action.TButton',
                 foreground=[('active', self.cores['acento']),
                           ('!active', self.cores['texto'])],
                 background=[('active', self.cores['bg_claro']),
                           ('!active', self.cores['bg_medio'])])
        
    def criar_interface(self):
        """Cria toda a interface do usuário."""
        # Container principal com padding e background
        container = ttk.Frame(self.root, padding="15")
        container.pack(fill=tk.BOTH, expand=True)
        
        # Banner superior com logo/título
        self.criar_banner(container)
        
        # === SEÇÃO SUPERIOR: Seleção de Arquivo ===
        self.criar_secao_arquivo(container)
        
        # Separador neon
        sep1 = ttk.Separator(container, orient=tk.HORIZONTAL)
        sep1.pack(fill=tk.X, pady=10)
        
        # === ÁREA PRINCIPAL: Duas Colunas ===
        area_principal = ttk.Frame(container)
        area_principal.pack(fill=tk.BOTH, expand=True)
        
        # Coluna Esquerda (Lista)
        self.criar_coluna_lista(area_principal)
        
        # Separador vertical neon
        sep2 = ttk.Separator(area_principal, orient=tk.VERTICAL)
        sep2.pack(side=tk.LEFT, fill=tk.Y, padx=10)
        
        # Coluna Direita (Detalhes)
        self.criar_coluna_detalhes(area_principal)
        
        # === BARRA DE STATUS ===
        self.criar_barra_status(container)
        
    def criar_banner(self, parent):
        """Cria banner superior futurista."""
        frame_banner = tk.Frame(parent, bg=self.cores['bg_medio'], 
                               height=50, relief=tk.FLAT, bd=2)
        frame_banner.pack(fill=tk.X, pady=(0, 15))
        frame_banner.pack_propagate(False)
        
        # Título principal
        tk.Label(frame_banner, 
                text="⚡ M3U EXTRACTOR",
                font=('Rajdhani', 20, 'bold'),
                fg=self.cores['acento'],
                bg=self.cores['bg_medio']).pack(side=tk.LEFT, padx=20)
        
        # Subtítulo
        tk.Label(frame_banner,
                text="NEXT-GEN STREAMING TOOL",
                font=('Rajdhani', 9),
                fg=self.cores['texto_dim'],
                bg=self.cores['bg_medio']).pack(side=tk.LEFT)
        
        # Créditos
        frame_creditos = tk.Frame(frame_banner, bg=self.cores['bg_medio'])
        frame_creditos.pack(side=tk.RIGHT, padx=20)
        
        tk.Label(frame_creditos,
                text="Powered by",
                font=('Rajdhani', 8),
                fg=self.cores['texto_dim'],
                bg=self.cores['bg_medio']).pack()
        
        tk.Label(frame_creditos,
                text="SISTEMASVOX",
                font=('Rajdhani', 11, 'bold'),
                fg=self.cores['acento3'],
                bg=self.cores['bg_medio']).pack()
        
    def criar_secao_arquivo(self, parent):
        """Cria a seção de seleção de arquivo."""
        frame = ttk.LabelFrame(parent, text=" 📂 ARQUIVO M3U ", padding="12")
        frame.pack(fill=tk.X, pady=(0, 10))
        
        # Frame interno para organização
        frame_controles = ttk.Frame(frame)
        frame_controles.pack(fill=tk.X)
        
        # Entry com o caminho do arquivo - estilo neon
        self.entry_arquivo = tk.Entry(frame_controles, 
                                     state='readonly',
                                     font=('Consolas', 9, 'bold'),
                                     bg=self.cores['bg_claro'],
                                     fg=self.cores['acento'],
                                     relief=tk.FLAT,
                                     bd=2,
                                     insertbackground=self.cores['acento'])
        self.entry_arquivo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8), ipady=6)
        
        # Botões de ação com estilo neon
        btn_procurar = tk.Button(frame_controles, 
                                text="🔍 SELECIONAR",
                                command=self.procurar_arquivo,
                                font=('Rajdhani', 9, 'bold'),
                                bg=self.cores['bg_medio'],
                                fg=self.cores['acento'],
                                activebackground=self.cores['acento'],
                                activeforeground=self.cores['bg_escuro'],
                                relief=tk.FLAT,
                                bd=0,
                                padx=15,
                                pady=8,
                                cursor='hand2')
        btn_procurar.pack(side=tk.LEFT, padx=3)
        
        btn_carregar = tk.Button(frame_controles, 
                                text="⚡ CARREGAR",
                                command=self.carregar_lista,
                                font=('Rajdhani', 10, 'bold'),
                                bg=self.cores['acento2'],
                                fg='white',
                                activebackground=self.cores['acento'],
                                activeforeground=self.cores['bg_escuro'],
                                relief=tk.FLAT,
                                bd=0,
                                padx=20,
                                pady=8,
                                cursor='hand2')
        btn_carregar.pack(side=tk.LEFT, padx=3)
        
        btn_limpar = tk.Button(frame_controles, 
                              text="✕ LIMPAR",
                              command=self.limpar_tudo,
                              font=('Rajdhani', 9, 'bold'),
                              bg=self.cores['bg_medio'],
                              fg=self.cores['erro'],
                              activebackground=self.cores['erro'],
                              activeforeground='white',
                              relief=tk.FLAT,
                              bd=0,
                              padx=15,
                              pady=8,
                              cursor='hand2')
        btn_limpar.pack(side=tk.LEFT, padx=3)
        
    def criar_coluna_lista(self, parent):
        """Cria a coluna esquerda com a lista de canais."""
        frame = ttk.Frame(parent)
        frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Cabeçalho com contador - neon style
        frame_header = tk.Frame(frame, bg=self.cores['bg_escuro'])
        frame_header.pack(fill=tk.X, pady=(0, 8))
        
        tk.Label(frame_header, 
                text="⚡ CANAIS DISPONÍVEIS",
                font=('Rajdhani', 12, 'bold'),
                fg=self.cores['acento'],
                bg=self.cores['bg_escuro']).pack(side=tk.LEFT)
        
        self.label_contador = tk.Label(frame_header, 
                                       text="(0 itens)",
                                       font=('Rajdhani', 10),
                                       fg=self.cores['texto_dim'],
                                       bg=self.cores['bg_escuro'])
        self.label_contador.pack(side=tk.LEFT, padx=8)
        
        # Campo de busca futurista
        frame_busca = tk.Frame(frame, bg=self.cores['bg_medio'], relief=tk.FLAT, bd=2)
        frame_busca.pack(fill=tk.X, pady=(0, 8))
        
        tk.Label(frame_busca, 
                text="🔍",
                font=('Segoe UI', 12),
                fg=self.cores['acento'],
                bg=self.cores['bg_medio']).pack(side=tk.LEFT, padx=(8, 5))
        
        self.entry_busca = tk.Entry(frame_busca, 
                                    font=('Rajdhani', 10),
                                    bg=self.cores['bg_medio'],
                                    fg=self.cores['texto'],
                                    relief=tk.FLAT,
                                    bd=0,
                                    insertbackground=self.cores['acento'])
        self.entry_busca.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8), ipady=6)
        self.entry_busca.insert(0, "Filtrar canais...")
        self.entry_busca.config(fg=self.cores['texto_dim'])
        self.entry_busca.bind('<FocusIn>', self.limpar_placeholder_busca)
        self.entry_busca.bind('<FocusOut>', self.restaurar_placeholder_busca)
        self.entry_busca.bind('<KeyRelease>', self.filtrar_lista)
        
        # Lista com scrollbars - estilo cyber
        frame_lista = tk.Frame(frame, bg=self.cores['bg_claro'], relief=tk.FLAT, bd=2)
        frame_lista.pack(fill=tk.BOTH, expand=True)
        
        scrollbar_y = tk.Scrollbar(frame_lista, 
                                  orient=tk.VERTICAL,
                                  bg=self.cores['bg_medio'],
                                  troughcolor=self.cores['bg_escuro'],
                                  activebackground=self.cores['acento2'])
        scrollbar_x = tk.Scrollbar(frame_lista, 
                                  orient=tk.HORIZONTAL,
                                  bg=self.cores['bg_medio'],
                                  troughcolor=self.cores['bg_escuro'],
                                  activebackground=self.cores['acento2'])
        
        self.listbox = tk.Listbox(frame_lista,
                                  yscrollcommand=scrollbar_y.set,
                                  xscrollcommand=scrollbar_x.set,
                                  exportselection=False,
                                  font=('Rajdhani', 11),
                                  bg=self.cores['bg_claro'],
                                  fg=self.cores['texto'],
                                  selectbackground=self.cores['acento2'],
                                  selectforeground='white',
                                  activestyle='none',
                                  relief=tk.FLAT,
                                  bd=0,
                                  highlightthickness=0,
                                  selectmode=tk.SINGLE)
        
        scrollbar_y.config(command=self.listbox.yview)
        scrollbar_x.config(command=self.listbox.xview)
        
        scrollbar_y.pack(side=tk.RIGHT, fill=tk.Y)
        scrollbar_x.pack(side=tk.BOTTOM, fill=tk.X)
        self.listbox.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        
        self.listbox.bind('<<ListboxSelect>>', self.on_selecao_manual)
        
    def criar_coluna_detalhes(self, parent):
        """Cria a coluna direita com detalhes do canal."""
        frame = ttk.Frame(parent, width=420)
        frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        frame.pack_propagate(False)
        
        # Cabeçalho futurista
        tk.Label(frame, 
                text="⚡ DETALHES DO CANAL",
                font=('Rajdhani', 13, 'bold'),
                fg=self.cores['acento'],
                bg=self.cores['bg_escuro']).pack(pady=(0, 12))
        
        # Nome do canal
        tk.Label(frame, 
                text="NOME:",
                font=('Rajdhani', 10, 'bold'),
                fg=self.cores['texto_dim'],
                bg=self.cores['bg_escuro']).pack(anchor=tk.W)
        
        self.entry_nome = tk.Entry(frame, 
                                  state='readonly',
                                  font=('Rajdhani', 11, 'bold'),
                                  bg=self.cores['bg_medio'],
                                  fg=self.cores['acento'],
                                  relief=tk.FLAT,
                                  bd=2,
                                  readonlybackground=self.cores['bg_medio'])
        self.entry_nome.pack(fill=tk.X, pady=(3, 12), ipady=6)
        
        # URL
        tk.Label(frame, 
                text="URL STREAM:",
                font=('Rajdhani', 10, 'bold'),
                fg=self.cores['texto_dim'],
                bg=self.cores['bg_escuro']).pack(anchor=tk.W)
        
        frame_url = tk.Frame(frame, bg=self.cores['bg_claro'], relief=tk.FLAT, bd=2)
        frame_url.pack(fill=tk.BOTH, expand=True, pady=(3, 12))
        
        scrollbar_url = tk.Scrollbar(frame_url, 
                                    orient=tk.VERTICAL,
                                    bg=self.cores['bg_medio'],
                                    troughcolor=self.cores['bg_escuro'])
        
        self.text_url = tk.Text(frame_url, 
                               height=8, 
                               wrap=tk.WORD,
                               state='disabled',
                               font=('Consolas', 9),
                               bg=self.cores['bg_claro'],
                               fg=self.cores['texto'],
                               relief=tk.FLAT,
                               bd=0,
                               padx=8,
                               pady=6,
                               yscrollcommand=scrollbar_url.set)
        scrollbar_url.config(command=self.text_url.yview)
        
        scrollbar_url.pack(side=tk.RIGHT, fill=tk.Y)
        self.text_url.pack(fill=tk.BOTH, expand=True)
        
        # === NAVEGAÇÃO CYBER ===
        tk.Label(frame, 
                text="⚡ NAVEGAÇÃO RÁPIDA:",
                font=('Rajdhani', 10, 'bold'),
                fg=self.cores['texto_dim'],
                bg=self.cores['bg_escuro']).pack(anchor=tk.W, pady=(10, 6))
        
        frame_nav = tk.Frame(frame, bg=self.cores['bg_escuro'])
        frame_nav.pack(fill=tk.X, pady=5)
        
        self.btn_anterior = tk.Button(frame_nav, 
                                      text="◄◄ ANTERIOR",
                                      command=lambda: self.navegar(-1),
                                      state='disabled',
                                      font=('Rajdhani', 10, 'bold'),
                                      bg=self.cores['bg_medio'],
                                      fg=self.cores['acento'],
                                      activebackground=self.cores['acento'],
                                      activeforeground=self.cores['bg_escuro'],
                                      disabledforeground=self.cores['texto_dim'],
                                      relief=tk.FLAT,
                                      bd=0,
                                      padx=10,
                                      pady=8,
                                      cursor='hand2')
        self.btn_anterior.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        
        # Indicador de posição - cyber display
        self.label_posicao = tk.Label(frame_nav, 
                                      text="- / -",
                                      font=('Rajdhani', 12, 'bold'),
                                      fg=self.cores['acento3'],
                                      bg=self.cores['bg_escuro'],
                                      width=8)
        self.label_posicao.pack(side=tk.LEFT, padx=4)
        
        self.btn_proximo = tk.Button(frame_nav, 
                                     text="PRÓXIMO ►►",
                                     command=lambda: self.navegar(1),
                                     state='disabled',
                                     font=('Rajdhani', 10, 'bold'),
                                     bg=self.cores['bg_medio'],
                                     fg=self.cores['acento'],
                                     activebackground=self.cores['acento'],
                                     activeforeground=self.cores['bg_escuro'],
                                     disabledforeground=self.cores['texto_dim'],
                                     relief=tk.FLAT,
                                     bd=0,
                                     padx=10,
                                     pady=8,
                                     cursor='hand2')
        self.btn_proximo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))
        
        # === AÇÕES ===
        # Linha divisória cyber
        divisor = tk.Frame(frame, height=2, bg=self.cores['acento2'])
        divisor.pack(fill=tk.X, pady=12)
        
        # Botão de copiar DESTAQUE
        self.btn_copiar = tk.Button(frame, 
                                    text="📋 COPIAR URL",
                                    command=self.copiar_url,
                                    state='disabled',
                                    font=('Rajdhani', 12, 'bold'),
                                    bg=self.cores['acento2'],
                                    fg='white',
                                    activebackground=self.cores['acento'],
                                    activeforeground=self.cores['bg_escuro'],
                                    disabledforeground=self.cores['texto_dim'],
                                    relief=tk.FLAT,
                                    bd=0,
                                    padx=15,
                                    pady=12,
                                    cursor='hand2')
        self.btn_copiar.pack(fill=tk.X, ipady=4)
        
        # Ações secundárias
        frame_acoes = tk.Frame(frame, bg=self.cores['bg_escuro'])
        frame_acoes.pack(fill=tk.X, pady=(10, 0))
        
        btn_exportar = tk.Button(frame_acoes, 
                                text="💾 EXPORTAR",
                                command=self.exportar_selecao,
                                state='disabled',
                                font=('Rajdhani', 9, 'bold'),
                                bg=self.cores['bg_medio'],
                                fg=self.cores['sucesso'],
                                activebackground=self.cores['sucesso'],
                                activeforeground=self.cores['bg_escuro'],
                                relief=tk.FLAT,
                                bd=0,
                                padx=10,
                                pady=6,
                                cursor='hand2')
        btn_exportar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        
        btn_sobre = tk.Button(frame_acoes, 
                             text="ℹ SOBRE",
                             command=self.mostrar_sobre,
                             font=('Rajdhani', 9, 'bold'),
                             bg=self.cores['bg_medio'],
                             fg=self.cores['acento'],
                             activebackground=self.cores['acento'],
                             activeforeground=self.cores['bg_escuro'],
                             relief=tk.FLAT,
                             bd=0,
                             padx=10,
                             pady=6,
                             cursor='hand2')
        btn_sobre.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))
        
    def criar_barra_status(self, parent):
        """Cria a barra de status na parte inferior."""
        frame = tk.Frame(parent, bg=self.cores['bg_medio'], relief=tk.FLAT, bd=2)
        frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(12, 0))
        
        # Frame interno
        frame_interno = tk.Frame(frame, bg=self.cores['bg_medio'])
        frame_interno.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)
        
        self.status_var = tk.StringVar()
        self.status_var.set("⚡ SISTEMA PRONTO | Selecione um arquivo M3U para começar")
        
        self.label_status = tk.Label(frame_interno, 
                                     textvariable=self.status_var,
                                     font=('Rajdhani', 10),
                                     fg=self.cores['sucesso'],
                                     bg=self.cores['bg_medio'],
                                     anchor=tk.W)
        self.label_status.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        # Indicador de atalhos - cyber style
        tk.Label(frame_interno, 
                text="[F1: AJUDA] [Ctrl+O: ABRIR] [Ctrl+C: COPIAR] [↑↓: NAVEGAR]",
                font=('Consolas', 8),
                fg=self.cores['texto_dim'],
                bg=self.cores['bg_medio']).pack(side=tk.RIGHT)
        
    def configurar_atalhos(self):
        """Configura atalhos de teclado."""
        self.root.bind('<Control-o>', lambda e: self.procurar_arquivo())
        self.root.bind('<Control-c>', lambda e: self.copiar_url())
        self.root.bind('<Control-l>', lambda e: self.limpar_tudo())
        self.root.bind('<F1>', lambda e: self.mostrar_ajuda())
        self.root.bind('<F5>', lambda e: self.carregar_lista())
        
        # Navegação com setas quando lista está focada
        self.listbox.bind('<Up>', lambda e: self.navegar_teclado(-1))
        self.listbox.bind('<Down>', lambda e: self.navegar_teclado(1))
        self.listbox.bind('<Return>', lambda e: self.copiar_url())
        
    # === FUNÇÕES DE AÇÃO ===
    
    def procurar_arquivo(self):
        """Abre diálogo para selecionar arquivo."""
        caminho = filedialog.askopenfilename(
            title="Selecione o arquivo M3U",
            filetypes=(("Listas M3U", "*.m3u *.m3u8"), ("Todos os arquivos", "*.*"))
        )
        if caminho:
            self.arquivo_atual = caminho
            self.entry_arquivo.config(state='normal')
            self.entry_arquivo.delete(0, tk.END)
            self.entry_arquivo.insert(0, caminho)
            self.entry_arquivo.config(state='readonly')
            
            self.atualizar_status(f"Arquivo selecionado: {Path(caminho).name}")
            # Auto-carrega após selecionar (Nielsen: Eficiência)
            self.root.after(100, self.carregar_lista)
            
    def carregar_lista(self):
        """Carrega os dados do arquivo."""
        if not self.arquivo_atual:
            messagebox.showwarning("Aviso", "Selecione um arquivo primeiro!")
            return
            
        self.atualizar_status("Carregando arquivo...")
        self.root.update()
        
        self.dados_carregados = extrair_links_m3u(self.arquivo_atual)
        
        self.listbox.delete(0, tk.END)
        self.limpar_detalhes()
        self.indice_atual = -1
        
        if self.dados_carregados:
            for nome, url in self.dados_carregados:
                self.listbox.insert(tk.END, nome)
            
            self.label_contador.config(text=f"({len(self.dados_carregados)} itens)")
            self.atualizar_status(f"✓ {len(self.dados_carregados)} canais carregados com sucesso")
            
            # Seleciona o primeiro automaticamente
            if len(self.dados_carregados) > 0:
                self.listbox.selection_set(0)
                self.listbox.activate(0)
                self.atualizar_detalhes(0)
        else:
            self.label_contador.config(text="(0 itens)")
            self.atualizar_status("⚠ Nenhum link válido encontrado no arquivo", erro=True)
            
    def atualizar_detalhes(self, indice):
        """Atualiza os campos de detalhe."""
        if not (0 <= indice < len(self.dados_carregados)):
            return
            
        self.indice_atual = indice
        nome, url = self.dados_carregados[indice]
        
        # Atualiza nome
        self.entry_nome.config(state='normal')
        self.entry_nome.delete(0, tk.END)
        self.entry_nome.insert(0, nome)
        self.entry_nome.config(state='readonly')
        
        # Atualiza URL
        self.text_url.config(state='normal')
        self.text_url.delete(1.0, tk.END)
        self.text_url.insert(1.0, url)
        self.text_url.config(state='disabled')
        
        # Atualiza indicador de posição
        self.label_posicao.config(text=f"{indice + 1} / {len(self.dados_carregados)}")
        
        # Habilita botões
        self.btn_copiar.config(state='normal')
        self.btn_anterior.config(state='normal' if indice > 0 else 'disabled')
        self.btn_proximo.config(state='normal' if indice < len(self.dados_carregados) - 1 else 'disabled')
        
    def on_selecao_manual(self, evento):
        """Evento de seleção manual na lista."""
        indices = self.listbox.curselection()
        if indices:
            self.atualizar_detalhes(indices[0])
            
    def navegar(self, direcao):
        """Navega para próximo/anterior e copia."""
        total = len(self.dados_carregados)
        if total == 0:
            return
            
        novo_indice = (self.indice_atual + direcao) % total
        
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(novo_indice)
        self.listbox.see(novo_indice)
        self.listbox.activate(novo_indice)
        
        self.atualizar_detalhes(novo_indice)
        self.copiar_url()
        
    def navegar_teclado(self, direcao):
        """Navegação com teclado."""
        indices = self.listbox.curselection()
        if not indices:
            if len(self.dados_carregados) > 0:
                self.listbox.selection_set(0)
                self.atualizar_detalhes(0)
            return
            
        indice_atual = indices[0]
        novo_indice = max(0, min(len(self.dados_carregados) - 1, indice_atual + direcao))
        
        if novo_indice != indice_atual:
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(novo_indice)
            self.listbox.see(novo_indice)
            self.atualizar_detalhes(novo_indice)
            
    def copiar_url(self):
        """Copia a URL para área de transferência."""
        if self.indice_atual < 0:
            return
            
        url = self.text_url.get(1.0, tk.END).strip()
        nome = self.entry_nome.get().strip()
        
        if url:
            pyperclip.copy(url)
            self.atualizar_status(f"✓ URL copiada: {nome}", temporario=True)
        else:
            self.atualizar_status("⚠ Nada para copiar", erro=True)
            
    def filtrar_lista(self, evento=None):
        """Filtra a lista de canais."""
        termo = self.entry_busca.get().strip()
        
        if not termo or termo == "Filtrar canais...":
            # Mostra todos
            self.listbox.delete(0, tk.END)
            for nome, url in self.dados_carregados:
                self.listbox.insert(tk.END, nome)
            self.label_contador.config(text=f"({len(self.dados_carregados)} itens)")
        else:
            # Filtra
            self.listbox.delete(0, tk.END)
            contador = 0
            for nome, url in self.dados_carregados:
                if termo.lower() in nome.lower():
                    self.listbox.insert(tk.END, nome)
                    contador += 1
            self.label_contador.config(text=f"({contador} de {len(self.dados_carregados)} itens)")
            
    def limpar_placeholder_busca(self, evento):
        """Remove placeholder do campo de busca."""
        if self.entry_busca.get() == "Filtrar canais...":
            self.entry_busca.delete(0, tk.END)
            self.entry_busca.config(fg=self.cores['texto'])
            
    def restaurar_placeholder_busca(self, evento):
        """Restaura placeholder se vazio."""
        if not self.entry_busca.get():
            self.entry_busca.insert(0, "Filtrar canais...")
            self.entry_busca.config(fg=self.cores['texto_dim'])
            
    def limpar_detalhes(self):
        """Limpa os campos de detalhes."""
        self.entry_nome.config(state='normal')
        self.entry_nome.delete(0, tk.END)
        self.entry_nome.config(state='readonly')
        
        self.text_url.config(state='normal')
        self.text_url.delete(1.0, tk.END)
        self.text_url.config(state='disabled')
        
        self.label_posicao.config(text="- / -")
        self.btn_copiar.config(state='disabled')
        self.btn_anterior.config(state='disabled')
        self.btn_proximo.config(state='disabled')
        
    def limpar_tudo(self):
        """Limpa tudo e reinicia."""
        if self.dados_carregados and messagebox.askyesno(
            "Confirmação", 
            "Deseja realmente limpar tudo?\nVocê precisará carregar o arquivo novamente."):
            
            self.dados_carregados.clear()
            self.indice_atual = -1
            self.arquivo_atual = None
            
            self.entry_arquivo.config(state='normal')
            self.entry_arquivo.delete(0, tk.END)
            self.entry_arquivo.config(state='readonly')
            
            self.listbox.delete(0, tk.END)
            self.limpar_detalhes()
            self.label_contador.config(text="(0 itens)")
            
            self.atualizar_status("Interface limpa. Pronto para novo arquivo.")
            
    def exportar_selecao(self):
        """Exporta o canal selecionado."""
        messagebox.showinfo("Em Desenvolvimento", 
                           "Funcionalidade de exportação em desenvolvimento!")
        
    def mostrar_sobre(self):
        """Mostra informações sobre o programa."""
        sobre_window = tk.Toplevel(self.root)
        sobre_window.title("Sobre - M3U Extractor")
        sobre_window.geometry("450x350")
        sobre_window.resizable(False, False)
        sobre_window.configure(bg=self.cores['bg_escuro'])
        
        # Banner
        banner = tk.Frame(sobre_window, bg=self.cores['acento2'], height=80)
        banner.pack(fill=tk.X)
        banner.pack_propagate(False)
        
        tk.Label(banner, 
                text="⚡ M3U EXTRACTOR",
                font=('Rajdhani', 24, 'bold'),
                fg='white',
                bg=self.cores['acento2']).pack(expand=True)
        
        # Conteúdo
        content = tk.Frame(sobre_window, bg=self.cores['bg_escuro'])
        content.pack(fill=tk.BOTH, expand=True, padx=30, pady=20)
        
        tk.Label(content, 
                text="Next-Gen Streaming Tool",
                font=('Rajdhani', 14),
                fg=self.cores['acento'],
                bg=self.cores['bg_escuro']).pack(pady=(0, 10))
        
        tk.Label(content, 
                text="VERSÃO 2.0 CYBER EDITION",
                font=('Rajdhani', 11, 'bold'),
                fg=self.cores['texto'],
                bg=self.cores['bg_escuro']).pack(pady=5)
        
        tk.Label(content, 
                text="Interface modernizada seguindo as\n"
                     "10 Heurísticas de Usabilidade de Nielsen\n"
                     "com design futurista e vibrante.",
                font=('Rajdhani', 10),
                fg=self.cores['texto_dim'],
                bg=self.cores['bg_escuro'],
                justify=tk.CENTER).pack(pady=15)
        
        # Divisor
        tk.Frame(content, height=2, bg=self.cores['acento2']).pack(fill=tk.X, pady=10)
        
        tk.Label(content, 
                text="POWERED BY",
                font=('Rajdhani', 9),
                fg=self.cores['texto_dim'],
                bg=self.cores['bg_escuro']).pack()
        
        tk.Label(content, 
                text="SISTEMASVOX",
                font=('Rajdhani', 18, 'bold'),
                fg=self.cores['acento3'],
                bg=self.cores['bg_escuro']).pack(pady=5)
        
        # Botão fechar
        tk.Button(content, 
                 text="⚡ FECHAR",
                 command=sobre_window.destroy,
                 font=('Rajdhani', 10, 'bold'),
                 bg=self.cores['acento2'],
                 fg='white',
                 activebackground=self.cores['acento'],
                 activeforeground=self.cores['bg_escuro'],
                 relief=tk.FLAT,
                 bd=0,
                 padx=30,
                 pady=8,
                 cursor='hand2').pack(pady=(15, 0))
        
    def mostrar_ajuda(self):
        """Mostra ajuda com atalhos."""
        ajuda_window = tk.Toplevel(self.root)
        ajuda_window.title("Ajuda - Atalhos")
        ajuda_window.geometry("500x601")
        ajuda_window.resizable(False, False)
        ajuda_window.configure(bg=self.cores['bg_escuro'])
        
        # Banner
        banner = tk.Frame(ajuda_window, bg=self.cores['acento'], height=70)
        banner.pack(fill=tk.X)
        banner.pack_propagate(False)
        
        tk.Label(banner, 
                text="⚡ CENTRAL DE AJUDA",
                font=('Rajdhani', 20, 'bold'),
                fg=self.cores['bg_escuro'],
                bg=self.cores['acento']).pack(expand=True)
        
        # Conteúdo
        content = tk.Frame(ajuda_window, bg=self.cores['bg_escuro'])
        content.pack(fill=tk.BOTH, expand=True, padx=25, pady=20)
        
        # Seção Atalhos
        tk.Label(content, 
                text="ATALHOS DE TECLADO",
                font=('Rajdhani', 13, 'bold'),
                fg=self.cores['acento'],
                bg=self.cores['bg_escuro']).pack(anchor=tk.W, pady=(0, 10))
        
        atalhos = [
            ("Ctrl+O", "Abrir arquivo M3U"),
            ("Ctrl+C", "Copiar URL selecionada"),
            ("Ctrl+L", "Limpar interface"),
            ("F1", "Mostrar esta ajuda"),
            ("F5", "Recarregar arquivo"),
            ("↑ / ↓", "Navegar pela lista"),
            ("Enter", "Copiar URL (lista focada)")
        ]
        
        for tecla, descricao in atalhos:
            frame_atalho = tk.Frame(content, bg=self.cores['bg_escuro'])
            frame_atalho.pack(fill=tk.X, pady=3)
            
            tk.Label(frame_atalho, 
                    text=tecla,
                    font=('Consolas', 10, 'bold'),
                    fg=self.cores['acento3'],
                    bg=self.cores['bg_medio'],
                    width=12,
                    anchor=tk.W,
                    padx=8,
                    pady=4).pack(side=tk.LEFT, padx=(0, 10))
            
            tk.Label(frame_atalho, 
                    text=descricao,
                    font=('Rajdhani', 10),
                    fg=self.cores['texto'],
                    bg=self.cores['bg_escuro'],
                    anchor=tk.W).pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        # Divisor
        tk.Frame(content, height=2, bg=self.cores['acento2']).pack(fill=tk.X, pady=15)
        
        # Dicas
        tk.Label(content, 
                text="💡 DICAS DE USO",
                font=('Rajdhani', 13, 'bold'),
                fg=self.cores['acento'],
                bg=self.cores['bg_escuro']).pack(anchor=tk.W, pady=(0, 8))
        
        dicas = [
            "• Use o campo de busca para filtrar canais",
            "• Navegue rapidamente com os botões ◄◄ e ►►",
            "• A URL é copiada automaticamente ao navegar",
            "• Clique em qualquer canal para ver detalhes"
        ]
        
        for dica in dicas:
            tk.Label(content, 
                    text=dica,
                    font=('Rajdhani', 10),
                    fg=self.cores['texto_dim'],
                    bg=self.cores['bg_escuro'],
                    anchor=tk.W).pack(anchor=tk.W, pady=2)
        
        # Botão fechar
        tk.Button(content, 
                 text="⚡ ENTENDI",
                 command=ajuda_window.destroy,
                 font=('Rajdhani', 11, 'bold'),
                 bg=self.cores['acento'],
                 fg=self.cores['bg_escuro'],
                 activebackground=self.cores['acento3'],
                 activeforeground='white',
                 relief=tk.FLAT,
                 bd=0,
                 padx=40,
                 pady=10,
                 cursor='hand2').pack(pady=(15, 0))
        
    def atualizar_status(self, mensagem, erro=False, temporario=False):
        """Atualiza a barra de status."""
        if erro:
            icon = "⚠"
            cor = self.cores['erro']
        else:
            icon = "⚡"
            cor = self.cores['sucesso']
            
        self.status_var.set(f"{icon} {mensagem}")
        self.label_status.config(fg=cor)
            
        if temporario:
            self.root.after(3000, lambda: self.status_var.set("⚡ SISTEMA PRONTO"))

# === EXECUÇÃO ===
if __name__ == "__main__":
    root = tk.Tk()
    app = ExtratorM3U(root)
    root.mainloop()