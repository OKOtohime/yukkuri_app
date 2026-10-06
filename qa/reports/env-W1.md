# 环境快照 W1（2026-10-07）

| 项 | 值 |
| --- | --- |
| 访问方式 | `ssh windows-dev`（密钥认证，BatchMode 可用） |
| 宿主 | R1 物理机上的 KVM 虚拟机 |
| 系统 | Windows 10 专业版 22H2（10.0.19045），中文界面，控制台编码 GBK（需设置 `PYTHONIOENCODING=utf-8`） |
| 资源 | 2 vCPU（AMD Ryzen 9 7940HX），8 GB 内存 |
| 声卡 | High Definition Audio Device；VB-Audio Virtual Cable（v45，被测输出线路）；VB-Audio Hi-Fi Cable × 3 个实例（2026-10-07 安装，见下方线路分配） |
| 代码 | `C:\yk\qa\yukkuri_app`（`git archive b993c40`） |
| Python | 3.12.14，venv：`C:\yk\qa\venv`（uv；`-e yukkuri_app` + `requirements-qa.txt`） |
| 依赖版本 | `qa/reports/freeze-W1.txt`（与 R1 相比只差 charset-normalizer、websockets 的小版本，并多了 colorama） |
| 数据目录 | `YUKKURI_DATA_DIR=C:\yk\qa\appdata-W1`（从 R1 复制，哈希与 R1 一致） |
| 第三方软件 | Discord 已安装（桌面快捷方式） |
| 旧的遗留内容 | `C:\yk\app`、`C:\yk\venv` 等是之前会话留下的，版本未知，**测试不使用** |

冒烟测试：在 W1 上，SenseVoice 对 AISHELL 中的一句话识别结果与参考文本完全一致；SileroVAD 切出 1 段。

## 虚拟线路分配

用 `qa/harness/win_cable_pairing.py` 实测：四条线路互相隔离，没有串音。

| 用途 | 播放端点（写入） | 采集端点（读取） |
| --- | --- | --- |
| 被测输出（应用 → 语音软件） | `CABLE Input (VB-Audio Virtual Cable)` | `CABLE Output (VB-Audio Virtual Cable)` |
| **注入**（测试 wav → 应用的麦克风） | `Hi-Fi Cable Input (2- VB-Audio Hi-Fi Cable)` | `Hi-Fi Cable Output (2- VB-Audio Hi-Fi Cable)` |
| **外放监测**（测试期间设为默认播放设备，充当"物理扬声器"） | `扬声器 (VB-Audio Hi-Fi Cable)` | `Hi-Fi Cable Output (VB-Audio Hi-Fi Cable)` |
| 备用 | `扬声器 (3- VB-Audio Hi-Fi Cable)` | `Hi-Fi Cable Output (3- VB-Audio Hi-Fi Cable)` |

测试工具注意事项：

1. **按名称匹配端点**，不要写死索引：设备索引会随枚举变化（重启前后已经变过）。
2. **丢弃陈旧缓冲**：VB 线路的环形缓冲会保留上一次的音频，新打开的采集流开头会读到残留数据。开流后至少丢弃 300ms。
3. **从 SSH 会话录音**：在线程里用 `sd.rec` 同时播放会报 WDM-KS 错误；改用回调式 `InputStream` 先开采集、再播放，就能正常工作。
4. 被测应用在 W1 上**必须显式指定 `--output-device`**（选 `CABLE Input`），否则会命中 P-05。只有专门测试自动选择逻辑（FR-OUT-05）的用例不指定。
