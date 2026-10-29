"""Read-only scans: check (layout errors -> TSV), samples (raw markup around findings), bold (bold share per file)."""
import html
import os
import re
from lxml import etree

from .common import HTML_EXT, open_db, open_epub, parse, select_books
from .i18n import t


def txt(s):
    return html.unescape(re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', s))).strip()


MOJI = re.compile(r'Р[°±µёЅЂ-Џ]Р|С[ЂЃ‚ѓ„…†‡€‰Љ‹ЊЌЋЏ]|Ã[\x80-\xbf]|Ð[\x80-\xbf]|вЂ[™”њќ¦]')
HEADING = re.compile(r'<(h[1-6])\b[^>]*(?<!/)>(.*?)</\1>', re.S)  # an empty <h2/> opens nothing
BOLD = re.compile(r'<(b|strong)\b[^>]*(?<!/)>(.*?)</\1>', re.S)
MARKUP = re.compile(r'&lt;/?(?:p|section|title|emphasis|strong|br)\b')
HEAD_MAX, BOLD_MAX, EMPTY_MAX = 300, 1500, 40
COLS = ['id', 'file', 'long_headings', 'max_heading', 'long_bold', 'mojibake', 'replacement_chars',
        'broken_images', 'broken_links', 'empty_chapters', 'broken_xhtml', 'markup_as_text']
COUNTS = [c for c in COLS[2:] if c != 'max_heading']


def html_files(z):
    return sorted(n for n in z.namelist() if n.endswith(HTML_EXT))


def scan(z):
    files = set(z.namelist())
    r = dict.fromkeys(COLS[2:], 0)
    for n in html_files(z):
        s = z.read(n).decode('utf-8', 'replace')
        try:
            parse(s.encode())
        except etree.XMLSyntaxError:
            r['broken_xhtml'] += 1
        for m in HEADING.finditer(s):  # longer than a paragraph: book text inside a heading
            k = len(txt(m.group(2)))
            if k > HEAD_MAX:
                r['long_headings'] += 1
                r['max_heading'] = max(r['max_heading'], k)
        for m in BOLD.finditer(s):
            r['long_bold'] += len(txt(m.group(2))) > BOLD_MAX
        body = txt(s)
        r['mojibake'] += len(MOJI.findall(body))
        r['replacement_chars'] += body.count('�')
        r['markup_as_text'] += len(MARKUP.findall(s))
        if len(body) < EMPTY_MAX and 'img' not in s:
            r['empty_chapters'] += 1
        d = os.path.dirname(n)
        for src in re.findall(r'<img\b[^>]*\bsrc="([^"#]+)"', s) + re.findall(r'xlink:href="([^"#]+)"', s):
            r['broken_images'] += os.path.normpath(os.path.join(d, html.unescape(src))) not in files
        for href in re.findall(r'<a\b[^>]*\bhref="([^"#:]+)(?:#[^"]*)?"', s):
            r['broken_links'] += os.path.normpath(os.path.join(d, html.unescape(href))) not in files
    return r


def run_check(a):
    db = open_db(a.library)
    rows = []
    for i in select_books(db, a.ids, a.tag):
        z, name = open_epub(db, a.library, i)
        if z is not None:
            rows.append({'id': i, 'file': name, **scan(z)})
    a.workdir.mkdir(parents=True, exist_ok=True)
    out = a.workdir / 'check.tsv'
    with open(out, 'w', encoding='utf-8') as f:
        f.write('\t'.join(COLS) + '\n' + ''.join('\t'.join(str(r[c]) for c in COLS) + '\n' for r in rows))
    print(t('check_total', n=len(rows), bad=sum(1 for r in rows if any(r[c] for c in COUNTS)), tsv=out))
    for c in COUNTS:
        print(t('check_col', col=c, books=sum(1 for r in rows if r[c]), total=sum(r[c] for r in rows)))
    return 0


SAMPLE_KINDS = ('heading', 'bold', 'replacement', 'mojibake', 'markup', 'empty')


def run_samples(a):
    db = open_db(a.library)
    z, _ = open_epub(db, a.library, a.id)
    if z is None:
        return 1
    pat = {'heading': (HEADING, HEAD_MAX), 'bold': (BOLD, BOLD_MAX)}
    k = 0
    for f in html_files(z):
        s = z.read(f).decode('utf-8', 'replace')
        if a.kind in pat:
            rx, least = pat[a.kind]
            for m in rx.finditer(s):
                if len(txt(m.group(2))) > least:
                    k += 1
                    if k <= a.count:
                        print(t('sample_long', f=f, pos=m.start(), n=len(txt(m.group(2))), before=repr(s[max(0, m.start() - 150):m.start()])))
                        print(t('sample_raw', raw=repr(m.group(0)[:400])))
        elif a.kind == 'replacement':
            for m in re.finditer('�', s):
                k += 1
                if k <= a.count:
                    print(f'--- {f}:', repr(s[max(0, m.start() - 80):m.start() + 80]))
        elif a.kind == 'mojibake':
            b = txt(s)
            for m in MOJI.finditer(b):
                k += 1
                if k <= a.count:
                    print(f'--- {f}:', repr(b[max(0, m.start() - 60):m.start() + 60]))
        elif a.kind == 'markup':
            for m in MARKUP.finditer(s):
                k += 1
                if k <= a.count:
                    print(f'--- {f}:', repr(s[max(0, m.start() - 100):m.start() + 150]))
        elif a.kind == 'empty' and len(txt(s)) < EMPTY_MAX and 'img' not in s:
            k += 1
            if k <= a.count:
                print(f'--- {f}:', repr(s[-600:]))
    print(t('sample_total', n=k))
    return 0


def run_bold(a):
    """Share of text in <b>/<strong> per HTML file (headings excluded); files over 2000 chars and 30% bold are listed."""
    db = open_db(a.library)
    for i in select_books(db, a.ids, a.tag):
        z, _ = open_epub(db, a.library, i)
        if z is None:
            continue
        tot, bold, title = {}, {}, {}
        for f in html_files(z):
            s = z.read(f).decode('utf-8', 'replace')
            body = HEADING.sub(' ', s)
            tot[f] = len(txt(body))
            bold[f] = sum(len(txt(m.group(2))) for m in BOLD.finditer(body))
            m = re.search(r'<h1\b[^>]*>(.*?)</h1>', s, re.S)
            title[f] = txt(m[1])[:50] if m else ''
        hi = [(k, bold[k] / tot[k]) for k in tot if tot[k] > 2000 and bold[k] / tot[k] > 0.3]
        top = '; '.join(f'{k} {d:.0%} «{title[k]}»' for k, d in sorted(hi, key=lambda x: -x[1])[:6])
        print(i, t('bold_line', files=len(tot), hi=len(hi)), top)
    return 0
