"""Draws pipeline-{light,dark}.svg next to this file (the README's "How it works" graphic).

    python3 docs/images/make_pipeline.py
"""
import sys
from pathlib import Path

THEMES = {
    "light": dict(text="#1F2328", muted="#59636E", box="#F6F8FA", line="#D0D7DE", arrow="#8C959F",
                  sub="#FFFFFF", accent="#0969DA"),
    "dark": dict(text="#E6EDF3", muted="#9198A1", box="#161B22", line="#30363D", arrow="#6E7681",
                 sub="#0D1117", accent="#4493F8"),
}
MIC, SYS, APP, REC = "#EB6834", "#2A78D6", "#1BAF7A", "#E5484D"
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"


def svg(t: dict) -> str:
    W, H = 1000, 262
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 38 {W} {H}" width="{W}" height="{H}" '
           f'font-family="{FONT}" role="img" aria-label="Sources are recorded, transcribed and diarized in parallel, '
           f'summarized, then filed to Obsidian">',
           '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
           f'<path d="M0,0 L10,5 L0,10 z" fill="{t["arrow"]}"/></marker></defs>']

    def box(x, y, w, h, fill=None, stroke=None, r=12):
        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill or t["box"]}" '
                   f'stroke="{stroke or t["line"]}" stroke-width="1.5"/>')

    def text(x, y, s, size=14, weight=400, color=None, anchor="start"):
        out.append(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{color or t["text"]}" '
                   f'text-anchor="{anchor}">{s}</text>')

    def arrow(x1, x2, y=150):
        out.append(f'<line x1="{x1}" y1="{y}" x2="{x2}" y2="{y}" stroke="{t["arrow"]}" stroke-width="2" '
                   f'marker-end="url(#a)"/>')

    # 1. Sources
    text(20, 62, "Sources", 13, 600, t["muted"])
    for i, (label, color) in enumerate([("Microphone", MIC), ("System audio", SYS), ("Apps (Zoom, Teams)", APP)]):
        y = 80 + i * 46
        box(20, y, 190, 36, r=18)
        out.append(f'<circle cx="40" cy="{y + 18}" r="6" fill="{color}"/>')
        text(56, y + 23, label, 14)
    arrow(214, 252)

    # 2. Record
    box(256, 80, 160, 128)
    out.append(f'<circle cx="279" cy="106" r="7" fill="{REC}"/>')
    text(294, 111, "Record", 16, 600)
    text(276, 140, "Earshot app", 13, 400, t["muted"])
    text(276, 160, "a track per source", 13, 400, t["muted"])
    text(276, 180, "mixed to one WAV", 13, 400, t["muted"])
    text(336, 232, "on Record, or when a", 12, 400, t["muted"], "middle")
    text(336, 248, "Zoom or Teams call starts", 12, 400, t["muted"], "middle")
    arrow(420, 458)

    # 3. Transcribe + speakers, in parallel
    box(462, 62, 210, 164)
    text(567, 84, "In parallel, on the GPU", 12, 600, t["muted"], "middle")
    for i, (title, sub) in enumerate([("Transcribe", "MLX Whisper"), ("Speakers", "pyannote")]):
        y = 96 + i * 62
        box(476, y, 182, 50, fill=t["sub"], r=9)
        text(492, y + 22, title, 15, 600)
        text(492, y + 40, sub, 13, 400, t["muted"])
    arrow(676, 714)

    # 4. Summarize
    box(718, 80, 140, 128)
    text(736, 111, "Summarize", 16, 600)
    text(736, 140, "meeting, lecture,", 13, 400, t["muted"])
    text(736, 160, "brief or your own", 13, 400, t["muted"])
    text(736, 180, "local or Claude*", 13, 400, t["muted"])
    arrow(862, 892)

    # 5. Obsidian
    box(896, 80, 94, 128, stroke=t["line"])
    out[-1] = out[-1].replace('stroke-width="1.5"', 'stroke-width="1.5" stroke-dasharray="5 4"')
    text(943, 111, "Obsidian", 15, 600, None, "middle")
    text(943, 140, "a note in", 13, 400, t["muted"], "middle")
    text(943, 160, "your vault", 13, 400, t["muted"], "middle")
    text(943, 180, "(optional)*", 13, 400, t["muted"], "middle")

    text(20, 288, "Audio never leaves your Mac.  * Claude, if you choose it for summaries or Obsidian filing, sees transcript text only.",
         12, 400, t["muted"])
    out.append("</svg>")
    return "\n".join(out) + "\n"


dest = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent
for name, theme in THEMES.items():
    (dest / f"pipeline-{name}.svg").write_text(svg(theme))
    print(dest / f"pipeline-{name}.svg")
