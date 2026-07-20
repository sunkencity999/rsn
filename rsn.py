#!/usr/bin/env python3
"""rsn — rsync, safe and sane.

A UX layer over rsync: unambiguous intent, automatic dry-run preview,
delete guards, readable summaries, and an --explain mode that decodes
any rsync command into English.

The battle-tested rsync engine does all real work. rsn just refuses to
let a trailing slash ruin your day.

Usage:
  rsn copy   SRC DST [options]   # additive copy (never deletes at DST)
  rsn backup SRC DST [options]   # archive copy: perms/times/links, never deletes
  rsn mirror SRC DST [options]   # exact mirror: deletes DST extras (guarded)
  rsn explain 'rsync -avzP --delete src/ dst'   # decode a command to English

Options (copy/backup/mirror):
  --contents        copy the *contents* of SRC into DST (like src/)
  --as-folder       copy SRC as a folder inside DST (like src) [asks if neither given]
  --only PATTERN    transfer only files matching PATTERN (repeatable, e.g. '*.jpg')
  --exclude PATTERN exclude files matching PATTERN (repeatable)
  --yes, -y         skip confirmation (for scripts/cron)
  --dry-run, -n     show the preview and stop
  --force-delete    override the mirror delete-guard
  --quiet, -q       minimal output
  -- ARGS...        pass anything after -- straight to rsync
"""

import os
import re
import shlex
import shutil
import subprocess
import sys

VERSION = "0.1.0"
DELETE_GUARD_FRACTION = 0.20   # refuse to delete more than 20% of dest...
DELETE_GUARD_MIN = 10          # ...when that's also more than 10 files
PREVIEW_SAMPLE = 8             # sample paths shown per category

C_RESET, C_BOLD, C_RED, C_GREEN, C_YELLOW, C_DIM = (
    "\033[0m", "\033[1m", "\033[31m", "\033[32m", "\033[33m", "\033[2m")


def tty() -> bool:
    return sys.stdout.isatty() and sys.stdin.isatty()


def col(code: str, s: str) -> str:
    return f"{code}{s}{C_RESET}" if sys.stdout.isatty() else s


def die(msg: str, code: int = 1):
    print(col(C_RED, f"rsn: {msg}"), file=sys.stderr)
    sys.exit(code)


def is_remote(path: str) -> bool:
    """host:path, user@host:path, or rsync:// URLs."""
    if path.startswith("rsync://"):
        return True
    m = re.match(r"^([A-Za-z0-9._@-]+):", path)
    # Avoid treating C: style (single letter) or ./relative as remote
    return bool(m) and not re.match(r"^[A-Za-z]:[/\\]", path)


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


# ---------------------------------------------------------------- explain --

