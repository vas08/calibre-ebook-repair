---
name: ebook-repair
description: Find and repair layout errors in the EPUB books of a calibre library with calibre-ebook-repair - mojibake, Russian text in a Latin "Cyrillic" font, book text stuffed into headings, whole chapters in bold, broken links and images, a "#genre" genre - then replace the books in calibre and copy the library to an Android reader over adb. Use when the user says "broken book", "garbled text in my ebook", "check my calibre library", "fix EPUB", "the AZW3 is wrong", "sync the library to my reader". NOT for buying or downloading books, plain format conversion of books that have no errors, or editing metadata (titles, authors, covers, tags).
---

# calibre-ebook-repair

The tool reads the calibre library and writes only to a working folder. One command, `apply`, writes to the library,
and the user runs it.

## Where the package is

1. `~/calibre-ebook-repair` (the README clones it there).
2. Otherwise look for a folder that contains `ebook_repair/__main__.py`, e.g.
   `find ~ -maxdepth 5 -path '*/ebook_repair/__main__.py' 2>/dev/null`.
3. Not found: ask the user where it is. Do not clone or install anything without asking.

Run every command from that folder: `cd <package> && python3 -m ebook_repair <command>`.
Library: `--library PATH`, else `$CALIBRE_LIBRARY`, else `~/Calibre Library`. Ask the user if none of these exists.
Working folder: `--workdir PATH`, default `./ebook-repair-work`. Keep it the same across the whole session.

## Order for any repair

1. **Class of error.** `python3 -m ebook_repair check [ID ...] [--tag NAME]`. Read the totals and
   `<workdir>/check.tsv`. `check` does not see the Latin font, latin-1 or `#genre`; if the user describes those, go to
   step 3 and read the `repair` counters.
2. **Samples.** `python3 -m ebook_repair samples KIND ID [COUNT]`, KIND one of `heading`, `bold`, `replacement`,
   `mojibake`, `markup`, `empty`. Show the user 1-3 raw samples and say in one line what is broken. A number in the
   report is not proof until the markup confirms it.
3. **Repair into the working folder.** For Russian books with the Latin font or latin-1 run
   `python3 -m ebook_repair build-dict` first; without the word lists that decoding is off. Then
   `python3 -m ebook_repair repair ID ...`. Pass the ids found in steps 1-2 rather than the whole library.
   In a book marked ru/en in calibre, repair removes CJK characters (not whole paragraphs), and only from a block that
   has more than 3 of them. `book N: skipped, cannot repair: ...` means that book was left out; report it.
   `ebook-convert not found` in the output means the AZW3 files were skipped: `apply` will refuse those books, so tell
   the user to install calibre and rerun `repair`. `ERROR ...` in the `azw3` column after a `Pillow is not installed`
   warning: suggest `pip install pillow` and a rerun.
