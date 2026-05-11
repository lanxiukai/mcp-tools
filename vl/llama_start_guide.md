# llama.cpp 一键启动脚本使用指南

## 简介

`llama_start.sh` 是一个用于快速启动和管理 llama.cpp 推理服务的 Bash 脚本。它提供两种运行模式，并内置了完整的后台进程管理功能，让你无需记忆复杂的命令行参数。

### 主要特性

- **自动扫描模型**：递归搜索脚本所在目录下 `models/gguf/` 的所有 `.gguf` 模型文件（自动排除缓存目录和 mmproj 文件）
- **两种运行模式**：API 服务模式（server）和交互式对话模式（cli）
- **视觉多模态**：自动检测模型同目录下的 `mmproj*.gguf` 文件，无需手动配置即可启用图像识别
- **后台运行**：server 模式默认后台运行，日志自动记录到文件
- **进程管理**：支持 `stop`、`status`、`restart`、`log` 等管理命令
- **GPU 加速**：自动启用 CUDA GPU 加速，支持全量 GPU 卸载
- **Flash Attention**：支持 `on` / `off` / `auto` 三档控制，长上下文场景下显著降低显存占用
- **MoE 混合推理**：支持将 Expert 权重留在 CPU 内存，适配显存不足时运行大型 MoE 模型

---

## 前提条件

1. **llama.cpp 已编译**：确保 `~/llama.cpp/build/bin/` 下存在以下可执行文件：
   - `llama-server`：API 服务
   - `llama-cli`：纯文本命令行对话
   - `llama-mtmd-cli`：多模态（视觉）命令行对话

   如未编译，执行以下命令（包含视觉所需的 `llama-mtmd-cli` target）：

   ```bash
   cd ~/llama.cpp
   cmake -B build -DGGML_CUDA=ON -DBUILD_SHARED_LIBS=OFF
   cmake --build build -j$(nproc) \
       --target llama-cli llama-mtmd-cli llama-server llama-gguf-split
   ```

2. **GGUF 模型文件**：至少需要一个 `.gguf` 格式的模型文件存放在仓库的 `models/gguf/` 目录下。
   可使用同目录下的下载脚本从 Hugging Face 获取模型：

   ```bash
   python ~/project/hf_models/hf_hub_download.py
   ```

3. **（可选）mmproj 视觉投影文件**：如需图像识别功能，还需下载对应的 `mmproj-F16.gguf` 文件，与主模型放在同一目录下。脚本会自动检测并启用。

---

## 快速开始

```bash
# 赋予执行权限（仅首次需要）
chmod +x ~/project/hf_models/vl/llama_start.sh

# 一键启动（交互式选择模型和模式）
./vl/llama_start.sh
```

运行后脚本会：

1. 自动扫描可用的 GGUF 模型（如果只有一个模型会自动选择）
2. 让你选择运行模式（server / cli）
3. 显示配置摘要后启动服务

---

## 命令参考

### 启动服务


| 命令                             | 说明               |
| ------------------------------ | ---------------- |
| `./vl/llama_start.sh`             | 交互式选择模型和模式       |
| `./vl/llama_start.sh server`      | 后台启动 API 服务      |
| `./vl/llama_start.sh server --fg` | 前台启动 API 服务（调试用） |
| `./vl/llama_start.sh cli`         | 启动交互式命令行对话（前台）   |


### 管理服务


| 命令                         | 说明                              |
| -------------------------- | ------------------------------- |
| `./vl/llama_start.sh status`  | 查看服务运行状态（PID、CPU、内存、最近日志）       |
| `./vl/llama_start.sh log`     | 实时查看服务日志（`tail -f`，Ctrl+C 退出查看） |
| `./vl/llama_start.sh stop`    | 停止后台运行的服务                       |
| `./vl/llama_start.sh restart` | 重启服务（先停止再启动）                    |
| `./vl/llama_start.sh help`    | 显示帮助信息                          |


### 指定模型路径

也可以在命令中直接指定模型文件路径，跳过交互式选择：

