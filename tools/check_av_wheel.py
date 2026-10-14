"""Smoke-test an installed PyAV wheel built by macos/build-av-wheel.sh or
windows/build-av-wheel.sh: no GPL encoders, the codecs the app and its
tests need, and a real AAC encode and decode. Exits non-zero on failure.
"""
import av, io, math, struct
# The licence itself is checked where it is decided: build-ffmpeg.sh
# refuses any configure that does not report LGPL.

# No GPL encoders, and the ones the app and its tests need.
for gone in ("libx264", "libx265"):
    try:
        av.codec.Codec(gone, "w")
    except Exception:
        pass
    else:
        raise SystemExit(f"{gone} is present")
for need in ("aac", "pcm_s16le", "libmp3lame"):
    av.codec.Codec(need, "w")

# Encode a second of tone as AAC in an m4a, then decode it back.
buf = io.BytesIO()
with av.open(buf, "w", format="ipod") as out:
    st = out.add_stream("aac", rate=16000)
    st.layout = "mono"
    frame = av.AudioFrame(format="s16", layout="mono", samples=16000)
    frame.sample_rate = 16000
    frame.planes[0].update(b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 16000)))
        for i in range(16000)))
    for p in st.encode(frame):
        out.mux(p)
    for p in st.encode(None):
        out.mux(p)
buf.seek(0)
with av.open(buf) as inp:
    n = sum(f.samples for f in inp.decode(audio=0))
assert n > 12000, n
print("aac round trip ok:", n, "samples; PyAV", av.__version__,
      "FFmpeg", av.library_versions["libavcodec"])
