"""A tiny calibre library built in a temp folder: metadata.db with the tables the code reads and EPUB books made of
invented text, one book per class of error, plus fake calibre/adb programs for tests that must not touch real ones."""
import io
import os
import sqlite3
import stat
import struct
import sys
import zipfile
import zlib
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ebook_repair.cli import main  # noqa: E402

AUTHOR = 'Fixture Author'
EN = ('The small boat left the harbour before dawn. Nobody on the pier saw it go, and the gulls were still asleep on '
      'the warm roofs. By noon the wind had turned, and the sailor tied the sail down with a length of old rope. '
      'He counted the hours by the colour of the water and sang a song about a lighthouse that nobody had built. ')
RU = ('Он шел по тихой улице и думал, что зимой здесь бывает темно. Она ждала его у старого моста, как они и '
      'договорились. Мы не знали, что из этого выйдет, но все шли вперед. Вы слышите, как за рекой поет ночная птица? '
      'Так бывает только летом, когда от воды идет тепло. Для него это было важно, ведь его дом стоял на самом краю '
      'деревни. Они пили чай из большой кружки и говорили о работе, о жизни, о детях. Если бы не дождь, они бы ушли '
      'на поле еще утром. Это был длинный день, и вечер пришел тихо. ')
LATIN_FONT = {'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ж': 'Ω', 'з': 'z', 'и': 'i', 'й': 'j',
              'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
              'ф': 'f', 'х': 'x', 'ц': 'c', 'ч': '\xe7', 'ш': '‚', 'щ': 'w', 'ъ': '`', 'ы': 'y', 'ь': '\xb4',
              'э': 'q', 'ю': '\xfc', 'я': '\xe4'}
ZH = '小船在黎明前离开了港口。码头上没有人看见它离开，海鸥还在温暖的屋顶上睡觉。'  # invented, like the rest
# CJK pasted into an English paragraph next to chars that must all survive: dashes, quotes, narrow no-break space,
# arrows, a black square and a stray replacement char
CJK_PARA = 'In Beijing the sign read 中华人民共和国 — “welcome”… 10\u202f000 people, ↑ see ⬛ \ufffd here.'
# one ideograph (fewer than 4 in a block: repair keeps it) and a real latin-1 word
LONE = 'One sign: 港 only. A café by the pier.'
LATIN1_WORDS = ['тихой', 'старого', 'моста', 'деревни', 'кружки']


def to_latin_font(s):
    return ''.join(LATIN_FONT.get(c.lower(), c).upper() if c.isupper() and c.lower() in LATIN_FONT
                   else LATIN_FONT.get(c, c) for c in s)


def latin1(w):
    return w.encode('cp1251').decode('latin-1')


def png():
    """A valid 2x2 grey PNG without any imaging library."""
    def chunk(k, d):
        return struct.pack('>I', len(d)) + k + d + struct.pack('>I', zlib.crc32(k + d) & 0xffffffff)
    raw = b''.join(b'\0' + b'\x80\x80' for _ in range(2))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 2, 2, 8, 0, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


def xhtml(title, body):
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" '
            '"http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">\n<html xmlns="http://www.w3.org/1999/xhtml">'
            f'<head><title>{title}</title></head><body>{body}</body></html>')


