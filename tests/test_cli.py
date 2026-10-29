import io
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from fixtures import ROOT, epub, png
from ebook_repair import common, i18n
from ebook_repair.common import azw3_source


class LangTest(unittest.TestCase):
    def pick(self, flag=None, **env):
        clean = {k: v for k, v in os.environ.items() if k not in ('EBOOK_REPAIR_LANG', 'LC_ALL', 'LANG')}
        with mock.patch.dict(os.environ, {**clean, **env}, clear=True):
            return i18n.pick(flag)

    def test_order(self):
        self.assertEqual(self.pick('en', EBOOK_REPAIR_LANG='ru', LANG='ru_RU.UTF-8'), 'en')
        self.assertEqual(self.pick(None, EBOOK_REPAIR_LANG='ru', LANG='en_US.UTF-8'), 'ru')
        self.assertEqual(self.pick(None, LC_ALL='ru_RU.UTF-8', LANG='en_US.UTF-8'), 'ru')
        self.assertEqual(self.pick(None, LANG='ru_RU.UTF-8'), 'ru')
        self.assertEqual(self.pick(None, LANG='de_DE.UTF-8'), 'en')
        self.assertEqual(self.pick(None), 'en')

    def test_every_message_has_both_languages(self):
        for k, v in i18n.M.items():
            self.assertEqual(len(v), 2, k)
            self.assertTrue(all(v), k)


class EntryTest(unittest.TestCase):
    def run_module(self, *args):
        return subprocess.run([sys.executable, '-m', 'ebook_repair', *args], cwd=ROOT, capture_output=True, text=True)

    def test_help(self):
        r = self.run_module('--help')
        self.assertEqual(r.returncode, 0, r.stderr)
        for c in ('check', 'samples', 'bold', 'repair', 'verify', 'apply', 'sync', 'azw3-check', 'build-dict'):
            self.assertIn(c, r.stdout)
        r = self.run_module('--lang', 'ru', 'check', '--help')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('только чтение', r.stdout)


class Azw3SourceTest(unittest.TestCase):
    def test_truncated_jpeg_is_reencoded(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            o = io.BytesIO()
            im = Image.new('RGB', (64, 64))
            im.putdata([(x * 7 % 256, x * 13 % 256, x * 31 % 256) for x in range(64 * 64)])  # a flat image is too short to cut
            im.save(o, 'JPEG')
            f = Path(tmp, 'book.epub')
            epub(f, 'Pictures', 'en', {'ch1.xhtml': '<p>x</p>'},
                 images={'images/cut.jpg': o.getvalue()[:-200], 'images/ok.png': png()})
            out = Path(tmp, 'out')  # the stand-in has the book's name: it must not land on the book itself
            out.mkdir()
            src, fixed = azw3_source(f, out)
            self.assertEqual(fixed, ['OEBPS/images/cut.jpg'])
            self.assertNotEqual(src, f)
            Image.open(io.BytesIO(zipfile.ZipFile(src).read('OEBPS/images/cut.jpg'))).load()
            self.assertEqual(azw3_source(src, tmp), (src, []))  # a sound book goes to ebook-convert as it is

    def test_without_pillow_images_stay(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp, 'book.epub')
            epub(f, 'Pictures', 'en', {'ch1.xhtml': '<p>x</p>'}, images={'images/cut.jpg': b'\xff\xd8\xff broken'})
            err = io.StringIO()
            with mock.patch.dict(sys.modules, {'PIL': None}), mock.patch.object(common, 'warned', set()), \
                    mock.patch.object(i18n, 'LANG', 'en'), mock.patch('sys.stderr', err):
                self.assertEqual(azw3_source(f, tmp), (f, []))
                self.assertEqual(azw3_source(f, tmp), (f, []))
            self.assertEqual(err.getvalue().splitlines(), [i18n.M['no_pillow'][0]])


if __name__ == '__main__':
    unittest.main()
