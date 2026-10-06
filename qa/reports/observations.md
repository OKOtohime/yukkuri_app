# 观察项汇总

按测试计划 6.2 节：观察项（OBS）是实现与附录 A 实现约定或代码自述不一致、但没有造成可观察需求偏差的情况。不分级，不计入准出统计。编号按 WP 分段（WP-02 用 OBS-201 起）。

## WP-02（2026-10-07）

| ID | 现象 | 线索 | 证据 | 后续 |
| --- | --- | --- | --- | --- |
| OBS-201 | 纯 NumPy 重采样的**升采样**通带衰减明显：8k→48k 时 1 kHz −0.5 dB、2 kHz −2.2 dB、3 kHz −7.6 dB、3.5 kHz −12.7 dB；16k→48k→16k 往返后 3 kHz 能量下降 8.1 dB（测试计划 4.2 的蜕变关系不成立）。降采样（采集路径）到 5 kHz 都在 −0.4 dB 以内 | IMPL-04（代码自述为"高质量重采样"） | `qa/reports/wp02/resample_response_numpy.json`；`test_fr_aud_03_resample_roundtrip_energy[3000/6000]` | Windows 播放器把 AquesTalk 的 8k 音频升到 CABLE 的 48k 正是走这条路径，Windows 上的合成音会比 Linux 发闷。FR-OUT-04 只要求音调、速度正确，交 **WP-11** 结合听感和频谱对比评估 |
| OBS-202 | 采集回调按 50ms 块逐块重采样，块边界处时间轴跳变：正弦 SINAD 只有 13~35 dB（3 kHz 时约 13 dB），整段重采样为 43~76 dB | IMPL-04 | `qa/reports/wp02/resample_fidelity_numpy.json` | 对识别没有可观察影响：FR-AUD-03 识别对等用例中，48k 立体声 CER 变化 −0.31pp、44.1k 单声道 0.00pp（`aud03_parity_*.json`） |
| OBS-203 | `.env` 解析：`export KEY=value` 行被应用忽略，`run.sh`（`. .env`）却能读到；行尾 `# 注释` 被并入值 | IMPL-20 | 探索性测试 | README 示例格式不受影响 |
| OBS-204 | `yukkuri.__version__` 为 `0.2.0`，`pyproject.toml` 为 `0.3.0` | — | `yukkuri/__init__.py` | — |
| OBS-205 | 麦克风增益下限 0.1 只在运行中 `set_gain()` 时生效，构造时（CLI `--mic-gain`、GUI 启动）不钳位 | IMPL-03 | `yukkuri/audio/capture.py:29` 与 `:41` | 越界值的可观察后果已记入 DEF-207 |
| OBS-206 | `--check` 与加载侧的判定条件不完全相同：VAD 只认 `<根>/silero_vad.onnx`（加载侧还认 `models/`）；Vosk 要求 `am/final.mdl` 存在（加载侧只要目录存在） | — | `model_downloader.check_model_status`、`config.find_*` | 官方工具产生的布局两者结论一致（`test_fr_cfg_03_check_agrees_with_loader` 全部通过）。只有手工摆放的目录会出现差异；残缺的 Vosk 目录会被加载侧选中，交 **WP-04/09** 在 QR-REL-03 中确认 |
| OBS-207 | `scipy` 不在依赖中，默认安装永远走纯 NumPy 重采样分支；scipy 分支（`resample_poly`）只在用户自行安装 scipy 时启用 | IMPL-04 | `pyproject.toml`、`freeze-R1.txt` | 本 WP 的重采样用例按"当前可用分支"参数化，装了 scipy 时自动覆盖两个分支 |

## 交给其他 WP 的线索（不是 OBS，也未判定）

| 给 | 线索 |
| --- | --- |
| WP-03 | ASR 输出只有标点（"."）时，G2P 产生 `。`，AquesTalk 合成出 32ms 全零 WAV 并推流（听不到）。按 FR-G2P-13 判定是否算"产生合成输出"。另：`Yeah.` → `いえあは。`（末尾 h 被读出） |
| WP-04 | README"密钥配置方式"写"按优先级由高到低：.env、环境变量、CLI"，与 FR-TTS-05 基线相反（文档缺陷候选）。`--speed` 越界同样不校验（BL-01） |
| WP-05 | `pipeline.stop()` 先冲刷 VAD 并投递最后一句，随即 `player.stop()`（工作线程 join 最多 1s，且为守护线程），最后一句可能播不完或被丢弃（FR-VAD-07）。`LinuxPipeWireMicManager.sink_exists()` 用子串匹配（`yukkuri_sink` 会匹配 `yukkuri_sink2`），FR-VMIC-01 中注意 |
| WP-09 | `LinuxPipeWirePlayer._is_sink_available()` 在 `pactl` 不存在或执行失败时返回 True，随后直接执行 `pw-play --target <sink>`；目标 sink 不存在时 pw-play 是否回落到默认设备，需在 QR-SEC-01 / FR-OUT-03 的故障注入中验证 |
| WP-07 | 基线参考：SenseVoice 对 AISHELL-1 清单前 100 句的 CER 为 4.89%（本 WP 的识别对等用例，16k 直接解码，归一化见 `qa/integration/wp02_util.py`） |
| WP-11 | DEF-211 使 W1 上 SenseVoice/VAD 模式没有任何语音输出，Windows 上依赖语音输入的 L3 用例在修复前全部受阻（文字播报不受影响）。OBS-201 同上 |