def epub(path, title, lang, chapters, toc=None, subjects=(), images=None, raw=None):
    """chapters: {file name: body}; raw: {file name: whole file text} for files that must stay malformed."""
    files = {n: xhtml(title, b) for n, b in chapters.items()}
    files.update(raw or {})
    toc = toc or [(n, n) for n in chapters]
    items = ''.join(f'<item id="x{k}" href="{n}" media-type="application/xhtml+xml"/>' for k, n in enumerate(files))
    items += ''.join(f'<item id="i{k}" href="{n}" media-type="image/png"/>' for k, n in enumerate(images or {}))
    opf = ('<?xml version="1.0" encoding="utf-8"?>\n<package xmlns="http://www.idpf.org/2007/opf" version="2.0" '
           'unique-identifier="uid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           f'<dc:title>{title}</dc:title><dc:creator>{AUTHOR}</dc:creator><dc:language>{lang}</dc:language>'
           f'<dc:identifier id="uid">fixture-{title}</dc:identifier>'
           + ''.join(f'<dc:subject>{s}</dc:subject>' for s in subjects) +
           '</metadata><manifest><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
           f'{items}</manifest><spine toc="ncx">'
           + ''.join(f'<itemref idref="x{k}"/>' for k in range(len(files))) + '</spine></package>')
    ncx = ('<?xml version="1.0" encoding="utf-8"?>\n<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
           f'<head><meta name="dtb:uid" content="fixture-{title}"/></head><docTitle><text>{title}</text></docTitle><navMap>'
           + ''.join(f'<navPoint id="n{k}" playOrder="{k + 1}"><navLabel><text>{lab}</text></navLabel>'
                     f'<content src="{src}"/></navPoint>' for k, (lab, src) in enumerate(toc)) + '</navMap></ncx>')
    container = ('<?xml version="1.0"?>\n<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                 '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                 '</rootfiles></container>')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo('mimetype'), 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        z.writestr('META-INF/container.xml', container)
        z.writestr('OEBPS/content.opf', opf)
        z.writestr('OEBPS/toc.ncx', ncx)
        for n, s in files.items():
            z.writestr('OEBPS/' + n, s)
        for n, b in (images or {}).items():
            z.writestr('OEBPS/' + n, b)


def p(s):
    return f'<p>{s}</p>'


# id -> (title, language, epub keyword arguments, check column it must show or None for repair-only classes)
BOOKS = {
    1: ('Clean', 'ru', dict(chapters={'ch1.xhtml': p(RU) * 2, 'ch2.xhtml': p(RU)}), None),
    2: ('Long heading', 'en', dict(
        chapters={'ch1.xhtml': f'<div id="c1"><h2>Harbour<br/>{EN * 2}</h2>{p(EN)}<p><img src="images/pic.png" alt="pic"/></p></div>',
                  'ch2.xhtml': f'<div id="c2"><h2>Second part</h2>{p(EN)}{p(LONE)}</div>'},
        toc=[('Harbour old', 'ch1.xhtml#c1'), ('Second part', 'ch2.xhtml#c2')],
        images={'images/pic.png': png()}), 'long_headings'),
    3: ('Bold', 'en', dict(chapters={'ch1.xhtml': p(f'<b>{EN * 6}</b>') + p(f'<b>{EN}</b>') + p('A plain line.')}), 'long_bold'),
    4: ('Mojibake', 'ru', dict(chapters={'ch1.xhtml': p(RU.encode('utf-8').decode('cp1251'))}), 'mojibake'),
    5: ('Latin1', 'ru', dict(chapters={'ch1.xhtml': p(RU) + p(' '.join(latin1(w) for w in LATIN1_WORDS))}), None),
    6: ('Replacement', 'en', dict(chapters={'ch1.xhtml': p(EN + 'A stray � mark.') + p('���') + p(EN)}),
        'replacement_chars'),
    7: ('Latin font', 'ru', dict(chapters={'ch1.xhtml': p(to_latin_font(RU)) * 3}), None),
    8: ('Genre', 'en', dict(chapters={'ch1.xhtml': p(EN), 'genre.xhtml': f'<h1>#genre</h1>{p(EN)}'},
                            toc=[('Start', 'ch1.xhtml'), ('#genre', 'genre.xhtml')], subjects=['#genre']), None),
    9: ('Broken image', 'en', dict(chapters={'ch1.xhtml': p(EN) + '<p><img src="images/missing.png" alt="x"/></p>'}),
        'broken_images'),
    10: ('Broken link', 'en', dict(chapters={'ch1.xhtml': p(EN + '<a href="missing.xhtml">see</a>')}), 'broken_links'),
    11: ('Empty chapter', 'en', dict(chapters={'ch1.xhtml': p(EN), 'ch2.xhtml': '<h2>End</h2>'}), 'empty_chapters'),
    12: ('Broken xhtml', 'en', dict(chapters={'ch1.xhtml': p(EN)},
                                    raw={'ch2.xhtml': xhtml('Broken xhtml', f'<p>{EN}</b></p>')}), 'broken_xhtml'),
    # a Chinese book: its CJK text is not a PDF stream; the broken genre makes repair rewrite the book
    14: ('Chinese', 'zh', dict(chapters={'ch1.xhtml': p(ZH) * 2}, subjects=['#genre']), None),
    15: ('CJK quote', 'en', dict(chapters={'ch1.xhtml': p(CJK_PARA) + p(EN)}), 'replacement_chars'),
    13: ('Markup', 'en', dict(chapters={'ch1.xhtml': p(EN + '&lt;p&gt;left over&lt;/p&gt;')}), 'markup_as_text'),
}
REPAIRED = {2, 3, 5, 6, 7, 8, 14, 15}
TAGGED = (1, 2)  # tag "Sample"


