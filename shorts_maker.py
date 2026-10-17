#!/usr/bin/env python3
"""YouTube लिंक -> कैप्शन वाले 9:16 Shorts.

Setup:
  pip install yt-dlp faster-whisper anthropic
  (ffmpeg इंस्टॉल होना चाहिए)
  (optional) export ANTHROPIC_API_KEY=...   # key na ho to free mode chalta hai

Use:
  python shorts_maker.py "https://youtube.com/watch?v=XXXX" -n 3 -d 45
"""
import argparse, json, os, subprocess
from pathlib import Path


def run(cmd):
    subprocess.run(cmd, check=True)


def download(url, out):
    # पहले से डाउनलोड हो चुकी हो तो दोबारा नहीं
    for ext in ("mp4", "mkv", "webm"):
        if (out / f"source.{ext}").exists():
            return out / f"source.{ext}"
    base = ["yt-dlp", "--merge-output-format", "mp4", "-o", str(out / "source.%(ext)s")]
    fmt = ["-f", "bv*[height<=1080]+ba/b[height<=1080]/b"]
    tries = [
        base + fmt + [url],
        base + fmt + ["--js-runtimes", "node", url],
        base + fmt + ["--js-runtimes", "deno", url],
        base + fmt + ["--extractor-args", "youtube:player_client=tv,ios", url],
        base + ["-f", "b", "--extractor-args", "youtube:player_client=android", url],
    ]
    for t in tries:
        if subprocess.run(t).returncode == 0:
            for ext in ("mp4", "mkv", "webm"):
                if (out / f"source.{ext}").exists():
                    return out / f"source.{ext}"
    raise SystemExit(
        "YouTube ne download block kiya. Video phone mein download karke Colab mein "
        "upload karein aur URL ki jagah uska path dein, jaise /content/video.mp4")


def load_audio(video):
    """ffmpeg से सीधे audio (16kHz mono) लेता है, ताकि PyAV की दिक्कत न आए।"""
    import numpy as np
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video), "-f", "f32le",
         "-ac", "1", "-ar", "16000", "-"],
        check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def transcribe(video, size):
    from faster_whisper import WhisperModel
    try:
        model = WhisperModel(size, device="cuda", compute_type="float16")
    except Exception:
        model = WhisperModel(size, compute_type="int8")
    segs, _ = model.transcribe(load_audio(video), word_timestamps=True, vad_filter=True)
    words, lines = [], []
    for s in segs:
        lines.append((s.start, s.end, s.text.strip()))
        for w in s.words:
            words.append((w.start, w.end, w.word.strip()))
    return words, lines


HOOKS = ["secret", "mistake", "never", "money", "truth", "best", "worst", "free",
         "सच", "राज", "राज़", "गलती", "कभी", "पैसा", "पैसे", "सबसे", "फ्री", "बड़ा",
         "sach", "raaz", "galti", "kabhi", "paisa", "sabse"]


def pick_clips_free(lines, n, dur):
    """बिना API key: जोश, सवाल, नंबर और हुक-शब्दों से स्कोर करके हिस्से चुनता है।"""
    cands = []
    for i in range(len(lines)):
        st = lines[i][0]
        for j in range(i, len(lines)):
            en = lines[j][1]
            if en - st > dur * 1.25:
                break
            if en - st >= dur * 0.75:
                txt = " ".join(l[2] for l in lines[i:j + 1])
                low = txt.lower()
                wps = len(txt.split()) / max(en - st, 1)
                score = (wps * 2 + (txt.count("?") + txt.count("!")) * 1.5
                         + sum(low.count(h) for h in HOOKS) * 1.5
                         + sum(c.isdigit() for c in txt) * 0.2
                         + (3 if lines[i][2].strip().endswith("?") else 0))
                cands.append((score, st, en, lines[i][2][:60]))
    cands.sort(reverse=True)
    picked = []
    for sc, st, en, t in cands:
        if all(en <= p["start"] or st >= p["end"] for p in picked):
            picked.append({"start": st, "end": en, "title": t})
        if len(picked) == n:
            break
    return picked


def ts(x):
    h, m, s = int(x // 3600), int(x % 3600 // 60), x % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def make_ass(words, start, end, path, font, per=3):
    head = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
        "[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,OutlineColour,"
        "BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,"
        "BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
        f"Style: Default,{font},72,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,5,0,2,60,60,520,1\n\n"
        "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )
    ws = [w for w in words if w[0] >= start and w[1] <= end]
    ev = []
    for i in range(0, len(ws), per):
        g = ws[i:i + per]
        a, b = g[0][0] - start, g[-1][1] - start
        ev.append(f"Dialogue: 0,{ts(a)},{ts(b)},Default,,0,0,0,,{' '.join(x[2] for x in g)}")
    Path(path).write_text(head + "\n".join(ev), encoding="utf-8")


def cut(src, clip, words, out, font):
    ass = out.with_suffix(".ass")
    make_ass(words, clip["start"], clip["end"], ass, font)
    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(clip["start"]), "-i", str(src.resolve()),
         "-t", str(clip["end"] - clip["start"]),
         "-vf", f"crop=ih*9/16:ih,scale=1080:1920,ass={ass.name}",
         "-c:v", "libx264", "-crf", "20", "-c:a", "aac", out.name],
        cwd=out.parent, check=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("url")
    p.add_argument("-n", type=int, default=3, help="कितने Shorts")
    p.add_argument("-d", type=int, default=45, help="हर Short लगभग कितने सेकंड")
    p.add_argument("--model", default="small", help="whisper: base/small/medium")
    p.add_argument("--font", default="Noto Sans Devanagari")
    p.add_argument("-o", default="shorts_out")
    a = p.parse_args()

    out = Path(a.o)
    out.mkdir(exist_ok=True)
    if Path(a.url).exists():
        src = Path(a.url)
    else:
        src = download(a.url, out)
    words, lines = transcribe(src, a.model)
    clips = []
    if os.getenv("ANTHROPIC_API_KEY"):
        try:
            clips = pick_clips(lines, a.n, a.d)
        except Exception as e:
            print("Claude fail hua, free mode use ho raha hai:", e)
    if not clips:
        clips = pick_clips_free(lines, a.n, a.d)
    for i, c in enumerate(clips, 1):
        f = out / f"short_{i}.mp4"
        cut(src, c, words, f, a.font)
        print(f"✔ {f}  ({c['start']:.0f}-{c['end']:.0f}s)  {c['title']}")


if __name__ == "__main__":
    main()
