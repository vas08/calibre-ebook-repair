"""repair with a fake ebook-convert (no calibre needed), verify on its result, and verify against spoiled copies:
every spoil must be caught by its own check."""
import csv
import io
import os
import re
import shutil
import tempfile
import unittest
import zipfile
from collections import Counter
from pathlib import Path
from unittest import mock

from lxml import etree

import fixtures as fx
from fixtures import (CJK_PARA, EN, LATIN1_WORDS, REPAIRED, RU, ZH, book_path, build_library, cli, epub, fake_bin, latin1,
                      to_latin_font, with_path)
from ebook_repair import common, translit
from ebook_repair import repair as repair_mod
from ebook_repair import verify as verify_mod
from ebook_repair.common import parse
from ebook_repair.repair import clean_junk
from ebook_repair.verify import verify, words

X = '{http://www.w3.org/1999/xhtml}'
NCX = '{http://www.daisy.org/z3986/2005/ncx/}'


class RepairTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.lib = build_library(root)
        cls.work = root / 'work'
        cls.patches = [mock.patch.dict(os.environ, with_path(fake_bin(root / 'bin', ['ebook-convert']))),
                       mock.patch.object(translit, 'DATA_DIR', root / 'data')]
        for p in cls.patches:
            p.start()
        cls.dict_rc, cls.dict_out, _ = cli('--library', str(cls.lib), 'build-dict')
        cls.rc, cls.out, cls.err = cli('--library', str(cls.lib), '--workdir', str(cls.work), 'repair')
        with open(cls.work / 'repair.tsv', encoding='utf-8') as f:
            cls.report = {int(r['id']): r for r in csv.DictReader(f, delimiter='\t')}

    @classmethod
    def tearDownClass(cls):
        for p in reversed(cls.patches):
            p.stop()
        cls.tmp.cleanup()

    def repaired(self, i, name='OEBPS/ch1.xhtml'):
        f = next((self.work / str(i)).glob('*.epub'))
        return zipfile.ZipFile(f).read(name)

    def tree(self, i, name='OEBPS/ch1.xhtml'):
        return etree.fromstring(self.repaired(i, name))

    def test_dict_built(self):
        self.assertEqual(self.dict_rc, 0)
        self.assertTrue((translit.DATA_DIR / 'dict_ru.tsv').exists())
        self.assertIn('dict_en.tsv', self.dict_out)

    def test_only_broken_books_repaired(self):
        self.assertEqual(self.rc, 0, self.err)
        self.assertEqual({int(p.name) for p in self.work.iterdir() if p.is_dir()}, REPAIRED)
        self.assertEqual(set(self.report), REPAIRED)
        self.assertTrue(all(r['azw3'] == 'ok' for r in self.report.values()))
        self.assertIn('book 12: OEBPS/ch2.xhtml does not parse, left as it is', self.err)

    def test_long_heading_split(self):
        r = self.tree(2)
        hs = list(r.iter(f'{X}h2'))
        self.assertEqual([''.join(h.itertext()) for h in hs], ['Harbour'])
        self.assertEqual(len(list(r.iter(f'{X}p'))), 3)
        ncx = etree.fromstring(self.repaired(2, 'OEBPS/toc.ncx'))
        self.assertEqual([t.text for t in ncx.iter(f'{NCX}text')][1:], ['Harbour', 'Second part'])
        rep = self.report[2]
        self.assertEqual((rep['headings_split'], rep['paragraphs_from_headings'], rep['toc_labels']), ('1', '1', '1'))

    def test_bold_removed(self):
        self.assertEqual(list(self.tree(3).iter(f'{X}b')), [])
        self.assertEqual(self.report[3]['files_unbolded'], '1')

    def test_replacement_paragraph_removed(self):
        ps = [''.join(p.itertext()) for p in self.tree(6).iter(f'{X}p')]
        self.assertEqual(len(ps), 2)
        self.assertIn('A stray � mark.', ps[0])  # a stray char inside text is only reported, not removed
        self.assertEqual(self.report[6]['replacement_paragraphs'], '1')

    def test_latin_font_decoded(self):
        body = next(self.tree(7).iter(f'{X}body'))
        self.assertEqual(words(etree.tostring(body, encoding='unicode')), words(f'<p>{RU}</p>' * 3))
        self.assertEqual(self.report[7]['files_from_latin_font'], '1')

    def test_latin1_decoded(self):
        last = list(self.tree(5).iter(f'{X}p'))[-1].text
        self.assertEqual(last.split(), LATIN1_WORDS)
        self.assertEqual(self.report[5]['words_from_latin1'], str(len(LATIN1_WORDS)))

    def test_genre_renamed(self):
        self.assertIn(b'<dc:subject>Other</dc:subject>', self.repaired(8, 'OEBPS/content.opf'))
        self.assertNotIn(b'#genre', self.repaired(8, 'OEBPS/content.opf'))
        self.assertEqual(next(self.tree(8, 'OEBPS/genre.xhtml').iter(f'{X}h1')).text, 'Other')
        self.assertIn(b'<text>Other</text>', self.repaired(8, 'OEBPS/toc.ncx'))

    def test_cjk_book_keeps_its_text(self):
        self.assertEqual([p.text for p in self.tree(14).iter(f'{X}p')], [ZH, ZH])
        self.assertEqual(self.report[14]['pdf_junk_paragraphs'], '0')
        self.assertEqual(self.report[14]['genre_in_ncx_opf'], '1')
        self.assertEqual(verify(str(self.lib), self.work, [14])[1], [])

    def test_pdf_junk_only_in_ru_or_en_book(self):
        for lang, cleaned in (('en', True), ('ru', True), ('zh', False), (None, False)):
            with self.subTest(lang=lang):
                r = etree.fromstring(f'<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Text {ZH}</p></body></html>')
                rep = {'pdf_junk_paragraphs': 0, 'replacement_paragraphs': 0}
                clean_junk(r, rep, lang)
                s = ''.join(r.itertext())
                if cleaned:
                    self.assertTrue(s.startswith('Text ') and '港口' not in s, s)
                else:
                    self.assertEqual(s, f'Text {ZH}')
                self.assertEqual(rep['pdf_junk_paragraphs'], int(cleaned))

    def test_cjk_removed_and_nothing_else(self):
        first = next(self.tree(15).iter(f'{X}p')).text
        self.assertEqual(first, re.sub('[\u3400-\u9fff]', '', CJK_PARA))
        self.assertIn('\ufffd', first)  # a stray replacement char inside text stays

    def test_only_p_of_replacement_chars_removed(self):
        r = etree.fromstring('<html xmlns="http://www.w3.org/1999/xhtml"><body><table><tr><td>a</td><td>\ufffd\ufffd</td>'
                             '</tr></table><ul><li>\ufffd</li></ul><h2>\ufffd</h2><p>\ufffd \ufffd</p><p>b</p></body></html>')
        rep = {'pdf_junk_paragraphs': 0, 'replacement_paragraphs': 0}
        clean_junk(r, rep, 'en')
        tags = [el.tag.split('}')[1] for el in r.iter() if isinstance(el.tag, str)]
        self.assertEqual(tags, ['html', 'body', 'table', 'tr', 'td', 'td', 'ul', 'li', 'h2', 'p'])
        self.assertEqual(rep['replacement_paragraphs'], 1)

    def test_parser_expands_no_entities(self):
        r = parse(b'<!DOCTYPE r [<!ENTITY a "SECRET">]><r>x &a; y</r>')
        self.assertNotIn('SECRET', ''.join(r.itertext()))

    def test_verify_clean(self):
        rc, out, err = cli('--library', str(self.lib), '--workdir', str(self.work), 'verify')
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(out.splitlines()[-1], 'DISCREPANCIES 0')
        self.assertIn("'books': 8", out)

    def test_verify_ru(self):
        rc, out, _ = cli('--lang', 'ru', '--library', str(self.lib), '--workdir', str(self.work), 'verify')
        self.assertEqual(out.splitlines()[-1], 'РАСХОЖДЕНИЙ 0')

    def test_rerun_without_dict_keeps_other_repairs(self):
        work = Path(self.tmp.name, 'nodict')
        with mock.patch.object(translit, 'DATA_DIR', Path(self.tmp.name, 'empty')):
            rc, out, _ = cli('--library', str(self.lib), '--workdir', str(work), 'repair')
        self.assertEqual(rc, 0)
        self.assertIn('no word list', out)
        self.assertEqual({int(p.name) for p in work.iterdir() if p.is_dir()}, REPAIRED - {5, 7})


