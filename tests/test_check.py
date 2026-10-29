import csv
import tempfile
import unittest
from pathlib import Path

from fixtures import BOOKS, TAGGED, build_library, cli
from ebook_repair.check import COUNTS


class CheckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.lib = build_library(cls.tmp.name)
        cls.work = Path(cls.tmp.name, 'work')
        cls.rc, cls.out, cls.err = cli('--library', str(cls.lib), '--workdir', str(cls.work), 'check')
        with open(cls.work / 'check.tsv', encoding='utf-8') as f:
            cls.rows = {int(r['id']): r for r in csv.DictReader(f, delimiter='\t')}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_all_books_checked(self):
        self.assertEqual(self.rc, 0, self.err)
        self.assertEqual(set(self.rows), set(BOOKS))
        self.assertIn('checked 15; with findings 10', self.out)

    def test_each_class_only_in_its_book(self):
        for i, (title, _, _, col) in BOOKS.items():
            found = {c for c in COUNTS if int(self.rows[i][c])}
            with self.subTest(book=title):
                self.assertEqual(found, {col} if col else set())

    def test_heading_length_reported(self):
        self.assertGreater(int(self.rows[2]['max_heading']), 300)

    def test_tag_and_ids_select_books(self):
        work = Path(self.tmp.name, 'w2')
        cli('--library', str(self.lib), '--workdir', str(work), 'check', '--tag', 'Sample')
        ids = [r.split('\t')[0] for r in (work / 'check.tsv').read_text(encoding='utf-8').splitlines()[1:]]
        self.assertEqual(ids, [str(i) for i in TAGGED])
        cli('--library', str(self.lib), '--workdir', str(work), 'check', '4', '9')
        ids = [r.split('\t')[0] for r in (work / 'check.tsv').read_text(encoding='utf-8').splitlines()[1:]]
        self.assertEqual(ids, ['4', '9'])

    def test_samples_and_bold(self):
        rc, out, _ = cli('--library', str(self.lib), 'samples', 'heading', '2')
        self.assertEqual(rc, 0)
        self.assertIn('total 1', out)
        self.assertIn('Harbour', out)
        rc, out, _ = cli('--library', str(self.lib), 'samples', 'replacement', '6', '10')
        self.assertIn('total 4', out)
        rc, out, _ = cli('--library', str(self.lib), 'bold', '3', '1')
        self.assertEqual(rc, 0)
        lines = out.splitlines()
        self.assertRegex(lines[0], r'^3 files 1, bold >30%: 1 OEBPS/ch1.xhtml 9\d%')
        self.assertRegex(lines[1], r'^1 files 2, bold >30%: 0')

    def test_missing_library(self):
        rc, _, err = cli('--library', str(Path(self.tmp.name, 'nope')), 'check')
        self.assertEqual(rc, 1)
        self.assertIn('no calibre library', err)


if __name__ == '__main__':
    unittest.main()
