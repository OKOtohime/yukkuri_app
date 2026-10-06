# 环境快照 R1 / R2（2026-10-07）

| 项 | 值 |
| --- | --- |
| 被测版本 | b993c40（工作区：qa/test-planning 分支，产品代码未改动） |
| 硬件 | AMD Ryzen 9 7940HX（16 核 32 线程），30 GB 内存 |
| 系统 | CachyOS，内核 7.2.8-2-cachyos |
| 音频 | PipeWire 1.6.9（pipewire-pulse）；pactl、pw-play、pw-record、pw-loopback 可用 |
| 默认设备（测试前） | sink：`bluez_output.20_C1_1C_80_D8_A4.1`（蓝牙耳机）；source：`alsa_input.pci-0000_06_00.6.analog-stereo` |
| Python | 3.12.13，venv：`~/.cache/yukkuri-qa/venv-R1`（`uv pip install -e . -r qa/requirements-qa.txt`） |
| 依赖版本 | `qa/reports/freeze-R1.txt` |
| 数据目录 | `YUKKURI_DATA_DIR=~/.cache/yukkuri-qa/appdata-R1`（由 `setup_models.py --all` 下载） |
| R2 | 同上，`taskset -c 0,1` |

## 模型

| 文件 | sha256 |
| --- | --- |
| silero_vad.onnx | 9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6 |
| sensevoice/model.int8.onnx | c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51 |
| sensevoice/tokens.txt | f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc |
| Vosk | model_zh、model_ja、model_en（官方 small 模型） |
| AquesTalk | 官方评价版 aqtk1_lnx_200，9 种声线，位于 `libs/aquestalk/lib64/<voice>/` |

`setup_models.py --check`：VAD、SenseVoice、Vosk ja/en 就绪；Vosk zh 显示"未安装"（见 WP-01 记录的预备发现 P-01）。

## 注意

- 仓库根目录的 `.venv`（2026-10-02 创建）缺少 `sherpa-onnx-core`，sherpa_onnx 会链接到系统的 `/usr/lib/libonnxruntime.so`（onnxruntime-opt-cuda 1.29），导入失败。测试一律使用 venv-R1。
- 系统测试会修改 PipeWire 拓扑（创建虚拟设备、临时切换默认 sink）。teardown 必须恢复默认设备，并卸载自己加载的模块。
