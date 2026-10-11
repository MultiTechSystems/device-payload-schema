#!/usr/bin/env python3
"""Run a command, stream its output, and keep a copy of it in a log file.

    python3 tools/run-logged.py build/lang-logs/go.log -- sh -c "go test ./..."

The log starts with a header recording when the run began and ends with a trailer
recording its exit status:

    # run-logged started=1791738426.123 cwd=/path/to/repo
    # run-logged command: sh -c "go test ./..."
    ...the command's own output...
    # run-logged exit=0

`tools/check-floors.py --from-logs` reads these logs instead of running the Go, Java and
C# suites a second time. The header's start time is what lets it decide a log is stale: a
source file modified after the run began means the log describes a tree that no longer
exists. A log without the trailer was interrupted and is never trusted.

The command's exit status is this script's exit status, so a Makefile recipe using it
fails exactly as it did when it ran the command directly.

Python 3.8 compatible, standard library only.
"""

import os
import shlex
import subprocess
import sys
import time

HEADER = "# run-logged started="
TRAILER = "# run-logged exit="


def main(argv):
    if len(argv) < 3 or argv[1] != "--":
        sys.stderr.write("usage: run-logged.py LOG -- COMMAND [ARG...]\n")
        return 2
    log_path, command = argv[0], argv[2:]
    os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
    # Written to a temporary name and renamed at the end, so an interrupted run leaves
    # the previous complete log in place rather than half of a new one.
    partial = log_path + ".partial"
    started = time.time()
    with open(partial, "w", encoding="utf-8", errors="replace") as log:
        log.write("%s%.3f cwd=%s\n" % (HEADER, started, os.getcwd()))
        log.write("# run-logged command: %s\n" % " ".join(shlex.quote(c) for c in command))
        log.flush()
        proc = subprocess.Popen(command, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
        out = sys.stdout.buffer
        for chunk in iter(lambda: proc.stdout.readline(), b""):
            out.write(chunk)
            out.flush()
            log.write(chunk.decode("utf-8", errors="replace"))
        code = proc.wait()
        log.write("%s%d\n" % (TRAILER, code))
    os.replace(partial, log_path)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
