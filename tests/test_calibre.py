"""Tests with the real ebook-convert; skipped when calibre is not installed."""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fixtures import build_library, cli
from ebook_repair import translit
from ebook_repair.common import MAC

HAVE_CALIBRE = bool(shutil.which('ebook-convert') or (MAC / 'ebook-convert').exists())


@unittest.skipUnless(HAVE_CALIBRE, 'calibre ebook-convert is not installed')
class RealCalibreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.lib = build_library(root)
        cls.work = root / 'work'
        cls.patch = mock.patch.object(translit, 'DATA_DIR', root / 'data')
        cls.patch.start()
        cli('--library', str(cls.lib), 'build-dict')
        cls.rc, cls.out, cls.err = cli('--library', str(cls.lib), '--workdir', str(cls.work), 'repair', '2', '7')

    @classmethod
    def tearDownClass(cls):
        cls.patch.stop()
        cls.tmp.cleanup()

    def epub(self, i):
        return next((self.work / str(i)).glob('*.epub'))

    def test_repair_makes_azw3_and_verify_is_clean(self):
        self.assertEqual(self.rc, 0, self.err)
        self.assertIn('azw3=ok', self.out)
        rc, out, err = cli('--library', str(self.lib), '--workdir', str(self.work), 'verify')
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(out.splitlines()[-1], 'DISCREPANCIES 0')

    def test_azw3_check(self):
        for i in (2, 7):
            rc, out, err = cli('azw3-check', str(self.epub(i)))
            self.assertEqual(rc, 0, out + err)
            self.assertEqual(out.splitlines()[-1], 'AZW3 VERIFIED')

    def test_azw3_check_red_on_other_book(self):
        with tempfile.TemporaryDirectory() as tmp:
            e = Path(tmp, 'mixed.epub')
            shutil.copy2(self.epub(2), e)
            shutil.copy2(self.epub(7).with_suffix('.azw3'), e.with_suffix('.azw3'))
            rc, out, _ = cli('azw3-check', str(e))
        self.assertEqual(rc, 1)
        self.assertEqual(out.splitlines()[-1], 'letters of EPUB and AZW3 differ')


if __name__ == '__main__':
    unittest.main()
