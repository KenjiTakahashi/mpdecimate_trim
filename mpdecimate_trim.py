#!/usr/bin/env python3

import argparse
import os
import sys
import time
from functools import partial
from os import path
from shutil import rmtree
from subprocess import run
from tempfile import mkdtemp


sys.stdout = sys.stderr


cargs = argparse.ArgumentParser(description="Trim video(+audio) clip, based on output from mpdecimate filter")
cargs.add_argument("--keep", action="store_true", help="Keep original file")
cargs.add_argument("--skip", type=int, help="Skip trimming, if less than SKIP parts found")
cargs.add_argument("--vaapi", type=str, help="Use VA-API device for hardware accelerated transcoding")
cargs.add_argument("--vaapi-decimate", nargs="?", const=True, help="Use VA-API device for hardware accelerated decimate filter")
cargs.add_argument("--videotoolbox", action="store_true", help="Use Apple Video Toolbox for hardware accelerated transcoding")
cargs.add_argument("--videotoolbox-decimate", action="store_true",help="Use Apple Video Toolbox for hardware accelerated decimate filter")
cargs.add_argument("--debug", action="store_true", help="Do not remove anything even on successful run. Use loglevel debug for all ffmpeg calls")
cargs.add_argument("filepath", help="File to trim")
cargs = cargs.parse_args()


phase = "decimate"


def prof(s):
    e = time.time()
    print(f"The {phase} phase took {time.strftime('%H:%M:%S', time.gmtime(e - s))}")
    return e

def profd(f):
    def a(*args, **kwargs):
        s = time.time()
        r = f(*args, **kwargs)
        prof(s)
        return r
    return a


tempdir = mkdtemp(prefix="mpdecimate_trim.")


_vaapi_args = ["-hwaccel", "vaapi", "-hwaccel_device"]

def hwargs_decimate():
    if cargs.videotoolbox_decimate:
        return ["-hwaccel", "videotoolbox"]

    if not cargs.vaapi_decimate:
        return []

    if cargs.vaapi_decimate is True:
        if not cargs.vaapi:
            raise Exception("--vaapi-decimate set to use --vaapi device, but --vaapi not set")

        return [*_vaapi_args, cargs.vaapi]

    return [*_vaapi_args, cargs.vaapi_decimate]

def hwargs_transcode():
    return [*_vaapi_args, cargs.vaapi, "-hwaccel_output_format", "vaapi"] if cargs.vaapi else []

def _ffmpeg(fi, co, *args, hwargs=[]):
    log_file_base = path.join(tempdir,  f"{phase}")
    log_file_out = f"{log_file_base}.stdout.log"
    log_file_err = f"{log_file_base}.stderr.log"

    args = ["ffmpeg", *hwargs, "-i", fi, *args]

    args_for_log = " ".join(arg.replace(" ", "\\ ") for arg in args)
    print(f"The {phase} phase is starting with command `{args_for_log}`")
    print(f"Standard output capture: {log_file_out}")
    print(f"Standard error capture: {log_file_err}")

    with open(log_file_out, "w") as out, open(log_file_err, "w") as err:
        result = run(args, stdout=out, stderr=err)
        out.flush()
        err.flush()
        if result.returncode == 0:
            if co:
                return log_file_err
            return

        print(f"The {phase} phase failed with code {result.returncode}")
        print("See above for where to look for details")

    sys.exit(3)

ffmpeg = profd(partial(_ffmpeg, cargs.filepath))


def trim(s, e, i, b1=b"v", b2=b""):
    trim = b"%f:%f" % (s, e) if e is not None else b"%f" % s
    return b"[0:%b]%btrim=%b,%bsetpts=PTS-STARTPTS[%b%d];" % (b1, b2, trim, b2, b1, i)

atrim = partial(trim, b1=b"a", b2=b"a")


def get_dframes(mpdecimate_fn):
    dframes = []
    with open(mpdecimate_fn) as mpdecimate:
        for line in mpdecimate:
            try:
                drop_count = int(line.split(" drop_count:")[1])
            except IndexError:
                continue
            pts_time = line.split("pts_time:")[1].split(" ")[0]

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


dframes2 = get_dframes(ffmpeg(
    True,
    "-vf", "mpdecimate=hi=576",
    "-loglevel", "debug",
    "-f", "null", "-",
    hwargs=hwargs_decimate(),
))
if cargs.skip and len(dframes2) < cargs.skip:
    print(f"less than {cargs.skip} parts detected, avoiding re-encode")
    sys.exit(2)


phase = "filter creation"


filter_fn = path.join(tempdir, "mpdecimate_filter")

@profd
def write_filter():
    print(f"The {phase} phase is starting")
    print(f"Filter definition: {filter_fn}")

    with open(filter_fn, "wb") as fg:
        for i, (s, e) in enumerate(dframes2):
            fg.write(trim(s, e, i))
            fg.write(b"\n")
            fg.write(atrim(s, e, i))
            fg.write(b"\n")
        fg.write(b"".join(b"[v%d][a%d]" % (i, i) for i in range(len(dframes2))))
        fg.write(b"concat=n=%d:a=1[vout][aout]" % len(dframes2))
        fg.flush()

write_filter()

phase = "transcode"


def get_enc_args():
    if cargs.videotoolbox:
        return ["hevc_videotoolbox", "-q:v", "65"]
    if cargs.vaapi:
        return ["hevc_vaapi", "-qp", "23"]
    return ["libx265", "-preset", "fast", "-crf", "30"]

fout, ext = path.splitext(cargs.filepath)
ffmpeg(
    False,
    "-filter_complex_script", filter_fn,
    "-map", "[vout]", "-map", "[aout]",
    "-c:v", *get_enc_args(),
    f"{fout}.trimmed{ext}",
    hwargs=hwargs_transcode(),
    *(["-loglevel", "debug"] if cargs.debug else []),
)


if cargs.debug:
    print("Debug enabled, not removing anything")
    sys.exit(0)

if not cargs.keep:
    print(f"Removing the original file at {cargs.filepath}")
    os.remove(cargs.filepath)

rmtree(tempdir)
