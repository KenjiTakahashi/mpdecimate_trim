Script to trim similar/duplicate fragments from video clips. While keeping audio in sync.

Note that it does not do what is often called "dropping frames" (i.e. removing them from container by replacing with PTS of a "similar enough" one from another part of the clip). It actually gets rid of them completely, making the resulting clip shorter in time.

Note also that some variables, such as mpdecimate thresholds or output codec settings, are currently hardcoded in the script. Mostly due to laziness ;-).

# Usage

Needs `Python3.6+` and `ffmpeg`.

```bash
$ mpdecimate_trim.py [--skip SKIP] [--keep] <filepath>
```

This will take file at `<filepath>`, detect frames with certain similarity, re-encode it with them removed (using `libx265`) and delete the original file.

The `--keep` switch makes it keep the original.

By default, re-encode happens even if no fragments to trim are found. This can be adjusted by setting `--skip` to minimum amount of remaining clip parts (e.g. `<=1` is equivalent to default, `2` means 1 trimmed fragment, and so on).

# vs_decimate?

Was a different experiment, using `vapoursynth`. Abandoned, because its' decimation algorithm does not fit my needs, and the whole process is also noticeably slower.

In case you want to use it for something, it needs `Python3.6+` and `vapoursynth` with `ffms2` and `damb`.