def rewrite(f, edit):
    z = zipfile.ZipFile(f)
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as zo:
        for info in z.infolist():
            zo.writestr(info, edit(info.filename, z.read(info)))
    Path(f).write_bytes(out.getvalue())


class OddBooksTest(unittest.TestCase):
    """Books outside the fixture's comfort zone: NCX DOCTYPE, CDATA, an OPF not in UTF-8, a lost anchor."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.lib = build_library(root)
        self.work = root / 'work'
        self.env = mock.patch.dict(os.environ, with_path(fake_bin(root / 'bin', ['ebook-convert'])))
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def run_cmd(self, *args):
        return cli('--library', str(self.lib), '--workdir', str(self.work), *args)

    def test_ncx_doctype_and_cdata_kept(self):
        def edit(n, d):
            if n.endswith('toc.ncx'):
                return d.replace(b'<ncx ', b'<!DOCTYPE ncx PUBLIC "-//NISO//DTD ncx 2005-1//EN" '
                                           b'"http://www.daisy.org/z3986/2005/ncx-2005-1.dtd">\n<ncx ', 1)
            if n.endswith('ch1.xhtml'):
                return d.replace(b'</head>', b'<style><![CDATA[ div > p { margin: 0 } ]]></style></head>', 1)
            return d
        rewrite(book_path(self.lib, 3), edit)
        rc, _, err = self.run_cmd('repair', '3')
        self.assertEqual(rc, 0, err)
        z = zipfile.ZipFile(next((self.work / '3').glob('*.epub')))
        self.assertIn(b'<!DOCTYPE ncx PUBLIC', z.read('OEBPS/toc.ncx'))
        self.assertIn(b'<![CDATA[ div > p { margin: 0 } ]]>', z.read('OEBPS/ch1.xhtml'))
        rc, out, _ = self.run_cmd('verify', '3')
        self.assertEqual(out.splitlines()[-1], 'DISCREPANCIES 0')

    def test_opf_not_utf8_skips_only_that_book(self):
        def edit(n, d):
            if n.endswith('.opf'):
                return d.decode().replace('encoding="utf-8"', 'encoding="windows-1251"').replace(
                    '<dc:title>Bold', '<dc:title>Жирный').encode('cp1251')
            return d
        rewrite(book_path(self.lib, 3), edit)
        rc, out, err = self.run_cmd('repair', '2', '3', '6')
        self.assertEqual(rc, 0, err)
        self.assertIn('book 3: skipped, cannot repair', err)
        with open(self.work / 'repair.tsv', encoding='utf-8') as f:
            self.assertEqual([r.split('\t')[0] for r in f.read().splitlines()[1:]], ['2', '6'])
        self.assertFalse((self.work / '3').exists())

    def test_lost_anchor_of_removed_paragraph_is_reported(self):
        rewrite(book_path(self.lib, 6), lambda n, d: d.replace('<p>���</p>'.encode(),
                                                               '<p id="mark">���</p>'.encode()))
        self.run_cmd('repair', '6')
        stat, bad = verify(str(self.lib), self.work, [6])
        self.assertEqual([c for c, _ in bad], ['refs_changed'])


class EntityAndModelTest(unittest.TestCase):
    """&nbsp; kept by the parser next to each repair, verify's own model of what repair may change."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.lib = build_library(root)
        cls.work = root / 'work'
        cls.patches = [mock.patch.dict(os.environ, with_path(fake_bin(root / 'bin', ['ebook-convert']))),
                       mock.patch.object(translit, 'DATA_DIR', root / 'data')]
        for p in cls.patches:
            p.start()
        cli('--library', str(cls.lib), 'build-dict')
        lines = '<br/>'.join(f'{EN[:120]} 小船' for _ in range(3))  # 2 ideographs a line, 6 in the heading
        epub(book_path(cls.lib, 3), 'Bold', 'en', {'ch1.xhtml': fx.p('Before&nbsp;after 中华人民共和国 text') + fx.p(EN),
                                                    'ch2.xhtml': f'<h2>Title<br/>{lines}</h2>' + fx.p(EN)})
        epub(book_path(cls.lib, 7), 'Latin font', 'ru', {'ch1.xhtml': fx.p(to_latin_font(RU).replace(' ', '&nbsp;', 1)) * 3})
        epub(book_path(cls.lib, 5), 'Latin1', 'ru', {'ch1.xhtml': fx.p(RU) + fx.p(
            '&nbsp;'.join(latin1(w) for w in LATIN1_WORDS))})
        cls.rc, _, cls.err = cli('--library', str(cls.lib), '--workdir', str(cls.work), 'repair', '3', '5', '7')

    @classmethod
    def tearDownClass(cls):
        for p in reversed(cls.patches):
            p.stop()
        cls.tmp.cleanup()

    def ch(self, i, name='ch1.xhtml'):
        return zipfile.ZipFile(next((self.work / str(i)).glob('*.epub'))).read('OEBPS/' + name).decode()

    def test_entities_repaired_around_and_verified(self):
        self.assertEqual(self.rc, 0, self.err)
        self.assertIn('Before&nbsp;after', self.ch(3))
        self.assertNotIn('中华', self.ch(3))
        self.assertIn('&nbsp;', self.ch(7))
        self.assertIn(RU.split('.')[0].split(' ', 1)[1], self.ch(7))
        self.assertIn('&nbsp;'.join(LATIN1_WORDS), self.ch(5))
        stat, bad = verify(str(self.lib), self.work, [3, 5, 7])
        self.assertEqual(bad, [])

    def test_long_heading_cjk_counted_per_line(self):
        self.assertEqual(self.ch(3, 'ch2.xhtml').count('小船'), 3)  # 2 a paragraph after the split: kept
        self.assertEqual([c for c, _ in verify(str(self.lib), self.work, [3])[1]], [])

    def test_latin1_only_for_latin1_letters(self):
        self.assertTrue(verify_mod.latin1_ok(latin1('секта'), 'секта'))
        self.assertFalse(verify_mod.latin1_ok('café', 'cafй'))

    def test_german_like_file_not_decoded(self):
        # Russian words in the Latin font among English: the word list knows them, function words are < 7%
        words = ['шел', 'ночная', 'важно', 'ведь', 'большой', 'кружки', 'жизни', 'детях', 'дождь', 'день', 'вечер',
                 'пришел', 'чай', 'краю', 'договорились']
        r = parse(fx.xhtml('x', fx.p(EN * 3 + ' '.join(to_latin_font(w) for w in words))).encode())
        before = etree.tostring(r)
        translit.load(translit.DATA_DIR)
        special = [w for w in map(to_latin_font, words) if repair_mod.FONT.search(w)]
        self.assertGreaterEqual(sum(all(translit.voc[x] for x in translit.decode(s).lower().split()) for s in special),
                                0.8 * len(special))  # the guard, not the word list, is what stops it
        rep = Counter()
        repair_mod.untranslit(r, rep)
        self.assertEqual(rep['files_from_latin_font'], 0)
        self.assertEqual(etree.tostring(r), before)


