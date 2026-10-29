"""repair: fixes EPUB files into <workdir>/<id>/ and makes an AZW3 next to each. The library is not touched.
- a heading longer than 300 chars is book text: lines up to <br/> become paragraphs, a short first line stays the heading;
  a long chapter title ("Chapter II.—...") is left alone;
- an HTML file where over 90% of the text is bold loses the bold (nothing can be emphasised in it anyway);
- in a Russian or English book (by calibre metadata) the CJK ideographs of a block holding more than 3 of them are
  removed (PDF stream debris); every other char stays, a stray replacement char inside text included;
- a <p> made only of replacement chars is removed; table cells, list items and headings stay;
- a broken genre name ("#genre") is renamed;
- Russian text set in a Latin "Cyrillic" font (ä=я, Ω=ж) is turned into Cyrillic;
- a cp1251 word read as latin-1 ("ñåêòà") is turned back into Cyrillic.
The last two need the word list from build-dict; without it they are off."""
import csv
import io
import posixpath
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path
from lxml import etree

from . import translit
from .common import (HTML_EXT, azw3_source, book_file, book_lang, epub_path, find_program, library_id, map_text, md5, open_db,
                     parse, select_books, texts, warn, write_record)
from .i18n import t

X = '{http://www.w3.org/1999/xhtml}'
NCX = '{http://www.daisy.org/z3986/2005/ncx/}'
H = {f'{X}h{i}' for i in range(1, 7)}
BR, P, B = f'{X}br', f'{X}p', (f'{X}b', f'{X}strong')
ITAL = (f'{X}i', f'{X}em')
BLOCK = {P, f'{X}li', f'{X}td', f'{X}th', f'{X}dd', f'{X}dt'}
HEAD_MAX, TITLE_MAX, BOLD_SHARE = 300, 150, 0.9
CHAPTER = re.compile(r'(chapter|глава|part|часть|книга|book|section|раздел|lecture|лекция|§|[ivxlc]+\.|\d+[.)])\s', re.I)
CJK = re.compile('[\u3400-\u9fff\uac00-\ud7af]')  # CJK ideographs and Hangul: what a PDF stream leaves
JUNK = CJK  # only these chars are removed; any other char, \ufffd included, stays
BAD_GENRE = {'#genre'}
NO_GENRE = {'ru': 'Без жанра', 'en': 'Other'}
FONT = re.compile('[\xe4\xfc\xe7Ω‚\xb4]')
LETTER = re.compile('[A-Za-z\xc0-ž]')
LATIN1 = re.compile('[\xc0-\xff\xa8\xb8]{3,}')
# Russian function words in this font; no one-letter ones: "i" and "a" also occur in a German formula ("Für i gilt: a = b")
FUNC = {'na', 'ne', '\xe7to', 'po', 'kak', 'no', 'iz', 'za', 'ot', 'dl\xe4', 'on', 'ona', 'oni', 'my', 'vy', 'ego', 'qto',
        'tak', 'li', 'by', 'ix', 'vse'}
AZW3_SKIPPED = 'skipped (no ebook-convert)'  # report value, like 'ok' and 'ERROR ...'
KEYS = ['headings_split', 'headings_to_paragraphs', 'paragraphs_from_headings', 'toc_labels', 'files_unbolded',
        'pdf_junk_paragraphs', 'replacement_paragraphs', 'genre_renamed', 'genre_in_ncx_opf', 'files_from_latin_font',
        'words_from_latin1']


def text(el):
    return ' '.join(''.join(el.itertext()).split())


def put(p, x):
    """String or element to the end of p; an element's tail moves with it."""
    if isinstance(x, str):
        if len(p):
            p[-1].tail = (p[-1].tail or '') + x
        else:
            p.text = (p.text or '') + x
    else:
        p.append(x)


def segments(h):
    segs = [[h.text] if h.text else []]
    for c in list(h):
        if c.tag == BR:
            segs.append([c.tail] if c.tail else [])
        else:
            segs[-1].append(c)
    return segs


