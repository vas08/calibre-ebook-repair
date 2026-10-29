# calibre-ebook-repair

English | [Русский](README.ru.md)

Finds layout errors in the EPUB books of a calibre library and repairs the common ones. It was written for Russian
books, where conversions often go wrong in two ways: mojibake (`РћРЅ С€РµР»` instead of `Он шел`) and Russian text
set in an old "Cyrillic" font that is really Latin letters (`Ωizn´` instead of `жизнь`). The layout checks (book text
stuffed into a heading, a whole chapter in bold, broken links and images, markup shown as text) work for a book in
any language.

You need it if you have a calibre library of a few hundred books or more, some of them converted from PDF, FB2 or old
DOC files, and you want to find the broken ones without opening each book.

## What it finds and what it repairs

`check` counts problems per book; `repair` fixes some of them into a separate folder.

| Problem | `check` column | `repair` |
|---|---|---|
| Book text inside a heading (a heading over 300 characters) | `long_headings`, `max_heading` | Fixed. Lines split by `<br/>` become paragraphs; a short first line (150 characters or less) stays the heading, and an NCX entry pointing at that chapter gets it as the label. Without `<br/>` the whole heading becomes a paragraph. A long heading that starts like a chapter title (`Chapter II. ...`) and has no `<br/>` is left alone. |
| Bold text over 1500 characters in a row | `long_bold` | Fixed only where 90% or more of a file's text (300 characters or more) is bold: the bold is removed, headings keep theirs. Use `bold` to see the share per file. |
| Mojibake: UTF-8 read as cp1251 or latin-1 (`РћРЅ`, `Ã©`) | `mojibake` | Not fixed. |
| Replacement characters `�` | `replacement_chars` | A `<p>` made only of `�` is removed. Table cells, list items and headings made of `�` stay, and so does a `�` inside normal text. |
| Chinese, Japanese or Korean glyphs pasted into a paragraph (debris of a PDF stream) | none | Only in books that calibre marks as Russian or English, and only in a paragraph, list item, table cell or heading with more than 3 of them: the CJK ideographs and Hangul are removed, everything else there (dashes, quotes, narrow spaces, other symbols) stays. Books in any other language, or with no language set, are not touched. |
| Russian text in a Latin "Cyrillic" font (`ä`=я, `Ω`=ж, `ç`=ч) | none, see Limitations | Decoded to Cyrillic. Needs the word list from `build-dict`. |
| cp1251 words read as latin-1 (`ñåêòà` = `секта`) | none | Decoded when the result is in the word list and a file has 3 or more such words. Needs `build-dict`. |
| Broken genre name `#genre` | none | Renamed to `Без жанра` in a Russian book, `Other` in any other, in the OPF, the NCX and in `<title>`, `<h1>`, `<a>`. |
| Image that is not in the book | `broken_images` | Not fixed. |
| Link to a file that is not in the book | `broken_links` | Not fixed. |
| Chapter file with under 40 characters of text and no image | `empty_chapters` | Not fixed. |
| XHTML that does not parse | `broken_xhtml` | Not fixed; `repair` copies such a file unchanged. |
| Markup shown as text (`&lt;p&gt;`) | `markup_as_text` | Not fixed. |

For every repaired EPUB `repair` also builds an AZW3 next to it with calibre's `ebook-convert`. Without
`ebook-convert` it still repairs the EPUB files and skips the AZW3 (see Requirements). The DOCTYPE of every file and
CDATA sections are kept. XML is parsed without external entities and without network access. A book that cannot be
processed at all (for example, an OPF that is not UTF-8) is skipped with `book N: skipped, cannot repair: ...`, and the
run goes on.

## Safety

- `check`, `samples`, `bold`, `build-dict`, `repair`, `verify`, `azw3-check` and `sync` only read the library. `metadata.db`
  is opened read-only. Reports and repaired files go to the working folder (`./ebook-repair-work` by default).
  `build-dict` writes its word lists to `data/` inside this repository.
- Only `apply` writes to the library. Run it yourself, with the calibre program closed: while calibre is open,
  `calibredb` does not write, and `apply` stops with exit code 2.
