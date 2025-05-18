import tkinter as tk
from tkinter import ttk
import psutil
import wmi
import threading
import time
import math
import os
import tempfile
import subprocess
import ipaddress
from PIL import Image, ImageTk, ImageDraw

class ToolTip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tipwin = None
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)

    def show(self, event):
        if self.tipwin or not self.text: return
        x = event.x_root + 10; y = event.y_root + 10
        self.tipwin = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        lbl = tk.Label(tw, text=self.text, justify='left',
                       background="#ffffe0", relief='solid', borderwidth=1,
                       font=("tahoma", "8", "normal"))
        lbl.pack(ipadx=1)

    def hide(self, event):
        if self.tipwin:
            self.tipwin.destroy()
            self.tipwin = None

class SystemMonitor:
    def __init__(self, root):
        self.root = root
        self.root.title("System Monitor Pro")
        self.root.geometry("900x460")
        self.root.configure(bg="#2D2D2D")

        c = wmi.WMI()
        cpu = c.Win32_Processor()[0]
        self.cpu_name = cpu.Name.strip()
        self.cpu_hw_max = cpu.MaxClockSpeed / 1000.0
        vm = psutil.virtual_memory()
        self.mem_hw_max = vm.total / (1024 ** 3)
        self.mem_name = f"{self.mem_hw_max:.2f} GB total"
        disk = c.Win32_DiskDrive()[0]
        self.disk_name = disk.Model.strip()
        self.disk_speed_max = 0.0
        self.cpu_cores = psutil.cpu_count(logical=True)

        self.colors = {
            'bg': '#2D2D2D', 'fg': '#ECECEC',
            'ac1': '#3498DB', 'ac2': '#2ECC71', 'ac3': '#E74C3C',
            'hdr': '#34495E'
        }
        base_r = 75
        self.gauge_center = 100
        self.gauge_radius = int(base_r * 1.05)
        self.needle_len = int(self.gauge_radius * 0.9)
        self.inner_radius = int(10 * 0.95)

        self.ping_target = "9.9.9.9"
        self.local_gw, self.provider_gw = self.discover_gateways(self.ping_target)

        self.load_icons()
        self.configure_styles()
        self.create_widgets()

        threading.Thread(target=self._disk_speed_test, daemon=True).start()

        self.process_cache = {}
        self.disk_counters = {}
        self.monitoring = True
        self.update_interval = 2000

        self.update_loop()

    def discover_gateways(self, target):
        out = subprocess.check_output(
            ["tracert", "-d", "-w", "100", "-h", "30", target],
            stderr=subprocess.DEVNULL, encoding='latin-1'
        )
        hops = []
        for line in out.splitlines():
            for tok in line.split():
                try:
                    addr = ipaddress.IPv4Address(tok)
                    hops.append(str(addr))
                except:
                    pass

        ip_pub_index = next((i for i, ip in enumerate(hops)
                             if not ipaddress.ip_address(ip).is_private
                             and ip != target), None)

        if ip_pub_index is not None:
            priv_before = [ip for ip in hops[:ip_pub_index]
                           if ipaddress.ip_address(ip).is_private]
            local = priv_before[-1] if priv_before else hops[0]
        else:
            priv = [ip for ip in hops if ipaddress.ip_address(ip).is_private]
            local = priv[-1] if priv else target

        pub = [ip for ip in hops
               if not ipaddress.ip_address(ip).is_private and ip != target]
        prov = next((ip for ip in pub if ip.endswith(".1")), None)
        prov = prov or (pub[0] if pub else target)

        return local, prov

    def load_icons(self):
        def circle(color, size=24):
            img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            d.ellipse((2, 2, size - 2, size - 2), fill=color)
            return ImageTk.PhotoImage(img)
        self.cpu_icon = circle(self.colors['ac1'])
        self.mem_icon = circle(self.colors['ac2'])
        self.disk_icon = circle(self.colors['ac3'])
        self.green_dot = circle(self.colors['ac2'], size=16)
        self.red_dot = circle(self.colors['ac3'], size=16)

    def configure_styles(self):
        s = ttk.Style()
        s.theme_use('clam')
        s.configure('Dark.TFrame', background=self.colors['bg'])
        s.configure('Header.TLabel',
                    background=self.colors['hdr'],
                    foreground=self.colors['fg'],
                    font=('Segoe UI', 10, 'bold'),
                    padding=2)
        s.configure('Treeview',
                    background=self.colors['bg'],
                    foreground=self.colors['fg'],
                    fieldbackground=self.colors['bg'],
                    borderwidth=0,
                    rowheight=16,
                    font=('Segoe UI', 8))
        s.configure('Treeview.Heading',
                    background=self.colors['hdr'],
                    foreground=self.colors['fg'],
                    font=('Segoe UI', 8, 'bold'),
                    relief='flat')

    def create_widgets(self):
        self.main_frame = ttk.Frame(self.root, style='Dark.TFrame')
        self.main_frame.pack(fill='both', expand=True, padx=2, pady=(2, 0))

        specs = [
            ('cpu', 'CPU', self.cpu_icon, self.colors['ac1'], '{:.2f}%', 'GHz', self.cpu_hw_max),
            ('memory', 'Memória', self.mem_icon, self.colors['ac2'], '{:.2f} GB', 'GB', self.mem_hw_max),
            ('disk', 'Disco', self.disk_icon, self.colors['ac3'], '{:.2f} MB/s', 'MB/s', 0.0),
        ]
        for key, title, icon, col, fmt, unit, maxhw in specs:
            self.create_gauge(key, title, icon, col, 100, maxhw, unit, fmt)

        self.ping_frame = ttk.Frame(self.root, style='Dark.TFrame')
        self.ping_frame.pack(fill='x', padx=8, pady=(4, 0))
        ttk.Label(self.ping_frame, text='PING', style='Header.TLabel').pack(side='left', padx=(0, 6))
        self.ping_ips = [self.local_gw, self.provider_gw, self.ping_target]
        self.ping_lbls = []
        for ip in self.ping_ips:
            lbl = ttk.Label(self.ping_frame, image=self.red_dot)
            lbl.pack(side='left', padx=4)
            ToolTip(lbl, ip)
            self.ping_lbls.append(lbl)

        footer = ttk.Frame(self.root, style='Dark.TFrame')
        footer.pack(fill='x', padx=2, pady=(2, 0))
        ttk.Label(footer, text=self.cpu_name, style='Header.TLabel', anchor='w').pack(side='left', expand=True, fill='x')
        ttk.Label(footer, text=self.mem_name, style='Header.TLabel', anchor='center').pack(side='left', expand=True, fill='x')
        ttk.Label(footer, text=self.disk_name, style='Header.TLabel', anchor='e').pack(side='left', expand=True, fill='x')

        ctrl = ttk.Frame(self.root, style='Dark.TFrame')
        ctrl.pack(fill='x', padx=2, pady=(0, 4))
        self.btn_pause = ttk.Button(ctrl, text='⏸ Pause', command=self.stop)
        self.btn_resume = ttk.Button(ctrl, text='▶ Resume', command=self.start, state=tk.DISABLED)
        self.btn_minimize = ttk.Button(ctrl, text='🗕 Minimize', command=self.root.iconify)
        self.btn_exit = ttk.Button(ctrl, text='⏏ Exit', command=self.root.quit)
        for b in (self.btn_pause, self.btn_resume, self.btn_minimize):
            b.pack(side='left', padx=2)
        self.btn_exit.pack(side='right', padx=2)

    def create_gauge(self, key, title, icon, color, pct_max, hw_max, hw_unit, tree_fmt):
        frm = ttk.Frame(self.main_frame, style='Dark.TFrame')
        frm.pack(side='left', padx=2, pady=2, fill='both', expand=True)
        setattr(self, f'{key}_fmt', tree_fmt)
        setattr(self, f'{key}_unit', hw_unit)
        setattr(self, f'{key}_pct_max', pct_max)
        setattr(self, f'{key}_angle', 135.0)

        hdr = ttk.Frame(frm, style='Dark.TFrame')
        hdr.pack(fill='x', pady=(0, 4))
        ttk.Label(hdr, image=icon, style='Header.TLabel').pack(side='left')
        ttk.Label(hdr, text=title, style='Header.TLabel').pack(side='left', padx=4)

        size = self.gauge_center * 2
        cvs = tk.Canvas(frm, width=size, height=size, bg=self.colors['bg'], highlightthickness=0)
        cvs.pack()
        setattr(self, f'{key}_canvas', cvs)
        self._draw_background(cvs, pct_max, hw_max)

        needle = cvs.create_line(
            self.gauge_center, self.gauge_center,
            self.gauge_center, self.gauge_center - self.needle_len,
            fill=color, width=4, tags='needle'
        )
        setattr(self, f'{key}_needle', needle)

        y1 = self.gauge_center + int(self.gauge_radius * 0.5)
        cvs.create_text(self.gauge_center, y1, text='0.0%', tags='value_text',
                        fill=self.colors['fg'], font=('Segoe UI', 12, 'bold'))
        y2 = self.gauge_center + int(self.gauge_radius * 0.7)
        cvs.create_text(self.gauge_center, y2, text=hw_unit, tags='hw_text',
                        fill=self.colors['fg'], font=('Segoe UI', 8))

        tree = ttk.Treeview(frm, columns=('P', 'V'), show='headings', height=5)
        tree.heading('P', text='Process', anchor='w')
        tree.heading('V', text='Value', anchor='e')
        tree.column('P', width=120, anchor='w')
        tree.column('V', width=60, anchor='e')
        tree.pack(fill='x', padx=2, pady=(4, 2))
        setattr(self, f'{key}_tree', tree)

    def _draw_background(self, cvs, pct_max, hw_max):
        sa, ea = 135, 405
        c, r = self.gauge_center, self.gauge_radius
        cvs.create_arc(c - r, c - r, c + r, c + r, start=sa, extent=ea - sa,
                       outline=self.colors['fg'], width=2, style=tk.ARC)
        for i, v in enumerate((0, 25, 50, 75, 100)):
            ang = math.radians(sa + i * 270 / 4)
            x1 = c + (r - 10) * math.cos(ang)
            y1 = c + (r - 10) * math.sin(ang)
            x2 = c + (r - 30) * math.cos(ang)
            y2 = c + (r - 30) * math.sin(ang)
            cvs.create_line(x1, y1, x2, y2, fill=self.colors['fg'], width=2)
            lx = c + (r - 45) * math.cos(ang)
            ly = c + (r - 45) * math.sin(ang)
            cvs.create_text(lx, ly, text=str(v),
                            fill=self.colors['fg'], font=('Segoe UI', 7, 'bold'))
        for i in range(5):
            ang = math.radians(sa + i * 270 / 4)
            val = (i / 4) * hw_max
            lx = c + (r + 20) * math.cos(ang)
            ly = c + (r + 20) * math.sin(ang)
            cvs.create_text(lx, ly, text=f'{val:.1f}',
                            fill=self.colors['fg'], font=('Segoe UI', 7))
        cvs.create_oval(c - self.inner_radius, c - self.inner_radius,
                        c + self.inner_radius, c + self.inner_radius,
                        fill=self.colors['ac3'])

    def _refresh_disk_gauge(self):
        cvs = self.disk_canvas
        needle = self.disk_needle
        cvs.delete("all")
        self._draw_background(cvs, pct_max=100, hw_max=self.disk_speed_max)
        cur = getattr(self, 'disk_angle', 135.0)
        rad = math.radians(cur)
        x = self.gauge_center + self.needle_len * math.cos(rad)
        y = self.gauge_center + self.needle_len * math.sin(rad)
        self.disk_needle = cvs.create_line(
            self.gauge_center, self.gauge_center, x, y,
            fill=self.colors['ac3'], width=4, tags='needle'
        )
        y1 = self.gauge_center + int(self.gauge_radius * 0.5)
        cvs.create_text(self.gauge_center, y1,
                        text='0.0%', tags='value_text',
                        fill=self.colors['fg'], font=('Segoe UI', 12, 'bold'))
        y2 = self.gauge_center + int(self.gauge_radius * 0.7)
        cvs.create_text(self.gauge_center, y2,
                        text='0.00 MB/s', tags='hw_text',
                        fill=self.colors['fg'], font=('Segoe UI', 8))

    def _disk_speed_test(self):
        path = os.path.join(tempfile.gettempdir(), 'disk_test.tmp')
        total = 0
        start = time.time()
        with open(path, 'wb') as f:
            while time.time() - start < 10:
                f.write(b'\0' * (1024 ** 2))
                f.flush()
                os.fsync(f.fileno())
                total += 1024 ** 2
        os.remove(path)
        self.disk_speed_max = total / (1024 ** 2) / 10
        self.root.after(0, self._refresh_disk_gauge)

    def format_bytes(self, value, per_second=False):
        units = ['B', 'KB', 'MB', 'GB', 'TB']
        if per_second:
            units = [u + '/s' for u in units]
        value = float(value)
        i = 0
        while value >= 1024 and i < len(units) - 1:
            value /= 1024
            i += 1
        return f"{value:.2f} {units[i]}"

    def update_loop(self):
        if not self.monitoring: return

        procs = self.get_process_data()

        pct = psutil.cpu_percent()
        freq = psutil.cpu_freq().current / 1000.0 if psutil.cpu_freq() else 0.0
        self._update_gauge('cpu', pct, freq, procs, 'cpu')

        vm = psutil.virtual_memory()
        self._update_gauge('memory', vm.percent, vm.used / (1024 ** 3), procs, 'mem')

        speeds = [self.get_disk_speed(p['pid'], p['read'], p['write']) for p in procs]  # in bytes/s
        total_spd_bytes = sum(speeds)
        total_spd_mb = total_spd_bytes / (1024 ** 2)  # in MB/s
        pct_spd = (total_spd_mb / self.disk_speed_max * 100) if self.disk_speed_max > 0 else 0
        data_d = [{**p, 'speed': s} for p, s in zip(procs, speeds)]  # speed in bytes/s
        self._update_gauge('disk', pct_spd, total_spd_mb, data_d, 'speed')

        for ip, lbl in zip(self.ping_ips, self.ping_lbls):
            res = subprocess.call(
                ['ping', '-n', '1', '-w', str(self.update_interval), ip],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            lbl.config(image=(self.green_dot if res == 0 else self.red_dot))

        self.root.after(self.update_interval, self.update_loop)

    def _update_gauge(self, key, pct, hw, data, dkey):
        fmt = getattr(self, f'{key}_fmt')
        unit = getattr(self, f'{key}_unit')
        pmax = getattr(self, f'{key}_pct_max')
        cvs = getattr(self, f'{key}_canvas')
        needle = getattr(self, f'{key}_needle')
        tree = getattr(self, f'{key}_tree')

        pct_c = max(0.0, min(pct, pmax))
        targ = 135 + (pct_c / 100) * 270
        self._animate_needle(key, needle, cvs, targ)

        cvs.itemconfigure('value_text', text=f'{pct_c:.1f}%')
        cvs.itemconfigure('hw_text', text=f'{hw:.2f} {unit}')

        tree.delete(*tree.get_children())
        if key == 'memory':
            total_mem = psutil.virtual_memory().total
            for it in sorted(data, key=lambda x: x['mem'], reverse=True)[:5]:
                mem_bytes = it['mem']
                mem_formatted = self.format_bytes(mem_bytes)
                mem_pct = (mem_bytes / total_mem) * 100 if total_mem > 0 else 0
                tree.insert('', 'end', values=(it['name'][:20], f"{mem_formatted} ({mem_pct:.1f}%)"))
        elif key == 'disk':
            for it in sorted(data, key=lambda x: x['speed'], reverse=True)[:5]:
                speed_formatted = self.format_bytes(it['speed'], per_second=True)
                tree.insert('', 'end', values=(it['name'][:20], speed_formatted))
        else:
            for it in sorted(data, key=lambda x: x[dkey], reverse=True)[:5]:
                tree.insert('', 'end', values=(it['name'][:20], fmt.format(it[dkey])))

    def _animate_needle(self, key, needle, cvs, targ):
        cur = getattr(self, f'{key}_angle')
        steps = 10
        da = (targ - cur) / steps
        def step(i):
            ang = cur + da * i
            rad = math.radians(ang)
            x = self.gauge_center + self.needle_len * math.cos(rad)
            y = self.gauge_center + self.needle_len * math.sin(rad)
            cvs.coords(needle, self.gauge_center, self.gauge_center, x, y)
            if i < steps:
                cvs.after(20, lambda: step(i + 1))
            else:
                setattr(self, f'{key}_angle', targ)
        step(1)

    def get_process_data(self):
        lst, now = [], time.time()
        for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'io_counters', 'memory_info']):
            try:
                nm = proc.info['name']
                if nm in ('System', 'System Idle Process'): continue
                prev = self.process_cache.get(proc.pid)
                if prev and now - prev['time'] < 1.5:
                    lst.append(prev); continue
                cpu_u = (proc.info['cpu_percent'] and
                         min(proc.info['cpu_percent'] / self.cpu_cores, 100)) or 0
                mem_info = proc.info['memory_info']
                mem = mem_info.rss if mem_info else 0  # in bytes
                io = proc.info['io_counters']
                rd = io.read_bytes if io else 0
                wr = io.write_bytes if io else 0
                d = {'pid': proc.pid, 'name': nm, 'cpu': cpu_u,
                     'mem': mem, 'read': rd, 'write': wr, 'time': now}
                self.process_cache[proc.pid] = d
                lst.append(d)
            except: pass
        return lst

    def get_disk_speed(self, pid, r, w):
        rec = self.disk_counters.get(pid)
        t = time.time()
        if not rec:
            self.disk_counters[pid] = (r, w, t)
            return 0
        lr, lw, lt = rec
        dt = t - lt
        if dt < 0.2:
            return 0
        spd = ((r - lr) + (w - lw)) / dt  # bytes per second
        self.disk_counters[pid] = (r, w, t)
        return spd

    def stop(self):
        self.monitoring = False
        self.btn_pause.config(state=tk.DISABLED)
        self.btn_resume.config(state=tk.NORMAL)

    def start(self):
        if not self.monitoring:
            self.monitoring = True
            self.btn_pause.config(state=tk.NORMAL)
            self.btn_resume.config(state=tk.DISABLED)
            self.update_loop()

if __name__ == '__main__':
    root = tk.Tk()
    app = SystemMonitor(root)
    root.mainloop()