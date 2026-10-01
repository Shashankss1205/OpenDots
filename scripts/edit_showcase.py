"""Add title cards and accelerate only measured waits in a live recording.

Requires ffmpeg and Pillow. The raw recording is retained separately.
"""
import argparse
import json
from pathlib import Path
import subprocess

from PIL import Image, ImageDraw, ImageFont

W, H = 1440, 1000
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def card(path, closing=False):
    image = Image.new("RGB", (W, H), "#0d171a")
    draw = ImageDraw.Draw(image)
    for y in range(H):
        draw.line((0, y, W, y), fill=(13, int(24-y/H*6), int(27-y/H*5)))
    mint, muted = "#a6edce", "#91aaa7"
    def text(x, y, value, size=22, color="#eaf5ef", bold=False):
        draw.text((x, y), value, font=ImageFont.truetype(BOLD if bold else FONT, size), fill=color)
    for x, y, color in [(108,112,mint),(130,112,mint),(108,134,mint),(130,134,"#51826d")]:
        draw.ellipse((x,y,x+12,y+12),fill=color)
    text(166,101,"OpenDots",40,bold=True)
    text(109,240,"REAL CODEX  /  LIVE WORKFLOW",18,mint)
    text(104,294,"Goals persist.",76,bold=True)
    text(104,389,"Work moves forward.",76,bold=True)
    text(109,524,"Event → diagnosis → approval → tests → retained patch",25,muted)
    draw.line((109,598,1331,598),fill="#335147",width=1)
    if closing:
        metrics=[("03","completed jobs"),("220","upstream tests pass"),("02","retained repair patches")]
        for i,(value,label) in enumerate(metrics):
            x=109+i*419
            text(x,645,value,64,mint,bold=True)
            text(x,737,label,21,muted)
        text(109,882,"Actual React client/server checks. Verified follow-up. No upstream publication.",18,muted)
    else:
        for i,(name,description) in enumerate([("PROACTIVE","React accessibility Scout"),("REACTIVE","Cachetools upstream repair")]):
            x=109+i*622
            draw.rounded_rectangle((x,638,x+596,799),radius=16,fill="#142623",outline="#345a49",width=1)
            text(x+27,665,name,15,mint,bold=True)
            text(x+27,711,description,25,bold=True)
        text(109,882,"Recorded live. Chapter captions added. Only waiting intervals are accelerated.",18,muted)
    image.save(path)


def edit(args):
    directory = Path(args.directory).resolve()
    report = json.loads((directory / "showcase-report.json").read_text())
    marks = {m["name"]:m["seconds"] for m in report["marks"]}
    card(directory / "cover.png")
    card(directory / "closing.png", True)
    segments = [(0, marks["planning-wait"], 1),
                (marks["planning-wait"], marks["planning-finished"], 5),
                (marks["planning-finished"], marks["execution-wait"], 1),
                (marks["execution-wait"], marks["execution-finished"], 7),
                (marks["execution-finished"], marks["end"], 1)]
    filters = ["[0:v]fps=30,format=yuv420p,setsar=1,setpts=PTS-STARTPTS[intro]"]
    for index,(start,end,speed) in enumerate(segments):
        filters.append(f"[1:v]trim=start={start}:end={end},setpts=(PTS-STARTPTS)/{speed},fps=30,format=yuv420p,setsar=1[s{index}]")
    filters.append("[2:v]fps=30,format=yuv420p,setsar=1,setpts=PTS-STARTPTS[outro]")
    filters.append("[intro]"+"".join(f"[s{i}]" for i in range(len(segments)))+"[outro]concat=n=7:v=1:a=0[video]")
    command=["ffmpeg","-y","-loglevel","error","-loop","1","-t","4","-i",str(directory / "cover.png"),
             "-i",str(directory / "showcase-raw.webm"),"-loop","1","-t","5","-i",str(directory / "closing.png"),
             "-filter_complex",";".join(filters),"-map","[video]","-c:v","libx264","-preset","medium","-crf","19",
             "-pix_fmt","yuv420p","-movflags","+faststart",str(Path(args.output).resolve())]
    subprocess.run(command,check=True)
    timing={"intro_seconds":4,"outro_seconds":5,"segments":[{"raw_start":a,"raw_end":b,"speed":s} for a,b,s in segments],
            "expected_seconds":9+sum((b-a)/s for a,b,s in segments),"editing":"No fabricated UI state or substituted results. Only measured planning/execution waits accelerated."}
    (directory / "edit-report.json").write_text(json.dumps(timing,indent=2)+"\n")
    print(json.dumps(timing))


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory",required=True)
    parser.add_argument("--output",required=True)
    edit(parser.parse_args())