- `repair` records what each book was made from in `<workdir>/<id>/source.json`: the resolved library path, the book id,
  the md5 of the library EPUB and the md5 of its AZW3 (`null` if the book has none). A new `repair` of a book deletes its old `verified.json`.
- `verify` compares the repaired EPUB with the library copy using its own list of allowed changes, not the repair code.
  Allowed: CJK ideographs removed in a Russian or English book, only where a block has more than 3 of them (a long
  heading is counted line by line between its `<br/>`); a `<p>` made only of `�` removed; a cp1251 word read as
  latin-1 turned back, if all its letters are in U+00C0-U+00FF; a word of the Latin "Cyrillic" font decoded, only in a
  file where at least 15% of the original's words carry the font's special characters; heading text moved into
  paragraphs; bold removed; `#genre` renamed. Everything else must match character for character. Ids
  and links must stay, other files must match byte for byte, the DOCTYPE must stay, and the book must come from this
  library with its EPUB unchanged since `repair`. A book with 0 discrepancies gets `<workdir>/<id>/verified.json` with
  the md5 of the original and the repaired files; a book with any discrepancy loses it.
- If the library already holds exactly the repaired EPUB and AZW3, `apply` reports `already applied` and does nothing,
  with or without `verified.json`. Otherwise it replaces a book only if its `verified.json` exists, the repaired files
  still have the md5 recorded there, they were made from this library, and the library EPUB is still the one they
  were made from. Anything else is refused with a status (see Commands) and the book is not touched.
- One exception finishes a half-done replacement: if the library EPUB is already the repaired one but the AZW3 is still
  the one recorded in `source.json` (for example, `calibredb` did not write the AZW3), `apply` replaces only the AZW3
  and backs up only the AZW3.
- Before replacing, `apply` copies both current formats to `<workdir>/<id>/before/<name>.<md5[:8]>.<ext>`. Each version
  is kept once and nothing there is overwritten. To roll a book back, put the copy back with calibre's own command:
  `calibredb add_format --with-library "<library>" <id> "<workdir>/<id>/before/<name>.<md5>.epub"`.
- After each replacement `apply` compares the md5 of the file in the library with the repaired one. On the first
  mismatch it stops working on that book.
- `sync` deletes nothing on the device without `--mirror --yes`, and even then only in a folder named like the
  library that holds a marker file with the library's name (see Commands).

## Requirements

- Python 3.9 or newer.
- `lxml` 5 or newer, for every command.
- `Pillow`, optional. Some damaged images (a truncated JPEG, for one) make calibre fail on the AZW3; `repair` rebuilds
  them with Pillow for the AZW3 only. Without Pillow it prints
  `Pillow is not installed: damaged images go into AZW3 as they are (pip install pillow)` once and goes on. If calibre
  then fails on such an image, the `azw3` column of `repair.tsv` shows `ERROR ...`.
- `fontTools`, optional. With it `verify` also reports new characters that the book's embedded fonts cannot show.
- calibre, for `azw3-check`, `apply` and the AZW3 step of `repair` (`ebook-convert` and `calibredb`). The tool looks for
  them in `PATH`, then in `/Applications/calibre.app/Contents/MacOS`. Without `ebook-convert`, `repair` prints
  `calibre program ebook-convert not found: EPUB files are repaired, AZW3 is skipped`, repairs the EPUB files and writes
  `skipped (no ebook-convert)` in the `azw3` column. `apply` needs both files, so such books then fail `verify`
  (`AZW3 missing or older than EPUB`) and `apply` (`NO REPAIRED FILE azw3`) until you install calibre and rerun
  `repair`.
- `adb` (Android platform tools), only for `sync`.

## Install

```sh
cd ~
git clone https://github.com/vas08/calibre-ebook-repair.git
cd calibre-ebook-repair
pip install "lxml>=5" pillow
```

There is nothing else to install. Run the commands from the repository folder: `python3 -m ebook_repair <command>`.

## Quick start

Tell the tool where the library is, once per shell, or add `--library PATH` to every command:

