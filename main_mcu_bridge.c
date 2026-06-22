/**
 * 主MCU端 - 以太网/CAN网关
 * STM32F4 + W5500以太网 + CAN接口
 * 接收PC命令，通过CAN/UDS向从MCU下发固件
 */

#include "stm32f4xx.h"
#include "w5500.h"
#include "can.h"
#include "uds.h"
#include "string.h"
#include "stdio.h"

#define MASTER_PORT 8888
#define BUFFER_SIZE 2048
#define BLOCK_SIZE 256

typedef struct {
    uint8_t frame_type;
    uint8_t ctrl_id;
    uint16_t sequence;
    uint32_t payload_len;
} FrameHeader_t;

typedef struct {
    uint32_t start_addr;
    uint32_t file_size;
    uint8_t file_type;
} FileInfo_t;

volatile FileInfo_t g_file_info = {0};
volatile uint32_t g_received_blocks = 0;
volatile uint8_t g_updating_ctrl_id = 0;

// CRC-32 计算
uint32_t calc_crc32(uint8_t *data, uint32_t len) {
    uint32_t crc = 0xFFFFFFFF;
    for (uint32_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (int j = 0; j < 8; j++) {
            crc = (crc >> 1) ^ ((crc & 1) ? 0xEDB88320 : 0);
        }
    }
    return crc ^ 0xFFFFFFFF;
}

// W5500套接字初始化
void ethernet_init(void) {
    // 配置W5500参数
    w5500_init();
    w5500_socket(0, Sn_MR_TCP, MASTER_PORT, 0);
    w5500_listen(0);  // 监听模式
    printf("[MASTER MCU] Listening on port %d\r\n", MASTER_PORT);
}

// 从以太网接收数据
uint16_t recv_ethernet_frame(uint8_t *buffer, uint16_t max_len) {
    uint16_t len = w5500_recv(0, buffer, max_len);
    return len;
}

// 发送以太网数据
void send_ethernet_frame(uint8_t *data, uint16_t len) {
    w5500_send(0, data, len);
}

// 处理文件信息帧
void handle_file_info(uint8_t *payload, uint16_t len) {
    if (len < 10) return;
    
    g_file_info.file_type = payload[0];
    g_file_info.start_addr = (payload[2] << 24) | (payload[3] << 16) | 
                             (payload[4] << 8) | payload[5];
    g_file_info.file_size = (payload[6] << 24) | (payload[7] << 16) | 
                            (payload[8] << 8) | payload[9];
    
    printf("[FILE INFO] Type=%d, StartAddr=0x%X, Size=%d\r\n", 
           g_file_info.file_type, g_file_info.start_addr, g_file_info.file_size);
}

// 处理数据块帧 (通过CAN/UDS发送给从MCU)
void handle_data_block(uint8_t ctrl_id, uint8_t *payload, uint16_t len) {
    uint16_t block_idx = (payload[0] << 8) | payload[1];
    uint16_t block_size = (payload[2] << 8) | payload[3];
    uint8_t *data = &payload[4];
    
    // 通过CAN/UDS发送数据到从MCU
    // 使用UDS服务0x36 (TransferData)
    
    CAN_Message_t can_msg = {0};
    can_msg.id = 0x600 + ctrl_id;  // CAN ID基于控制器
    can_msg.dlc = (block_size <= 7) ? block_size + 1 : 8;  // +1 for UDS SID
    
    can_msg.data[0] = 0x36;  // UDS TransferData service
    can_msg.data[1] = (block_idx >> 8) & 0xFF;
    can_msg.data[2] = block_idx & 0xFF;
    
    if (block_size > 0) {
        memcpy(&can_msg.data[3], data, block_size);
    }
    
    CAN_Send(&can_msg);
    
    printf("[DATA BLOCK] Sent block %d (%d bytes) to ECU %d via CAN\r\n", 
           block_idx, block_size, ctrl_id);
}

// 处理升级完成
void handle_upgrade_complete(uint8_t ctrl_id) {
    // 通过CAN/UDS发送完成命令
    // UDS服务0x22 (WriteDataByIdentifier) 设置重启标志
    
    uint8_t uds_cmd[] = {0x22, 0xF1, 0x90};  // 写入重启标识符
    CAN_Message_t can_msg = {0};
    can_msg.id = 0x600 + ctrl_id;
    can_msg.dlc = 3;
    memcpy(can_msg.data, uds_cmd, 3);
    
    CAN_Send(&can_msg);
    
    printf("[UPGRADE] ECU %d: Upgrade complete, restarting...\r\n", ctrl_id);
}

