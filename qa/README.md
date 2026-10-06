# qa/ — yukkuri_app 测试工程

测试依据：`docs/requirements.md`（需求）、`docs/test-plan.md`（计划，工作包见第 9 节）。
本目录与上游 `tests/` 分开，不修改产品代码和 `pyproject.toml`。

## 环境速查

| 环境 | Python 解释器 | 应用数据目录（`YUKKURI_DATA_DIR`） | 备注 |
| --- | --- | --- | --- |
| R1 | `~/.cache/yukkuri-qa/venv-R1/bin/python` | `~/.cache/yukkuri-qa/appdata-R1` | 不要用仓库根目录的 `.venv`（缺 `sherpa-onnx-core`，sherpa_onnx 无法导入） |
| R2 | 同 R1，用 `taskset -c 0,1` 运行 | 同 R1 | |
| W1 | `C:\yk\qa\venv\Scripts\python.exe` | `C:\yk\qa\appdata-W1` | 代码在 `C:\yk\qa\yukkuri_app`（b993c40）；`ssh windows-dev`；设置 `PYTHONIOENCODING=utf-8` |
| R3 | 未准备（`fedora-dev` 未开机） | — | WP-11 开始前准备 |

**R1 和 W1 跑在同一台物理机上**（W1 是 R1 上的 KVM 虚拟机），性能测试不要在两个环境同时进行。

## 常用命令

```bash
# R1：安装测试依赖（已完成）
uv pip install --python ~/.cache/yukkuri-qa/venv-R1/bin/python -e . -r qa/requirements-qa.txt

# R1：运行 QA 测试
YUKKURI_DATA_DIR=~/.cache/yukkuri-qa/appdata-R1 ~/.cache/yukkuri-qa/venv-R1/bin/python -m pytest qa -m "not soak"

# 重新生成评测语料（清单已入库，音频不入库）
~/.cache/yukkuri-qa/venv-R1/bin/python qa/eval/asr/prepare_corpora.py all
~/.cache/yukkuri-qa/ttsenv/bin/python qa/eval/asr/gen_mixed_tts.py
```

## 数据

| 清单（入库） | 音频（不入库，`qa/data/corpora/`） | 内容 |
| --- | --- | --- |
| `data/manifests/aishell1_test.tsv` | `aishell1_test/` | AISHELL-1 test 抽样 500 句（种子 20261007） |
| `data/manifests/librispeech_test_clean.tsv` | `librispeech_test_clean/` | LibriSpeech test-clean 抽样 500 句 |
| `data/manifests/cv21_ja_test.tsv` | `cv21_ja_test/` | Common Voice 21 ja test 抽样 500 句（排除 down_votes > 0） |
| `data/manifests/musan_noise.tsv` | `musan_noise/` | MUSAN noise 全部 930 条 |
| `data/manifests/mixed_tts.tsv` | `mixed_tts/` | 中英混说 TTS 子集 100 句（edge-tts 和 Kokoro 各 4 个音色，加混响和噪声） |
| `data/manifests/system_inject.tsv` | 引用上述音频 | 系统测试注入集 115 条（三语 1~6s 共 100 条、> 8s 长句 10 条、纯噪声 5 条） |
| `data/mixed_zh_en_script.tsv` | `mixed_real/`（待录制） | 混说脚本 130 句：MIX001~030 由真人录制，MIX031~130 用于 TTS |
| `data/g2p_golden.csv` | — | G2P 黄金集 215 条（WP-03，测试负责人评审定稿）；中文期望写拼音，经映射表展开 |
| `data/tgscc_level1.txt` | — | 《通用规范汉字表》一级字 3500 字（QR-FUNC-06） |
| `data/aquestalk_alphabet.json` | — | AquesTalk 接受符号表（`eval/g2p/probe_alphabet.py` 在 R1 实测生成，L1 预判用） |

所有音频均为 16kHz 单声道 PCM16，由 ffmpeg 转换（不使用被测代码的重采样器）。
原始下载和工具缓存在 `~/.cache/yukkuri-qa/`（`raw/`、`corpora/`、`tts/`、`ttsenv/`、`win/`）。

## 约定

- 用例命名：`test_<需求ID小写>_<场景>`
- pytest 标记：见 `conftest.py`
- 缺陷：`reports/defects/DEF-xxx.md`；观察项：`reports/observations.md`
- 编号按 WP 分段，避免并行会话冲突：WP-nn 的缺陷用 DEF-nn1 起，观察项用 OBS-nn1 起（WP-02：DEF-201、OBS-201）
- 已登记缺陷的用例用 `xfail(strict=True, raises=AssertionError, reason="DEF-xxx")` 标注，缺陷修复后会 XPASS 并让套件失败
- WP-02 的测量产物在 `reports/wp02/`；QR-MNT 工具：`eval/mnt/run_upstream_clean.py`（干净环境跑上游单测）、`eval/mnt/coverage_scope.py`（按需求口径统计覆盖率）
- WP-03 的测量产物在 `reports/wp03/`；G2P 工具在 `eval/g2p/`：`run_qr_func_07.py`（QR-FUNC-07 全量评测）、`fuzz_corpus.py`（固定种子模糊语料）、`aqtk_worker.py`/`aqtk_client.py`（子进程合成，崩溃可定位）、`probe_alphabet.py`
- AquesTalk 错误码在 Windows 评价版 DLL 上语义不同：未定义记号写回 1 起算字节位置而不是 105，判定合成失败原因时不要只看返回码（见 DEF-301）