class NoCalibreTest(unittest.TestCase):
    """Without ebook-convert every book is still repaired and reported; AZW3 is skipped."""

    def test_repair_without_ebook_convert(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lib, work = build_library(root), root / 'work'
            (root / 'empty-bin').mkdir()
            with mock.patch.dict(os.environ, {'PATH': str(root / 'empty-bin')}), \
                    mock.patch.object(common, 'MAC', root / 'no-calibre'):
                rc, out, err = cli('--library', str(lib), '--workdir', str(work), 'repair')
            self.assertEqual(rc, 0, err)
            self.assertEqual(err.count('ebook-convert not found'), 1)
            want = REPAIRED - {5, 7}  # no word list in this test: the two decoders are off
            self.assertEqual({int(p.name) for p in work.iterdir() if p.is_dir()}, want)
            self.assertEqual(len(list(work.glob('*/*.epub'))), len(want))
            self.assertEqual(list(work.glob('*/*.azw3')), [])
            with open(work / 'repair.tsv', encoding='utf-8') as f:
                rows = list(csv.DictReader(f, delimiter='\t'))
            self.assertEqual({int(r['id']) for r in rows}, want)
            self.assertEqual({r['azw3'] for r in rows}, {'skipped (no ebook-convert)'})


# spoil -> code of the check that must catch it (the port of the original spoil test)
SPOILS = {
    'word': 'text_diverged',
    'id': 'refs_changed',
    'label': 'toc_label',
    'empty label': 'toc_label',
    'target': 'toc_targets',
    'image': 'bytes_changed',
    'opf': 'opf_changed',
    'ncx': 'ncx_changed',
    'doctype': 'doctype_changed',
    'one ideograph': 'text_diverged',  # repair removes CJK only where a block has more than 3
    'same length': 'text_diverged',  # small -> smell
    'cafe': 'text_diverged',  # café -> cafй: c, a, f are not latin-1 letters
    'english in cyrillic': 'text_diverged',  # an English paragraph "decoded" by the Latin font table
}


def font_read(m):
    return ''.join(verify_mod.FONT.get(c, c) for c in m[0].decode().lower()).encode()


def spoil(src, dst, kind):
    z = zipfile.ZipFile(src)
    out = io.BytesIO()
    hit = False
    with zipfile.ZipFile(out, 'w') as zo:
        for info in z.infolist():
            d = z.read(info)
            n = info.filename
            if kind == 'word' and n == 'OEBPS/ch2.xhtml':
                d2 = re.sub(rb'(<p>[^<]*?)\s\w+', rb'\1', d, count=1)
            elif kind == 'id' and n == 'OEBPS/ch2.xhtml':
                d2 = d.replace(b'id="c2"', b'id="c2x"')
            elif kind in ('label', 'empty label') and n.endswith('toc.ncx'):
                new = b'Foreign' if kind == 'label' else b''
                d2 = re.sub(rb'(<navLabel><text>)[^<]+(</text></navLabel><content src="ch1)', rb'\1' + new + rb'\2', d, count=1)
            elif kind == 'target' and n.endswith('toc.ncx'):
                d2 = d.replace(b'src="ch1.xhtml#c1"', b'src="ch1.xhtml#c19"')
            elif kind == 'image' and n == 'OEBPS/images/pic.png':
                d2 = d + b'\0'
            elif kind == 'opf' and n.endswith('.opf'):
                d2 = re.sub(rb'(<dc:title[^>]*>)', rb'\1X', d, count=1)
            elif kind == 'ncx' and n.endswith('toc.ncx'):
                d2 = re.sub(rb'(<docTitle><text>)', rb'\1X', d, count=1)
            elif kind == 'doctype' and n == 'OEBPS/ch2.xhtml':
                d2 = re.sub(rb'<!DOCTYPE[^>]*>\n?', b'', d, count=1)
            elif kind == 'one ideograph' and n == 'OEBPS/ch2.xhtml':
                d2 = d.replace('港 '.encode(), b'')
            elif kind == 'same length' and n == 'OEBPS/ch2.xhtml':
                d2 = d.replace(b'The small boat', b'The smell boat')
            elif kind == 'cafe' and n == 'OEBPS/ch2.xhtml':
                d2 = d.replace('café'.encode(), 'cafй'.encode())
            elif kind == 'english in cyrillic' and n == 'OEBPS/ch2.xhtml':
                d2 = re.sub(rb'<p>[^<]+</p>', lambda m: re.sub(rb'[A-Za-z]+', font_read, m[0][3:-4]).join((b'<p>', b'</p>')),
                            d, count=1)
            else:
                d2 = d
            hit |= d2 != d
            zo.writestr(info, d2)
    assert hit, f'spoil "{kind}" did not apply'
    dst.write_bytes(out.getvalue())
    t = src.stat().st_mtime  # the old time, or every spoil would only show up as "AZW3 older than EPUB"
    os.utime(dst, (t, t))


class SpoilTest(unittest.TestCase):
    """Verify must turn red on every spoil, and by its own check."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.lib = build_library(root)
        cls.work = root / 'work'
        with mock.patch.dict(os.environ, with_path(fake_bin(root / 'bin', ['ebook-convert']))):
            rc, _, err = cli('--library', str(cls.lib), '--workdir', str(cls.work), 'repair', '2')
        assert rc == 0, err
        cls.epub = next((cls.work / '2').glob('*.epub'))
        cls.keep = root / 'keep.epub'
        shutil.copy2(cls.epub, cls.keep)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def tearDown(self):
        shutil.copy2(self.keep, self.epub)

    def codes(self):
        return [c for c, _ in verify(str(self.lib), self.work, [2])[1]]

    def test_clean_is_green(self):
        self.assertEqual(self.codes(), [])

    def test_each_spoil_caught_by_its_check(self):
        for kind, want in SPOILS.items():
            with self.subTest(spoil=kind):
                spoil(self.keep, self.epub, kind)
                codes = self.codes()
                self.assertIn(want, codes)
                self.assertNotIn('azw3_stale', codes)
                shutil.copy2(self.keep, self.epub)

    def test_old_azw3(self):
        azw3 = self.epub.with_suffix('.azw3')
        os.utime(self.epub, (azw3.stat().st_mtime + 5,) * 2)  # EPUB newer than AZW3 = AZW3 of an earlier run
        self.assertEqual(self.codes(), ['azw3_stale'])


if __name__ == '__main__':
    unittest.main()
