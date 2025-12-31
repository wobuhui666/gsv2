# GSV TTS Proxy

智能 TTS 代理服务 - 支持 Token 轮询负载均衡，与 OpenAI API 完全兼容。

## 功能特性

- ✅ **OpenAI 兼容** - 完全兼容 OpenAI Chat Completion 和 TTS API 格式
- ✅ **Token 轮询** - 支持多个 API Token 轮询使用，实现负载均衡
- ✅ **失败自动恢复** - Token 失败自动临时禁用，定时恢复检测
- ✅ **流式响应** - 支持流式返回 LLM 响应
- ✅ **TTS 预生成** - 在流式接收文本的同时异步预生成 TTS
- ✅ **智能缓存** - 使用内存缓存，支持 LRU + TTL 淘汰策略
- ✅ **按句分段** - 智能按句子分段，确保 TTS 质量
- ✅ **API 鉴权** - 支持 Bearer Token 验证，保护 API 端点

## 架构图

```
┌─────────────────────────────────────────────────────────────┐
│                    GSV TTS Proxy 服务                        │
│                                                             │
│  用户请求 ──► Chat API 反代 ──► NewAPI (流式)               │
│                    │                                        │
│                    ▼                                        │
│              文本分段器 ──► TTS 缓存管理器                    │
│                              │                              │
│                              ▼                              │
│                       Token 轮询器                           │
│                    ┌────┬────┬────┐                         │
│                    │ T1 │ T2 │ T3 │  ◄── GSV API Tokens     │
│                    └────┴────┴────┘                         │
│                              │                              │
│                              ▼                              │
│                        GSV TTS API                          │
│                                                             │
│  用户请求 TTS ──► 从缓存获取 ──► 返回音频                    │
└─────────────────────────────────────────────────────────────┘
```

## 快速开始

### 1. 配置环境变量

复制示例配置文件并填入实际值：

```bash
cp .env.example .env
```

编辑 `.env` 文件：

```bash
# 必需配置
GSV_API_TOKENS=token1,token2,token3
NEWAPI_BASE_URL=https://your-newapi.com
NEWAPI_API_KEY=sk-xxxx

# 可选配置
GSV_DEFAULT_VOICE=原神-中文-胡桃_ZH
```

### 2. Docker 部署（推荐）

```bash
# 构建并启动
docker-compose up -d

# 查看日志
docker-compose logs -f
```

### 3. 本地运行

```bash
# 安装依赖
pip install -r requirements.txt

# 启动服务
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## API 接口

### API 鉴权

`/v1/chat/completions` 和 `/v1/audio/speech` 端点需要 API 鉴权。请在请求头中包含 `Authorization` 头：

```bash
Authorization: Bearer sk-xxxx
```

其中 `sk-xxxx` 是您在 `.env` 文件中配置的 `NEWAPI_API_KEY` 值。

**无需鉴权的端点**：
- `/` - 服务信息
- `/health` - 健康检查
- `/cache/stats` - 缓存统计
- `/cache/clear` - 清空缓存
- `/tokens/stats` - Token 轮询统计
- `/v1/models` - 列出模型

### Chat Completion（反向代理）

与 OpenAI Chat Completion API 完全兼容：

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-xxxx" \
  -d '{
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true
  }'
```

**特殊参数**：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `tts_enabled` | bool | true | 是否启用 TTS 预生成 |
| `tts_model` | string | 配置默认值 | TTS 模型名称 |

### TTS 语音合成

与 OpenAI TTS API 兼容：

```bash
curl -X POST http://localhost:8000/v1/audio/speech \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-xxxx" \
  -d '{
    "model": "GSVI-v4",
    "input": "要合成的文本"
  }' \
  --output speech.wav
```

### 健康检查

```bash
curl http://localhost:8000/health
```

### 缓存统计

```bash
curl http://localhost:8000/cache/stats
```

### Token 轮询统计

```bash
curl http://localhost:8000/tokens/stats
```

响应示例：
```json
{
  "total_tokens": 3,
  "healthy_tokens": 3,
  "disabled_tokens": 0,
  "current_index": 1,
  "tokens": [
    {
      "id": "15d5****a7d7",
      "success_count": 10,
      "failure_count": 0,
      "is_disabled": false,
      "disabled_until": null
    }
  ]
}
```

## 配置说明

| 环境变量 | 必需 | 默认值 | 说明 |
|----------|------|--------|------|
| `GSV_API_TOKENS` | ✅ | - | GSV API Tokens，逗号分隔 |
| `NEWAPI_BASE_URL` | ✅ | - | NewAPI 基础 URL |
| `NEWAPI_API_KEY` | ✅ | - | NewAPI API Key |
| `GSV_API_URL` | ❌ | https://gsv2p.acgnai.top/v1/audio/speech | GSV TTS API 地址 |
| `GSV_DEFAULT_VOICE` | ❌ | 原神-中文-胡桃_ZH | 默认语音角色 |
| `GSV_DEFAULT_MODEL` | ❌ | GSVI-v4 | 默认 TTS 模型 |
| `TTS_REQUEST_TIMEOUT` | ❌ | 60 | TTS 请求超时（秒） |
| `TTS_RETRY_COUNT` | ❌ | 2 | TTS 请求重试次数 |
| `CACHE_MAX_SIZE` | ❌ | 1000 | 缓存最大条目数 |
| `CACHE_TTL` | ❌ | 3600 | 缓存过期时间（秒） |
| `LOG_LEVEL` | ❌ | INFO | 日志级别 |

## Token 轮询机制

### 工作原理

1. **Round-Robin 轮询**：按顺序循环使用配置的 Token
2. **失败检测**：连续失败达到阈值（默认 3 次）自动禁用
3. **自动恢复**：禁用后定时恢复（默认 5 分钟）
4. **健康检查**：每次获取 Token 时检查是否可用

### 统计信息

通过 `/tokens/stats` 端点可以查看：
- Token 总数和健康数量
- 每个 Token 的成功/失败计数
- 禁用状态和恢复时间

## GSV TTS API 请求格式

本服务使用固定的请求格式调用 GSV TTS API，只动态修改 `input` 字段：

```json
{
  "model": "GSVI-v4",
  "input": "动态文本",
  "voice": "原神-中文-胡桃_ZH",
  "response_format": "wav",
  "speed": 1,
  "instructions": "默认",
  "other_params": {
    "text_lang": "中英混合",
    "prompt_lang": "中文",
    "emotion": "默认",
    "top_k": 10,
    "top_p": 1,
    "temperature": 1,
    "text_split_method": "按标点符号切",
    "batch_size": 1,
    "batch_threshold": 0.75,
    "split_bucket": true,
    "fragment_interval": 0.3,
    "parallel_infer": true,
    "repetition_penalty": 1.35,
    "sample_steps": 16,
    "if_sr": false,
    "seed": -1
  }
}
```

## 工作流程

1. **接收 Chat 请求**：代理接收 OpenAI 格式的 Chat Completion 请求
2. **流式转发**：无论原请求是否流式，都向 NewAPI 发起流式请求
3. **文本分段**：使用智能分段器按句子分割流式文本
4. **TTS 预生成**：每个完整句子异步提交到 TTS 缓存管理器
5. **Token 轮询**：TTS 请求使用 Round-Robin 策略选择可用 Token
6. **返回响应**：流式文本立即返回，不等待 TTS 完成
7. **获取 TTS**：后续请求 TTS 时从缓存获取（如正在生成则等待）

## 许可证

MIT License