"""verify: independent check of <workdir>/<id>/*.epub against the EPUB in the library. verify does not reuse the
repairer's text code: it keeps its own model of allowed changes and everything else is a discrepancy.
Allowed: a <p> made only of replacement chars removed; CJK ideographs removed in a Russian or English book, only in a
block with more than 3 of them; a cp1251 word read as latin-1 turned back; a word of the Latin "Cyrillic" font decoded
by verify's own table (a soft sign may split it in two); heading text moved into paragraphs; bold removed; the broken
genre renamed. Also checked: ids and links of the original (before any of that), other files byte for byte, OPF except
the genre, NCX except entry labels, DOCTYPE, AZW3 newer than the EPUB, no long headings left, no new chars missing from
an embedded font, and that the repair was made from this library's current EPUB (source.json).
On success a book gets <id>/verified.json with the md5 of the repaired files and of the original: apply needs it.
azw3-check: letters of an EPUB and the AZW3 next to it are the same (both through ebook-convert to txt)."""
import html
import io
import re
import subprocess
import tempfile
import unicodedata
import zipfile
from collections import Counter
from pathlib import Path
from lxml import etree

from .common import (HTML_EXT, book_file, book_lang, fail, library_id, md5, open_db, parse, program, read_record,
                     text_slots, texts, write_record)
from .i18n import t
from .repair import BAD_GENRE, CHAPTER, H, HEAD_MAX, NCX, NO_GENRE, ncx_name, text, toc_key

