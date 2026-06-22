"""
自定义以太网协议定义
PC -> 主MCU (Master MCU) -> 从MCU (Slave ECU via CAN/UDS)
"""

import struct
from enum import IntEnum
from dataclasses import dataclass
from typing import List
import zlib

class FrameType(IntEnum):
    """协议帧类型"""
    HANDSHAKE = 0x01        # 握手
    FILE_INFO = 0x02        # 文件信息
    DATA_BLOCK = 0x03       # 数据块
    CTRL_INFO = 0x04        # 控制器信息
    STATUS = 0x05           # 状态反馈
    ERROR = 0xFF            # 错误

class ErrorCode(IntEnum):
    """错误码"""
    SUCCESS = 0x00
    INVALID_FRAME = 0x01
    FILE_NOT_FOUND = 0x02
    INVALID_CHECKSUM = 0x03
    WRITE_FAILED = 0x04
    TIMEOUT = 0x05
    UNKNOWN_CTRL = 0x06

@dataclass
class FrameHeader:
    """协议帧头 (12字节)"""
    magic: bytes = b'FWUP'  # 魔数 4字节
    frame_type: int = 0     # 1字节
    ctrl_id: int = 0        # 1字节 (0-主MCU, 1-255为从MCU地址)
    sequence: int = 0       # 2字节 (序列号)
    payload_len: int = 0    # 4字节 (数据长度)
    
    def to_bytes(self) -> bytes:
        """转换为字节流"""
        return (
            self.magic +
            struct.pack('>BBHI', self.frame_type, self.ctrl_id, self.sequence, self.payload_len)
        )
    
    @classmethod
    def from_bytes(cls, data: bytes) -> 'FrameHeader':
        """从字节流解析"""
        if len(data) < 12 or data[:4] != b'FWUP':
            raise ValueError("Invalid frame header")
        frame_type, ctrl_id, sequence, payload_len = struct.unpack('>BBHI', data[4:12])
        return cls(b'FWUP', frame_type, ctrl_id, sequence, payload_len)

class Frame:
    """协议帧"""
    def __init__(self, frame_type: int, ctrl_id: int, sequence: int, payload: bytes = b''):
        self.header = FrameHeader(b'FWUP', frame_type, ctrl_id, sequence, len(payload))
        self.payload = payload
        self.checksum = self._calc_checksum()
    
    def _calc_checksum(self) -> bytes:
        """计算CRC-32校验"""
        data = self.header.to_bytes() + self.payload
        crc = zlib.crc32(data) & 0xffffffff
        return struct.pack('>I', crc)
    
    def to_bytes(self) -> bytes:
        """完整帧 = 帧头(12) + 载荷 + 校验(4)"""
        return self.header.to_bytes() + self.payload + self.checksum
    
    @classmethod
    def from_bytes(cls, data: bytes) -> 'Frame':
        """从字节流解析帧"""
        if len(data) < 16:
            raise ValueError("Frame too short")
        
        header = FrameHeader.from_bytes(data[:12])
        payload = data[12:12 + header.payload_len]
        checksum = data[12 + header.payload_len:12 + header.payload_len + 4]
        
        frame = cls(header.frame_type, header.ctrl_id, header.sequence, payload)
        if frame.checksum != checksum:
            raise ValueError("Checksum mismatch")
        
        return frame

# ==================== 协议载荷定义 ====================

@dataclass
class HandshakePayload:
    """握手载荷"""
    version: str = "1.0"
    
    def to_bytes(self) -> bytes:
        return self.version.encode('utf-8')

@dataclass
class FileInfoPayload:
    """文件信息载荷 (总长度 = 1+1+4+4+32 = 42字节)"""
    file_type: int          # 1字节: 0=HEX, 1=BIN
    target_addr_type: int   # 1字节: 0=Flash, 1=SRAM
    start_address: int      # 4字节: 起始地址
    file_size: int          # 4字节: 文件大小
    file_name: str          # 32字节: 文件名
    
    def to_bytes(self) -> bytes:
        name_bytes = self.file_name.encode('utf-8')[:32]
        name_bytes += b'\x00' * (32 - len(name_bytes))
        return struct.pack('>BBII', self.file_type, self.target_addr_type, 
                          self.start_address, self.file_size) + name_bytes
    
    @classmethod
    def from_bytes(cls, data: bytes) -> 'FileInfoPayload':
        if len(data) < 42:
            raise ValueError("FileInfo payload too short")
        file_type, target_addr_type, start_addr, file_size = struct.unpack('>BBII', data[:10])
        file_name = data[10:42].rstrip(b'\x00').decode('utf-8')
        return cls(file_type, target_addr_type, start_addr, file_size, file_name)

@dataclass
class DataBlockPayload:
    """数据块载荷"""
    block_index: int        # 2字节: 块索引
    block_size: int         # 2字节: 本块大小
    data: bytes             # 可变: 数据内容
    
    def to_bytes(self) -> bytes:
        return struct.pack('>HH', self.block_index, self.block_size) + self.data
    
    @classmethod
    def from_bytes(cls, data: bytes) -> 'DataBlockPayload':
        if len(data) < 4:
            raise ValueError("DataBlock payload too short")
        block_index, block_size = struct.unpack('>HH', data[:4])
        block_data = data[4:4 + block_size]
        return cls(block_index, block_size, block_data)

@dataclass
class ControllerInfoPayload:
    """控制器信息 (查询从MCU)"""
    ctrl_id: int            # 1字节: 控制器ID
    operation: int          # 1字节: 0=查询, 1=准备升级, 2=开始升级, 3=完成升级
    
    def to_bytes(self) -> bytes:
        return struct.pack('>BB', self.ctrl_id, self.operation)
    
    @classmethod
    def from_bytes(cls, data: bytes) -> 'ControllerInfoPayload':
        if len(data) < 2:
            raise ValueError("ControllerInfo payload too short")
        ctrl_id, operation = struct.unpack('>BB', data[:2])
        return cls(ctrl_id, operation)

@dataclass
class StatusPayload:
    """状态反馈"""
    ctrl_id: int            # 1字节: 控制器ID
    status: int             # 1字节: 状态码
    progress: int           # 1字节: 进度百分比(0-100)
    error_code: int         # 1字节: 错误码
    
    def to_bytes(self) -> bytes:
        return struct.pack('>BBBB', self.ctrl_id, self.status, self.progress, self.error_code)
    
    @classmethod
    def from_bytes(cls, data: bytes) -> 'StatusPayload':
        if len(data) < 4:
            raise ValueError("Status payload too short")
        ctrl_id, status, progress, error_code = struct.unpack('>BBBB', data[:4])
        return cls(ctrl_id, status, progress, error_code)