```sh
export CALIBRE_LIBRARY="$HOME/Calibre Library"
```

1. Scan the library. The full report goes to `ebook-repair-work/check.tsv`, one row per book.

   ```sh
   python3 -m ebook_repair check
   ```

   ```
   checked 15; with findings 10; report ebook-repair-work/check.tsv
     long_headings: books 1, total 1
     long_bold: books 1, total 1
     mojibake: books 1, total 149
     ...
   ```

2. Look at the raw markup before you trust a number. The book id is the one calibre shows (column "id" in the
   report):

   ```sh
   python3 -m ebook_repair samples heading 2
   ```

3. Build the word lists from your own books. Only books marked as Russian or English in calibre are read.

   ```sh
   python3 -m ebook_repair build-dict
   ```

4. Repair. Without ids every book with an EPUB is processed, and only books with something to fix get a folder
   `ebook-repair-work/<id>/` with the new EPUB and AZW3. The counters go to `ebook-repair-work/repair.tsv`.

   ```sh
   python3 -m ebook_repair repair 2 7
   ```

5. Check the result. Each line above `DISCREPANCIES 0` names a book and what is wrong with it; that book will not be
   applied. Books with no discrepancies get `verified.json`, and only they can be applied.

   ```sh
   python3 -m ebook_repair verify
   ```

6. Check that the AZW3 lost no letters (one book at a time):

   ```sh
   python3 -m ebook_repair azw3-check "ebook-repair-work/7/Title - Author.epub"
   ```

7. Make sure `repair` has finished: it builds each AZW3 right after the EPUB, and `apply` needs both. Close calibre.
   Then replace the books in the library. Books that did not pass `verify` are refused with `NOT VERIFIED`:

   ```sh
   python3 -m ebook_repair apply
   ```

8. Optional: copy the library to an Android reader. The first command only counts; the second pushes new and changed
   files, puts the marker file `.ebook-repair-library` into the device folder and deletes nothing.

   ```sh
   python3 -m ebook_repair sync
   python3 -m ebook_repair sync --add
   ```

   Check the books on the reader. To remove from the device the files that are no longer in the library, count again,
   read the list of stale files under the numbers, and only then delete them:

   ```sh
   python3 -m ebook_repair sync
   python3 -m ebook_repair sync --mirror --yes
   ```

## Commands

Global options go before or after the command:

| Option | Default |
|---|---|
| `--lang en\|ru` | see "Message language" |
| `--library PATH` | `$CALIBRE_LIBRARY`, else `~/Calibre Library` |
| `--workdir PATH` | `./ebook-repair-work`, relative to the current folder |

`check`, `bold`, `repair` and `build-dict` take book ids or `--tag NAME`. With neither, they take every book with an
EPUB.

| Command | What it does | Exit code |
|---|---|---|
| `check [ID ...] [--tag NAME]` | Counts problems, writes `<workdir>/check.tsv`. Each run overwrites the report. | 0, findings or not |
| `samples KIND ID [COUNT]` | Prints raw markup around the first COUNT findings (3 by default). KIND: `heading`, `bold`, `replacement`, `mojibake`, `markup`, `empty`. | 0; 1 if the book has no EPUB |
| `bold [ID ...] [--tag NAME]` | Per book: number of HTML files and how many of them (over 2000 characters) are more than 30% bold, with the worst six. | 0 |
| `build-dict [ID ...] [--tag NAME]` | Writes `data/dict_ru.tsv` and `data/dict_en.tsv` from the books. | 0 |
| `repair [ID ...] [--tag NAME]` | Repairs into `<workdir>/<id>/`, builds an AZW3, updates `<workdir>/repair.tsv`. | 0 |
| `verify [ID ...]` | Compares `<workdir>/<id>/` with the library, writes `verified.json` for each book with no discrepancies and deletes it for the rest. Default: every `<workdir>/<id>` folder. The dictionaries are not needed. | 0 if no discrepancies; 1 otherwise |
| `azw3-check EPUB` | Converts the EPUB and the AZW3 next to it to text and compares the letters. | 0 if the same; 1 otherwise |
| `apply [ID ...]` | Checks `verified.json` and `source.json`, backs up, replaces EPUB and AZW3 through `calibredb add_format`, checks md5, appends a status per book to `<workdir>/apply.log`. | 0 if every book is `verified` or `already-applied`; 1 if any other status, or `calibredb` is missing; 2 if calibre is open |
| `sync [--add \| --mirror] [--yes] [--force] [--serial S] [--device-dir DIR]` | Compares the library with a folder on the device by path, size and time. | 0; 1 on an adb error, a stop (see below), a size mismatch after pushing, or files left that are not in the library after `--mirror`; 2 if there is no device, several devices without `--serial`, a bad `--device-dir`, or `--mirror` is refused |

