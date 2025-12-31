"""WAV 音频拼接工具"""

import struct
import logging
from typing import List

logger = logging.getLogger(__name__)


def parse_wav_header(wav_data: bytes) -> dict:
    """
    解析 WAV 文件头。
    
    :param wav_data: WAV 文件字节
    :return: 包含格式信息的字典
    """
    if len(wav_data) < 44:
        raise ValueError("WAV data too short")
    
    # RIFF header
    riff = wav_data[0:4]
    if riff != b'RIFF':
        raise ValueError(f"Invalid RIFF header: {riff}")
    
    wave = wav_data[8:12]
    if wave != b'WAVE':
        raise ValueError(f"Invalid WAVE header: {wave}")
    
    # 查找 fmt chunk
    pos = 12
    fmt_data = None
    data_start = None
    data_size = None
    
    while pos < len(wav_data) - 8:
        chunk_id = wav_data[pos:pos+4]
        chunk_size = struct.unpack('<I', wav_data[pos+4:pos+8])[0]
        
        if chunk_id == b'fmt ':
            fmt_data = wav_data[pos+8:pos+8+chunk_size]
        elif chunk_id == b'data':
            data_start = pos + 8
            data_size = chunk_size
            break
        
        pos += 8 + chunk_size
        # 对齐到偶数字节
        if chunk_size % 2 == 1:
            pos += 1
    
    if fmt_data is None or data_start is None:
        raise ValueError("Could not find fmt or data chunk")
    
    # 解析 fmt chunk
    audio_format = struct.unpack('<H', fmt_data[0:2])[0]
    num_channels = struct.unpack('<H', fmt_data[2:4])[0]
    sample_rate = struct.unpack('<I', fmt_data[4:8])[0]
    byte_rate = struct.unpack('<I', fmt_data[8:12])[0]
    block_align = struct.unpack('<H', fmt_data[12:14])[0]
    bits_per_sample = struct.unpack('<H', fmt_data[14:16])[0]
    
    return {
        'audio_format': audio_format,
        'num_channels': num_channels,
        'sample_rate': sample_rate,
        'byte_rate': byte_rate,
        'block_align': block_align,
        'bits_per_sample': bits_per_sample,
        'data_start': data_start,
        'data_size': data_size,
        'fmt_data': fmt_data,
    }


def extract_audio_data(wav_data: bytes) -> bytes:
    """
    从 WAV 文件中提取纯音频数据。
    
    :param wav_data: WAV 文件字节
    :return: 音频数据字节
    """
    header = parse_wav_header(wav_data)
    return wav_data[header['data_start']:header['data_start'] + header['data_size']]


def create_wav_header(
    num_channels: int,
    sample_rate: int,
    bits_per_sample: int,
    data_size: int,
    audio_format: int = 1,  # PCM
) -> bytes:
    """
    创建 WAV 文件头。
    
    :param num_channels: 通道数
    :param sample_rate: 采样率
    :param bits_per_sample: 位深
    :param data_size: 音频数据大小
    :param audio_format: 音频格式 (1=PCM)
    :return: WAV 文件头字节 (44 bytes)
    """
    byte_rate = sample_rate * num_channels * bits_per_sample // 8
    block_align = num_channels * bits_per_sample // 8
    
    header = bytearray(44)
    
    # RIFF header
    header[0:4] = b'RIFF'
    struct.pack_into('<I', header, 4, data_size + 36)  # ChunkSize
    header[8:12] = b'WAVE'
    
    # fmt chunk
    header[12:16] = b'fmt '
    struct.pack_into('<I', header, 16, 16)  # Subchunk1Size (PCM = 16)
    struct.pack_into('<H', header, 20, audio_format)  # AudioFormat
    struct.pack_into('<H', header, 22, num_channels)  # NumChannels
    struct.pack_into('<I', header, 24, sample_rate)  # SampleRate
    struct.pack_into('<I', header, 28, byte_rate)  # ByteRate
    struct.pack_into('<H', header, 32, block_align)  # BlockAlign
    struct.pack_into('<H', header, 34, bits_per_sample)  # BitsPerSample
    
    # data chunk
    header[36:40] = b'data'
    struct.pack_into('<I', header, 40, data_size)  # Subchunk2Size
    
    return bytes(header)


def concatenate_wav(wav_files: List[bytes]) -> bytes:
    """
    拼接多个 WAV 文件为一个。
    
    假设所有 WAV 文件格式相同（采样率、位深、通道数）。
    
    :param wav_files: WAV 文件字节列表
    :return: 拼接后的 WAV 文件字节
    """
    if not wav_files:
        return b""
    
    # 过滤空文件
    wav_files = [w for w in wav_files if w and len(w) > 44]
    
    if not wav_files:
        return b""
    
    if len(wav_files) == 1:
        return wav_files[0]
    
    try:
        # 解析第一个 WAV 文件获取格式信息
        first_header = parse_wav_header(wav_files[0])
        
        # 提取所有音频数据
        all_audio_data = []
        total_size = 0
        
        for i, wav in enumerate(wav_files):
            try:
                audio_data = extract_audio_data(wav)
                all_audio_data.append(audio_data)
                total_size += len(audio_data)
            except Exception as e:
                logger.warning(f"Failed to extract audio from segment {i}: {e}")
                continue
        
        if not all_audio_data:
            return b""
        
        # 合并音频数据
        combined_data = b''.join(all_audio_data)
        
        # 创建新的 WAV 头
        new_header = create_wav_header(
            num_channels=first_header['num_channels'],
            sample_rate=first_header['sample_rate'],
            bits_per_sample=first_header['bits_per_sample'],
            data_size=len(combined_data),
            audio_format=first_header['audio_format'],
        )
        
        result = new_header + combined_data
        
        logger.debug(
            f"Concatenated {len(wav_files)} WAV files: "
            f"{len(combined_data)} bytes audio data, "
            f"{len(result)} bytes total"
        )
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to concatenate WAV files: {e}")
        # 如果拼接失败，返回第一个文件
        return wav_files[0] if wav_files else b""