# flag -> (name, description, danger)   danger: None | "warn" | "danger"
FLAG_DB = {
    "-a": ("archive", "recurse + preserve symlinks, perms, times, group, owner, devices (= -rlptgoD)", None),
    "-r": ("recursive", "recurse into directories", None),
    "-l": ("links", "copy symlinks as symlinks", None),
    "-p": ("perms", "preserve permissions", None),
    "-t": ("times", "preserve modification times", None),
    "-g": ("group", "preserve group", None),
    "-o": ("owner", "preserve owner (needs root)", None),
    "-D": ("devices/specials", "preserve device and special files (needs root)", None),
    "-v": ("verbose", "list transferred files", None),
    "-q": ("quiet", "suppress non-error output", None),
    "-z": ("compress", "compress data in transit (good over networks, wasted effort locally)", None),
    "-P": ("partial+progress", "keep partially transferred files and show per-file progress", None),
    "-n": ("dry-run", "PREVIEW ONLY — no changes are made", None),
    "-u": ("update", "skip files that are newer at the destination", None),
    "-c": ("checksum", "compare by checksum, not size+mtime (slow but thorough)", None),
    "-H": ("hard-links", "preserve hard links", None),
    "-A": ("acls", "preserve ACLs (implies -p)", None),
    "-X": ("xattrs", "preserve extended attributes", None),
    "-x": ("one-file-system", "don't cross filesystem boundaries", None),
    "-S": ("sparse", "handle sparse files efficiently", None),
    "-e": ("rsh", "remote shell to use (e.g. ssh)", None),
    "-h": ("human-readable", "human-readable numbers", None),
    "--archive": ("archive", "same as -a", None),
    "--dry-run": ("dry-run", "PREVIEW ONLY — no changes are made", None),
    "--delete": ("delete", "DELETE files at destination that don't exist at source", "danger"),
    "--delete-after": ("delete-after", "delete dest extras AFTER transfer completes", "danger"),
    "--delete-before": ("delete-before", "delete dest extras BEFORE transfer", "danger"),
    "--delete-during": ("delete-during", "delete dest extras during transfer", "danger"),
    "--delete-excluded": ("delete-excluded", "ALSO delete dest files matching your excludes", "danger"),
    "--remove-source-files": ("remove-source-files", "DELETE source files after transferring them (a move!)", "danger"),
    "--inplace": ("inplace", "update files in place (risky if interrupted; breaks hardlink dedup)", "warn"),
    "--append": ("append", "append to shorter dest files (assumes prefix identical!)", "warn"),
    "--partial": ("partial", "keep partially transferred files on interrupt", None),
    "--progress": ("progress", "show per-file progress (noisy; --info=progress2 is cleaner)", None),
    "--exclude": ("exclude", "skip files matching pattern", None),
    "--include": ("include", "re-include files that a later exclude would skip (ORDER MATTERS)", None),
    "--exclude-from": ("exclude-from", "read exclude patterns from file", None),
    "--filter": ("filter", "full filter-rule mini-language", None),
    "--bwlimit": ("bwlimit", "throttle bandwidth", None),
    "--stats": ("stats", "print transfer statistics", None),
    "--numeric-ids": ("numeric-ids", "don't map uid/gid by name", None),
    "--hard-links": ("hard-links", "preserve hard links", None),
    "--compress": ("compress", "same as -z", None),
    "--verbose": ("verbose", "same as -v", None),
    "--update": ("update", "same as -u", None),
    "--checksum": ("checksum", "same as -c", None),
    "--backup": ("backup", "rename replaced/deleted dest files instead of losing them", None),
    "--backup-dir": ("backup-dir", "where to stash those renamed files", None),
    "--link-dest": ("link-dest", "hardlink unchanged files against a reference dir (snapshot backups)", None),
    "--ignore-existing": ("ignore-existing", "skip files that already exist at dest (even if different)", "warn"),
    "--existing": ("existing", "only update files that already exist at dest; create nothing", "warn"),
    "--max-size": ("max-size", "skip files larger than this", None),
    "--min-size": ("min-size", "skip files smaller than this", None),
    "--info": ("info", "fine-grained output control (progress2 = single overall progress bar)", None),
    "--itemize-changes": ("itemize-changes", "show a change-flags string per file", None),
    "--out-format": ("out-format", "custom per-file output format", None),
    "--chown": ("chown", "force owner/group at destination", None),
    "--mkpath": ("mkpath", "create destination's missing path components", None),
    "--size-only": ("size-only", "compare by size only, ignore mtime (risky: same-size edits missed)", "warn"),
    "--ignore-times": ("ignore-times", "transfer everything even if size+mtime match", None),
    "--whole-file": ("whole-file", "skip delta algorithm, copy whole files (default for local)", None),
    "--fuzzy": ("fuzzy", "look for similar dest files as delta basis", None),
    "--timeout": ("timeout", "I/O timeout in seconds", None),
}

EXPANDABLE = set("rlptgoDvzqnucHAXxShPe")