```bash
./vl/llama_start.sh server ~/project/hf_models/models/gguf/unsloth/Qwen3.5-35B-A3B-GGUF/Qwen3.5-35B-A3B-Q4_K_M.gguf

# 同样模型启动多模态视觉对话（自动检测 mmproj）
./vl/llama_start.sh cli ~/project/hf_models/models/gguf/unsloth/Qwen3.5-35B-A3B-GGUF/Qwen3.5-35B-A3B-Q4_K_M.gguf
```

---

## 运行模式详解

### Server 模式（API 服务）

启动一个 OpenAI 兼容的 HTTP API 服务，**默认后台运行**。

```bash
./vl/llama_start.sh server
```

启动成功后会显示：

```
[INFO] 服务启动成功！
  PID:      12345
  API 地址: http://0.0.0.0:8080
  API 文档: http://0.0.0.0:8080/docs
  日志文件: ~/.llama/llama-server.log
```

#### API 调用示例

服务启动后，可以通过标准 OpenAI API 格式调用：

```bash
# Chat Completions（对话补全）
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "local-model",
    "messages": [
      {"role": "user", "content": "你好，请介绍一下你自己"}
    ]
  }'
```

#### 接入第三方客户端

由于兼容 OpenAI API 格式，你可以将以下地址配置到各种客户端中：

- **API Base URL**: `http://localhost:8080/v1`
- **API Key**: 任意值即可（如 `sk-local`）

支持的客户端包括：Open WebUI、Cherry Studio、ChatBox、Chatwise 等。

### CLI 模式（命令行对话）

启动交互式命令行对话，**始终前台运行**（因为需要键盘交互）。

```bash
./vl/llama_start.sh cli
```

进入对话后：

- 直接输入文本，按回车发送
- 输入 `/bye` 或按 `Ctrl+C` 退出

**视觉模式**：若检测到 mmproj 文件，脚本会自动切换为 `llama-mtmd-cli`（多模态专用工具），并在启动时提示：

```
[INFO] 检测到 mmproj，使用多模态 CLI: llama-mtmd-cli
[提示] 视觉模式下可通过 --image /path/to/image.jpg 传入图片
```

---

## 配置说明

脚本顶部的 **配置区域** 包含所有可调参数，你可以根据硬件情况和需求修改：

### 通用参数


| 参数              | 默认值                     | 说明                                             |
| --------------- | ----------------------- | ---------------------------------------------- |
| `LLAMA_BIN_DIR` | `~/llama.cpp/build/bin` | llama.cpp 可执行文件目录                              |
| `MODEL_DIR`     | `models/gguf/`（脚本所在目录下）     | 模型搜索目录（自动排除 `.cache` 子目录和 mmproj 文件）           |
| `RUN_DIR`       | `~/.llama`              | 日志和 PID 文件目录                                   |
| `GPU_LAYERS`    | `999`                   | GPU 卸载层数（999 = 全部卸载到 GPU）                      |
| `MMPROJ_FILE`   | `""`（自动检测）              | 视觉投影文件路径，见下方说明                                 |
| `FLASH_ATTN`    | `on`                    | Flash Attention 模式，见下方说明                       |
| `CPU_MOE`       | `32`                    | MoE Expert 权重放置策略，见下方说明                        |
| `CTX_SIZE`      | `32`                    | 上下文长度（**单位：K Tokens**，32 = 32K = 32768 tokens） |
| `THREADS`       | `nproc/2`               | CPU 线程数，MoE 模型建议手动调优，见下方说明                     |


### Server 模式参数


| 参数         | 默认值       | 说明                   |
| ---------- | --------- | -------------------- |
| `HOST`     | `0.0.0.0` | 监听地址（0.0.0.0 允许外部访问） |
| `PORT`     | `8080`    | 监听端口                 |
| `PARALLEL` | `1`       | 最大并发请求数              |


### CLI 模式参数


