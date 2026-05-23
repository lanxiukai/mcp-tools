# ASR Pipeline — 播客长音频转写

离线 CLI 管线，将 2-3 小时播客转写为带**说话人标注**和**词级时间戳**的结构化文本。480s 分块策略，12GB 显存可稳定运行。

## 文件

| 文件 | 用途 |
|---|---|
| `pipeline.py` | 管线入口（编排 transcribe → diarize → merge） |
| `transcribe.py` | ASR 转录阶段 |
| `diarize.py` | 说话人分离阶段（pyannote，需 HF_TOKEN） |
| `merge.py` | 合并转录 + 说话人标注 |
| `preprocess.py` | 音频预处理 |

## 用法

```bash
# 基本用法
conda run -n qwen-asr python asr-pipeline/pipeline.py podcast.mp3 --language English -o ./output/

# 长音频加速（跳过词级时间戳，提速 4×+）
python asr-pipeline/pipeline.py long.mp3 --language English --no-timestamps

# 中文播客 + 限定说话人数
python asr-pipeline/pipeline.py interview.mp3 --language Chinese --num-speakers 3
```

## 关键参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--no-timestamps` | off | 跳过词级时间戳 |
| `--no-diarize` | off | 跳过说话人分离 |
| `--num-speakers N` | 自动 | 限定最大说话人数 |
| `--max-new-tokens` | 4096 | 生成 token 上限 |

## 产物

JSON（metadata + segments + full_text）、SRT（字幕）、TXT（纯文本）