def explain(cmdline: str) -> int:
    try:
        tokens = shlex.split(cmdline)
    except ValueError as e:
        die(f"can't parse that command: {e}")
    if tokens and os.path.basename(tokens[0]) in ("rsync", "rsn"):
        tokens = tokens[1:]
    if not tokens:
        die("nothing to explain — pass a full rsync command in quotes")

    flags, positional, dangers = [], [], []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t == "--":
            positional.extend(tokens[i + 1:])
            break
        if t.startswith("--"):
            base = t.split("=", 1)[0]
            val = t.split("=", 1)[1] if "=" in t else None
            info = FLAG_DB.get(base)
            if info is None:
                flags.append((t, "(unknown to rsn — check `man rsync`)", None))
            else:
                desc = info[1] + (f"  [{val}]" if val else "")
                flags.append((base, desc, info[2]))
                if info[2] == "danger":
                    dangers.append(base)
            # flags whose value is the NEXT token
            if val is None and base in ("--exclude", "--include", "--exclude-from",
                                        "--filter", "--bwlimit", "--backup-dir",
                                        "--link-dest", "--max-size", "--min-size",
                                        "--chown", "--out-format", "--timeout"):
                if i + 1 < len(tokens):
                    flags[-1] = (base, FLAG_DB[base][1] + f"  [{tokens[i+1]}]", FLAG_DB[base][2])
                    i += 1
        elif t.startswith("-") and len(t) > 1:
            for ch in t[1:]:
                f = f"-{ch}"
                info = FLAG_DB.get(f)
                if info:
                    flags.append((f, info[1], info[2]))
                    if info[2] == "danger":
                        dangers.append(f)
                else:
                    flags.append((f, "(unknown to rsn — check `man rsync`)", None))
                if ch == "e" and i + 1 < len(tokens):
                    flags[-1] = ("-e", FLAG_DB["-e"][1] + f"  [{tokens[i+1]}]", None)
                    i += 1
        else:
            positional.append(t)
        i += 1

    print(col(C_BOLD, "Flags:"))
    for f, desc, danger in flags:
        mark = col(C_RED, " ⚠ DESTRUCTIVE") if danger == "danger" else (
            col(C_YELLOW, " ⚠ caution") if danger == "warn" else "")
        print(f"  {col(C_BOLD, f):<24} {desc}{mark}")

    if len(positional) >= 2:
        *srcs, dst = positional
        print()
        print(col(C_BOLD, "Paths:"))
        for s in srcs:
            if s.rstrip("/") != s or s.endswith("/"):
                print(f"  SRC  {s}\n       trailing slash → copies the {col(C_BOLD, 'CONTENTS')} of this directory into DST")
            else:
                print(f"  SRC  {s}\n       no trailing slash → copies the directory {col(C_BOLD, 'ITSELF')} (as {os.path.basename(s.rstrip('/'))}/) into DST")
        print(f"  DST  {dst}  {col(C_DIM, '(trailing slash on DST is cosmetic)')}")
    elif len(positional) == 1:
        print(f"\n  Single path {positional[0]!r} → rsync will just LIST it, not copy.")

    if dangers:
        print()
        print(col(C_RED + C_BOLD, "Danger check:"))
        if any("delete" in d for d in dangers):
            srcs_slash = [s for s in positional[:-1] if s.endswith("/")] if len(positional) >= 2 else []
            print("  --delete removes anything at DST that isn't at SRC.")
            if len(positional) >= 2 and not srcs_slash:
                print("  Note: without a trailing slash on SRC, only the copied subfolder is mirrored —")
                print("  but combined with the wrong DST path this is the classic wipe-your-backup trap.")
            print(f"  {col(C_BOLD, 'Always')} run with -n (dry-run) first, or use `rsn mirror` which previews automatically.")
    print()
    print(col(C_DIM, "English summary:"))
    verb = "mirror (with deletions!)" if any("delete" in d for d in dangers) else "copy"
    if len(positional) >= 2:
        src_desc = ", ".join(
            (f"contents of {s}" if s.endswith("/") else f"folder {s}") for s in positional[:-1])
        print(f"  {verb.capitalize()} {src_desc} → {positional[-1]}")
    return 0


# ------------------------------------------------------------ sync engine --

ITEM_RE = re.compile(r"^(\S{11,12}) (.+)$")


