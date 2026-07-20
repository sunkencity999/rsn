# rsn — rsync, safe and sane

A UX layer over rsync. The battle-tested engine does all real work; rsn just
refuses to let a trailing slash ruin your day.

## Why

- `rsync -a src dst` vs `rsync -a src/ dst` do fundamentally different things.
  Combined with `--delete`, that one character has wiped countless backups.
- rsync's safety model is "you should have run --dry-run first."
- `-a` = `-rlptgoD`. Nobody remembers that. Include/exclude ordering is a
  mini-language. Exit code 23 tells you nothing.

## Commands

    rsn copy   SRC DST    # additive copy — never deletes at DST
    rsn backup SRC DST    # archive copy (perms/owners/times) — never deletes
    rsn mirror SRC DST    # exact mirror — deletes DST extras (guarded)
    rsn explain 'rsync -avzP --delete src/ dst'    # decode to English

## Safety model

1. **Explicit intent** — on a TTY, rsn *asks* whether you mean "the folder"
   or "its contents" (or take `--contents` / `--as-folder`). No slash traps.
2. **Automatic preview** — every run dry-runs first and shows
   `+new ~updated -deleted` with sample paths before touching anything.
3. **Delete guard** — mirror refuses to remove >20% of the destination
   (and >10 files) without `--force-delete`. Exit code 3.
4. **Script-safe** — `--yes` for cron; non-TTY without `--yes` refuses to act.

## Options

    --only '*.jpg'     transfer only matching files (repeatable)
    --exclude PAT      exclude (repeatable)
    -n / --dry-run     preview and stop
    -y / --yes         no confirmation (scripts)
    --force-delete     override the delete guard
    -- ARGS            pass anything after -- straight to rsync

Single file, Python 3 stdlib only. MIT.