| 参数              | 默认值    | 说明                     |
| --------------- | ------ | ---------------------- |
| `CHAT_TEMPLATE` | （空）    | 对话模板（留空自动检测）           |
| `TEMPERATURE`   | `0.6`  | 采样温度（越高越随机）            |
| `TOP_P`         | `0.95` | Top-P 核采样              |
| `MAX_TOKENS`    | `-1`   | 最大生成 token 数（-1 = 无限制） |


---

## 视觉多模态功能

Qwen3.5 系列支持图像识别，需要在主模型文件之外额外下载 **视觉投影文件（mmproj）**。

### 下载 mmproj 文件

在 `hf_hub_download.py` 中设置 `TARGET_PATTERN`，同时包含主模型和 mmproj：

```python
TARGET_PATTERN = ["Q4_K_M", "mmproj-F16"]
```

执行后文件会被下载到模型同目录下，例如：

```
~/project/hf_models/models/gguf/unsloth/Qwen3.5-35B-A3B-GGUF/
├── Qwen3.5-35B-A3B-Q4_K_M.gguf   # 主模型
└── mmproj-F16.gguf                 # 视觉投影文件（899 MB）
```

### mmproj 文件版本选择

仓库提供三种精度，推荐使用 **F16**：

| 文件名 | 精度 | 大小 | 推荐 |
| --- | --- | --- | --- |
| `mmproj-F16.gguf` | FP16 | 899 MB | ✅ 推荐，Unsloth 官方示例使用此版本 |
| `mmproj-BF16.gguf` | BF16 | 903 MB | 也可以，RTX 系列原生支持 |
| `mmproj-F32.gguf` | FP32 | 1.79 GB | 不推荐，体积翻倍但效果无明显提升 |

### MMPROJ_FILE 配置项

`MMPROJ_FILE` 控制视觉投影文件的加载方式：

| 值 | 行为 |
| --- | --- |
| `""`（默认） | 自动在模型同目录下查找 `mmproj*.gguf`，找到则启用视觉功能 |
| `"/path/to/mmproj-F16.gguf"` | 强制使用指定路径的文件 |
| `"none"` | 禁用视觉功能（即使同目录存在 mmproj 文件） |

### 视觉功能如何工作

**自动检测**：只需将 `mmproj-F16.gguf` 与主模型放在同目录，无需修改任何配置，脚本会在启动时自动检测并提示：

```
[INFO] 自动检测到 mmproj 文件: mmproj-F16.gguf
  视觉投影:   mmproj-F16.gguf [视觉已启用]
```

**Server 模式**（推荐）：启动后 API 接口自动支持图像输入，通过 `image_url` 字段传图：

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "local-model",
    "messages": [
      {
        "role": "user",
        "content": [
          {
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64,<BASE64_DATA>"}
          },
          {"type": "text", "text": "这张图片里有什么？"}
        ]
      }
    ]
  }'
```

也可以使用支持视觉的第三方客户端（如 Open WebUI），直接在对话框中上传图片。

**CLI 模式**：脚本自动切换为 `llama-mtmd-cli`，在交互时可通过如下格式传入图片路径（具体语法以 `llama-mtmd-cli --help` 为准）：

```
>>> Describe this image: [/path/to/photo.jpg]
```

### 仅使用纯文本模式

若已有 mmproj 文件但暂时不需要视觉功能，在脚本配置区设置：

```bash
MMPROJ_FILE="none"
```

---

## Flash Attention

通过 `FLASH_ATTN` 参数控制，支持三个值：


| 值      | 效果                                   |
| ------ | ------------------------------------ |
| `auto` | 由 llama.cpp 自动判断是否启用（llama.cpp 内置默认） |
| `on`   | 强制开启，显著降低长上下文（32K+）的显存占用并提升速度        |
| `off`  | 强制关闭，使用标准 Attention（兼容性最佳）           |


**前提**：llama.cpp 编译时需启用 CUDA（`-DGGML_CUDA=ON`）。验证当前版本是否支持：

```bash
~/llama.cpp/build/bin/llama-server --help | grep flash
```

有输出则表示支持，无需重新编译。

---

## MoE 模型支持

通过 `CPU_MOE` 参数精细控制 Expert 权重的存放位置：


| 值       | 传给 llama.cpp 的参数 | 效果                                              |
| ------- | ---------------- | ----------------------------------------------- |
| `false` | 不传               | Expert 权重全部卸到 GPU（显存充足时最快）                      |
| `all`   | `--cpu-moe`      | 全部 Expert 留在 CPU 内存（最省显存）                       |
| 数字 `N`  | `--n-cpu-moe N`  | 前 N 层 Expert 留在 CPU，其余层 Expert 卸到 GPU（**折中最优**） |


**Dense 模型**（如 Qwen3.5-9B）保持 `CPU_MOE=false` 即可，此参数仅对 MoE 架构生效。

### 如何查询 MoE 模型的层数和专家数

每个模型的架构参数不同，配置 `CPU_MOE` 前需先确认实际层数。最快的方法是直接扫描 GGUF 文件头（毫秒级完成，无需加载模型）：

```bash
python3 << 'EOF'
import struct