def parse_dry_run(lines):
    """Parse itemized dry-run output into (new, changed, deleted) path lists."""
    new, changed, deleted = [], [], []
    for line in lines:
        if line.startswith("*deleting"):
            deleted.append(line.split(None, 1)[1] if " " in line else line)
            continue
        m = ITEM_RE.match(line)
        if not m:
            continue
        code, path = m.groups()
        if code[0] not in ("<", ">", "c") or path in (".", "./"):
            continue
        if "+++++++" in code:
            new.append(path)
        else:
            changed.append(path)
    return new, changed, deleted


def parse_stats(text):
    stats = {}
    pats = {
        "files": r"Number of files: ([\d,]+)",
        "transferred": r"Number of (?:regular )?files transferred: ([\d,]+)",
        "total_size": r"Total file size: ([\d,]+)",
        "sent_size": r"Total transferred file size: ([\d,]+)",
    }
    for k, p in pats.items():
        m = re.search(p, text)
        if m:
            stats[k] = int(m.group(1).replace(",", ""))
    return stats


def count_dest_files(dst: str) -> int:
    n = 0
    for _, _, files in os.walk(dst):
        n += len(files)
        if n > 500_000:
            break
    return n


def confirm(prompt: str) -> bool:
    try:
        return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def build_cmd(mode, src, dst, only, excludes, extra, delete):
    cmd = ["rsync"]
    if mode == "copy":
        cmd += ["-rlt"]              # recurse, links, times — perms as system default
    else:                            # backup / mirror
        cmd += ["-a"]
    if delete:
        cmd += ["--delete"]
    for pat in only:
        cmd += ["--include=*/", f"--include={pat}"]
    if only:
        cmd += ["--exclude=*", "--prune-empty-dirs"]
    for pat in excludes:
        cmd += [f"--exclude={pat}"]
    cmd += ["--mkpath"]
    cmd += extra
    cmd += [src, dst]
    return cmd