`apply` statuses, on screen and in `apply.log`:

| Screen | Log | Meaning |
|---|---|---|
| `verified` | `verified` | Replaced and the md5 matches. |
| `already applied: ...` | `already-applied` | The library already holds exactly these repaired files. Checked before `verified.json`, so `verify` after `apply` does not break it. Counts as success. |
| `NOT VERIFIED` | `not-verified` | No `verified.json`: run `verify`. |
| `OTHER LIBRARY` | `other-library` | The files were repaired from another library. |
| `CHANGED AFTER VERIFY` | `changed-after-verify` | The repaired files are not the ones `verify` passed: run `verify` again. |
| `LIBRARY CHANGED` | `library-changed` | The book in calibre is not the copy `repair` started from. Left as it is. |
| `NO REPAIRED FILE <fmt>` | `no-file <fmt>` | The working folder lacks the EPUB or the AZW3. |
| `NO BACKUP <fmt>` | `no-backup <fmt>` | The backup copy failed; nothing replaced. |
| `MISMATCH <fmt>` | `mismatch <fmt>` | After the replacement the library file has another md5. `apply` stops on this book. |

`verify` also reports a book with no `source.json` (`no source.json: repair this book again`), one repaired from
another library, one whose library EPUB changed after `repair`, a changed DOCTYPE and a book it cannot read
(`cannot check: ...`).

More on `sync`:

- The device folder is `--device-dir`, else `/sdcard/Books/<name of the library folder>`, taken from the resolved path,
  so `--library .` gives the real name. A library path with no folder name needs `--device-dir`. `--device-dir` must
  be an absolute path, not `/`, without `.` or `..`.
- Without flags it prints counts of new, changed and stale files, then the stale files one per line, relative to the
  device folder. These are the files `--mirror --yes` would delete. Control characters in names are shown escaped
  (`\n`). Nothing is changed.
- `--add` pushes new and changed files, a checked copy of `metadata.db` and `metadata_db_prefs_backup.json`. It deletes
  nothing. If the device folder is named like the library folder (letter case aside), it also writes the marker file
  `.ebook-repair-library` with the library folder name inside. Into any other folder, such as `/sdcard/Books` itself, it
  pushes the books but writes no marker and prints
  `marker not written: <folder> is not named like the library (<name>); --mirror will refuse there`.
- `--mirror --yes` does the same and deletes stale files and empty folders inside the device folder. This cannot be
  undone. It refuses with exit code 2 and changes nothing when:
  - `--yes` is missing (it then prints the counts and the stale list);
  - the device folder is not named like the library folder
    (`stop: --mirror deletes only in a folder named like the library: ...`);
  - the device folder has no `.ebook-repair-library` left by an earlier `--add` or `--mirror`;
  - the marker names another library (`stop: .../.ebook-repair-library names "...", not this library "..."`);
  - the library has no book files at all (`--force` does not help here);
  - more than half of the files on the device would be deleted and `--force` is not given.

  `--force` lifts only the last refusal. A missing or foreign marker in a folder named like the library is fixed by one
  `sync --add` into that folder.
- Never pushed: `.caltrash`, `full-text-search.db`, `.DS_Store` and other files in the library root.
- Names are compared case-insensitively, because phone storage often is. If two library files differ only in letter
  case, a file and a folder share a path, or a library folder cannot be read (`stop: a library folder cannot be read`),
  `sync` stops with exit code 1 and changes nothing.

