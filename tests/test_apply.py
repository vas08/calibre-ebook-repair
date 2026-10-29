"""apply with fake calibredb and pgrep in PATH."""
import hashlib
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from fixtures import EN, RU, book_path, build_library, cli, epub, fake_bin, p, with_path
from test_repair import spoil

USER_V2 = 'USER EDITED VERSION, the only copy'


def md5(f):
    return hashlib.md5(Path(f).read_bytes()).hexdigest()


def has(f, s):
    z = zipfile.ZipFile(f)
    return any(s.encode() in z.read(n) for n in z.namelist())


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.lib = build_library(root)
        self.work = root / 'work'
        self.log = root / 'calls.log'
        env = with_path(fake_bin(root / 'bin', ['ebook-convert', 'calibredb', 'pgrep']))
        env['FAKE_LOG'] = str(self.log)
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        for cmd in ('repair', 'verify'):
            rc, out, err = self.run_cmd(cmd, '2', '3', '6')
            assert rc == 0, out + err
        self.orig = {(i, f): md5(book_path(self.lib, i, f)) for i, f in ((2, 'epub'), (2, 'azw3'), (3, 'epub'))}

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def run_cmd(self, *args, lib=None):
        return cli('--library', str(lib or self.lib), '--workdir', str(self.work), *args)

    def apply(self, *ids):
        return self.run_cmd('apply', *ids)

    def repaired(self, i, fmt):
        return next((self.work / str(i)).glob('*.' + fmt))

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_refuses_when_calibre_open(self):
        with mock.patch.dict(os.environ, {'FAKE_CALIBRE_OPEN': '1'}):
            rc, out, err = self.apply('2')
        self.assertEqual(rc, 2)
        self.assertIn('the calibre program is open', err)
        self.assertEqual(md5(book_path(self.lib, 2, 'epub')), self.orig[2, 'epub'])
        self.assertFalse((self.work / '2' / 'before').exists())
        self.assertEqual(self.calls(), [])

    def test_backup_replace_md5(self):
        rc, out, err = self.apply('2', '3')
        self.assertEqual(rc, 0, out + err)
        self.assertIn('books 2; not verified: 0', out)
        name = book_path(self.lib, 2, 'epub').stem
        self.assertEqual(sorted(os.listdir(self.work / '2' / 'before')),
                         sorted([f'{name}.{self.orig[2, "azw3"][:8]}.azw3', f'{name}.{self.orig[2, "epub"][:8]}.epub']))
        for f in ('epub', 'azw3'):
            self.assertEqual(md5(self.work / '2' / 'before' / f'{name}.{self.orig[2, f][:8]}.{f}'), self.orig[2, f])
        for i in (2, 3):
            for f in ('epub', 'azw3'):  # book 3 had no AZW3: it is added
                self.assertEqual(md5(book_path(self.lib, i, f)), md5(self.repaired(i, f)))
        self.assertEqual(len(os.listdir(self.work / '3' / 'before')), 1)
        self.assertEqual((self.work / 'apply.log').read_text().splitlines(), ['2\tverified', '3\tverified'])
        calls = [eval(x) for x in self.calls()]
        self.assertEqual(calls[0], ['add_format', '--with-library', str(self.lib), '2', str(self.repaired(2, 'epub'))])
        self.assertEqual(len(calls), 4)

    def test_rerun_is_already_applied(self):
        self.apply('2')
        rc, out, _ = self.apply('2')
        self.assertEqual(rc, 0)
        self.assertIn('2 already applied', out)
        self.assertEqual(len(self.calls()), 2)  # nothing replaced the second time

    def test_user_copy_after_apply_is_never_lost(self):
        self.apply('2')
        lib_epub = book_path(self.lib, 2, 'epub')
        # the user's newer, only copy; it has a long heading again, so it can be repaired too
        epub(lib_epub, 'Long heading', 'en', {'ch1.xhtml': p(USER_V2) + f'<h2>Again<br/>{EN * 2}</h2>' + p(RU)})
        v2 = md5(lib_epub)
        rc, out, _ = self.apply()
        self.assertEqual(rc, 1)
        self.assertIn('2 LIBRARY CHANGED', out)
        self.assertEqual(md5(lib_epub), v2)
        # a new repair of v2 + verify + apply keeps both earlier versions, each once
        for cmd in ('repair', 'verify', 'apply'):
            self.run_cmd(cmd, '2')
        kept = {md5(f) for f in (self.work / '2' / 'before').glob('*.epub')}
        self.assertEqual(kept, {self.orig[2, 'epub'], v2})
        self.assertTrue(any(has(f, USER_V2) for f in (self.work / '2' / 'before').glob('*.epub')))

    def test_workdir_of_another_library(self):
        other = build_library(Path(self.tmp.name, 'other'))
        b3 = book_path(other, 3, 'epub')
        epub(b3, 'Another book', 'en', {'ch1.xhtml': p('A completely different book in library B.')})
        before = md5(b3)
        rc, out, _ = self.run_cmd('apply', '3', lib=other)
        self.assertEqual(rc, 1)
        self.assertIn('3 OTHER LIBRARY', out)
        self.assertEqual(md5(b3), before)
        rc, out, _ = self.run_cmd('verify', '3', lib=other)  # verify on library B does not bless library A's files
        self.assertEqual(rc, 1)
        self.assertIn('repaired from another library', out)

    def test_apply_needs_verify(self):
        e = self.repaired(2, 'epub')
        keep = Path(self.tmp.name, 'keep.epub')
        shutil.copy2(e, keep)
        spoil(keep, e, 'word')
        rc, out, _ = self.run_cmd('verify', '2')
        self.assertEqual(rc, 1)
        self.assertFalse((self.work / '2' / 'verified.json').exists())  # a failed verify removes the old record
        rc, out, _ = self.apply('2')
        self.assertEqual(rc, 1)
        self.assertIn('2 NOT VERIFIED', out)
        self.assertEqual(md5(book_path(self.lib, 2, 'epub')), self.orig[2, 'epub'])

    def test_changed_after_verify(self):
        e = self.repaired(3, 'epub')
        keep = Path(self.tmp.name, 'keep.epub')
        shutil.copy2(e, keep)
        spoil(keep, e, 'opf')
        rc, out, _ = self.apply('3')
        self.assertEqual(rc, 1)
        self.assertIn('3 CHANGED AFTER VERIFY', out)
        self.assertEqual(self.calls(), [])

    def test_new_repair_needs_new_verify(self):
        self.assertTrue((self.work / '2' / 'verified.json').exists())
        rc, _, err = self.run_cmd('repair', '2')
        self.assertEqual(rc, 0, err)
        self.assertFalse((self.work / '2' / 'verified.json').exists())
        rc, out, _ = self.apply('2')
        self.assertIn('2 NOT VERIFIED', out)

    def test_source_record_of_other_copy_is_not_enough(self):
        # repair stopped between writing source.json for the user's new copy and removing the old verify record
        lib_epub = book_path(self.lib, 2, 'epub')
        epub(lib_epub, 'Long heading', 'en', {'ch1.xhtml': p(USER_V2)})
        v2 = md5(lib_epub)
        rec = self.work / '2' / 'source.json'
        rec.write_text(rec.read_text().replace(self.orig[2, 'epub'], v2))
        rc, out, _ = self.apply('2')
        self.assertEqual(rc, 1)
        self.assertIn('2 LIBRARY CHANGED', out)
        self.assertEqual(md5(lib_epub), v2)

    def test_azw3_changed_after_verify(self):
        a = self.repaired(2, 'azw3')
        a.write_bytes(a.read_bytes() + b'\0')
        rc, out, _ = self.apply('2')
        self.assertEqual(rc, 1)
        self.assertIn('2 CHANGED AFTER VERIFY', out)
        self.assertEqual(self.calls(), [])

    def test_verify_after_apply_then_apply_is_already_applied(self):
        self.apply('2')
        rc, out, _ = self.run_cmd('verify', '2')  # the library now holds the repaired copy: no longer its source
        self.assertEqual(rc, 1)
        self.assertFalse((self.work / '2' / 'verified.json').exists())
        rc, out, _ = self.apply('2')
        self.assertEqual(rc, 0, out)
        self.assertIn('2 already applied', out)
        self.assertEqual(len(self.calls()), 2)

    def test_only_azw3_left_is_finished(self):
        with mock.patch.dict(os.environ, {'FAKE_CALIBREDB_NOOP': 'azw3'}):
            rc, out, _ = self.apply('2')
        self.assertEqual(rc, 1)
        self.assertIn('2 MISMATCH azw3', out)
        self.assertEqual(md5(book_path(self.lib, 2, 'epub')), md5(self.repaired(2, 'epub')))
        self.assertEqual(md5(book_path(self.lib, 2, 'azw3')), self.orig[2, 'azw3'])
        rc, out, _ = self.apply('2')
        self.assertEqual(rc, 0, out)
        self.assertIn('2 verified', out)
        self.assertEqual(len(self.calls()), 3)
        self.assertIn("'2', ", self.calls()[-1])
        self.assertTrue(self.calls()[-1].rstrip("']").endswith('.azw3'))
        self.assertEqual(md5(book_path(self.lib, 2, 'azw3')), md5(self.repaired(2, 'azw3')))
        self.assertEqual({b.suffix for b in (self.work / '2' / 'before').iterdir()}, {'.epub', '.azw3'})

    def test_silent_no_write_is_a_mismatch(self):
        with mock.patch.dict(os.environ, {'FAKE_CALIBREDB_NOOP': '1'}):
            rc, out, _ = self.apply('3')
        self.assertEqual(rc, 1)
        self.assertIn('3 MISMATCH epub', out)
        self.assertEqual((self.work / 'apply.log').read_text(), '3\tmismatch epub\n')
        self.assertEqual(len(self.calls()), 1)  # stopped at the first mismatch: the AZW3 was not tried

    def test_backups_before_first_replacement(self):
        def broken_backup(d, cur):
            if cur.suffix == '.azw3':
                raise OSError('disk full')
            return real(d, cur)
        from ebook_repair import apply as ap
        real = ap.backup
        with mock.patch.object(ap, 'backup', broken_backup):
            rc, out, _ = self.apply('2')
        self.assertEqual(rc, 1)
        self.assertIn('2 NO BACKUP azw3', out)
        self.assertEqual(self.calls(), [])  # the EPUB was not replaced either

    def test_missing_repaired_file(self):
        self.repaired(6, 'azw3').unlink()
        rc, out, _ = self.apply('6')
        self.assertEqual(rc, 1)
        self.assertIn('6 NO REPAIRED FILE azw3', out)
        self.assertEqual(self.calls(), [])  # nothing replaced when a file is missing


if __name__ == '__main__':
    unittest.main()
