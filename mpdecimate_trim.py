#!/usr/bin/env python3

import argparse
import os
import sys
import time
from functools import partial
from os import path
from subprocess import run
from tempfile import NamedTemporaryFile


cargs = argparse.ArgumentParser(description="Trim video(+audio) clip, based on output from mpdecimate filter")
cargs.add_argument("--skip", type=int, help="Skip trimming, if less than SKIP parts found")
cargs.add_argument("--keep", action="store_true", help="Keep original file")
cargs.add_argument("--vaapi", type=str, help="Use VA-API device for hardware accelerated transcoding")
cargs.add_argument("filepath", help="File to trim")
cargs = cargs.parse_args()


def prof(s):
    e = time.time()
    print(time.strftime("%H:%M:%S", time.gmtime(e - s)))
    return e


def profd(f):
    def a(*args, **kwargs):
        s = time.time()
        r = f(*args, **kwargs)
        prof(s)
        return r
    return a


def _ffmpeg(fi, co, *args, vaapi=None):
    hwargs = ["-hwaccel", "vaapi", "-hwaccel_device", vaapi, "-hwaccel_output_format", "vaapi"] if vaapi else []
    return run(["ffmpeg", *hwargs, "-i", fi, *args], check=True, capture_output=co)


ffmpeg = profd(partial(_ffmpeg, cargs.filepath))


def trim(s, e, i, b1=b"v", b2=b""):
    trim = b"%f:%f" % (s, e) if e is not None else b"%f" % s
    return b"[0:%b]%btrim=%b,%bsetpts=PTS-STARTPTS[%b%d];" % (b1, b2, trim, b2, b1, i)


atrim = partial(trim, b1=b"a", b2=b"a")


def get_dframes(mpdecimate):
    dframes = []
    for line in mpdecimate.split(b"\n"):
        try:
            drop_count = int(line.split(b" drop_count:")[1])
        except IndexError:
            continue
        pts_time = line.split(b"pts_time:")[1].split(b" ")[0]

        if drop_count == -1:
            pts_time = float(pts_time)
            if dframes:
                ff1, ff2 = dframes[-1]
                if pts_time - ff2 < 10:
                    dframes[-1][1] = pts_time
                    continue

            dframes.append([pts_time])
        elif drop_count == 1 and dframes:
            pts_time = float(pts_time)
            if len(dframes[-1]) == 2:
                dframes[-1][1] = pts_time
            else:
                dframes[-1].append(pts_time)

    if len(dframes[-1]) == 1:
        dframes[-1].append(None)
    elif drop_count < 0:
        dframes[-1][1] = None

    return [[f1, f2] for f1, f2 in dframes if f2 is None or f2 - f1 > 1]


dframes2 = get_dframes(ffmpeg(True, "-vf", "mpdecimate=hi=576", "-loglevel", "debug", "-f", "null", "-").stderr)
if cargs.skip and len(dframes2) < cargs.skip:
    print("less than 2 parts detected, avoiding re-encode")
    sys.exit(2)


def get_enc_args():
    if cargs.vaapi:
        return ["hevc_vaapi", "-qp", "23"]
    return ["libx265", "-preset", "fast", "-crf", "30"]

with NamedTemporaryFile(prefix="mpdecimate_trim.") as fg:
    for i, (s, e) in enumerate(dframes2):
        fg.write(trim(s, e, i))
        fg.write(b"\n")
        fg.write(atrim(s, e, i))
        fg.write(b"\n")
    fg.write(b"".join(b"[v%d][a%d]" % (i, i) for i in range(len(dframes2))))
    fg.write(b"concat=n=%d:a=1[vout][aout]" % len(dframes2))
    fg.flush()

    fout, ext = path.splitext(cargs.filepath)
    ffmpeg(
        False,
        "-filter_complex_script", fg.name,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", *get_enc_args(),
        f"{fout}.trimmed{ext}",
        vaapi=cargs.vaapi,
    )

    if not cargs.keep:
        os.remove(cargs.filepath)
