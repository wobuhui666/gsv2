"""流式文本分段器 - 基于 Genie-TTS TextSplitter 改进"""

import re
from typing import List, Set, Pattern, Optional


class StreamingTextSplitter:
    """
    流式文本分段器
    
    支持在流式接收文本时实时检测完整句子，
    适用于处理 LLM 流式响应。
    """
    
    def __init__(self, max_len: int = 40, min_len: int = 5):
        """
        初始化流式文本分段器。

        :param max_len: 软限制最大长度 (Effective Length)。超过此长度遇到分隔符时会切分。
        :param min_len: 硬限制最小长度 (Effective Length)。小于此长度遇到终止符也不会切分。
        """
        self.max_len: int = max_len
        self.min_len: int = min_len
        self.buffer: str = ""

        # 1. 定义基础字符集合
        # 只要标点块中包含这些字符，就视为 Ending (终止符)
        self.end_chars: Set[str] = {
            '。', '！', '？', '…',
            '!', '?', '.'
        }

        # 2. 定义标点符号全集 (用于正则匹配和长度计算过滤)
        self.all_puncts_chars: Set[str] = self.end_chars | {
            '，', '、', '；', '：', '——',
            ',', ';', ':',
            '"', '"', ''', ''', '"', "'",
        }

        # 3. 编译正则表达式
        # 使用非捕获组 (?:) 配合 + 号，实现贪婪匹配连续标点
        # sort + escape 确保正则安全且优先匹配长标点
        sorted_puncts: List[str] = sorted(list(self.all_puncts_chars), key=len, reverse=True)
        escaped_puncts: List[str] = [re.escape(p) for p in sorted_puncts]
        self.pattern: Pattern = re.compile(f"((?:{'|'.join(escaped_puncts)})+)")

    @staticmethod
    def get_char_width(char: str) -> int:
        """计算单字符宽度：ASCII算1，其他（中日韩）算2"""
        return 1 if ord(char) < 128 else 2

    def get_effective_len(self, text: str) -> int:
        """
        计算字符串的有效长度。
        逻辑：跳过标点符号，仅计算内容字符。
        例如："你好......" -> 有效长度为 4 (你好)，而不是 10。
        """
        length = 0
        for char in text:
            # 如果是标点符号集合里的字符，不计入长度
            if char in self.all_puncts_chars:
                continue
            length += self.get_char_width(char)
        return length

    def is_terminator_block(self, block: str) -> bool:
        """
        判断一个标点块是否起到结束句子的作用。
        只要块中包含任意一个结束字符（如句号），则视为结束块。
        """
        for char in block:
            if char in self.end_chars:
                return True
        return False

    def feed(self, text: str) -> List[str]:
        """
        输入文本片段，返回已完成的句子列表。
        
        :param text: 新接收的文本片段
        :return: 已完成的句子列表（可能为空）
        """
        if not text:
            return []
        
        # 累积文本到缓冲区
        self.buffer += text
        
        # 尝试分割缓冲区
        return self._try_split()
    
    def _try_split(self) -> List[str]:
        """尝试从缓冲区中分割出完整的句子"""
        if not self.buffer:
            return []
        
        # 清理换行符
        clean_buffer = self.buffer.replace('\n', '')
        
        # 正则切分
        segments: List[str] = self.pattern.split(clean_buffer)
        
        sentences: List[str] = []
        current_buffer: str = ""
        
        for i, segment in enumerate(segments):
            if not segment:
                continue
            
            # 判断当前片段是否是标点块
            is_punct_block = segment[0] in self.all_puncts_chars
            
            if is_punct_block:
                current_buffer += segment
                
                # 计算缓冲区内容的【有效长度】
                eff_len = self.get_effective_len(current_buffer)
                
                # 判断是否是最后一个分段
                is_last_segment = (i == len(segments) - 1) or all(
                    not s for s in segments[i+1:]
                )
                
                # 如果不是最后一个分段，可以进行切分判断
                if not is_last_segment:
                    if self.is_terminator_block(segment):
                        # 结束符号 -> 检查 min_len
                        if eff_len >= self.min_len:
                            sentences.append(current_buffer.strip())
                            current_buffer = ""
                    else:
                        # 分隔符号 -> 检查 max_len
                        if eff_len >= self.max_len:
                            sentences.append(current_buffer.strip())
                            current_buffer = ""
            else:
                # 纯文本
                current_buffer += segment
        
        # 更新缓冲区为剩余内容
        self.buffer = current_buffer
        
        return sentences
    
    def flush(self) -> Optional[str]:
        """
        刷新缓冲区，返回剩余的文本。
        通常在流式接收结束时调用。
        
        :return: 剩余的文本，如果为空则返回 None
        """
        if not self.buffer:
            return None
        
        remaining = self.buffer.strip()
        self.buffer = ""
        
        if remaining and self.get_effective_len(remaining) > 0:
            return remaining
        return None
    
    def reset(self):
        """重置分段器状态"""
        self.buffer = ""
    
    def split_text(self, text: str) -> List[str]:
        """
        一次性分割完整文本（非流式模式）。
        
        :param text: 完整文本
        :return: 分割后的句子列表
        """
        self.reset()
        sentences = self.feed(text)
        remaining = self.flush()
        if remaining:
            sentences.append(remaining)
        return sentences