def build_library(root):
    lib = Path(root, 'Fixture Library')
    lib.mkdir(parents=True)
    db = sqlite3.connect(str(lib / 'metadata.db'))
    db.executescript('''
        create table books (id integer primary key, title text, path text);
        create table data (id integer primary key, book integer, format text, name text, uncompressed_size integer);
        create table tags (id integer primary key, name text);
        create table books_tags_link (id integer primary key, book integer, tag integer);
        create table languages (id integer primary key, lang_code text);
        create table books_languages_link (id integer primary key, book integer, lang_code integer, item_order integer);
        insert into tags values (1, 'Sample');
        insert into languages values (1, 'rus'), (2, 'eng'), (3, 'zho');
    ''')
    for i, (title, lang, kw, _) in BOOKS.items():
        rel, name = f'{AUTHOR}/{title} ({i})', f'{title} - {AUTHOR}'
        (lib / rel).mkdir(parents=True)
        f = lib / rel / f'{name}.epub'
        epub(f, title, lang, **kw)
        db.execute('insert into books values (?, ?, ?)', (i, title, rel))
        db.execute('insert into data (book, format, name, uncompressed_size) values (?, ?, ?, ?)',
                   (i, 'EPUB', name, f.stat().st_size))
        db.execute('insert into books_languages_link (book, lang_code, item_order) values (?, ?, 0)',
                   (i, {'ru': 1, 'en': 2, 'zh': 3}[lang]))
        if i in TAGGED:
            db.execute('insert into books_tags_link (book, tag) values (?, 1)', (i,))
    rel, name = f'{AUTHOR}/{BOOKS[2][0]} (2)', f'{BOOKS[2][0]} - {AUTHOR}'
    (lib / rel / f'{name}.azw3').write_bytes(b'old azw3 ' * 50)  # book 2 also has an AZW3 to back up
    db.execute("insert into data (book, format, name, uncompressed_size) values (2, 'AZW3', ?, 450)", (name,))
    db.commit()
    db.close()
    (lib / 'full-text-search.db').write_bytes(b'fts')
    (lib / 'metadata_db_prefs_backup.json').write_text('{}')
    (lib / '.caltrash' / 'b').mkdir(parents=True)
    (lib / '.caltrash' / 'b' / 'gone.epub').write_bytes(b'trash')
    return lib


def book_path(lib, i, fmt='epub'):
    title = BOOKS[i][0]
    return Path(lib, f'{AUTHOR}/{title} ({i})', f'{title} - {AUTHOR}.{fmt}')


def cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = main(['--lang', 'en', *argv])
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
    return rc, out.getvalue(), err.getvalue()