GENRE_RE = re.compile('<dc:subject>(?:' + '|'.join(map(re.escape, BAD_GENRE)) + ')</dc:subject>')  # its own, not the repairer's
FONT_EXT = ('.ttf', '.otf')
X = '{http://www.w3.org/1999/xhtml}'
BLOCKS = {X + n for n in ('p', 'li', 'td', 'th', 'dd', 'dt', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6')}
CJK_ONLY = re.compile('[㐀-鿿가-힯]')  # the only chars a PDF-stream cleanup may drop
CJK_LANGS = ('ru', 'en')
# Latin "Cyrillic" font, verify's own table: spelling after NFD and lower() -> letter
FONT = {'ä': 'я', 'ü': 'ю', 'ç': 'ч', 'ê': 'ж', 'a': 'а', 'b': 'б', 'v': 'в', 'g': 'г',
        'd': 'д', 'e': 'е', 'z': 'з', 'i': 'и', 'j': 'й', 'k': 'к', 'l': 'л', 'm': 'м', 'n': 'н', 'o': 'о', 'p': 'п',
        'r': 'р', 's': 'с', 't': 'т', 'u': 'у', 'f': 'ф', 'x': 'х', 'c': 'ц', 'h': 'ч', 'w': 'щ', 'y': 'ы', 'q': 'э',
        'ω': 'ж', 'ˇ': 'ж', '‚': 'ш', '\xb4': 'ь', '`': 'ь', '\xe6': 'ь', '\xdf': 'ъ'}
# a file counts as set in that font when at least FONT_MIN_SHARE of its words carry one of these chars (font letters and
# the MacRoman layer); measured on the test text: Russian in the font 26%, English 0%
FONT_SPECIAL = set('\xe4\xfc\xe7\u2126\u03a9\u201a\xb4`\u02c7\xc4\xdc\xc7\xdb\xda\xb8\xd4\xce\xd3\xcf\xcc\xcb\xcd\xc2\u2021\u02da\uf8ff')
FONT_MIN_SHARE = 0.15
BR = X + 'br'


def words(s):
    # a block tag and <br/> split words, an inline one (<b>, <i>) does not: "road.</b>335" is one word on screen
    return html.unescape(re.sub(r'<[^>]+>', '', re.sub(
        r'</?(?:p|h[1-6]|div|li|td|th|br|blockquote|ul|ol|tr|table)\b[^>]*>', ' ', s))).split()


def refs(r):
    return Counter((k, v) for el in r.iter() if isinstance(el.tag, str) for k, v in el.attrib.items()
                   if k in ('id', 'href', 'src'))


def font_chars(z):
    """Code points of the embedded fonts; None when there are none or fontTools is not installed."""
    names = [n for n in z.namelist() if n.lower().endswith(FONT_EXT)]
    if not names:
        return None
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        return None
    return set().union(*(TTFont(io.BytesIO(z.read(n))).getBestCmap() for n in names))


def allowed_removals(r, lang):
    """The only removals repair may make, applied to the original."""
    for el in list(r.iter(X + 'p')):
        s = ''.join(el.itertext())
        if '�' in s and not s.replace('�', '').strip():
            parent, prev = el.getparent(), el.getprevious()
            if el.tail:
                if prev is not None:
                    prev.tail = (prev.tail or '') + el.tail
                else:
                    parent.text = (parent.text or '') + el.tail
            parent.remove(el)
    if lang in CJK_LANGS:
        for el in r.iter(*BLOCKS):
            for seg in segments(el):
                if len(CJK_ONLY.findall(''.join(getattr(n, a) or '' for n, a in seg))) > 3:
                    for n, a in seg:
                        if getattr(n, a):
                            setattr(n, a, CJK_ONLY.sub('', getattr(n, a)))


def segments(el):
    """Text pieces of a block; a long heading is cut at its own <br/>, as repair turns those lines into paragraphs."""
    slots = list(text_slots(el))
    if el.tag not in H or len(text(el)) <= HEAD_MAX or el.find(BR) is None:
        return [slots]
    segs = [[]]
    for n, a in slots:
        if a == 'tail' and n.tag == BR and n.getparent() is el:
            segs.append([])
        segs[-1].append((n, a))
    return segs


def font_file(r):
    ws = ' '.join(texts(r)).split()
    return bool(ws) and sum(any(c in FONT_SPECIAL for c in w) for w in ws) >= FONT_MIN_SHARE * len(ws)


def latin1_ok(a, b):
    """Only a word whose letters are all latin-1 letters (U+00C0-U+00FF), as a cp1251 word read as latin-1 is."""
    letters = [c for c in a if c.isalpha()]
    if not letters or any(not '\xc0' <= c <= '\xff' for c in letters):
        return False
    try:
        return a.encode('latin-1').decode('cp1251') == b
    except UnicodeError:
        return False


def font_ok(a, b):
    """b is a read in the Latin "Cyrillic" font (or its MacRoman/MacCyrillic layer), letter case aside."""
    try:
        if a.encode('mac_roman').decode('mac_cyrillic').replace('Ќ', 'К').replace('ѓ', 'г') == b:
            return True
    except UnicodeError:
        pass
    x, y = unicodedata.normalize('NFD', a).lower(), b.lower()
    i = j = 0
    while i < len(x):
        if i + 1 < len(x) and x[i + 1] == '́':  # acute over a letter is a soft sign before it
            if y[j:j + 1] != 'ь' or FONT.get(x[i], x[i]) != y[j + 1:j + 2]:
                return False
            i, j = i + 2, j + 2
            continue
        hit = next((k for k, c in FONT.items() if x.startswith(k, i) and c == y[j:j + 1]), None)
        if hit:
            i, j = i + len(hit), j + 1
        elif x[i] == y[j:j + 1]:
            i, j = i + 1, j + 1
        elif unicodedata.category(x[i]) == 'Mn':  # other marks of the font carry no letter
            i += 1
        else:
            return False
    return j == len(y)


def first_diff(wx, wy, stat, font=False):
    """(i, j) where the repaired words leave the allowed changes, or None; font: the file is set in the Latin font."""
    i = j = 0
    while i < len(wx) and j < len(wy):
        a, b = wx[i], wy[j]
        if a == b:
            i, j = i + 1, j + 1
        elif latin1_ok(a, b) or (font and font_ok(a, b)):
            stat['words_decoded'] += 1
            i, j = i + 1, j + 1
        elif font and j + 1 < len(wy) and b[-1:] in 'ьЬ' and font_ok(a, b + wy[j + 1]):
            stat['words_decoded'] += 1
            i, j = i + 1, j + 2
        else:
            return i, j
    return None if (i, j) == (len(wx), len(wy)) else (i, j)


def doctype(r):
    return r.getroottree().docinfo.doctype


def check_source(lib, d, src, i):
    rec = read_record(d / 'source.json')
    if rec is None:
        return [('no_source', t('v_no_source', id=i))]
    if rec.get('library') != library_id(lib):
        return [('other_library', t('v_other_library', id=i, lib=rec.get('library')))]
    if rec.get('source_md5') != md5(src):
        return [('source_changed', t('v_source_changed', id=i))]
    return []


def verify_book(lib, workdir, db, i, stat):
    """List of (code, line) discrepancies for one book."""
    row = book_file(db, i)
    if row is None:
        return [('no_epub', t('no_epub', id=i))]
    path, name = row
    lang = book_lang(db, i)
    d = workdir / str(i)
    pe, pa, src = d / f'{name}.epub', d / f'{name}.azw3', Path(lib, path, name + '.epub')
    if not pe.exists():
        return [('no_repaired', t('v_no_repaired', id=i, path=pe))]
    bad = check_source(lib, d, src, i)
    a, b = zipfile.ZipFile(src), zipfile.ZipFile(pe)
    if a.namelist() != b.namelist():
        return bad + [('file_list', t('v_file_list', id=i))]
    if b.infolist()[0].filename != 'mimetype' or b.infolist()[0].compress_type != zipfile.ZIP_STORED:
        bad.append(('mimetype', t('v_mimetype', id=i)))
    if not pa.exists() or pa.stat().st_size < 10000 or pa.stat().st_mtime < pe.stat().st_mtime:
        bad.append(('azw3_stale', t('v_azw3_stale', id=i)))
    stat['books'] += 1
    cmap = font_chars(b)
    ncx = ncx_name(a.namelist())
    heads = {}  # NCX target -> text of the first heading inside that element in the repaired file
    for n in a.namelist():
        x, y = a.read(n), b.read(n)
        if not n.endswith(HTML_EXT + ('.ncx', '.opf')):
            if x != y:
                bad.append(('bytes_changed', t('v_bytes_changed', id=i, file=n)))
            continue
        if n.endswith('.opf'):
            s = x.decode('utf-8')
            if all(y.decode('utf-8') != GENRE_RE.sub(f'<dc:subject>{g}</dc:subject>', s) for g in NO_GENRE.values()):
                bad.append(('opf_changed', t('v_opf_changed', id=i, file=n)))
        if not n.endswith(HTML_EXT):
            continue
        try:
            tx = parse(x)
        except etree.XMLSyntaxError:  # repair leaves a file it cannot parse as it was
            if x != y:
                bad.append(('bytes_changed', t('v_bytes_changed', id=i, file=n)))
            continue
        try:
            ty = parse(y)
        except etree.XMLSyntaxError as e:
            bad.append(('xml_broken', t('v_xml_broken', id=i, file=n, err=e)))
            continue
        if doctype(tx) != doctype(ty):
            bad.append(('doctype_changed', t('v_doctype_changed', id=i, file=n)))
        long_before = sum(len(text(h)) > HEAD_MAX for h in tx.iter(*H))
        # ids, links and chars come from the original BEFORE the allowed removals: they would hide a lost <p id>?</p>
        refs0, chars0, font = refs(tx), set(''.join(tx.itertext())), font_file(tx)
        allowed_removals(tx, lang)
        for el in ty.iter():  # the "no genre" name in place of a broken genre is intended
            if isinstance(el.tag, str) and el.text in NO_GENRE.values():
                hit = next((s for s in tx.iter(el.tag) if s.text in BAD_GENRE and s.getparent().tag == el.getparent().tag
                            and s.get('href') == el.get('href')), None)
                if hit is not None:
                    hit.text = el.text
        wx, wy = words(etree.tostring(tx, encoding='unicode')), words(etree.tostring(ty, encoding='unicode'))
        diff = first_diff(wx, wy, stat, font)
        if diff:
            k, m = diff
            bad.append(('text_diverged', t('v_text_diverged', id=i, file=n, k=k, a=wx[max(k - 3, 0):k + 3],
                                           b=wy[max(m - 3, 0):m + 3], na=len(wx), nb=len(wy))))
        if refs0 != refs(ty):
            bad.append(('refs_changed', t('v_refs_changed', id=i, file=n, a=list((refs0 - refs(ty)).items())[:3],
                                          b=list((refs(ty) - refs0).items())[:3])))
        if cmap is not None:
            new = {c for c in ''.join(ty.itertext()) if not c.isspace() and ord(c) not in cmap} - chars0
            if new:
                bad.append(('font_glyphs', t('v_font_glyphs', id=i, file=n, chars=''.join(sorted(new))[:40])))
        stat['long_headings_before'] += long_before
        for h in ty.iter(*H):
            if len(text(h)) > HEAD_MAX:
                stat['long_headings_after'] += 1
                if not CHAPTER.match(text(h)):
                    bad.append(('long_heading', t('v_long_heading', id=i, file=n, text=text(h)[:80])))
        if ncx:
            for el in ty.iter():
                if isinstance(el.tag, str) and el.get('id'):
                    h = next(el.iter(*H), None)
                    heads[toc_key(n, ncx, el.get('id'))] = text(h) if h is not None else None
    if ncx:
        sa, sb = (parse(z.read(ncx)) for z in (a, b))
        if doctype(sa) != doctype(sb):
            bad.append(('doctype_changed', t('v_doctype_changed', id=i, file=ncx)))
        for lab in (lab for r in (sa, sb) for nl in r.iter(f'{NCX}navLabel') for lab in nl):
            lab.text = None  # labels are compared one by one below
        if etree.tostring(sa) != etree.tostring(sb):
            bad.append(('ncx_changed', t('v_ncx_changed', id=i)))
        pa_, pb_ = (list(parse(z.read(ncx)).iter(f'{NCX}navPoint')) for z in (a, b))
        if [p.find(f'{NCX}content').get('src') for p in pa_] != [p.find(f'{NCX}content').get('src') for p in pb_]:
            bad.append(('toc_targets', t('v_toc_targets', id=i)))
        else:
            for p, q in zip(pa_, pb_):
                la, lb = (z.findtext(f'{NCX}navLabel/{NCX}text') for z in (p, q))
                src_ = q.find(f'{NCX}content').get('src')
                # an empty label matches nothing: a target without a heading also has heads[src] None
                if la != lb and not (lb and lb == heads.get(src_)) and not (la in BAD_GENRE and lb in NO_GENRE.values()):
                    bad.append(('toc_label', t('v_toc_label', id=i, a=(la or '')[:40], b=(lb or '')[:40])))
    return bad


def verify(lib, workdir, ids=None):
    """(stat, [(code, line)]) for the given ids or every <workdir>/<id> folder; writes or removes verified.json."""
    db = open_db(lib)
    ids = [int(x) for x in ids or []] or sorted(int(p.name) for p in workdir.iterdir() if p.is_dir() and p.name.isdigit())
    stat, bad = Counter(), []
    for i in ids:
        d = workdir / str(i)
        try:
            b = verify_book(lib, workdir, db, i, stat)
        except (OSError, zipfile.BadZipFile, etree.XMLSyntaxError, ValueError) as e:  # ValueError: not UTF-8
            b = [('book_error', t('v_book_error', id=i, err=e))]
        rec = d / 'verified.json'
        if b or not d.is_dir():
            if d.is_dir():
                rec.unlink(missing_ok=True)
        else:
            path, name = book_file(db, i)
            write_record(rec, {'library': library_id(lib), 'source_md5': md5(Path(lib, path, name + '.epub')),
                               'epub_md5': md5(d / f'{name}.epub'), 'azw3_md5': md5(d / f'{name}.azw3')})
        bad += b
    return stat, bad


def run_verify(a):
    if not a.workdir.is_dir():
        fail(t('no_workdir', path=a.workdir))
    stat, bad = verify(a.library, a.workdir, a.ids)
    print(dict(stat))
    print('\n'.join(line for _, line in bad) or t('v_ok'))
    return 1 if bad else 0


def run_azw3_check(a):
    ec = program('ebook-convert')
    epub = Path(a.epub)
    azw3 = epub.with_suffix('.azw3')
    txt = {}
    with tempfile.TemporaryDirectory() as td:
        for src in (epub, azw3):
            out = Path(td) / f'{src.suffix[1:]}.txt'
            r = subprocess.run([ec, str(src), str(out)], capture_output=True, text=True)
            if r.returncode or not out.exists():
                fail(t('convert_failed', name=src.name, rc=r.returncode, err=r.stderr[-300:]))
            txt[src.suffix] = out.read_text(encoding='utf-8')
    ca, cb = (Counter(ch for ch in txt[s] if ch.isalpha()) for s in ('.epub', '.azw3'))
    print(t('azw3_letters', a=sum(ca.values()), b=sum(cb.values()), miss=sum((ca - cb).values())))
    if ca != cb:
        print(t('azw3_differ'))
        return 1
    print(t('azw3_ok'))
    return 0
