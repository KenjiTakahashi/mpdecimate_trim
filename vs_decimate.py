#!/usr/bin/env python3

import sys
import time
import vapoursynth as vs


def eprint(m):
    print(m, file=sys.stderr)


def eprof(s):
    e = time.time()
    eprint(time.strftime("%H:%M:%S", time.gmtime(e - s)))
    return e


def df(clip):
    dframes = []
    for i, f in enumerate(clip.frames()):
        if f.props["VDecimateDrop"]:
            if dframes and len(dframes[-1]) == 1:
                dframes[-1].append(i)
        elif not dframes or len(dframes[-1]) == 2:
            dframes.append([i])

    if len(dframes[-1]) == 1:
        dframes[-1].append(None)

    eprint(len(dframes))
    return dframes


def anal(clip):
    s = time.time()
    a = vs.core.vivtc.VDecimate(clip, dryrun=True, cycle=5, dupthresh=1.0, blockx=512, blocky=512)
    out = vs.core.std.Splice([clip[i1:i2] for i1, i2 in df(a)])
    eprof(s)
    return out


# eprint(anal.get_frame(32708).props)
fi = vs.core.ffms2.Source(source="a.mp4")
fi = vs.core.damb.Read(fi, "audio.wav")
# fo = muf.avg_decimate(f)
# out = anal(fi)
out = anal(anal(anal(anal(fi))))
out = vs.core.damb.Write(out, "newaudio.wav")

out.set_output()