def italic_only(p):
    return not (p.text or '').strip() and all(c.tag in ITAL and not (c.tail or '').strip() for c in p)


def fix_heading(h):
    """None: heading kept; otherwise (new heading text or None, number of paragraphs taken out)."""
    t_ = text(h)
    if len(t_) <= HEAD_MAX or any(c.tag not in ITAL + B + (BR, f'{X}a', f'{X}sup', f'{X}sub', f'{X}span', f'{X}img')
                                  for c in h.iter() if c is not h):
        return None
    has_br = any(c.tag == BR for c in h)
    if not has_br and CHAPTER.match(t_):
        return None
    ps = []
    for seg in segments(h):
        p = etree.Element(P)
        for x in seg:
            put(p, x)
        if text(p) or p.find(f'.//{X}img') is not None:
            ps.append(p)
    title = ps.pop(0) if has_br and ps and len(text(ps[0])) <= TITLE_MAX and not italic_only(ps[0]) else None
    parent, i = h.getparent(), h.getparent().index(h)
    tail, keep = h.tail, dict(h.attrib)
    if title is not None:
        h.clear()
        h.attrib.update(keep)
        h.text = title.text
        for c in list(title):
            h.append(c)
        h.tail = None
        i += 1
    else:
        parent.remove(h)
        if keep and ps:
            ps[0].attrib.update(keep)
    for k, p in enumerate(ps):
        parent.insert(i + k, p)
    last = ps[-1] if ps else (h if title is not None else None)
    if last is not None:
        last.tail = tail
    elif tail and i:
        parent[i - 1].tail = (parent[i - 1].tail or '') + tail
    elif tail:
        parent.text = (parent.text or '') + tail
    return (text(h) if title is not None else None), len(ps)


def has_ancestor(el, tags):
    return any(a.tag in tags for a in el.iterancestors())


def bold_share(r):
    tot = bold = 0
    for el in r.iter(*BLOCK):
        if has_ancestor(el, H) or has_ancestor(el, BLOCK):  # <li><p> is counted once, by the outer block
            continue
        tot += len(text(el))
        bold += sum(len(text(b)) for b in el.iter(*B) if not has_ancestor(b, B))
    return tot, bold / tot if tot else 0


def unbold(r, rep):
    tot, share = bold_share(r)
    if tot >= 300 and share >= BOLD_SHARE:
        for el in r.iter(*BLOCK):
            if not has_ancestor(el, H) and next(el.iter(*H), None) is None:  # bold of a heading stays
                etree.strip_tags(el, *B)
        rep['files_unbolded'] += 1


PDF_JUNK_LANGS = ('ru', 'en')


def clean_junk(r, rep, lang):
    """CJK glyphs in a Russian or English book (by calibre metadata) are a PDF stream pasted into the text; in a book
    of any other or unknown language they may be its text and stay. A <p> made only of replacement chars is garbage
    in any language; table cells, list items and headings stay, or columns and numbering would shift."""
    for el in list(r.iter(*BLOCK, *H)):
        s = ''.join(el.itertext())
        if el.tag == P and '�' in s and not re.sub('[�\\s]', '', s):
            parent, prev = el.getparent(), el.getprevious()
            if el.tail:  # text after the paragraph stays with its space: "A<p>?</p> B" must not become "AB"
                if prev is not None:
                    prev.tail = (prev.tail or '') + el.tail
                else:
                    parent.text = (parent.text or '') + el.tail
            parent.remove(el)
            rep['replacement_paragraphs'] += 1
        elif lang in PDF_JUNK_LANGS and len(CJK.findall(s)) > 3:
            map_text(el, lambda x: JUNK.sub('', x))
            rep['pdf_junk_paragraphs'] += 1


