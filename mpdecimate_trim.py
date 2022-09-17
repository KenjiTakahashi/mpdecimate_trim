#!/usr/bin/env python3

import argparse
import os
import sys
import time
from functools import partial
from os import path
from subprocess import run
from tempfile import NamedTemporaryFile


sys.stdout = sys.stderr


cargs = argparse.ArgumentParser(description="Trim video(+audio) clip, based on output from mpdecimate filter")
cargs.add_argument("--skip", type=int, help="Skip trimming, if less than SKIP parts found")
#Rami changed default of keep to true, overwrite as default is dangerous, so now we have --del_source argument if is is needed to delete source file
cargs.add_argument("--del_source", action="store_true", help="Delete original file", default=False)
cargs.add_argument("--video_encoder", help="Video encoder(defult is libx264)", default="libx264")
cargs.add_argument("--crf", help="Constant rate factor (CRF) - determine the quality of the encoding, default is 17 (very high quality), for lower video size use higher CRF values", default="17")
cargs.add_argument("--vaapi", type=str, help="Use VA-API device for hardware accelerated transcoding")
cargs.add_argument("--vaapi-decimate", nargs="?", const=True, help="Use VA-API device for hardware accelerated decimate filter")
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


_hwargs = ["-hwaccel", "vaapi", "-hwaccel_device"]


def hwargs_decimate():
    if not cargs.vaapi_decimate:
        return []

    if cargs.vaapi_decimate is True:
        if not cargs.vaapi:
            raise Exception("--vaapi-decimate set to use --vaapi device, but --vaapi not set")

        return [*_hwargs, cargs.vaapi]

    return [*_hwargs, cargs.vaapi_decimate]


def hwargs_transcode():
    return [*_hwargs, cargs.vaapi, "-hwaccel_output_format", "vaapi"] if cargs.vaapi else []


def _ffmpeg(fi, co, *args, hwargs=[]):
    args = ["ffmpeg", *hwargs, "-i", fi, *args]
  	 # rami shell=false(default) is better https://stackoverflow.com/questions/3172470/actual-meaning-of-shell-true-in-subprocess 
	 # rami use capture_output=True and print (result) show the result	 
    result = run(args, capture_output=True, shell=False)
    

    if result.returncode == 0:
        return result

    print(f"Command {args} failed with code {result.returncode}")
 	 # rami extra print, it might show more info
    print("--------    Full Result String    --------")
    print (result)
    print("--------    STDOUT S    --------")
    print(result.stdout.decode())
    print("--------    STDOUT E    --------")
    print("--------    STDERR S    --------")
    print(result.stderr.decode())
    print("--------    STDERR E    --------")
    sys.exit(3)


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

#Rami info
print ("\nStep1: Finding duplicated frames\n",flush=True)
dframes2 = get_dframes(ffmpeg(
    True,
    "-vf", "mpdecimate=hi=576",
    "-loglevel", "debug",
    "-f", "null", "-",
    hwargs=hwargs_decimate(),
).stderr)
if cargs.skip and len(dframes2) < cargs.skip:
    print(f"less than {cargs.skip} parts detected, avoiding re-encode")
    sys.exit(2)

# Rami - changed libx265 to libx264, and CRF 30 to 17 
def get_enc_args():
    if cargs.vaapi:
        return ["hevc_vaapi", "-qp", "23"]
    return [{cargs.video_encoder}, "-preset", "fast", "-crf", {cargs.crf}]
#Rami - I Added delete=False, as it was not working without it in my Windows enviroment
with NamedTemporaryFile(prefix="mpdecimate_trim.",delete=False) as fg:
    for i, (s, e) in enumerate(dframes2):
        fg.write(trim(s, e, i))
        fg.write(b"\n")
        fg.write(atrim(s, e, i))
        fg.write(b"\n")
    fg.write(b"".join(b"[v%d][a%d]" % (i, i) for i in range(len(dframes2))))
    fg.write(b"concat=n=%d:a=1[vout][aout]" % len(dframes2))
    fg.flush()

    fout, ext = path.splitext(cargs.filepath)
	 
	 # Rami Added timestamp to filename so if the files exsist the program will not freeze
    # Added AAC 160Kbit audio encoding instead of the default Vorbis	 
    print ("\nStep2: Encoding the unduplicated frames\n",flush=True)
    import time
    timestr = time.strftime("%Y%m%d_%H%M%S") 
    ffmpeg(
        False,
        "-filter_complex_script", fg.name,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", *get_enc_args(), "-c:a","aac", "-b:a", "160k",
        f"{fout}_{timestr}.trimmed{ext}",
        hwargs=hwargs_transcode(),
    )
	 
    if cargs.del_source:
        print ("\nDeleting source file (--del_source was specified)")
        os.remove(cargs.filepath)
    else:
	     print ("\nKeeping source file {If you want to delete source file use --del_source}")
