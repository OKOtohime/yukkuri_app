"""W1 虚拟线路配对检查：向每个播放端点放 1kHz 音，测各采集端点的 RMS（丢弃开流后的陈旧缓冲）。用法: python win_cable_pairing.py"""
import time, numpy as np, sounddevice as sd
ha = sd.query_hostapis()
devs = [(i, d) for i, d in enumerate(sd.query_devices()) if "WASAPI" in ha[d["hostapi"]]["name"]]
outs = [(i, d) for i, d in devs if d["max_output_channels"] > 0 and ("Hi-Fi" in d["name"] or "CABLE Input" in d["name"])]
ins = [(i, d) for i, d in devs if d["max_input_channels"] > 0 and ("Hi-Fi" in d["name"] or "CABLE Output" in d["name"])]
for oi, od in outs:
    res = []
    for ii, idd in ins:
        chunks = []
        try:
            istream = sd.InputStream(samplerate=int(idd["default_samplerate"]), channels=idd["max_input_channels"], device=ii,
                                     callback=lambda x, f, t, s: chunks.append(x.copy()))
            istream.start()
        except Exception as e:
            res.append(f"{ii}:RECERR"); continue
        time.sleep(0.5)
        skip = len(chunks)
        sr, ch = int(od["default_samplerate"]), od["max_output_channels"]
        t = np.arange(int(sr * 0.6)) / sr
        tone = np.repeat((0.3 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)[:, None], ch, axis=1)
        try:
            sd.play(tone, samplerate=sr, device=oi); sd.wait()
        except Exception:
            res.append(f"{ii}:PLAYERR")
        time.sleep(0.2); istream.stop(); istream.close()
        x = np.concatenate(chunks[skip:]) if len(chunks) > skip else np.zeros(1)
        time.sleep(0.5)
        res.append(f"{ii}:{float(np.sqrt(np.mean(x ** 2))):.4f}")
    print(f"play {oi} [{od['name']}] -> " + " ".join(res))
print("captures:", [(i, d["name"]) for i, d in ins])