4. **Verify.** `python3 -m ebook_repair verify ID ...`. It writes `<workdir>/<id>/verified.json` for every book with
   no discrepancies and removes it from the rest; `apply` takes only books that have it. Show each discrepancy line
   and find out why. Common ones: `no source.json: repair this book again` (working folder from an older version:
   rerun `repair`), `the EPUB in the library changed after repair` (rerun `repair`), `ids/links changed` after a
   removed `<p id>` of only `�` (that book cannot be applied), `text diverged` in a file
   where only a small part is in the Latin font (verify allows that decoding only when 15% or more of the words carry
   the font's special characters; that book cannot be applied). A lone `AZW3 missing or older than EPUB` on a tiny
   book can be the 10000-byte threshold; step 5 decides.
5. **AZW3.** For each repaired book:
   `python3 -m ebook_repair azw3-check "<workdir>/<id>/<file>.epub"`. Needs `AZW3 VERIFIED` and exit code 0.
6. **Hand `apply` to the user.** One line: "Close calibre and run:
   `cd <package> && python3 -m ebook_repair --library "<library>" --workdir "<workdir>" apply ID ...`".
   List only ids that passed steps 4 and 5, and only after `repair` has finished. Exit code 2 means calibre is open.
7. **Check what apply did.** Read `<workdir>/apply.log` (appended on every run; the last line per book counts).
   `verified` and `already-applied` are fine. `not-verified`, `changed-after-verify`: run `verify` again.
   `other-library`, `library-changed`: the book was not touched; tell the user. `no-file`, `no-backup`, `mismatch`:
   report with the book id. The old files are in `<workdir>/<id>/before/<name>.<md5[:8]>.<ext>`. Do not run
   `verify` after `apply` to "confirm": it reports `the EPUB in the library changed after repair` and removes
   `verified.json`; that is expected, and a repeated `apply` still says `already applied`. A `mismatch azw3` after the
   EPUB went in is finished by running the same `apply` again: it replaces only the AZW3.
8. **Sync, if asked.** The order for the user:
   1. `python3 -m ebook_repair sync`: counts only; show the numbers.
   2. `sync --add` when the user wants the files on the reader. Into a folder named like the library folder it also
      writes the marker `.ebook-repair-library` with the library name; into any other folder it writes no marker and
      says `marker not written`.
   3. The user checks the reader.
   4. `sync` again: it prints the stale files under the numbers, one path per line. Show the list.
   5. `sync --mirror --yes`: only on an explicit request and after the user said "yes" to that list. If it stops with
      `more than half`, show the numbers; `--force` only after a second explicit "yes".
   `--mirror` refuses (exit code 2) when the device folder is not named like the library, has no marker, or its
   marker names another library; `--force` does not lift these. The fix is one `sync --add` into the folder named
   like the library. Never point `--device-dir` at a shared folder like `/sdcard/Books`, and keep the library's folder
   free of other files: `--mirror` deletes whatever there is not in the library.

## Rules that do not bend

- Do not run `apply` yourself. Do not run `sync --mirror` yourself. Give the user the exact command.
- Never add `--yes` or `--force` on your own. Both go into a command only after the user has seen the list of files
  to be deleted and said yes.
- `apply` only after `repair` has finished building the AZW3 files and `verify` passed for those books.
- Never delete book formats in calibre and never delete files in the library folder. A duplicate format is the
  user's insurance against a bad conversion.
- What to remove from the library is the user's decision. Report, do not act.
- The library is read-only for every command except `apply`. Do not edit `metadata.db` or files in the library by
  hand, and do not run `calibredb` commands other than the one `apply` uses.

## Exit codes

| Command | 0 | 1 | 2 |
|---|---|---|---|
| `check`, `bold`, `build-dict`, `samples` | done | `samples`: book has no EPUB; any: no library | - |
| `repair` | done, also without `ebook-convert` or with skipped books | no library | - |
| `verify` | no discrepancies | discrepancies or no working folder | - |
| `azw3-check` | same letters | letters differ or conversion failed | - |
| `apply` | all `verified` or `already-applied` | any other status | calibre is open |
| `sync` | done | adb error, stop, size mismatch | no device, several devices, bad `--device-dir`, `--mirror` refused (no `--yes`, folder not named like the library, no marker, marker of another library, empty library, over half stale without `--force`) |

## What repair can and cannot do

Fixes: text in long headings, files that are 90%+ bold, `<p>` of only `�`, CJK characters from PDF debris (ru/en books only), the Latin
"Cyrillic" font, cp1251 read as latin-1, `#genre`. Only reports: mojibake (`РћРЅ`), broken images and links, empty
chapters, XHTML that does not parse, markup shown as text. Do not promise the user a fix for those. Suggest fixing
them in calibre's own editor or getting a better copy of the book.

## Language

Messages follow `--lang en|ru`, then `EBOOK_REPAIR_LANG`, then the locale. Use `--lang en` for this skill so the
lines above match the output.