MODEL = "/path/to/your/model.gguf"   # ← 改为实际路径
TARGETS = [b'block_count', b'expert_count', b'expert_used_count', b'context_length']

with open(MODEL, 'rb') as f:
    data = f.read(2 * 1024 * 1024)   # 只读前 2MB，元数据全在这里

for key in TARGETS:
    pos = data.find(key)
    if pos == -1:
        continue
    klen = len(key)
    t  = struct.unpack_from('<I', data, pos + klen)[0]       # 类型字段
    if t == 4:   # UINT32
        val = struct.unpack_from('<I', data, pos + klen + 4)[0]
        print(f"  {key.decode()} = {val}")
    elif t == 10:  # UINT64
        val = struct.unpack_from('<Q', data, pos + klen + 4)[0]
        print(f"  {key.decode()} = {val}")
EOF
```

以 `Qwen3.5-35B-A3B-Q4_K_M.gguf` 为例，输出为：

```
  block_count       = 40     ← 总层数（即 Expert 总层数）
  expert_count      = 256    ← 每层专家总数
  expert_used_count = 8      ← 每次推理激活的专家数
  context_length    = 262144 ← 最大上下文（256K tokens）
```

也可以在 llama.cpp 启动日志里直接读取（启动时会打印模型元数据）：

```bash
./vl/llama_start.sh log   # 服务启动后查看日志，找 n_layer / n_expert 行
```

---

### 典型场景：NVIDIA GPU（12GB）+ Qwen3.5-35B-A3B（21GB）

该模型架构参数（通过上方脚本确认）：


| 参数                 | 数值               |
| ------------------ | ---------------- |
| 总层数（`block_count`） | **40**           |
| 每层专家总数             | 256（每次推理激活约 8 个） |
| 文件大小               | 21GB（Q4_K_M）     |


权重分布估算：


| 权重类型                           | 大小         | 建议位置   |
| ------------------------------ | ---------- | ------ |
| Dense 层（Attention + Embedding） | ~2GB       | GPU    |
| KV Cache（32K 上下文）              | ~1–2GB     | GPU    |
| Expert 层（前 N 层）                | N × ~475MB | CPU 内存 |
| Expert 层（其余层）                  | 剩余         | GPU    |


12GB 显存扣除 Dense 层和 KV Cache 约剩 **8–9GB** 可放 Expert，约可容纳 **17–19 层**，推荐起始配置：

```bash
GPU_LAYERS=999  # Dense/Attention 层全部卸到 GPU
CPU_MOE=24      # 前 24 层 Expert 留在内存，后 16 层 Expert 在 GPU
```

**调优方法**：从 `CPU_MOE=24` 开始，若不 OOM 则逐步减小（更多 Expert 上 GPU 更快），若 OOM 则增大。

**适用模型**：Qwen3.5-35B-A3B、DeepSeek-V3、Kimi-K2 等 MoE 架构模型。

### CPU 线程数对 MoE 速度的影响

使用 `--n-cpu-moe` 时，CPU 端负责处理部分 Expert 层的矩阵乘法，线程数直接影响这部分的速度。

- **瓶颈**：内存带宽（读取 Expert 权重）+ CPU 矩阵乘法算力
- **超线程（SMT）收益有限**：超过物理核心数后速度提升不明显，有时因缓存争抢反而略慢
- **建议**：从物理核心数开始测试，逐步尝试 1.25x、1.5x 倍的逻辑核心数

以 multi-core CPU（16 物理核 / 32 逻辑核）为例，建议测试值为 `16`、`20`、`24`：

```bash
~/llama.cpp/build/bin/llama-cli \
  --model ~/project/hf_models/models/gguf/unsloth/Qwen3.5-35B-A3B-GGUF/Qwen3.5-35B-A3B-Q4_K_M.gguf \
  --n-gpu-layers 999 --n-cpu-moe 24 \
  --threads 20 \
  --prompt "写一首关于秋天的诗" --n-predict 100 2>&1 | grep "eval time"