## Message language

English and Russian. The choice, first match wins: `--lang`, then the `EBOOK_REPAIR_LANG` variable, then the locale
(`LC_ALL`, then `LANG`; anything starting with `ru` gives Russian), else English.

```sh
python3 -m ebook_repair --lang ru check
```

TSV column names, the `repair` counters (`headings_split=1 azw3=ok`), the `verify` statistics (`books`,
`words_decoded`, ...) and the status words in `apply.log` stay in English in both languages.

## Using it from Claude Code

The `skill/` folder has a Claude Code skill in two versions, `skill/en/ebook-repair` and `skill/ru/ebook-repair-ru`.
Copy the one you want, or both:

```sh
mkdir -p ~/.claude/skills
cp -r skill/en/ebook-repair ~/.claude/skills/
```

The skill expects the repository in `~/calibre-ebook-repair`; elsewhere Claude looks for it or asks you. It makes
Claude follow the order above: find, show samples, repair into the working folder, verify. The skill tells Claude not
to run `apply` or `sync --mirror` itself but to give you the command, and never to add `--yes` or `--force` before you
have seen the list of files to be deleted and said yes.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

The tests build a small calibre library from invented text in a temporary folder, one book per error class, and use
fake `ebook-convert`, `calibredb`, `pgrep` and `adb`. Three tests need the real `ebook-convert` and are skipped without calibre, as
they are in CI. CI runs the tests on Python 3.9 and 3.14.

## Limitations

- `check` does not see the Latin "Cyrillic" font, cp1251 read as latin-1, or `#genre`. Only `repair` finds them; read
  its counters `files_from_latin_font`, `words_from_latin1`, `genre_renamed`.
- Decoding the Latin font and latin-1 in `repair` needs a word list built from the library's own books (`build-dict`).
  Without it `repair` says so and does the rest. A library with few Russian books gives a short list and fewer decoded
  files.
- In a book marked Russian or English, a block with more than 3 CJK characters counts as PDF debris and loses them.
- `verify` checks Latin-font words against its own table, which is looser than the repair decoder. It can let some
  wrong decodings through.
- In a Latin-font file an English word without the font's special characters stays as it is only when the English
  list from `build-dict` has it. `repair` turns other English words into Cyrillic (`Clean` → `Цлеан`), and `verify`
  does not see it. The more English books the library has, the fuller that list.
- `verify` allows Latin-font decoding only in a file where at least 15% of the original's words carry the font's
  special characters. `repair` decodes a file at a lower share, so a file with only a little text in that font can be
  repaired and then fail `verify` with `text diverged`; `apply` will not take that book.
- A `<p id="...">` made only of `�` is removed by `repair`, but `verify` then reports a lost id (`ids/links changed`),
  so `apply` will not take that book.
- Working folders made by an earlier version of this tool have no `source.json`. `verify` reports
  `no source.json: repair this book again`: run `repair` on those books again.
- `verify` after `apply` reports `the EPUB in the library changed after repair` (the library now holds the repaired
  file), exits with 1 and deletes `verified.json`. The book in the library is fine, and a later `apply` still says
  `already applied`.
- The marker only says which library filled the folder. If a folder named like the library also holds files that did
  not come from it, `--mirror --yes` deletes them, as long as they are not more than half of the folder. Keep that
  folder for this library only.
- `verify` treats an AZW3 under 10000 bytes as missing. On a very small book you may see `AZW3 missing or older than
  EPUB` even though the conversion worked; check that book with `azw3-check`.
- In `--lang ru` help, argparse's own words (`usage`, `positional arguments` and the like) stay in English.
- `apply` detects an open calibre only with `pgrep -x calibre`. On Windows there is no `pgrep`, so the check passes
  silently: close calibre yourself. Windows is not supported.
- `sync` has been tested only against a fake `adb`, not on a real device. Whether `adb push` keeps file times is not
  confirmed; if it does not, the next `sync` will count those files as changed again.
- CI has no calibre, so the three `ebook-convert` tests are skipped there.

## License

MIT, see [LICENSE](LICENSE).
