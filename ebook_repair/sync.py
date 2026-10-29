"""sync: the calibre library -> a folder on an Android device over adb, compared by path, size and modification time.
Default: only count new, changed and stale files. --add: push new and changed files and metadata.db, delete nothing.
--mirror: the same plus delete stale files and empty folders (irreversible, needs --yes). It deletes only in a folder
named like the library folder that already holds the marker file .ebook-repair-library with that name inside (written
by --add and --mirror, only into such a folder), never when the library has no book files, and asks for --force when
more than half of the device files would go.
Never pushed: .caltrash, full-text-search.db and other files in the library root except metadata.db and its prefs backup.
Paths are compared after NFD + casefold + NFC: device storage may not tell letter case apart."""
import os
import posixpath
import shlex
import sqlite3
import subprocess
import tempfile
import unicodedata
import uuid
from pathlib import Path

from .common import fail, open_db
from .i18n import t

ROOT_FILES = ('metadata.db', 'metadata_db_prefs_backup.json')
MARKER = '.ebook-repair-library'


def key(p):
    return unicodedata.normalize('NFC', unicodedata.normalize('NFD', p).casefold())  # casefold of NFC is not closed


def run(*a):
    """stdout as bytes (no text=True: a \\r in a name must not turn into \\n); any adb error stops everything."""
    r = subprocess.run(a, capture_output=True)
    if r.returncode:
        fail(t('sync_adb_failed', cmd=' '.join(a[:4]), err=r.stderr.decode(errors='replace').strip()[:300]))
    return r.stdout


def pick_device(serial):
    lines = run('adb', 'devices').decode(errors='replace').splitlines()[1:]
    ds = [f[0] for f in (x.split() for x in lines) if len(f) > 1 and f[1] == 'device']
    if serial:
        if serial not in ds:
            fail(t('sync_no_serial', serial=serial, devices=', '.join(ds) or '-'), rc=2)
        return serial
    if not ds:
        fail(t('sync_no_device'), rc=2)
    if len(ds) > 1:
        fail(t('sync_many_devices', devices=', '.join(ds)), rc=2)
    return ds[0]


def boom(e):
    raise e  # an unreadable library folder must not look like deleted books


def local_files(lib):
    """{relative path: (size, mtime)} of the files that go to the device, root files excluded."""
    loc = {}
    try:
        for root, ds, fs in os.walk(lib, onerror=boom):
            if Path(root) == Path(lib):
                ds[:] = [d for d in ds if d != '.caltrash']
                continue
            for f in fs:
                if f != '.DS_Store':  # .DS_Store on the device is junk and becomes stale
                    p = Path(root, f)
                    st = p.stat()
                    loc[p.relative_to(lib).as_posix()] = (st.st_size, int(st.st_mtime))
    except OSError as e:
        fail(t('sync_unreadable', err=e))
    return loc


def device_dir(lib, arg):
    """The folder on the device: --device-dir, else /sdcard/Books/<name of the library folder>."""
    if arg is None:
        name = Path(lib).resolve().name
        if name in ('', '.', '..'):
            fail(t('sync_bad_library_name', path=lib), rc=2)
        return '/sdcard/Books/' + name
    parts = arg.split('/')
    if not arg.startswith('/') or not arg.strip('/') or any(x in ('.', '..') for x in parts):
        fail(t('sync_bad_device_dir', path=arg), rc=2)
    return arg.rstrip('/')


def shown(p):
    """A path for the screen: control chars escaped, so one name never reads as two."""
    return ''.join(c if c.isprintable() else c.encode('unicode_escape').decode('ascii') for c in p)


def device_files(adb, remote):
    """({path: (size, mtime)} of files in subfolders, root file names, folder names) under the device folder."""
    out = adb('shell', f"if [ -d {shlex.quote(remote)} ]; then cd {shlex.quote(remote)} && "
                       f"find . -mindepth 1 -printf '%M|%s|%T@|%P\\0'; fi")
    dev, droot, ddirs = {}, [], []
    for line in out.decode('utf-8', 'surrogateescape').split('\0')[:-1]:  # NUL: a name may contain a newline
        m, s, tm, p = line.split('|', 3)
        if not p or p.startswith('/') or '..' in p.split('/'):
            fail(t('sync_bad_path', path=p))  # deletion stays inside the device folder
        if m.startswith('d'):
            ddirs.append(p)
        elif '/' in p:
            dev[p] = (int(s), int(tm.split('.')[0]))
        else:
            droot.append(p)
    return dev, droot, ddirs


