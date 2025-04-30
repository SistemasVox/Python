import tkinter as tk
from tkinter import ttk
import psutil
import wmi                        # pip install wmi
import threading
import time
import math
import os
import tempfile
from PIL import Image, ImageTk, ImageDraw

class SystemMonitor:
    def __init__(self, root):
        #--------------------------------------------------
        # Janela
        #--------------------------------------------------
        self.root = root
        self.root.title("System Monitor Pro")
        self.root.geometry("780x420")
        self.root.resizable(True, True)

        #--------------------------------------------------
        # Hardware info
        #--------------------------------------------------
        c = wmi.WMI()
        cpu = c.Win32_Processor()[0]
        self.cpu_name   = cpu.Name.strip()
        self.cpu_hw_max = cpu.MaxClockSpeed / 1000.0   # GHz

        vm = psutil.virtual_memory()
        self.mem_hw_max = vm.total / (1024**3)        # GB
        self.mem_name   = f"{self.mem_hw_max:.2f} GB total"

        self.disk_speed_max = 0.0
        self.disk_name      = c.Win32_DiskDrive()[0].Model.strip()
        self.disk_updated   = False

        self.cpu_cores = psutil.cpu_count(logical=True)

        # dimensões gauge
        base_radius        = 75
        self.gauge_center  = 100
        self.gauge_radius  = int(base_radius * 1.05)
        self.needle_len    = int(self.gauge_radius * 0.9)
        self.inner_radius  = int(10 * 0.95)

        # cores
        self.colors = {
            'bg':  '#2D2D2D',
            'fg':  '#ECECEC',
            'ac1': '#3498DB',
            'ac2': '#2ECC71',
            'ac3': '#E74C3C',
            'hdr': '#34495E'
        }

        # UI
        self.load_icons()
        self.configure_styles()
        self.create_widgets()

        # dispara teste de disco
        threading.Thread(target=self._disk_speed_test, daemon=True).start()

        # monitora
        self.setup_monitoring()

    def load_icons(self):
        def circle(color, size=24):
            img = Image.new('RGBA', (size, size), (0,0,0,0))
            d   = ImageDraw.Draw(img)
            d.ellipse((2,2,size-2,size-2), fill=color)
            return ImageTk.PhotoImage(img)
        self.cpu_icon  = circle(self.colors['ac1'])
        self.mem_icon  = circle(self.colors['ac2'])
        self.disk_icon = circle(self.colors['ac3'])

    def configure_styles(self):
        s = ttk.Style(); s.theme_use('clam')
        s.configure('Dark.TFrame', background=self.colors['bg'])
        s.configure('Header.TLabel',
                    background=self.colors['hdr'],
                    foreground=self.colors['fg'],
                    font=('Segoe UI',10,'bold'),
                    padding=2)
        s.configure('Treeview',
                    background=self.colors['bg'],
                    foreground=self.colors['fg'],
                    fieldbackground=self.colors['bg'],
                    borderwidth=0,
                    rowheight=16,
                    font=('Segoe UI',8))
        s.configure('Treeview.Heading',
                    background=self.colors['hdr'],
                    foreground=self.colors['fg'],
                    font=('Segoe UI',8,'bold'),
                    relief='flat')

    def create_widgets(self):
        # frame central
        self.main_frame = ttk.Frame(self.root, style='Dark.TFrame')
        self.main_frame.pack(fill='both', expand=True, padx=2, pady=(2,0))

        # cria gauges
        for key,title,icon,color,fmt,unit,maxhw in [
            ('cpu',    'CPU',    self.cpu_icon,  self.colors['ac1'], '{:.2f}%',    'GHz',   self.cpu_hw_max),
            ('memory', 'Memory', self.mem_icon,  self.colors['ac2'], '{:.2f} MB', 'GB',    self.mem_hw_max),
            ('disk',   'Disk',   self.disk_icon, self.colors['ac3'], '{:.2f} MB/s','MB/s', self.disk_speed_max),
        ]:
            self.create_gauge(key, title, icon, color,
                              pct_max=100, hw_max=maxhw, hw_unit=unit,
                              tree_fmt=fmt)

        # footer (com âncoras válidas)
        footer = ttk.Frame(self.root, style='Dark.TFrame')
        footer.pack(fill='x', padx=2, pady=(4,0))
        ttk.Label(footer, text=self.cpu_name,  style='Header.TLabel', anchor='w').pack(side='left',   expand=True, fill='x')
        ttk.Label(footer, text=self.mem_name,   style='Header.TLabel', anchor='center').pack(side='left', expand=True, fill='x')
        ttk.Label(footer, text=self.disk_name,  style='Header.TLabel', anchor='e').pack(side='left',   expand=True, fill='x')

        # controles
        ctrl = ttk.Frame(self.root, style='Dark.TFrame')
        ctrl.pack(fill='x', padx=2, pady=2)
        self.btn_pause    = ttk.Button(ctrl, text='⏸ Pause',   command=self.stop_monitoring)
        self.btn_resume   = ttk.Button(ctrl, text='▶ Resume',  command=self.restart_monitoring, state=tk.DISABLED)
        self.btn_minimize = ttk.Button(ctrl, text='🗕 Minimize',command=self.root.iconify)
        self.btn_exit     = ttk.Button(ctrl, text='⏏ Exit',    command=self.root.quit)
        for btn in (self.btn_pause, self.btn_resume, self.btn_minimize):
            btn.pack(side='left', padx=2)
        self.btn_exit.pack(side='right', padx=2)

    def create_gauge(self, key, title, icon, color, pct_max, hw_max, hw_unit, tree_fmt):
        frm = ttk.Frame(self.main_frame, style='Dark.TFrame')
        frm.pack(side='left', padx=2, pady=2, fill='both', expand=True)
        setattr(self, f'{key}_frame', frm)
        setattr(self, f'{key}_fmt',      tree_fmt)
        setattr(self, f'{key}_unit',     hw_unit)
        setattr(self, f'{key}_pct_max',  pct_max)
        setattr(self, f'{key}_angle',    135.0)  # ângulo inicial

        # header
        hdr = ttk.Frame(frm, style='Dark.TFrame'); hdr.pack(fill='x', pady=(0,4))
        ttk.Label(hdr, image=icon, style='Header.TLabel').pack(side='left', padx=2)
        ttk.Label(hdr, text=title,   style='Header.TLabel').pack(side='left', padx=4)

        # canvas
        size   = self.gauge_center*2
        canvas = tk.Canvas(frm, width=size, height=size,
                           bg=self.colors['bg'], highlightthickness=0)
        canvas.pack()
        setattr(self, f'{key}_canvas', canvas)
        self._draw_background(canvas, pct_max, hw_max)
        needle = canvas.create_line(
            self.gauge_center, self.gauge_center,
            self.gauge_center, self.gauge_center - self.needle_len,
            fill=color, width=4, tags='needle'
        )
        setattr(self, f'{key}_needle', needle)

        # textos
        y_val = self.gauge_center + int(self.gauge_radius*0.5)
        canvas.create_text(self.gauge_center, y_val,
                           text='0.0%', tags='value_text',
                           fill=self.colors['fg'], font=('Segoe UI',12,'bold'))
        y_hw = self.gauge_center + int(self.gauge_radius*0.7)
        canvas.create_text(self.gauge_center, y_hw,
                           text=hw_unit, tags='hw_text',
                           fill=self.colors['fg'], font=('Segoe UI',8))

        # tabela top5
        tree = ttk.Treeview(frm, columns=('Proc','Val'), show='headings', height=5)
        tree.heading('Proc', text='Process', anchor='w')
        tree.heading('Val',  text='Value',   anchor='e')
        tree.column('Proc', width=120, anchor='w')
        tree.column('Val',  width=60,  anchor='e')
        tree.pack(fill='x', padx=2, pady=(4,2))
        setattr(self, f'{key}_tree', tree)

    def _draw_background(self, canvas, pct_max, hw_max):
        sa, ea = 135, 405; c, r = self.gauge_center, self.gauge_radius
        canvas.create_arc(c-r,c-r,c+r,c+r, start=sa, extent=ea-sa,
                          outline=self.colors['fg'], width=2, style=tk.ARC)
        for i,v in enumerate((0,25,50,75,100)):
            ang = math.radians(sa + i*270/4)
            x1,y1 = c+(r-10)*math.cos(ang), c+(r-10)*math.sin(ang)
            x2,y2 = c+(r-30)*math.cos(ang), c+(r-30)*math.sin(ang)
            canvas.create_line(x1,y1,x2,y2, fill=self.colors['fg'], width=2)
            lx,ly = c+(r-45)*math.cos(ang), c+(r-45)*math.sin(ang)
            canvas.create_text(lx, ly, text=str(v),
                               fill=self.colors['fg'], font=('Segoe UI',7,'bold'))
        for i in range(5):
            ang = math.radians(sa + i*270/4)
            val = (i/4)*hw_max
            lx,ly = c+(r+20)*math.cos(ang), c+(r+20)*math.sin(ang)
            canvas.create_text(lx, ly, text=f'{val:.1f}',
                               fill=self.colors['fg'], font=('Segoe UI',7))
        canvas.create_oval(c-self.inner_radius, c-self.inner_radius,
                           c+self.inner_radius, c+self.inner_radius,
                           fill=self.colors['ac3'])

    def setup_monitoring(self):
        self.process_cache   = {}
        self.disk_counters   = {}
        self.monitoring      = True
        self.update_interval = 2000  # 2 s
        self.update_gadgets()

    def update_gadgets(self):
        if not self.monitoring: return
        procs = self.get_process_data()

        # recria gauge disco quando speed disponível
        if self.disk_speed_max>0 and not self.disk_updated:
            self.disk_updated = True
            self.disk_frame.destroy()
            self.create_gauge('disk', 'Disk', self.disk_icon, self.colors['ac3'],
                              pct_max=100, hw_max=self.disk_speed_max,
                              hw_unit='MB/s', tree_fmt='{:.2f} MB/s')

        # CPU
        pct  = psutil.cpu_percent()
        freq = psutil.cpu_freq().current/1000.0 if psutil.cpu_freq() else 0.0
        self._update_gauge('cpu', pct, freq, procs, 'cpu')

        # Memória
        vm   = psutil.virtual_memory()
        pct  = vm.percent
        used = vm.used / (1024**3)
        self._update_gauge('memory', pct, used, procs, 'mem')

        # Disco
        speeds    = [self.get_disk_speed(p['pid'], p['read'], p['write']) for p in procs]
        total_spd = sum(speeds)
        pct_spd   = (total_spd/self.disk_speed_max*100) if self.disk_speed_max>0 else 0
        disk_data = [{**p,'speed':sp} for p,sp in zip(procs,speeds)]
        self._update_gauge('disk', pct_spd, total_spd, disk_data, 'speed')

        self.root.after(self.update_interval, self.update_gadgets)

    def _update_gauge(self, key, pct, hw_val, data, data_key):
        fmt     = getattr(self, f'{key}_fmt')
        unit    = getattr(self, f'{key}_unit')
        pct_max = getattr(self, f'{key}_pct_max')
        canvas  = getattr(self, f'{key}_canvas')
        needle  = getattr(self, f'{key}_needle')
        tree    = getattr(self, f'{key}_tree')

        # clamp e alvo
        pct_c = max(0.0, min(pct, pct_max))
        target_ang = 135 + (pct_c/100)*270

        # anima
        self.animate_needle(key, needle, canvas, target_ang)

        # textos
        canvas.itemconfigure('value_text', text=f'{pct_c:.1f}%')
        canvas.itemconfigure('hw_text',    text=f'{hw_val:.2f} {unit}')

        # tabela
        tree.delete(*tree.get_children())
        for it in sorted(data, key=lambda x: x[data_key], reverse=True)[:5]:
            tree.insert('', 'end',
                        values=(it['name'][:20], fmt.format(it[data_key])))

    def animate_needle(self, key, needle, canvas, target_ang):
        current_ang = getattr(self, f'{key}_angle')
        steps = 10; da = (target_ang-current_ang)/steps; delay = 20
        def step(i):
            ang = current_ang + da*i
            rad = math.radians(ang)
            x = self.gauge_center + self.needle_len*math.cos(rad)
            y = self.gauge_center + self.needle_len*math.sin(rad)
            canvas.coords(needle, self.gauge_center, self.gauge_center, x, y)
            if i<steps:
                canvas.after(delay, lambda: step(i+1))
            else:
                setattr(self, f'{key}_angle', target_ang)
        step(1)

    def get_process_data(self):
        lst, now = [], time.time()
        for proc in psutil.process_iter(['pid','name','cpu_percent','io_counters']):
            try:
                name = proc.info['name']
                if name in ['System Idle Process','System']: continue
                prev = self.process_cache.get(proc.info['pid'])
                if prev and now - prev['time'] < 1.5:
                    lst.append(prev); continue

                cpu_u = min(proc.info['cpu_percent']/self.cpu_cores,100.0) if proc.info['cpu_percent'] else 0.0
                if cpu_u < 1.0: continue

                mem_mb = proc.memory_info().rss/(1024**2) if proc.memory_info() else 0
                rd = proc.info['io_counters'].read_bytes  if proc.info['io_counters'] else 0
                wr = proc.info['io_counters'].write_bytes if proc.info['io_counters'] else 0

                d = {'pid':proc.info['pid'],'name':name,'cpu':cpu_u,
                     'mem':mem_mb,'read':rd,'write':wr,'time':now}
                self.process_cache[proc.info['pid']] = d
                lst.append(d)
            except:
                continue
        return lst

    def get_disk_speed(self, pid, r, w):
        rec = self.disk_counters.get(pid); t = time.time()
        if not rec:
            self.disk_counters[pid] = (r, w, t); return 0.0
        lr, lw, lt = rec; dt = t - lt
        if dt<0.2: return 0.0
        spd = ((r-lr)+(w-lw))/(dt*(1024**2))
        self.disk_counters[pid] = (r, w, t)
        return round(spd,2)

    def _disk_speed_test(self):
        path = os.path.join(tempfile.gettempdir(), 'disk_test.tmp')
        total = 0; start = time.time()
        with open(path,'wb') as f:
            while time.time()-start<10:
                chunk=b'\0'*(1024**2); f.write(chunk)
                f.flush(); os.fsync(f.fileno()); total+=len(chunk)
        os.remove(path)
        self.disk_speed_max = total/(1024**2)/10

    def stop_monitoring(self):
        if self.monitoring:
            self.monitoring=False
            self.btn_pause.config(state=tk.DISABLED)
            self.btn_resume.config(state=tk.NORMAL)

    def restart_monitoring(self):
        if not self.monitoring:
            self.monitoring=True
            self.btn_pause.config(state=tk.NORMAL)
            self.btn_resume.config(state=tk.DISABLED)
            self.update_gadgets()

if __name__ == '__main__':
    root = tk.Tk()
    app  = SystemMonitor(root)
    root.mainloop()