FAKE = {
    # writes a stand-in AZW3 big enough for verify's size check
    'ebook-convert': '''
open(sys.argv[2], 'wb').write(b'FAKE AZW3 ' * 2000)
''',
    # add_format copies the file over the format in the library, or adds the format; FAKE_CALIBREDB_NOOP: silently
    # writes nothing, as calibredb does while the calibre program is open
    'calibredb': '''
import os, shutil, sqlite3
if os.environ.get('FAKE_LOG'):
    open(os.environ['FAKE_LOG'], 'a').write(repr(sys.argv[1:]) + '\\n')
a = sys.argv[1:]
assert a[:2] == ['add_format', '--with-library'], a
lib, i, f = a[2], int(a[3]), a[4]
fmt = f.rsplit('.', 1)[1].upper()
if os.environ.get('FAKE_CALIBREDB_NOOP') in ('1', fmt.lower()):  # "azw3": only that format is not written
    sys.exit(0)
db = sqlite3.connect(os.path.join(lib, 'metadata.db'))
path = db.execute('select path from books where id = ?', (i,)).fetchone()[0]
row = db.execute('select name from data where book = ? and format = ?', (i, fmt)).fetchone()
name = row[0] if row else db.execute('select name from data where book = ?', (i,)).fetchone()[0]
if not row:
    db.execute('insert into data (book, format, name, uncompressed_size) values (?, ?, ?, ?)', (i, fmt, name, os.path.getsize(f)))
    db.commit()
shutil.copyfile(f, os.path.join(lib, path, name + '.' + fmt.lower()))
''',
    # "calibre is running" when FAKE_CALIBRE_OPEN is set
    'pgrep': '''
import os
sys.exit(0 if os.environ.get('FAKE_CALIBRE_OPEN') and sys.argv[1:] == ['-x', 'calibre'] else 1)
''',
    # a device whose storage is the folder FAKE_ADB_ROOT; devices are listed in FAKE_ADB_DEVICES (comma separated)
    'adb': '''
import os, re, shlex, shutil
root = os.environ['FAKE_ADB_ROOT']
devs = os.environ.get('FAKE_ADB_DEVICES', 'SER1').split(',')
a = sys.argv[1:]
if os.environ.get('FAKE_LOG'):
    open(os.environ['FAKE_LOG'], 'a').write(repr(a) + '\\n')
if a == ['devices']:
    print('List of devices attached')
    for d in devs:
        print(d + '\\tdevice')
    sys.exit(0)
assert a[0] == '-s', a
if a[1] not in devs:
    sys.exit("error: device '%s' not found" % a[1])
a = a[2:]
dev = lambda p: os.path.join(root, p.lstrip('/'))
if a[0] == 'push':
    os.makedirs(os.path.dirname(dev(a[2])), exist_ok=True)
    shutil.copy2(a[1], dev(a[2]))
    sys.exit(0)
assert a[0] == 'shell', a
cmd = ' '.join(a[1:])
tok = shlex.split(cmd.replace(';', ' ; '))
if tok[0] == 'cat':
    if not os.path.isfile(dev(tok[1])):
        sys.exit('cat: no such file')
    sys.stdout.buffer.write(open(dev(tok[1]), 'rb').read())
    sys.exit(0)
base = dev(tok[tok.index('cd') + 1])
if '-printf' in cmd:
    if os.path.isdir(base):
        out = b''
        for r, ds, fs in os.walk(base):
            for n in ds + fs:
                full = os.path.join(r, n)
                st = os.stat(full)
                rel = os.path.relpath(full, base)
                out += ('%s|%d|%f|%s' % ('d' if n in ds else '-', st.st_size, st.st_mtime, rel)).encode() + b'\\0'
        sys.stdout.buffer.write(out)
    sys.exit(0)
if 'xargs -0 rm' in cmd:
    lst = dev(re.search(r'< (\\S+)', cmd).group(1))
    for rel in open(lst, 'rb').read().split(b'\\0')[:-1]:
        f = os.path.join(base, rel.decode())
        if os.path.isfile(f):
            os.remove(f)
    os.remove(lst)
    for r, ds, fs in sorted(os.walk(base), key=lambda x: -len(x[0])):
        if r != base and not os.listdir(r):
            os.rmdir(r)
    sys.exit(0)
sys.exit('fake adb: unknown command ' + cmd)
''',
}


def fake_bin(folder, names):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for n in names:
        f = folder / n
        f.write_text(f'#!{sys.executable}\nimport sys\n' + FAKE[n].lstrip('\n'))
        f.chmod(f.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(folder)


def with_path(folder):
    return {'PATH': folder + os.pathsep + os.environ.get('PATH', '')}