def compare(loc, dev, droot, ddirs):
    """(new, changed, stale) or a refusal when a file and a folder share one path."""
    dl = {key(p): p for p in dev}
    ll = {key(p) for p in loc}
    if len(ll) != len(loc):
        fail(t('sync_case_twins'))
    anc = lambda ps: {key('/'.join(p.split('/')[:i])) for p in ps for i in range(1, p.count('/') + 1)}  # noqa: E731
    da, la = anc(dev) | {key(d) for d in ddirs}, anc(loc)
    clash = sorted({p for p in loc if key(p) in da} | {p for p in list(dev) + droot if key(p) in la})
    if clash:  # adb push would put the file inside the folder of the same name
        fail(t('sync_clash', n=len(clash), paths=clash[:5]))
    new = sorted(p for p in loc if key(p) not in dl)
    changed = sorted(p for p in loc if key(p) in dl and (dl[key(p)] != p or loc[p] != dev[dl[key(p)]]))
    stale = sorted(p for p in dev if key(p) not in ll)
    return new, changed, stale


def run_sync(a):
    lib = a.library
    db = open_db(lib)
    remote = device_dir(lib, a.device_dir)
    serial = pick_device(a.serial)
    adb = lambda *x: run('adb', '-s', serial, *x)  # noqa: E731
    loc = local_files(lib)
    dev, droot, ddirs = device_files(adb, remote)
    new, changed, stale = compare(loc, dev, droot, ddirs)
    dl = {key(p): p for p in dev}
    mb = lambda ps: round(sum(loc[p][0] for p in ps) / 2 ** 20)  # noqa: E731
    print(t('sync_count', remote=remote, serial=serial, dev=len(dev), loc=len(loc), new=len(new), new_mb=mb(new),
            changed=len(changed), changed_mb=mb(changed), stale=len(stale),
            case=sum(dl[key(p)] != p for p in changed)))
    if stale and (not (a.add or a.mirror) or (a.mirror and not a.yes)):
        print('\n'.join(shown(p) for p in stale))  # what --mirror --yes would delete
    if not (a.add or a.mirror):
        return 0
    name = Path(lib).resolve().name
    own = key(posixpath.basename(remote)) == key(name)  # e.g. /sdcard/Books itself is never the library's folder
    if a.mirror:
        if not a.yes:
            fail(t('sync_need_yes'), rc=2)
        if not own:
            fail(t('sync_folder_name', remote=remote, name=name), rc=2)
        if MARKER not in droot:  # never clean a folder this tool did not fill
            fail(t('sync_no_marker', remote=remote, marker=MARKER), rc=2)
        found = adb('shell', f'cat {shlex.quote(remote + "/" + MARKER)}').decode('utf-8', 'replace').strip()
        if found != name:
            fail(t('sync_marker_other', remote=remote, marker=MARKER, found=found[:80], name=name), rc=2)
        if not loc:
            fail(t('sync_empty_library', lib=lib), rc=2)
        if len(stale) * 2 > len(dev) and not a.force:
            fail(t('sync_too_many_stale', stale=len(stale), dev=len(dev)), rc=2)
    todo = new + changed
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp, 'metadata.db')
        dst = sqlite3.connect(str(snap))
        db.backup(dst)  # calibre may be open: backup gives a whole copy, WAL included
        ok = dst.execute('pragma integrity_check').fetchone()[0] == 'ok'
        dst.close()
        if not ok:
            fail(t('sync_bad_db'))
        for n, p in enumerate(todo, 1):
            adb('push', str(Path(lib, p)), f'{remote}/{p}')
            if n % 200 == 0:
                print(t('sync_progress', n=n, total=len(todo)), flush=True)
        adb('push', str(snap), f'{remote}/metadata.db')
        if own:  # the marker holds the library folder name; sync --mirror deletes only where it matches
            mark = Path(tmp, MARKER)
            mark.write_text(name + '\n', encoding='utf-8')
            adb('push', str(mark), f'{remote}/{MARKER}')
        else:
            print(t('sync_marker_skipped', remote=remote, name=name))
        if Path(lib, ROOT_FILES[1]).exists():
            adb('push', str(Path(lib, ROOT_FILES[1])), f'{remote}/{ROOT_FILES[1]}')
        if a.mirror and stale:
            lst = Path(tmp, 'stale.lst')
            lst.write_bytes(b''.join(p.encode('utf-8', 'surrogateescape') + b'\0' for p in stale))
            tmp_dev = f'/data/local/tmp/ebook-repair-{uuid.uuid4().hex}.lst'  # own name: nobody else's file is overwritten
            adb('push', str(lst), tmp_dev)
            adb('shell', f"cd {shlex.quote(remote)} && xargs -0 rm -f -- < {tmp_dev} && rm -f {tmp_dev}"
                         " && find . -mindepth 1 -type d -empty -delete")
    dev2, _, _ = device_files(adb, remote)
    d2 = {key(p): v[0] for p, v in dev2.items()}
    bad = [p for p in loc if d2.get(key(p)) != loc[p][0]]
    left = [p for p in dev2 if key(p) not in {key(x) for x in loc}]
    print(t('sync_done', pushed=len(todo), deleted=len(stale) if a.mirror else 0, bad=len(bad), left=len(left)),
          *bad[:5], sep='\n')
    return 1 if bad or (a.mirror and left) else 0