// 发送握手响应
void send_handshake_response(void) {
    uint8_t response[20];
    uint8_t pos = 0;
    
    // 帧头
    memcpy(&response[pos], "FWUP", 4);
    pos += 4;
    
    // 帧类型: STATUS (0x05)
    response[pos++] = 0x05;
    response[pos++] = 0;   // 控制器ID
    response[pos++] = 0;
    response[pos++] = 1;   // 序列号
    
    // 载荷长度: 4 (StatusPayload)
    response[pos++] = 0;
    response[pos++] = 0;
    response[pos++] = 0;
    response[pos++] = 4;
    
    // 载荷: ctrl_id=0, status=0, progress=0, error_code=0
    response[pos++] = 0;
    response[pos++] = 0;
    response[pos++] = 0;
    response[pos++] = 0;
    
    // CRC-32
    uint32_t crc = calc_crc32(response, pos);
    response[pos++] = (crc >> 24) & 0xFF;
    response[pos++] = (crc >> 16) & 0xFF;
    response[pos++] = (crc >> 8) & 0xFF;
    response[pos++] = crc & 0xFF;
    
    send_ethernet_frame(response, pos);
    printf("[HANDSHAKE] Response sent\r\n");
}

// 主以太网处理循环
void ethernet_process(void) {
    static uint8_t rx_buffer[BUFFER_SIZE];
    uint16_t rx_len;
    
    // 接收以太网帧
    rx_len = recv_ethernet_frame(rx_buffer, BUFFER_SIZE);
    if (rx_len < 16) return;  // 最小帧长度
    
    // 检查魔数
    if (strncmp((char*)rx_buffer, "FWUP", 4) != 0) return;
    
    // 解析帧头
    FrameHeader_t header;
    header.frame_type = rx_buffer[4];
    header.ctrl_id = rx_buffer[5];
    header.sequence = (rx_buffer[6] << 8) | rx_buffer[7];
    header.payload_len = (rx_buffer[8] << 24) | (rx_buffer[9] << 16) | 
                        (rx_buffer[10] << 8) | rx_buffer[11];
    
    uint16_t frame_len = 12 + header.payload_len + 4;
    if (rx_len < frame_len) return;
    
    // 校验CRC
    uint32_t crc_received = (rx_buffer[frame_len-4] << 24) | 
                           (rx_buffer[frame_len-3] << 16) |
                           (rx_buffer[frame_len-2] << 8) | 
                           rx_buffer[frame_len-1];
    uint32_t crc_calc = calc_crc32(rx_buffer, frame_len - 4);
    
    if (crc_received != crc_calc) {
        printf("[ERROR] CRC mismatch\r\n");
        return;
    }
    
    uint8_t *payload = &rx_buffer[12];
    
    // 处理不同帧类型
    switch (header.frame_type) {
        case 0x01:  // HANDSHAKE
            printf("[HANDSHAKE] Received from PC\r\n");
            send_handshake_response();
            break;
        
        case 0x02:  // FILE_INFO
            printf("[FILE_INFO] Received\r\n");
            handle_file_info(payload, header.payload_len);
            g_updating_ctrl_id = header.ctrl_id;
            break;
        
        case 0x03:  // DATA_BLOCK
            handle_data_block(header.ctrl_id, payload, header.payload_len);
            break;
        
        case 0x04:  // CTRL_INFO
            if (payload[1] == 3) {  // 完成升级
                handle_upgrade_complete(header.ctrl_id);
            }
            break;
    }
}

// CAN接收中断
void CAN1_RX0_IRQHandler(void) {
    // 处理CAN消息
    // 例如: 从MCU的回复
}

// 主函数
int main(void) {
    SystemInit();
    ethernet_init();
    CAN_Init();
    
    printf("[MASTER MCU] Firmware bridge started\r\n");
    
    while (1) {
        ethernet_process();
        HAL_Delay(10);
    }
    
    return 0;
}
