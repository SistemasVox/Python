#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Script interativo em Python para analisar logs do dnsmasq.
Adiciona funcionalidade altamente aprimorada para identificar acessos a conteúdos adultos
usando uma lista de domínios suspeitos e técnicas avançadas de detecção.
"""

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
import re
from datetime import datetime, timedelta
from collections import Counter, defaultdict
import os
import requests
import threading
import time

class AnalisadorDnsmasq(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Analisador dnsmasq")
        self.geometry("1000x600")
        
        # Cache para domínios adultos
        self.dominios_adultos = set()
        self.ultima_atualizacao = None
        self.download_em_progresso = False
        self.download_status = tk.StringVar(value="Lista de domínios não carregada")
        self.estatisticas_adulto = {}
        self.relatorio_adulto_detalhado = []

        # Seleção de arquivo
        tk.Label(self, text="Arquivo de log dnsmasq:").pack(anchor='w', padx=10, pady=(10,0))
        frm_file = tk.Frame(self)
        frm_file.pack(fill='x', padx=10)
        self.entry_file = tk.Entry(frm_file)
        self.entry_file.pack(side='left', fill='x', expand=True)
        tk.Button(frm_file, text="Selecionar...", command=self.selecionar_arquivo).pack(side='left', padx=5)

        # Parâmetros
        frm_params = tk.Frame(self)
        frm_params.pack(fill='x', padx=10, pady=5)
        tk.Label(frm_params, text="TOP N:").pack(side='left')
        self.spin_n = tk.Spinbox(frm_params, from_=1, to=100, width=5)
        self.spin_n.pack(side='left', padx=5)
        self.spin_n.delete(0, 'end'); self.spin_n.insert(0, '10')

        tk.Label(frm_params, text="Período:").pack(side='left', padx=(20,0))
        self.var_periodo = tk.StringVar(value='all')
        tk.OptionMenu(frm_params, self.var_periodo, 'all','day','week','month').pack(side='left', padx=5)

        tk.Label(frm_params, text="Filtrar por IP:").pack(side='left', padx=(20,0))
        self.combo_ip = ttk.Combobox(frm_params, width=15, state='readonly')
        self.combo_ip.pack(side='left', padx=5)
        self.combo_ip['values'] = ['Todos']
        self.combo_ip.current(0)
        self.combo_ip.bind('<<ComboboxSelected>>', self.filtrar_por_ip)

        # Status do download
        tk.Label(self, textvariable=self.download_status).pack(anchor='w', padx=10)

        # Botões
        btn_frame = tk.Frame(self)
        btn_frame.pack(pady=5)
        tk.Button(btn_frame, text="Analisar 😊", command=self.analisar).pack(side='left', padx=10)
        tk.Button(btn_frame, text="Analisar Domínios 📄", command=self.analisar_dominios).pack(side='left', padx=10)
        tk.Button(btn_frame, text="Atualizar Lista Adulto 🔄", command=self.iniciar_download_lista).pack(side='left', padx=10)
        tk.Button(btn_frame, text="Adulto 🚫", command=self.analisar_adulto).pack(side='left', padx=10)
        tk.Button(btn_frame, text="Salvar Relatório 💾", command=self.salvar_relatorio).pack(side='left', padx=10)
        
        # Botão para relatório detalhado (inicialmente oculto)
        self.btn_relatorio_detalhado = tk.Button(btn_frame, text="Salvar Detalhado 📊", command=self.salvar_relatorio_detalhado)
        
        # Área de relatório
        self.txt_relatorio = scrolledtext.ScrolledText(self, wrap='none', font=('Courier', 10))
        self.txt_relatorio.pack(fill='both', expand=True, padx=10, pady=5)
        self.relatorio_texto = ''
        self.entradas_filtradas = []
        
        # Iniciar download da lista automaticamente
        self.after(1000, self.iniciar_download_lista)

    def selecionar_arquivo(self):
        arquivo = filedialog.askopenfilename(
            title="Selecione o arquivo de log",
            filetypes=[("Log files","*.log *.txt"),("Todos","*.*")]
        )
        if arquivo:
            self.entry_file.delete(0, 'end')
            self.entry_file.insert(0, arquivo)

    def iniciar_download_lista(self):
        if self.download_em_progresso:
            messagebox.showinfo("Download em andamento", "Já existe um download em andamento. Por favor, aguarde.")
            return
            
        self.download_em_progresso = True
        self.download_status.set("Baixando lista de domínios adultos...")
        threading.Thread(target=self._carregar_lista_adulta_thread, daemon=True).start()

    def _carregar_lista_adulta_thread(self):
        try:
            url = 'https://raw.githubusercontent.com/4skinSkywalker/Anti-Porn-HOSTS-File/master/HOSTS.txt'
            response = requests.get(url)
            response.raise_for_status()
            
            dominios = set()
            linhas = response.text.splitlines()
            
            for linha in linhas:
                linha = linha.strip()
                if linha and not linha.startswith('#'):
                    partes = linha.split()
                    if len(partes) >= 2 and partes[0] == '0.0.0.0':
                        dominios.add(partes[1].lower())
            
            self.dominios_adultos = dominios
            self.ultima_atualizacao = datetime.now()
            
            # Atualizar a interface do usuário no thread principal
            self.after(0, lambda: self.download_status.set(
                f"Lista de domínios adultos carregada: {len(self.dominios_adultos)} domínios. Última atualização: {self.ultima_atualizacao.strftime('%Y-%m-%d %H:%M:%S')}"
            ))
            
        except Exception as e:
            # Atualizar a interface do usuário no thread principal
            self.after(0, lambda: self.download_status.set(f"Erro ao baixar lista: {str(e)}"))
            self.after(0, lambda: messagebox.showerror("Erro", f"Falha ao carregar lista de domínios adultos: {e}"))
        
        finally:
            self.download_em_progresso = False

    def _ler_entradas(self):
        caminho = self.entry_file.get().strip()
        if not caminho or not os.path.isfile(caminho):
            messagebox.showerror("Erro", "Arquivo inválido ou não selecionado :(")
            return None
        padrao = re.compile(
            r'^(?P<mes>\w+)\s+(?P<dia>\d+)\s+(?P<hora>\d+:\d+:\d+).+?'
            r'query\[(?P<tipo>\w+)\]\s+(?P<dominio>\S+)\s+from\s+(?P<ip>\d+\.\d+\.\d+\.\d+)'
        )
        entradas = []
        ano = datetime.now().year
        try:
            with open(caminho, 'r', encoding='utf-8', errors='ignore') as f:
                for linha in f:
                    m = padrao.search(linha)
                    if m:
                        mes = datetime.strptime(m.group('mes'), '%b').month
                        dia = int(m.group('dia'))
                        h, mn, s = map(int, m.group('hora').split(':'))
                        dt = datetime(ano, mes, dia, h, mn, s)
                        entradas.append({'dt': dt, 'tipo': m.group('tipo'), 'dominio': m.group('dominio').lower(), 'ip': m.group('ip')})
        except Exception as e:
            messagebox.showerror("Erro", f"Falha ao ler o arquivo: {e}")
            return None
        return entradas

    def _filtrar_periodo(self, entradas):
        periodo = self.var_periodo.get()
        agora = datetime.now()
        if periodo == 'day': inicio = agora.replace(hour=0, minute=0, second=0)
        elif periodo == 'week': inicio = (agora - timedelta(days=6)).replace(hour=0, minute=0, second=0)
        elif periodo == 'month': inicio = agora.replace(day=1, hour=0, minute=0, second=0)
        else: inicio = None
        return [e for e in entradas if inicio is None or e['dt'] >= inicio]

    def analisar(self):
        entradas = self._ler_entradas()
        if entradas is None: return
        try: n = int(self.spin_n.get())
        except: n = 10
        filtradas = self._filtrar_periodo(entradas)
        cont_dom = Counter(e['dominio'] for e in filtradas)
        cont_ip = Counter(e['ip'] for e in filtradas)
        cont_hora = Counter(e['dt'].hour for e in filtradas)
        cont_tipo = Counter(e['tipo'] for e in filtradas)
        total = len(filtradas)
        periodo = self.var_periodo.get()
        agora = datetime.now()

        linhas = []
        hdr = f" RELATÓRIO DNSMASQ ({periodo.upper()}) "
        border = '+' + '-'*len(hdr) + '+'
        linhas.extend([border, f"|{hdr}|", border,
                       f"Gerado em: {agora.strftime('%Y-%m-%d %H:%M:%S')}", border,
                       f"Total de consultas: {total}", f"Domínios únicos: {len(cont_dom)}", f"IPs únicos: {len(cont_ip)}", border,
                       f" TOP {n} DOMÍNIOS ", border])
        for dom, cnt in cont_dom.most_common(n): linhas.append(f" {dom:<30} {cnt:>5}")
        linhas.extend([border, f" TOP {n} IPS CLIENTES ", border])
        for ip, cnt in cont_ip.most_common(n): linhas.append(f" {ip:<30} {cnt:>5}")
        linhas.extend([border, f" CONSULTAS POR HORA ", border])
        for h in sorted(cont_hora): linhas.append(f" {h:02d}:00 {' '*25}{cont_hora[h]:>5}")
        linhas.extend([border, f" CONSULTAS POR TIPO ", border])
        for t, cnt in cont_tipo.items(): linhas.append(f" {t:<30} {cnt:>5}")
        linhas.append(border)

        self.relatorio_texto = "\n".join(linhas)
        self.txt_relatorio.delete('1.0', 'end')
        self.txt_relatorio.insert('1.0', self.relatorio_texto)
        
        # Ocultar botão de relatório detalhado se estiver visível
        self.btn_relatorio_detalhado.pack_forget()

    def analisar_dominios(self):
        entradas = self._ler_entradas()
        if entradas is None: return
        self.entradas_filtradas = self._filtrar_periodo(entradas)
        
        ips_unicos = sorted(set(e['ip'] for e in self.entradas_filtradas))
        self.combo_ip['values'] = ['Todos'] + ips_unicos
        self.combo_ip.current(0)
        
        self._gerar_relatorio_dominios(self.entradas_filtradas)
        
        # Ocultar botão de relatório detalhado se estiver visível
        self.btn_relatorio_detalhado.pack_forget()

    def filtrar_por_ip(self, event):
        if not self.entradas_filtradas: return
        ip_selecionado = self.combo_ip.get()
        if ip_selecionado == 'Todos':
            filtradas = self.entradas_filtradas
        else:
            filtradas = [e for e in self.entradas_filtradas if e['ip'] == ip_selecionado]
        self._gerar_relatorio_dominios(filtradas)

    def _gerar_relatorio_dominios(self, filtradas):
        cont = Counter()
        last_dt = defaultdict(lambda: datetime.min)
        last_ip = {}
        for e in filtradas:
            dom = e['dominio']
            cont[dom] += 1
            if e['dt'] >= last_dt[dom]:
                last_dt[dom] = e['dt']
                last_ip[dom] = e['ip']
        
        agora = datetime.now()
        periodo = self.var_periodo.get()
        ip_selecionado = self.combo_ip.get()
        filtro_ip = f"Filtrado por IP: {ip_selecionado}" if ip_selecionado != 'Todos' else "Todos os IPs"
        
        linhas = []
        hdr = f" DOMÍNIOS DNSMASQ ({periodo.upper()}) - {filtro_ip} "
        border = '+' + '-'*len(hdr) + '+'
        col_head = f" {'DOMÍNIO':<40} {'QTD':>7} {'ÚLTIMO ACESSO':>20} {'IP ÚLTIMO':>15} "
        border2 = '+' + '-'*40 + '+' + '-'*7 + '+' + '-'*20 + '+' + '-'*15 + '+'
        linhas.extend([border, f"|{hdr}|", border,
                       f"Gerado em: {agora.strftime('%Y-%m-%d %H:%M:%S')}", border, col_head, border2])
        for dom, cnt in cont.most_common():
            dt_str = last_dt[dom].strftime('%Y-%m-%d %H:%M:%S')
            ip = last_ip.get(dom, '')
            linhas.append(f" {dom:<40} {cnt:>7} {dt_str:>20} {ip:>15}")
        linhas.append(border2)
        
        self.relatorio_texto = "\n".join(linhas)
        self.txt_relatorio.delete('1.0', 'end')
        self.txt_relatorio.insert('1.0', self.relatorio_texto)

    def analisar_adulto(self):
        if not self.dominios_adultos:
            resposta = messagebox.askyesno("Atenção", "Lista de domínios adultos não carregada. Deseja baixá-la agora?")
            if resposta:
                self.iniciar_download_lista()
                messagebox.showinfo("Aguarde", "Por favor, aguarde o download concluir e tente novamente.")
            return
            
        entradas = self._ler_entradas()
        if entradas is None: return
        
        filtradas = self._filtrar_periodo(entradas)
        
        # Análise avançada de domínios
        acessos_adultos = []
        dominios_analisados = {}  # Cache para análises anteriores
        
        # Palavras-chave suspeitas adicionais para verificação
        palavras_chave = [
            'xxx', 'porn', 'adult', 'sex', 'nude', 'naked', 
            'escort', 'cam', 'mature', 'fuck', 'xnxx', 'redtube', 
            'xvideos', 'penis', 'vagina', 'hardcore'
        ]
        
        for entrada in filtradas:
            dominio = entrada['dominio'].lower()
            
            # Verifica cache para evitar análises repetidas
            if dominio in dominios_analisados:
                if dominios_analisados[dominio]['classificacao'] == 'adulto':
                    entrada_copy = entrada.copy()
                    entrada_copy['razao'] = dominios_analisados[dominio]['razao']
                    acessos_adultos.append(entrada_copy)
                continue
            
            classificacao = 'normal'
            razao = None
            
            # 1. Verificação direta na lista de domínios adultos
            if dominio in self.dominios_adultos:
                classificacao = 'adulto'
                razao = "Correspondência exata na lista"
            
            # 2. Verificação de domínio raiz na lista (ignora subdomínios)
            elif any(dominio.endswith('.' + d) for d in self.dominios_adultos):
                classificacao = 'adulto'
                for d in self.dominios_adultos:
                    if dominio.endswith('.' + d):
                        razao = f"Domínio raiz '{d}' está na lista"
                        break
            
            # 3. Verificação de subdomínios
            else:
                partes = dominio.split('.')
                for i in range(1, len(partes)):
                    subdominio = '.'.join(partes[i:])
                    if subdominio in self.dominios_adultos:
                        classificacao = 'adulto'
                        razao = f"Subdomínio '{subdominio}' está na lista"
                        break
            
            # 4. Análise de palavras-chave no domínio
            if classificacao == 'normal':
                encontradas = []
                # Verifica palavras-chave suspeitas
                for palavra in palavras_chave:
                    if palavra in dominio:
                        encontradas.append(palavra)
                
                if encontradas:
                    classificacao = 'adulto'
                    razao = f"Palavras-chave suspeitas: {', '.join(encontradas)}"
            
            # Salva resultado no cache
            dominios_analisados[dominio] = {
                'classificacao': classificacao,
                'razao': razao
            }
            
            # Adiciona à lista de acessos adultos se classificado como tal
            if classificacao == 'adulto':
                entrada_copy = entrada.copy()
                entrada_copy['razao'] = razao
                acessos_adultos.append(entrada_copy)
        
        # Estatísticas
        self.estatisticas_adulto = {
            'total_acessos': len(acessos_adultos),
            'ips_unicos': len(set(e['ip'] for e in acessos_adultos)),
            'dominios_unicos': len(set(e['dominio'] for e in acessos_adultos)),
            'tipos_correspondencia': Counter(e['razao'] for e in acessos_adultos)
        }
        
        self._gerar_relatorio_adulto(acessos_adultos)

    def _gerar_relatorio_adulto(self, acessos):
        if not acessos:
            messagebox.showinfo("Informação", "Nenhum acesso a conteúdo adulto detectado.")
            return
        
        # Salvar acessos para relatório detalhado
        self.relatorio_adulto_detalhado = acessos
        
        agora = datetime.now()
        periodo = self.var_periodo.get()
        
        # Agrupar acessos por IP
        acessos_por_ip = defaultdict(list)
        for acesso in acessos:
            acessos_por_ip[acesso['ip']].append(acesso)
        
        linhas = []
        hdr = f" ACESSOS A CONTEÚDO ADULTO ({periodo.upper()}) "
        border = '+' + '-'*len(hdr) + '+'
        col_head = f" {'HORA':<20} {'DOMÍNIO':<40} {'IP':<15} {'MOTIVO DETECÇÃO':<30}"
        border2 = '+' + '-'*20 + '+' + '-'*40 + '+' + '-'*15 + '+' + '-'*30 + '+'
        
        est = self.estatisticas_adulto
        linhas.extend([
            border, f"|{hdr}|", border,
            f"Gerado em: {agora.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Total de acessos adultos detectados: {est['total_acessos']}",
            f"IPs únicos envolvidos: {est['ips_unicos']}",
            f"Domínios adultos únicos acessados: {est['dominios_unicos']}",
            border,
            " ESTATÍSTICAS POR TIPO DE DETECÇÃO:",
        ])
        
        # Adicionar estatísticas dos tipos de correspondência
        for tipo, contagem in est['tipos_correspondencia'].most_common():
            pct = (contagem / est['total_acessos']) * 100
            linhas.append(f" - {tipo}: {contagem} ({pct:.1f}%)")
        
        linhas.extend([border, col_head, border2])
        
        # Mostra os acessos agrupados por IP
        for ip, acessos_ip in sorted(acessos_por_ip.items(), 
                                key=lambda x: len(x[1]), 
                                reverse=True):
            # Cabeçalho para o IP
            linhas.append(f" {'IP: ' + ip:<20} {'Total de acessos: ' + str(len(acessos_ip)):<40} {'':<15} {'':<30}")
            
            # Lista de acessos para este IP, ordenados por data
            for e in sorted(acessos_ip, key=lambda x: x['dt'], reverse=True):
                hora = e['dt'].strftime('%Y-%m-%d %H:%M:%S')
                dominio = e['dominio']
                razao = e['razao']
                linhas.append(f" {hora:<20} {dominio:<40} {'':<15} {razao:<30}")
            
            linhas.append(border2)
        
        self.relatorio_texto = "\n".join(linhas)
        self.txt_relatorio.delete('1.0', 'end')
        self.txt_relatorio.insert('1.0', self.relatorio_texto)
        
        # Exibir botão para relatório detalhado se houver muitos acessos
        if len(acessos) > 50:
            self.txt_relatorio.insert('end', "\n\nNota: Este relatório contém muitos acessos. Use o botão 'Salvar Relatório Detalhado' para exportar todos os detalhes.")
            self.btn_relatorio_detalhado.pack(side='left', padx=10)
        else:
            self.btn_relatorio_detalhado.pack_forget()

    def salvar_relatorio(self):
        if not self.relatorio_texto:
            messagebox.showwarning("Atenção", "Nenhum relatório gerado para salvar 😕")
            return
        path = filedialog.asksaveasfilename(
            defaultextension='.txt', filetypes=[('Text file','*.txt'),('All','*.*')], title='Salvar relatório como'
        )
        if path:
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(self.relatorio_texto)
                messagebox.showinfo("Sucesso", f"Relatório salvo em:\n{path} 😊")
            except Exception as e:
                messagebox.showerror("Erro", f"Falha ao salvar: {e}")

    def salvar_relatorio_detalhado(self):
        """Salva um relatório detalhado de todos os acessos a conteúdo adulto em formato CSV"""
        if not self.relatorio_adulto_detalhado:
            messagebox.showwarning("Atenção", "Nenhum relatório de adulto gerado para salvar")
            return
        
        path = filedialog.asksaveasfilename(
            defaultextension='.csv', 
            filetypes=[('CSV file','*.csv'),('All','*.*')], 
            title='Salvar relatório detalhado como'
        )
        
        if path:
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    # Cabeçalho
                    f.write("Data,Hora,IP,Dominio,Tipo,Razao\n")
                    
                    # Dados
                    for acesso in sorted(self.relatorio_adulto_detalhado, key=lambda x: x['dt'], reverse=True):
                        data = acesso['dt'].strftime('%Y-%m-%d')
                        hora = acesso['dt'].strftime('%H:%M:%S')
                        ip = acesso['ip']
                        dominio = acesso['dominio'].replace(',', ';')  # Evitar quebra de CSV
                        tipo = acesso['tipo']
                        razao = acesso['razao'].replace(',', ';') if 'razao' in acesso else ''
                        
                        f.write(f"{data},{hora},{ip},{dominio},{tipo},{razao}\n")
                
                messagebox.showinfo("Sucesso", f"Relatório detalhado salvo em:\n{path} 📊")
            except Exception as e:
                messagebox.showerror("Erro", f"Falha ao salvar: {e}")

if __name__ == '__main__':
    AnalisadorDnsmasq().mainloop()