def run_sync(mode, argv):
    only, excludes, extra = [], [], []
    yes = dry_only = force_delete = quiet = False
    contents = as_folder = False
    paths = []

    it = iter(range(len(argv)))
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--":
            extra = argv[i + 1:]
            break
        elif a == "--contents":
            contents = True
        elif a == "--as-folder":
            as_folder = True
        elif a == "--only":
            i += 1; only.append(argv[i])
        elif a == "--exclude":
            i += 1; excludes.append(argv[i])
        elif a in ("--yes", "-y"):
            yes = True
        elif a in ("--dry-run", "-n"):
            dry_only = True
        elif a == "--force-delete":
            force_delete = True
        elif a in ("--quiet", "-q"):
            quiet = True
        elif a.startswith("-"):
            die(f"unknown option {a!r} (use `--` to pass raw rsync flags)")
        else:
            paths.append(a)
        i += 1

    if len(paths) != 2:
        die(f"`rsn {mode}` needs exactly SRC and DST (got {len(paths)})")
    src, dst = paths

    if not is_remote(src) and not os.path.exists(src.rstrip("/")):
        die(f"source not found: {src}")

    src_is_dir = is_remote(src) or os.path.isdir(src.rstrip("/"))

    # --- resolve slash intent explicitly ---
    if src_is_dir:
        had_slash = src.endswith("/")
        if contents and as_folder:
            die("--contents and --as-folder are mutually exclusive")
        if not contents and not as_folder:
            if had_slash:
                contents = True          # user already said so with the slash
            elif mode == "mirror":
                contents = True          # mirror means "make DST look like SRC"
            elif yes or not tty():
                as_folder = True         # script mode: safe additive default
            else:
                base = os.path.basename(src.rstrip("/"))
                print(f"Copy {col(C_BOLD, src)} …")
                print(f"  1) as a folder    → {os.path.join(dst, base)}/")
                print(f"  2) contents only  → {dst}/  (files land directly inside)")
                choice = input("Which? [1/2] ").strip()
                if choice == "2":
                    contents = True
                elif choice == "1":
                    as_folder = True
                else:
                    die("cancelled")
        src = src.rstrip("/") + ("/" if contents else "")

    delete = mode == "mirror"
    cmd = build_cmd(mode, src, dst, only, excludes, extra, delete)

    # --- dry run preview ---
    dry_cmd = cmd[:-2] + ["--dry-run", "--itemize-changes", "--stats"] + cmd[-2:]
    r = subprocess.run(dry_cmd, capture_output=True, text=True)
    if r.returncode not in (0, 23, 24):
        die(f"rsync dry-run failed (exit {r.returncode}):\n{r.stderr.strip()}", r.returncode)

    new, changed, deleted = parse_dry_run(r.stdout.splitlines())
    stats = parse_stats(r.stdout)

    if not quiet:
        print(col(C_BOLD, f"\nPlan: rsn {mode} → {dst}"))
        print(col(C_DIM, f"  ({' '.join(shlex.quote(c) for c in cmd)})"))
        print(f"  {col(C_GREEN,  f'+ {len(new)} new')}"
              f"   {col(C_YELLOW, f'~ {len(changed)} updated')}"
              f"   {col(C_RED,    f'- {len(deleted)} deleted')}"
              f"   ({human_bytes(stats.get('sent_size', 0))} to transfer)")
        for label, items, c in (("new", new, C_GREEN), ("updated", changed, C_YELLOW),
                                ("deleted", deleted, C_RED)):
            for p in items[:PREVIEW_SAMPLE]:
                print(col(c, f"    {'+' if label=='new' else '~' if label=='updated' else '-'} {p}"))
            if len(items) > PREVIEW_SAMPLE:
                print(col(C_DIM, f"      … and {len(items) - PREVIEW_SAMPLE} more {label}"))

    if not (new or changed or deleted):
        print(col(C_GREEN, "Already in sync — nothing to do."))
        return 0

    # --- delete guard ---
    if deleted and not force_delete and not is_remote(dst) and os.path.isdir(dst):
        total_dest = count_dest_files(dst)
        if total_dest and len(deleted) > DELETE_GUARD_MIN and \
           len(deleted) / total_dest > DELETE_GUARD_FRACTION:
            pct = 100 * len(deleted) / total_dest
            print(col(C_RED + C_BOLD, f"\n⛔ Delete guard: this would remove {len(deleted)} of ~{total_dest} "
                  f"files at the destination ({pct:.0f}%)."))
            print("   If that's really what you want, re-run with --force-delete.")
            return 3

    if dry_only:
        print(col(C_DIM, "(dry-run — no changes made)"))
        return 0

    if not yes:
        if not tty():
            die("refusing to proceed without confirmation on a non-TTY (use --yes)")
        word = f"{col(C_RED, f'DELETE {len(deleted)} files and ')}" if deleted else ""
        if not confirm(f"\n{word}apply these changes?"):
            print("Cancelled — nothing was changed.")
            return 0

    # --- real run ---
    real_cmd = cmd[:-2] + ["--stats"] + (["--info=progress2"] if tty() and not quiet else []) + cmd[-2:]
    proc = subprocess.run(real_cmd, capture_output=False if tty() else True,
                          text=True)
    if proc.returncode == 24:
        print(col(C_YELLOW, "note: some source files vanished during transfer (usually fine)"))
    elif proc.returncode == 23:
        print(col(C_YELLOW, "⚠ some files could not be transferred (permissions?) — see above"))
    elif proc.returncode != 0:
        die(f"rsync failed (exit {proc.returncode})", proc.returncode)

    print(col(C_GREEN + C_BOLD, f"✓ Done: {len(new)} added, {len(changed)} updated, {len(deleted)} deleted."))
    return 0


# ----------------------------------------------------------------- main ---

def main():
    if shutil.which("rsync") is None:
        die("rsync is not installed")
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    if args[0] in ("-V", "--version"):
        print(f"rsn {VERSION} (wrapping: {subprocess.run(['rsync', '--version'], capture_output=True, text=True).stdout.splitlines()[0]})")
        return 0
    cmd, rest = args[0], args[1:]
    if cmd == "explain":
        return explain(" ".join(rest))
    if cmd in ("copy", "backup", "mirror"):
        return run_sync(cmd, rest)
    die(f"unknown command {cmd!r} — try: copy, backup, mirror, explain")


if __name__ == "__main__":
    sys.exit(main())
