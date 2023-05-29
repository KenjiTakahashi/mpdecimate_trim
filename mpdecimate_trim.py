#!/usr/bin/env python3

import argparse
import os
import re
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
cargs.add_argument("--output-to-cwd", action="store_true", help="")
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


mpdecimate_fn = ffmpeg(
    True,
    "-vf", "mpdecimate=hi=576",
    "-loglevel", "debug",
    "-f", "null", "-",
    hwargs=hwargs_decimate(),
)


phase = "filter creation"


re_decimate = re.compile(r"^.* (keep|drop) pts:(\d+) pts_time:(\d+(?:\.\d+)?) drop_count:-?\d+$")
re_audio_in = re.compile(r"^\s*Input stream #\d:\d \(audio\): \d+ packets read \(\d+ bytes\); \d+ frames decoded \(\d+ samples\);\s*$")
re_audio_out = re.compile(r"^\s*Output stream #\d:\d \(audio\): \d+ frames encoded \(\d+ samples\); \d+ packets muxed \(\d+ bytes\);\s*$")

def get_frames_to_keep(mpdecimate_fn):
    to_keep = []
    dropping = True

    has_audio_in = False
    has_audio_out = False

    with open(mpdecimate_fn) as mpdecimate:
        for line in mpdecimate:
            values = re_decimate.findall(line)
            if not values:
                has_audio_in = has_audio_in or re_audio_in.fullmatch(line)
                has_audio_out = has_audio_out or re_audio_out.fullmatch(line)
                continue
            values = values[0]
            keep = values[0] == "keep"
            pts_time = float(values[2])

            if keep and dropping:
                to_keep.append([pts_time, None])
                dropping = False
            elif not keep and not dropping:
                to_keep[-1][1] = pts_time
                dropping = True

                if cargs.debug:
                    print(f"Keeping times {to_keep[-1][0]}-{to_keep[-1][1]}")

    return (to_keep, bool(has_audio_in and has_audio_out))

def trim(s, e, i, b1=b"v", b2=b""):
    trim = b"%f:%f" % (s, e) if e is not None else b"%f" % s
    return b"[0:%b]%btrim=%b,%bsetpts=PTS-STARTPTS[%b%d];" % (b1, b2, trim, b2, b1, i)

atrim = partial(trim, b1=b"a", b2=b"a")


filter_fn = path.join(tempdir, "mpdecimate_filter")

@profd
def write_filter():
    print(f"The {phase} phase is starting")
    print(f"Filter definition: {filter_fn}")

    frames_to_keep, has_audio = get_frames_to_keep(mpdecimate_fn)
    print(has_audio)
    if cargs.skip and len(frames_to_keep) < cargs.skip:
        print(f"Less than {cargs.skip} parts detected, avoiding re-encode")
        sys.exit(2)

    with open(filter_fn, "wb") as fg:
        for i, (s, e) in enumerate(frames_to_keep):
            fg.write(trim(s, e, i))
            fg.write(b"\n")
            fg.write(atrim(s, e, i))
            fg.write(b"\n")
        fg.write(b"".join(b"[v%d][a%d]" % (i, i) for i in range(len(frames_to_keep))))
        fg.write(b"concat=n=%d:a=1[vout][aout]" % len(frames_to_keep))
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
if cargs.output_to_cwd:
    fout = path.basename(fout)
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