```

取 `tokens/s` 最高的值填入脚本的 `THREADS`。

---

## Dense 模型 GPU 卸载建议（Qwen3.5-27B）

Qwen3.5-27B 是 **Dense 架构**（非 MoE），运行时应设置 `CPU_MOE=false`。当显存不足以容纳完整模型时，通过调整 `GPU_LAYERS` 实现部分卸载（部分层留在 CPU 内存）。

### 模型架构参数


| 参数                 | 数值                                                                 |
| ------------------ | ------------------------------------------------------------------ |
| 总层数（`block_count`） | **64**                                                             |
| Hidden Size        | 5120                                                               |
| 注意力类型              | 混合架构（48 层 Gated DeltaNet / Linear Attention + 16 层 Full Attention） |
| 最大上下文              | 262144（256K tokens）                                                |


混合架构的优势：仅 16 层使用标准 KV Cache，其余 48 层使用固定大小的线性注意力状态，因此 32K 上下文时 KV Cache 约 **2 GB**，远低于同层数的纯 Transformer 模型。

### 各量化版本文件大小与每层权重


| 量化格式   | 文件大小    | 每层权重约   |
| ------ | ------- | ------- |
| Q4_K_M | 16.5 GB | ~264 MB |
| Q5_K_M | 19.2 GB | ~307 MB |
| Q6_K   | 22.1 GB | ~354 MB |
| Q8_0   | 28.6 GB | ~457.6 MB |


### 推荐 GPU_LAYERS 设置（32K 上下文，Flash Attention ON）

下表给出不同显存下的建议起始值（已预留 KV Cache + 系统开销），实际可上下微调 ±3 层：


| 显存    | Q4_K_M (16.5GB) | Q5_K_M (19.2GB) | Q6_K (22.1GB) | Q8_0 (28.6GB) |
| ----- | --------------- | --------------- | ------------- | ------------- |
| 12 GB | 34              | 27              | 22            | 15            |
| 16 GB | 48              | 40              | 34            | 25            |
| 24 GB | 999 ✓           | 999 ✓           | 60            | 44            |


> `999` 表示全部 64 层 + Embedding 均放入 GPU，即完全 GPU 推理。

### 典型场景：NVIDIA GPU（12GB）+ Qwen3.5-27B-Q4_K_M（16.7GB）

```bash
GPU_LAYERS=33     # 前 33 层卸到 GPU，剩余 31 层留在 CPU
CPU_MOE=false     # Dense 模型，关闭 MoE 参数
CTX_SIZE=32       # 32K 上下文
FLASH_ATTN=on     # 建议开启
```

**调优方法**：从建议值开始，若不 OOM 则逐步增大 `GPU_LAYERS`（每次 +2），直到接近显存上限；若 OOM 则减小。GPU 层数越多，推理速度越快。

---

## 文件说明


| 路径                               | 说明                        |
| -------------------------------- | ------------------------- |
| `~/project/hf_models/vl/llama_start.sh`     | 启动脚本本体                    |
| `~/project/hf_models/hf_hub_download.py` | 模型下载脚本                    |
| `~/.llama/llama-server.pid`      | 后台服务进程 PID 文件             |
| `~/.llama/llama-server.log`      | 后台服务日志文件                  |
| `~/.llama/llama-server.model`    | 记录当前运行模型路径（restart 时自动沿用） |

**模型目录结构示例（以 Qwen3.5-35B-A3B 为例）**：

```
~/project/hf_models/models/gguf/unsloth/Qwen3.5-35B-A3B-GGUF/
├── Qwen3.5-35B-A3B-Q4_K_M.gguf   # 主模型（22 GB，4bit 量化）
├── Qwen3.5-35B-A3B-Q8_0.gguf     # 主模型（36.9 GB，8bit 量化）
├── mmproj-F16.gguf                 # 视觉投影文件（899 MB，自动检测）
├── README.md
└── config.json
```


---

## 常见问题

### Q: 启动失败，提示找不到可执行文件？

需要先编译 llama.cpp，包含视觉工具：

```bash
cd ~/llama.cpp
cmake -B build -DGGML_CUDA=ON -DBUILD_SHARED_LIBS=OFF
cmake --build build -j$(nproc) \
    --target llama-cli llama-mtmd-cli llama-server llama-gguf-split
