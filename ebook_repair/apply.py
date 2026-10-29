"""apply: repaired files from <workdir>/<id>/ go into calibre as the EPUB and AZW3 of the same book, only when verify
passed on exactly these files (verified.json), they were made from this library (source.json) and the book's EPUB in
the library is still the one they were made from. Every format version the library had is first copied to
<workdir>/<id>/before/<name>.<md5[:8]>.<ext>, once per content, nothing is overwritten. After each replacement the
file in the library must have the md5 of the repaired one. Log: <workdir>/apply.log, one "id<TAB>status" line per book."""
import shutil
import subprocess
from pathlib import Path

from .common import book_file, fail, library_id, md5, open_db, program, read_record
from .i18n import t

FORMATS = ('epub', 'azw3')
DONE = ('verified', 'already-applied')


def calibre_running():
    """With the calibre program open, calibredb silently does not write to the library."""
    try:
        return subprocess.run(['pgrep', '-x', 'calibre'], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0
    except FileNotFoundError:
        return False


def library_file(lib, i, fmt):
    row = book_file(open_db(lib), i, fmt.upper())  # a fresh connection sees what calibredb has just written
    return Path(lib, row[0], f'{row[1]}.{fmt}') if row else None


def backup(d, cur):
    """Copies a library file to before/<name>.<md5[:8]>.<ext> unless that content is already kept."""
    bk = d / 'before' / f'{cur.stem}.{md5(cur)[:8]}{cur.suffix}'
    if not bk.exists():
        bk.parent.mkdir(exist_ok=True)
        tmp = bk.with_name(bk.name + '.tmp')  # an interrupted copy must not pass for a backup
        shutil.copy2(cur, tmp)
        tmp.replace(bk)


def apply_book(lib, cdb, d, i):
    """Status: 'verified', 'already-applied', 'no-file <fmt>', 'not-verified', 'other-library', 'changed-after-verify',
    'library-changed', 'no-backup <fmt>' or 'mismatch <fmt>'."""
    new = {f: sorted(d.glob('*.' + f)) for f in FORMATS}
    for f in FORMATS:  # both files must be there before anything is replaced
        if len(new[f]) != 1:
            return f'no-file {f}'
    nm = {f: md5(new[f][0]) for f in FORMATS}
    cur = {f: library_file(lib, i, f) for f in FORMATS}
    have = {f: md5(cur[f]) if cur[f] is not None and cur[f].exists() else None for f in FORMATS}
    if have == nm:  # before the verify record: verify after apply removes it, and that is no reason to fail
        return 'already-applied'
    ver, src = read_record(d / 'verified.json'), read_record(d / 'source.json')
    if ver is None:
        return 'not-verified'
    if src is None or src.get('library') != library_id(lib) or ver.get('library') != library_id(lib):
        return 'other-library'
    if nm['epub'] != ver.get('epub_md5') or nm['azw3'] != ver.get('azw3_md5'):
        return 'changed-after-verify'
    if have['epub'] == nm['epub'] and 'source_azw3_md5' in src and have['azw3'] == src['source_azw3_md5']:
        todo = ('azw3',)  # an earlier apply stopped after the EPUB: finish the AZW3
    elif have['epub'] != ver.get('source_md5') or have['epub'] != src.get('source_md5'):
        return 'library-changed'  # the book was changed after repair: its current copy is not touched
    else:
        todo = FORMATS
    for f in todo:  # all backups before the first replacement; a format the book lacks is simply added
        if have[f] is not None:
            try:
                backup(d, cur[f])
            except OSError:
                return f'no-backup {f}'
    for f in todo:
        with open(d / 'apply.out', 'ab') as log:
            subprocess.run([cdb, 'add_format', '--with-library', lib, str(i), str(new[f][0])],
                           stdout=log, stderr=subprocess.STDOUT)
        now = library_file(lib, i, f)
        if now is None or not now.exists() or md5(now) != nm[f]:
            return f'mismatch {f}'  # stop at the first mismatch
    return 'verified'


def run_apply(a):
    cdb = program('calibredb')
    open_db(a.library)  # a clear line when the library is missing
    if calibre_running():
        fail(t('apply_calibre_open'), rc=2)
    if not a.workdir.is_dir():
        fail(t('no_workdir', path=a.workdir))
    ids = [int(x) for x in a.ids] or sorted(int(p.name) for p in a.workdir.iterdir() if p.is_dir() and p.name.isdigit())
    bad = 0
    for i in ids:
        status = apply_book(a.library, cdb, a.workdir / str(i), i)
        with open(a.workdir / 'apply.log', 'a', encoding='utf-8') as f:
            f.write(f'{i}\t{status}\n')
        code, _, fmt = status.partition(' ')
        print(i, t('apply_' + code.replace('-', '_'), fmt=fmt))
        bad += status not in DONE
    print(t('apply_total', n=len(ids), bad=bad, log=a.workdir / 'apply.log'))
    return 1 if bad else 0
