"""Shared helpers: calibre library and programs, book selection, XML parser, md5 records, AZW3 source."""
import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
import zipfile
from pathlib import Path

from lxml import etree

from .i18n import t

MAC = Path('/Applications/calibre.app/Contents/MacOS')
HTML_EXT = ('.xhtml', '.html', '.htm')
IMAGE_EXT = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp')


def parse(data):
    """Every XML in a book goes through here: no entity expansion, no network, CDATA kept as CDATA."""
    return etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False,
                                                  strip_cdata=False, huge_tree=False))


def text_slots(el, own_tail=False):
    """(node, 'text' or 'tail') for every piece of text under el in document order: text only of elements (an entity
    such as &nbsp; kept by the parser has a read-only text), tails of every node; el's own tail only with own_tail."""
    if isinstance(el.tag, str):
        yield el, 'text'
    for c in el:
        yield from text_slots(c, True)
    if own_tail:
        yield el, 'tail'


def texts(el):
    return [s for n, a in text_slots(el) for s in [getattr(n, a)] if s]


def map_text(el, fn):
    """Replaces every piece of text under el (not el's own tail) by fn(piece)."""
    for n, a in list(text_slots(el)):
        s = getattr(n, a)
        if s:
            setattr(n, a, fn(s))


def md5(p):
    h = hashlib.md5()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def read_record(p):
    """source.json / verified.json of a book's working folder, or None."""
    try:
        return json.loads(Path(p).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def write_record(p, data):
    tmp = Path(str(p) + '.tmp')
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding='utf-8')
    tmp.replace(p)


def library_id(lib):
    """How a working folder remembers its library: the resolved path."""
    return str(Path(lib).resolve())


def fail(msg, rc=1):
    print(msg, file=sys.stderr)
    raise SystemExit(rc)


def warn(msg):
    print(msg, file=sys.stderr, flush=True)


warned = set()


def warn_once(key):
    if key not in warned:
        warned.add(key)
        warn(t(key))


def library_path(arg=None):
    return str(Path(arg or os.environ.get('CALIBRE_LIBRARY') or Path.home() / 'Calibre Library').expanduser())


def open_db(lib):
    """metadata.db read-only; a missing library gives a clear line instead of "unable to open database file"."""
    p = Path(lib, 'metadata.db')
    if not p.exists():
        fail(t('no_library', path=p))
    return sqlite3.connect(p.resolve().as_uri() + '?mode=ro', uri=True)


def find_program(name):
    return shutil.which(name) or (str(MAC / name) if (MAC / name).exists() else None)


def program(name):
    p = find_program(name)
    if not p:
        fail(t('no_program', name=name))
    return p


def select_books(db, ids, tag):
    if ids:
        return [int(x) for x in ids]
    if tag:
        return [r[0] for r in db.execute(
            'select l.book from books_tags_link l join tags t on t.id = l.tag where t.name = ? order by l.book', (tag,))]
    return [r[0] for r in db.execute("select distinct book from data where format = 'EPUB' order by book")]


def book_file(db, i, fmt='EPUB'):
    """(folder inside the library, file name without extension) or None."""
    return db.execute('select b.path, d.name from books b join data d on d.book = b.id where b.id = ? and d.format = ?',
                      (i, fmt)).fetchone()


def book_lang(db, i):
    r = db.execute('select l.lang_code from books_languages_link bl join languages l on l.id = bl.lang_code '
                   'where bl.book = ?', (i,)).fetchone()
    return {'rus': 'ru', 'ru': 'ru', 'eng': 'en', 'en': 'en'}.get(r[0] if r else '')


def epub_path(db, lib, i):
    row = book_file(db, i)
    if row is None:
        warn(t('no_epub', id=i))
        return None, None
    return Path(lib, row[0], row[1] + '.epub'), row[1]


def open_epub(db, lib, i):
    p, name = epub_path(db, lib, i)
    if p is None:
        return None, None
    try:
        return zipfile.ZipFile(p), name
    except (OSError, zipfile.BadZipFile) as e:
        warn(t('unreadable', id=i, err=e))
        return None, None


def azw3_source(epub, tmp):
    """calibre cannot put a damaged image (truncated JPEG with EXIF) into AZW3: it re-saves it with PIL and fails.
    For AZW3 such an image is replaced by its readable part re-encoded by PIL; the EPUB itself is not changed.
    Returns the file for ebook-convert and the names of replaced images. Without Pillow nothing is replaced."""
    try:
        from PIL import Image, ImageFile, ImageOps
    except ImportError:
        warn_once('no_pillow')
        return epub, []

    def load(b):
        im = Image.open(io.BytesIO(b))
        im.load()
        return im
    fixed = {}
    with zipfile.ZipFile(epub) as z:
        for n in z.namelist():
            if not n.lower().endswith(IMAGE_EXT):
                continue
            b = z.read(n)
            try:
                load(b)
                continue
            except Exception:
                pass
            ImageFile.LOAD_TRUNCATED_IMAGES = True
            try:
                im = load(b)
                o = io.BytesIO()
                # EXIF is dropped, so rotation goes into pixels; the colour profile stays or colours shift
                ImageOps.exif_transpose(im).save(o, im.format, quality=95, icc_profile=im.info.get('icc_profile'))
                fixed[n] = o.getvalue()
            except Exception:
                pass  # not even partly readable: calibre drops it
            finally:
                ImageFile.LOAD_TRUNCATED_IMAGES = False
        if not fixed:
            return epub, []
        dst = Path(tmp) / Path(epub).name
        with zipfile.ZipFile(dst, 'w') as zo:
            for i in z.infolist():
                zo.writestr(i, fixed[i.filename] if i.filename in fixed else z.read(i))
    return dst, sorted(fixed)
