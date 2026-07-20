#!/usr/bin/env python3
"""Scripted demo driver for the rsn README GIF. Run under `asciinema rec -c`."""
import os, pty, select, sys, time

RSN = os.path.expanduser("~/.local/bin/rsn")
BASE = "/tmp/rsn-demo"

def out(s=""):
    sys.stdout.write(s); sys.stdout.flush()

def type_cmd(s, cps=0.035):
    out("\033[1;32m$\033[0m ")
    for ch in s:
        out(ch); time.sleep(cps)
    time.sleep(0.4); out("\n")

def run_pty(argv, answers=None, cwd=None):
    """Run argv in a pty, relay output, send answers[i][1] when answers[i][0] seen."""
    answers = list(answers or [])
    pid, fd = pty.fork()
    if pid == 0:
        if cwd: os.chdir(cwd)
        os.execvp(argv[0], argv)
    buf = b""
    while True:
        try:
            r, _, _ = select.select([fd], [], [], 0.05)
        except OSError:
            break
        if r:
            try:
                data = os.read(fd, 4096)
            except OSError:
                break
            if not data:
                break
            os.write(1, data)
            buf += data
            if answers and answers[0][0].encode() in buf:
                trigger, resp = answers.pop(0)
                time.sleep(1.1)
                os.write(fd, resp.encode())
                buf = b""
    os.waitpid(pid, 0)

def setup():
    import shutil
    shutil.rmtree(BASE, ignore_errors=True)
    photos = f"{BASE}/photos"; backup = f"{BASE}/backup/photos"
    os.makedirs(f"{photos}/2026-07"); os.makedirs(backup)
    for i in range(2214, 2228):
        open(f"{photos}/2026-07/IMG_{i}.jpg", "w").write("x" * 2048)
    open(f"{photos}/index.db", "w").write("new index " * 200)
    # backup has an older index + a stale temp file
    open(f"{backup}/index.db", "w").write("old")
    os.utime(f"{backup}/index.db", (1000000000, 1000000000))
    open(f"{backup}/stale-tmp.bin", "w").write("junk")
    # scene 2: an empty dir pretending to be the source (dead mount)
    os.makedirs(f"{BASE}/dead-mount/photos")
    return photos, backup

def main():
    photos, backup = setup()
    os.environ["PS1"] = "$ "
    out("\033[2J\033[H")

    out("\033[1m# rsn — rsync, safe and sane\033[0m\n")
    time.sleep(1.2)

    out("\n\033[2m# 1. Mirror with automatic preview + confirmation\033[0m\n")
    time.sleep(0.6)
    type_cmd("rsn mirror photos backup/photos")
    run_pty([RSN, "mirror", "photos", "backup/photos"],
            answers=[("[y/N]", "y\n")], cwd=BASE)
    time.sleep(1.6)

    out("\n\033[2m# 2. Dead mount? The delete guard has your back\033[0m\n")
    time.sleep(0.8)
    type_cmd("rsn mirror /tmp/rsn-demo/dead-mount/photos backup/photos --yes")
    run_pty([RSN, "mirror", f"{BASE}/dead-mount/photos", "backup/photos", "--yes"],
            cwd=BASE)
    time.sleep(2.0)

    out("\n\033[2m# 3. Decode any rsync command into English\033[0m\n")
    time.sleep(0.8)
    type_cmd("rsn explain 'rsync -az --delete src/ host:/backups'")
    run_pty([RSN, "explain", "rsync -az --delete src/ host:/backups"], cwd=BASE)
    time.sleep(2.5)

if __name__ == "__main__":
    main()
