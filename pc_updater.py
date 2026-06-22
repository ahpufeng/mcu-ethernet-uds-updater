"""
PC上位机 - 以太网固件更新工具
负责与主MCU通信，由主MCU通过CAN/UDS向从MCU下发固件
"""

import socket
import struct
import time
import threading
from queue import Queue
from pathlib import Path
from protocol import (
    Frame, FrameType, FrameHeader, FileInfoPayload, 
    DataBlockPayload, ControllerInfoPayload, StatusPayload
)
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PCFirmwareUpdater:
    """PC端固件更新器"""
    
    def __init__(self, host: str, port: int = 8888, timeout: float = 10.0):
        self.host = host
        self.port = port
        self.socket = None
        self.timeout = timeout
        self.status_queue = Queue()
        self.sequence_number = 0
        self.running = False
        self.recv_thread = None
        
    def _log(self, msg: str, level: str = "INFO"):
        """日志输出"""
        self.status_queue.put((level, msg))
        getattr(logger, level.lower())(msg)
    
    def connect(self) -> bool:
        """连接主MCU"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(self.timeout)
            self.socket.connect((self.host, self.port))
            self.running = True
            
            # 启动接收线程
            self.recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
            self.recv_thread.start()
            
            self._log(f"✓ 已连接到主MCU: {self.host}:{self.port}")
            
            # 握手
            if not self._handshake():
                return False
            
            return True
        except Exception as e:
            self._log(f"✗ 连接失败: {e}", "ERROR")
            return False
    
    def disconnect(self):
        """断开连接"""
        self.running = False
        if self.socket:
            self.socket.close()
        self._log("已断开连接")
    
    def _handshake(self) -> bool:
        """握手协议"""
        try:
            self.sequence_number = 1
            frame = Frame(FrameType.HANDSHAKE, 0, self.sequence_number, b'PC-Updater/1.0')
            self.socket.sendall(frame.to_bytes())
            
            # 等待握手响应 (带超时)
            response = self._recv_frame_with_timeout(timeout=2)
            if response and response.header.frame_type == FrameType.STATUS:
                self._log("✓ 握手成功")
                return True
            else:
                self._log("✗ 握手失败", "ERROR")
                return False
        except Exception as e:
            self._log(f"✗ 握手异常: {e}", "ERROR")
            return False
    
    def _recv_loop(self):
        """接收线程"""
        buffer = b''
        while self.running:
            try:
                data = self.socket.recv(4096)
                if not data:
                    self._log("连接被关闭", "WARNING")
                    break
                buffer += data
                
                # 尝试解析完整帧
                while len(buffer) >= 16:  # 最小帧长度
                    try:
                        # 查找魔数
                        magic_pos = buffer.find(b'FWUP')
                        if magic_pos == -1:
                            buffer = b''
                            break
                        
                        if magic_pos > 0:
                            buffer = buffer[magic_pos:]
                        
                        if len(buffer) < 12:
                            break
                        
                        header = FrameHeader.from_bytes(buffer[:12])
                        frame_len = 12 + header.payload_len + 4
                        
                        if len(buffer) < frame_len:
                            break
                        
                        frame = Frame.from_bytes(buffer[:frame_len])
                        buffer = buffer[frame_len:]
                        
                        # 处理接收的帧
                        self._handle_response(frame)
                    except Exception as e:
                        self._log(f"帧解析错误: {e}", "WARNING")
                        buffer = b''
                        break
            except socket.timeout:
                pass
            except Exception as e:
                if self.running:
                    self._log(f"接收异常: {e}", "ERROR")
                break
        
        self.running = False
    
    def _recv_frame_with_timeout(self, timeout: float = 5) -> Frame:
        """带超时的阻塞接收单个帧"""
        start_time = time.time()
        buffer = b''
        
        while time.time() - start_time < timeout:
            try:
                data = self.socket.recv(4096)
                if data:
                    buffer += data
                    
                    try:
                        magic_pos = buffer.find(b'FWUP')
                        if magic_pos >= 0:
                            buffer = buffer[magic_pos:]
                            if len(buffer) >= 12:
                                header = FrameHeader.from_bytes(buffer[:12])
                                frame_len = 12 + header.payload_len + 4
                                if len(buffer) >= frame_len:
                                    frame = Frame.from_bytes(buffer[:frame_len])
                                    return frame
                    except:
                        pass
            except socket.timeout:
                continue
        
        return None
    
    def _handle_response(self, frame: Frame):
        """处理响应帧"""
        if frame.header.frame_type == FrameType.STATUS:
            payload = StatusPayload.from_bytes(frame.payload)
            self._log(f"状态更新 - 控制器:{payload.ctrl_id} "
                     f"进度:{payload.progress}% 错误:{payload.error_code}")
    
    def query_controller(self, ctrl_id: int) -> bool:
        """查询控制器是否在线"""
        try:
            self.sequence_number += 1
            payload = ControllerInfoPayload(ctrl_id, 0).to_bytes()
            frame = Frame(FrameType.CTRL_INFO, ctrl_id, self.sequence_number, payload)
            self.socket.sendall(frame.to_bytes())
            
            self._log(f"查询控制器 {ctrl_id}...")
            response = self._recv_frame_with_timeout(timeout=2)
            
            if response and response.header.frame_type == FrameType.STATUS:
                payload = StatusPayload.from_bytes(response.payload)
                if payload.error_code == 0:
                    self._log(f"✓ 控制器 {ctrl_id} 在线")
                    return True
            
            self._log(f"✗ 控制器 {ctrl_id} 未响应", "WARNING")
            return False
        except Exception as e:
            self._log(f"查询失败: {e}", "ERROR")
            return False
    
    def send_file_to_controller(self, ctrl_id: int, hex_file: str, 
                               start_address: int = 0x08000000):
        """发送文件到指定控制器"""
        try:
            file_path = Path(hex_file)
            if not file_path.exists():
                self._log(f"✗ 文件不存在: {hex_file}", "ERROR")
                return False
            
            # 读取文件
            with open(hex_file, 'rb') as f:
                file_data = f.read()
            
            self._log(f"开始升级控制器 {ctrl_id}: {file_path.name} ({len(file_data)} 字节)")
            
            # 1. 发送文件信息
            if not self._send_file_info(ctrl_id, hex_file, start_address, len(file_data)):
                return False
            
            time.sleep(0.5)
            
            # 2. 分块发送数据
            BLOCK_SIZE = 256
            total_blocks = (len(file_data) + BLOCK_SIZE - 1) // BLOCK_SIZE
            
            for block_idx in range(total_blocks):
                offset = block_idx * BLOCK_SIZE
                chunk = file_data[offset:offset + BLOCK_SIZE]
                
                if not self._send_data_block(ctrl_id, block_idx, chunk):
                    self._log(f"✗ 发送数据块 {block_idx} 失败", "ERROR")
                    return False
                
                progress = int((block_idx + 1) / total_blocks * 100)
                if (block_idx + 1) % 10 == 0:
                    self._log(f"进度: {progress}% ({block_idx + 1}/{total_blocks})")
            
            time.sleep(0.5)
            
            # 3. 通知升级完成
            if not self._send_upgrade_complete(ctrl_id):
                return False
            
            self._log(f"✓ 控制器 {ctrl_id} 升级完成！")
            return True
            
        except Exception as e:
            self._log(f"✗ 升级失败: {e}", "ERROR")
            return False
    
    def _send_file_info(self, ctrl_id: int, file_name: str, 
                       start_address: int, file_size: int) -> bool:
        """发送文件信息"""
        try:
            self.sequence_number += 1
            file_type = 1 if file_name.endswith('.bin') else 0  # 0=HEX, 1=BIN
            
            payload = FileInfoPayload(
                file_type=file_type,
                target_addr_type=0,  # Flash
                start_address=start_address,
                file_size=file_size,
                file_name=Path(file_name).name
            ).to_bytes()
            
            frame = Frame(FrameType.FILE_INFO, ctrl_id, self.sequence_number, payload)
            self.socket.sendall(frame.to_bytes())
            
            self._log(f"发送文件信息: {Path(file_name).name} ({file_size} 字节)")
            
            # 等待确认
            response = self._recv_frame_with_timeout(timeout=2)
            return response is not None
            
        except Exception as e:
            self._log(f"发送文件信息失败: {e}", "ERROR")
            return False
    
    def _send_data_block(self, ctrl_id: int, block_idx: int, data: bytes) -> bool:
        """发送数据块"""
        try:
            self.sequence_number += 1
            payload = DataBlockPayload(block_idx, len(data), data).to_bytes()
            frame = Frame(FrameType.DATA_BLOCK, ctrl_id, self.sequence_number, payload)
            self.socket.sendall(frame.to_bytes())
            
            # 等待ACK
            response = self._recv_frame_with_timeout(timeout=2)
            return response is not None
            
        except Exception as e:
            self._log(f"发送数据块失败: {e}", "ERROR")
            return False
    
    def _send_upgrade_complete(self, ctrl_id: int) -> bool:
        """发送升级完成信号"""
        try:
            self.sequence_number += 1
            payload = ControllerInfoPayload(ctrl_id, 3).to_bytes()  # 3=完成升级
            frame = Frame(FrameType.CTRL_INFO, ctrl_id, self.sequence_number, payload)
            self.socket.sendall(frame.to_bytes())
            
            response = self._recv_frame_with_timeout(timeout=3)
            return response is not None
            
        except Exception as e:
            self._log(f"发送完成信号失败: {e}", "ERROR")
            return False
