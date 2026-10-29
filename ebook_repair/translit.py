"""Russian text set in a Latin "Cyrillic" font (ä=я, Ω=ж, ‚=ш, ´=ь) -> Cyrillic, and the word lists it relies on.
The lists data/dict_ru.tsv and data/dict_en.tsv ("word<TAB>count") are built by build-dict from the user's own library;
without them the decoder is off."""
import html
import re
import unicodedata
from collections import Counter
from pathlib import Path

from .common import HTML_EXT, book_lang, open_db, open_epub, select_books
from .i18n import t

DATA_DIR = Path(__file__).resolve().parent.parent / 'data'
voc, en = Counter(), Counter()  # filled in place by load(): other modules keep these references


def read(p):
    c = Counter()
    if p.exists():
        for line in p.read_text(encoding='utf-8').splitlines():
            w, _, k = line.partition('\t')
            c[w] = int(k)
    return c


def load(data_dir=None):
    d = Path(data_dir or DATA_DIR)
    voc.clear()
    voc.update(read(d / 'dict_ru.tsv'))
    en.clear()
    en.update(read(d / 'dict_en.tsv'))


LOW = {'a': 'а', 'b': 'б', 'v': 'в', 'g': 'г', 'd': 'д', 'e': 'е', 'z': 'з', 'i': 'и', 'j': 'й', 'k': 'к', 'l': 'л',
       'm': 'м', 'n': 'н', 'o': 'о', 'p': 'п', 'r': 'р', 's': 'с', 't': 'т', 'u': 'у', 'f': 'ф', 'x': 'х', 'c': 'ц',
       'h': 'ч', 'w': 'щ', 'y': 'ы', 'q': 'э', 'Ω': 'ж', 'Ω': 'ж', '‚': 'ш', '´': 'ь', '`': 'ъ'}
UP = {k.upper(): v.upper() for k, v in LOW.items() if k.isalpha() and k.isascii()}
UP['ˇ'] = 'Ж'
MAC = set('\xdb\xda\xb8\xd4\xce\xd3\xcf\xcc\xcb\xcd\xc2\u2021\u02da') | {'\uf8ff'}  # second layer: MacCyrillic read as MacRoman («èÛÚ¸» = «Путь»)
TOK = re.compile("[A-Za-z\xc0-\xff\u0100-\u017e\u2126\u03a9\u201a\xb4`\u02c7\u2021\xb8\uf8ff\u02da\xb7\u201e]+")  # \uf8ff is «р» of the second layer
PRE = {'Ê': 'Ж', 'æ': 'ь', 'ß': 'ъ'}
SPECIAL = set('\xe4\xfc\xe7\u03a9\u201a\xb4`\u02c7\xc4\xdc\xc7\xca\xe6\xdf') | {chr(c) for c in range(0xC0, 0x180)}


def decode(w):
    """Font word -> Cyrillic. A soft sign glued to the next letter (ś = ´s) often ate a space: the word is split
    when both parts are in the word list and the whole is not. An English word without special letters stays."""
    if MAC & set(w):
        try:
            d = w.encode('mac_roman').decode('mac_cyrillic').replace('Ќ', 'К').replace('ѓ', 'г')
            if re.fullmatch('[а-яёА-ЯЁ-]+', d):
                return d
        except UnicodeError:
            pass
    if not SPECIAL & set(w) and w.lower() in en and not voc[plain(w).lower()]:
        return w
    return plain(w)


def plain(w):
    up = w.isupper() and len(w) > 1
    res, soft = [], []
    for c in unicodedata.normalize('NFD', ''.join(PRE.get(c, c) for c in w)):
        if c == '́':  # acute over a letter is ´ before it
            soft.append(len(res) - 1)
            res.insert(len(res) - 1, 'Ь' if up else 'ь')
        elif c == '̈' and res:  # ä/ü after NFD: a + ¨
            res[-1] = {'а': 'я', 'у': 'ю', 'А': 'Я', 'У': 'Ю'}.get(res[-1], res[-1])
        elif c == '̧' and res:  # ç: c + cedilla
            res[-1] = {'ц': 'ч', 'Ц': 'Ч'}.get(res[-1], res[-1])
        elif c == '`':
            res.append('Ь' if up else 'ь')
        elif unicodedata.category(c) == 'Mn':  # other marks of the font (È, Ñ) have no glyph and no meaning
            continue
        else:
            res.append(LOW.get(c) or UP.get(c) or c)
    d = ''.join(res)
    for k in reversed(soft):
        left, right = d[:k + 1], d[k + 1:]
        if right and not voc[d.lower()] and voc[left.lower()] and voc[right.split(' ')[0].lower()]:
            d = left + ' ' + right
    return d


RU, EN = re.compile('[а-яё]+'), re.compile('[a-z]+')


def run_build_dict(a):
    """Word lists from the library's own books: Russian words from Russian EPUBs, English from English. Read-only."""
    db = open_db(a.library)
    lists = {'ru': Counter(), 'en': Counter()}
    for i in select_books(db, a.ids, a.tag):
        lang = book_lang(db, i)
        if not lang:
            continue
        z, _ = open_epub(db, a.library, i)
        if z is None:
            continue
        for n in z.namelist():
            if n.endswith(HTML_EXT):
                s = html.unescape(re.sub(r'<[^>]+>', ' ', z.read(n).decode('utf-8', 'replace'))).lower()
                lists[lang].update((RU if lang == 'ru' else EN).findall(s))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for lang, c in lists.items():
        f = DATA_DIR / f'dict_{lang}.tsv'
        tmp = f.with_suffix('.tmp')  # an interrupted write must not pass for a word list
        tmp.write_text(''.join(f'{w}\t{k}\n' for w, k in c.most_common()), encoding='utf-8')
        tmp.replace(f)
        print(t('dict_written', f=f, words=len(c), total=sum(c.values())))
    return 0