def untranslit(r, rep):
    """A file where 5% of Latin words (of 200 or more) contain ä/ü/ç/Ω/‚/´ and 80% of those decode to Russian words
    is Russian text in a Latin font. Lone ‚ and ´ are punctuation and stay (‚ sometimes stands for a comma)."""
    toks = [w for s in texts(r) for w in translit.TOK.findall(s) if LETTER.search(w)]
    special = [w for w in toks if FONT.search(w)]
    if len(toks) < 200 or len(special) < 0.05 * len(toks):
        return
    if sum(all(translit.voc[w] for w in translit.decode(s).lower().split()) for s in special) < 0.8 * len(special):
        return
    # German ("für", "über") passes the word list too; measured: Russian books 14-16% function words, others <= 2.4%
    if sum(w.lower() in FUNC for w in toks) < 0.07 * len(toks):
        return

    def sub(s):
        return translit.TOK.sub(lambda m: translit.decode(m[0]) if LETTER.search(m[0]) else m[0], s) if s else s
    map_text(r, sub)
    rep['files_from_latin_font'] += 1


def unlatin1(r, rep):
    """A cp1251 word read as latin-1 ("ñåêòà" = "секта") goes back when the result is in the word list and the file
    has at least 3 such words: a lone "àéè" in a Russian book is an accent example, not mojibake."""
    def dec(w):
        try:
            d = w.encode('latin-1').decode('cp1251')
        except UnicodeError:
            return None
        return d if translit.voc[d.lower()] else None
    if sum(dec(m[0]) is not None for s in texts(r) for m in LATIN1.finditer(s)) < 3:
        return

    def fix(m):
        d = dec(m[0])
        if d is None:
            return m[0]
        rep['words_from_latin1'] += 1
        return d
    map_text(r, lambda s: LATIN1.sub(fix, s))


def ncx_name(names):
    return next((n for n in names if n.endswith('.ncx')), None)


def toc_key(n, ncx, el_id):
    """How an NCX entry points at element el_id of file n: path relative to the NCX folder plus #id."""
    return posixpath.relpath(n, posixpath.dirname(ncx) or '.') + '#' + el_id


def fix_epub(src, dst, lang, rep):
    """Returns the names of HTML files left as they were because they do not parse."""
    zin = zipfile.ZipFile(src)
    names = zin.namelist()
    ncx = ncx_name(names)
    trees, labels, skipped = {}, {}, []
    for n in names:
        if n.endswith(HTML_EXT):
            try:
                trees[n] = parse(zin.read(n))
            except etree.XMLSyntaxError:
                skipped.append(n)
    for n, r in trees.items():
        for h in list(r.iter(*H)):
            div = h.getparent()
            first = div is not None and next(iter(div), None) is h
            res = fix_heading(h)
            if res is None:
                continue
            new, k = res
            rep['headings_to_paragraphs' if new is None else 'headings_split'] += 1
            rep['paragraphs_from_headings'] += k
            if new and first and div.get('id') and ncx:
                labels[toc_key(n, ncx, div.get('id'))] = new
    for r in trees.values():
        unbold(r, rep)
        clean_junk(r, rep, lang)
        untranslit(r, rep)
        unlatin1(r, rep)
    genre = NO_GENRE.get(lang, NO_GENRE['en'])
    for r in trees.values():  # a broken genre is renamed where it is the whole text of a title, heading or link
        for el in r.iter(f'{X}title', f'{X}h1', f'{X}a'):
            if el.text in BAD_GENRE and not len(el):
                el.text = genre
                rep['genre_renamed'] += el.tag == f'{X}h1'
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as zo:
        for info in zin.infolist():
            n = info.filename
            data = zin.read(n)
            if n in trees:  # the tree keeps the file's own DOCTYPE
                data = etree.tostring(trees[n].getroottree(), xml_declaration=True, encoding='utf-8')
            if n == ncx:
                doc = parse(data)
                for np in doc.iter(f'{NCX}navPoint'):
                    s, lab = np.find(f'{NCX}content').get('src'), np.find(f'{NCX}navLabel/{NCX}text')
                    if labels.get(s):
                        lab.text = labels[s]
                        rep['toc_labels'] += 1
                    elif lab is not None and lab.text in BAD_GENRE:
                        lab.text = genre
                        rep['genre_in_ncx_opf'] += 1
                data = etree.tostring(doc.getroottree(), xml_declaration=True, encoding='utf-8')  # DOCTYPE kept
            if n.endswith('.opf'):
                s = data.decode('utf-8')
                for g in BAD_GENRE:
                    rep['genre_in_ncx_opf'] += s.count(f'<dc:subject>{g}</dc:subject>')  # uncounted, the file is dropped
                    s = s.replace(f'<dc:subject>{g}</dc:subject>', f'<dc:subject>{genre}</dc:subject>')
                data = s.encode('utf-8')
            zo.writestr(info, data)  # same order and compression: mimetype first and stored
    Path(dst).write_bytes(out.getvalue())
    return skipped


