"""
PyCharm GUI - 以太网CAN/UDS固件更新工具
"""

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
import threading
from pathlib import Path
from pc_updater import PCFirmwareUpdater

class UpdaterGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("以太网→CAN/UDS 固件更新工具 v2.0")
        self.root.geometry("900x700")
        
        self.updater = None
        self.selected_files = {}  # {ctrl_id: file_path}
        
        # ==================== 连接设置 ====================
        frame_conn = tk.LabelFrame(root, text="连接设置", font=("Arial", 10, "bold"))
        frame_conn.pack(fill=tk.X, padx=10, pady=10)
        
        tk.Label(frame_conn, text="主MCU IP:").grid(row=0, column=0, padx=5, pady=5)
        self.entry_ip = tk.Entry(frame_conn, width=20)
        self.entry_ip.insert(0, "192.168.1.100")
        self.entry_ip.grid(row=0, column=1, padx=5, pady=5)
        
        tk.Label(frame_conn, text="端口:").grid(row=0, column=2, padx=5, pady=5)
        self.entry_port = tk.Entry(frame_conn, width=10)
        self.entry_port.insert(0, "8888")
        self.entry_port.grid(row=0, column=3, padx=5, pady=5)
        
        tk.Label(frame_conn, text="超时(s):").grid(row=0, column=4, padx=5, pady=5)
        self.entry_timeout = tk.Entry(frame_conn, width=8)
        self.entry_timeout.insert(0, "10")
        self.entry_timeout.grid(row=0, column=5, padx=5, pady=5)
        
        self.btn_connect = tk.Button(frame_conn, text="连接", command=self.connect_mcu, 
                                     width=12, bg="lightgreen")
        self.btn_connect.grid(row=0, column=6, padx=5, pady=5)
        
        self.btn_disconnect = tk.Button(frame_conn, text="断开", command=self.disconnect_mcu, 
                                        width=12, state=tk.DISABLED, bg="lightcoral")
        self.btn_disconnect.grid(row=0, column=7, padx=5, pady=5)
        
        self.label_status = tk.Label(frame_conn, text="未连接", fg="red", font=("Arial", 9))
        self.label_status.grid(row=0, column=8, padx=10, pady=5)
        
        # ==================== 控制器管理 ====================
        frame_ctrl = tk.LabelFrame(root, text="控制器管理 (CAN/UDS)", font=("Arial", 10, "bold"))
        frame_ctrl.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # 树形表格
        self.tree = ttk.Treeview(frame_ctrl, columns=("ctrl_id", "file", "status", "action"), 
                                 height=10)
        self.tree.column("#0", width=0, stretch=tk.NO)
        self.tree.column("ctrl_id", anchor=tk.CENTER, width=80, heading="控制器ID")
        self.tree.column("file", anchor=tk.W, width=300, heading="固件文件")
        self.tree.column("status", anchor=tk.CENTER, width=100, heading="状态")
        self.tree.column("action", anchor=tk.CENTER, width=150, heading="操作")
        
        self.tree.heading("#0", text="", anchor=tk.W)
        self.tree.heading("ctrl_id", text="控制器ID", anchor=tk.CENTER)
        self.tree.heading("file", text="固件文件", anchor=tk.W)
        self.tree.heading("status", text="状态", anchor=tk.CENTER)
        self.tree.heading("action", text="操作", anchor=tk.CENTER)
        
        self.tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.tree.bind('<Double-1>', self.on_tree_double_click)
        
        # 添加控制器按钮
        frame_add = tk.Frame(frame_ctrl)
        frame_add.pack(fill=tk.X, padx=5, pady=5)
        
        tk.Label(frame_add, text="新增控制器 ID (0-255):").pack(side=tk.LEFT, padx=5)
        self.entry_new_ctrl = tk.Entry(frame_add, width=10)
        self.entry_new_ctrl.pack(side=tk.LEFT, padx=5)
        
        tk.Button(frame_add, text="添加", command=self.add_controller, width=10).pack(side=tk.LEFT, padx=5)
        tk.Button(frame_add, text="删除选中", command=self.delete_controller, width=10).pack(side=tk.LEFT, padx=5)
        tk.Button(frame_add, text="查询在线状态", command=self.query_all_controllers, width=15).pack(side=tk.LEFT, padx=5)
        
        # ==================== 操作按钮 ====================
        frame_action = tk.Frame(root)
        frame_action.pack(fill=tk.X, padx=10, pady=10)
        
        self.btn_start = tk.Button(frame_action, text="▶ 开始升级所有", command=self.start_all_updates,
                                   width=20, bg="dodgerblue", fg="white", font=("Arial", 11, "bold"),
                                   state=tk.DISABLED)
        self.btn_start.pack(side=tk.LEFT, padx=5)
        
        self.btn_stop = tk.Button(frame_action, text="⏹ 停止", command=self.stop_updates,
                                  width=15, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=5)
        
        # ==================== 日志输出 ====================
        tk.Label(root, text="日志输出:", font=("Arial", 10, "bold")).pack(anchor=tk.W, padx=10)
        
        self.log_text = scrolledtext.ScrolledText(root, height=8, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        
        # 配置日志颜色
        self.log_text.tag_config("INFO", foreground="black")
        self.log_text.tag_config("WARNING", foreground="orange")
        self.log_text.tag_config("ERROR", foreground="red")
        self.log_text.tag_config("SUCCESS", foreground="green")
        
        self.update_logs()
    
    def on_tree_double_click(self, event):
        """双击树项选择文件"""
        item = self.tree.selection()[0]
        ctrl_id = int(self.tree.item(item)["values"][0])
        self.select_file_for_controller(ctrl_id, item)
    
    def select_file_for_controller(self, ctrl_id, item):
        """为控制器选择文件"""
        file_path = filedialog.askopenfilename(
            filetypes=[("Hex文件", "*.hex"), ("BIN文件", "*.bin"), ("所有文件", "*.*")],
            title=f"为控制器 {ctrl_id} 选择固件文件"
        )
        if file_path:
            self.selected_files[ctrl_id] = file_path
            file_name = Path(file_path).name
            values = self.tree.item(item)["values"]
            self.tree.item(item, values=(values[0], file_name, values[2], values[3]))
    
    def connect_mcu(self):
        """连接主MCU"""
        try:
            ip = self.entry_ip.get()
            port = int(self.entry_port.get())
            timeout = float(self.entry_timeout.get())
            
            self.updater = PCFirmwareUpdater(ip, port, timeout)
            if self.updater.connect():
                self.btn_connect.config(state=tk.DISABLED)
                self.btn_disconnect.config(state=tk.NORMAL)
                self.btn_start.config(state=tk.NORMAL)
                self.entry_ip.config(state=tk.DISABLED)
                self.entry_port.config(state=tk.DISABLED)
                self.label_status.config(text="已连接", fg="green")
            else:
                messagebox.showerror("错误", "连接失败，请检查IP和端口")
        except ValueError:
            messagebox.showerror("错误", "端口和超时必须是数字")
    
    def disconnect_mcu(self):
        """断开连接"""
        if self.updater:
            self.updater.disconnect()
        
        self.btn_connect.config(state=tk.NORMAL)
        self.btn_disconnect.config(state=tk.DISABLED)
        self.btn_start.config(state=tk.DISABLED)
        self.entry_ip.config(state=tk.NORMAL)
        self.entry_port.config(state=tk.NORMAL)
        self.label_status.config(text="未连接", fg="red")
    
    def add_controller(self):
        """添加控制器"""
        try:
            ctrl_id = int(self.entry_new_ctrl.get())
            if 0 <= ctrl_id <= 255:
                self.tree.insert("", "end", values=(ctrl_id, "未选择", "待命", "双击选择文件"))
                self.entry_new_ctrl.delete(0, tk.END)
            else:
                messagebox.showerror("错误", "控制器ID必须在0-255之间")
        except ValueError:
            messagebox.showerror("错误", "请输入有效的数字")
    
    def delete_controller(self):
        """删除选中的控制器"""
        selected = self.tree.selection()
        for item in selected:
            ctrl_id = int(self.tree.item(item)["values"][0])
            if ctrl_id in self.selected_files:
                del self.selected_files[ctrl_id]
            self.tree.delete(item)
    
    def query_all_controllers(self):
        """查询所有在线控制器"""
        if not self.updater:
            messagebox.showwarning("警告", "请先连接主MCU")
            return
        
        thread = threading.Thread(target=self._query_thread)
        thread.daemon = True
        thread.start()
    
    def _query_thread(self):
        """查询线程"""
        for item in self.tree.get_children():
            ctrl_id = int(self.tree.item(item)["values"][0])
            if self.updater.query_controller(ctrl_id):
                values = self.tree.item(item)["values"]
                self.tree.item(item, values=(values[0], values[1], "在线", values[3]))
            else:
                values = self.tree.item(item)["values"]
                self.tree.item(item, values=(values[0], values[1], "离线", values[3]))
    
    def start_all_updates(self):
        """开始升级所有控制器"""
        if not self.updater:
            messagebox.showwarning("警告", "请先连接主MCU")
            return
        
        thread = threading.Thread(target=self._update_all_thread)
        thread.daemon = True
        thread.start()
        
        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
    
    def _update_all_thread(self):
        """升级线程"""
        for item in self.tree.get_children():
            values = self.tree.item(item)["values"]
            ctrl_id = int(values[0])
            file_path = values[1]
            
            if file_path == "未选择" or ctrl_id not in self.selected_files:
                continue
            
            self.updater.send_file_to_controller(ctrl_id, self.selected_files[ctrl_id])
        
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
    
    def stop_updates(self):
        """停止升级"""
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
    
    def update_logs(self):
        """更新日志显示"""
        if self.updater:
            while not self.updater.status_queue.empty():
                level, msg = self.updater.status_queue.get()
                self.log_text.config(state=tk.NORMAL)
                self.log_text.insert(tk.END, f"[{level}] {msg}\n", level)
                self.log_text.see(tk.END)
                self.log_text.config(state=tk.DISABLED)
        
        self.root.after(200, self.update_logs)

if __name__ == "__main__":
    root = tk.Tk()
    gui = UpdaterGUI(root)
    root.mainloop()