```

### Q: 如何启用图像识别（视觉功能）？

1. 下载 mmproj 文件（与主模型放同一目录）：
   ```python
   # hf_hub_download.py 中设置
   TARGET_PATTERN = ["Q4_K_M", "mmproj-F16"]
   ```
2. 正常启动脚本，视觉功能会自动激活，无需额外配置。

### Q: 有 mmproj 文件但不想开启视觉功能？

在脚本配置区设置：

```bash
MMPROJ_FILE="none"
```

### Q: 启动失败，提示找不到模型文件？

确保 `models/gguf/` 目录下有 `.gguf` 格式的模型文件。可以用下载脚本获取：

```bash
python ~/project/hf_models/hf_hub_download.py
```

### Q: 如何修改监听端口？

编辑 `llama_start.sh`，找到配置区域，修改 `PORT` 的值：

```bash
PORT=8080  # 修改为你想要的端口
```

### Q: 如何查看 GPU 是否正常工作？

启动服务后查看日志：

```bash
./vl/llama_start.sh log
```

日志中出现 `CUDA` 相关信息即表示 GPU 加速已生效。

### Q: 如何增大上下文长度？

编辑脚本中的 `CTX_SIZE` 参数（单位为 **K Tokens**）。注意更大的上下文长度需要更多显存：

```bash
CTX_SIZE=16   # 16K tokens（16384 tokens）
CTX_SIZE=32   # 32K tokens（32768 tokens）
CTX_SIZE=128  # 128K tokens（131072 tokens）
```

### Q: 运行 MoE 模型时显存不够怎么办？

使用 `CPU_MOE` 的数字模式，将部分 Expert 层留在内存，同时充分利用剩余显存：

```bash
CPU_MOE=15   # 前 15 层 Expert 留在内存，其余卸到 GPU
```

若仍然 OOM，增大数字直到稳定；若想进一步提速，减小数字让更多 Expert 上 GPU。全量放内存则用 `CPU_MOE=all`。

详见上方 [MoE 模型支持](#moe-模型支持) 章节。

### Q: 如何更新 llama.cpp 到最新版本？

拉取最新代码后重新编译即可：

```bash
cd ~/llama.cpp
git pull
cmake -B build -DGGML_CUDA=ON -DBUILD_SHARED_LIBS=OFF
cmake --build build -j$(nproc) \
    --target llama-cli llama-mtmd-cli llama-server llama-gguf-split
```

更新完成后，重启服务使新版本生效：

```bash
cd ~/project/hf_models
./vl/llama_start.sh restart
```