def run_repair(a):
    db = open_db(a.library)
    translit.load()
    if not translit.voc:
        print(t('repair_no_dict', path=translit.DATA_DIR / 'dict_ru.tsv'), flush=True)
    rows, ec = [], find_program('ebook-convert')
    if ec is None:
        warn(t('repair_no_convert'))
    for i in select_books(db, a.ids, a.tag):
        src, name = epub_path(db, a.library, i)
        if src is None:
            continue
        d = a.workdir / str(i)
        d.mkdir(parents=True, exist_ok=True)
        dst = d / src.name
        rep = dict.fromkeys(KEYS, 0)
        try:
            skipped = fix_epub(src, dst, book_lang(db, i), rep)
        except (OSError, zipfile.BadZipFile, etree.XMLSyntaxError, ValueError) as e:  # ValueError: OPF not in UTF-8
            warn(t('repair_book_failed', id=i, err=e))
            skipped, rep = [], dict.fromkeys(KEYS, 0)
        for n in skipped:
            warn(t('repair_skipped', id=i, file=n))
        if not any(rep.values()):
            dst.unlink(missing_ok=True)
            if any(d.iterdir()):
                print(i, t('repair_nothing_leftover', dir=d), flush=True)
            else:
                d.rmdir()
            continue
        # what the repair was made from: apply refuses another library or a changed original
        old_azw3 = book_file(db, i, 'AZW3')
        old_azw3 = Path(a.library, old_azw3[0], old_azw3[1] + '.azw3') if old_azw3 else None
        write_record(d / 'source.json', {'library': library_id(a.library), 'book_id': i, 'source_md5': md5(src),
                                         'source_azw3_md5': md5(old_azw3) if old_azw3 and old_azw3.exists() else None})
        (d / 'verified.json').unlink(missing_ok=True)  # a new repair needs a new verify
        azw3 = dst.with_suffix('.azw3')
        if azw3.exists():
            azw3.unlink()  # a failed conversion must not leave the AZW3 of an earlier run
        if ec is None:
            rep['azw3'] = AZW3_SKIPPED
        else:
            with tempfile.TemporaryDirectory() as td:
                s, _ = azw3_source(dst, td)
                r = subprocess.run([ec, str(s), str(azw3), '--no-inline-toc', '--chapter-mark', 'none'],
                                   capture_output=True, text=True)
            rep['azw3'] = 'ok' if r.returncode == 0 and azw3.exists() else 'ERROR ' + r.stderr[-200:]
        rows.append({'id': i, 'file': name, **rep})
        print(i, name[:40], ' '.join(f'{k}={v}' for k, v in rep.items() if v), flush=True)
    a.workdir.mkdir(parents=True, exist_ok=True)
    done = len(rows)
    tsv = a.workdir / 'repair.tsv'
    old = list(csv.DictReader(open(tsv, encoding='utf-8'), delimiter='\t')) if tsv.exists() else []
    new = {str(r['id']): r for r in rows}  # a rerun on some ids updates their rows, the rest stay
    rows = [new.pop(r['id'], r) for r in old] + list(new.values())
    with open(tsv, 'w', encoding='utf-8') as f:
        w = csv.DictWriter(f, ['id', 'file'] + KEYS + ['azw3'], delimiter='\t', restval=0)
        w.writeheader()
        w.writerows(rows)
    print(t('repair_done', n=done, tsv=tsv))
    return 0
