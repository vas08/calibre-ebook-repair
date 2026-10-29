"""sync with a fake adb in PATH whose device storage is a temp folder."""
import os
import tempfile
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

from fixtures import build_library, cli, fake_bin, with_path
from ebook_repair.sync import MARKER, compare, device_files, key, local_files

STALE = 'Gone Author/Gone (99)/Gone - Gone Author.epub'
ROOT_PUSHED = {'metadata.db', 'metadata_db_prefs_backup.json', MARKER}


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.lib = build_library(root)
        self.dev = root / 'device'
        self.remote = self.dev / 'sdcard' / 'Books' / 'Fixture Library'
        self.log = root / 'adb.log'
        env = with_path(fake_bin(root / 'bin', ['adb']))
        env.update(FAKE_ADB_ROOT=str(self.dev), FAKE_LOG=str(self.log))
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        self.loc = local_files(str(self.lib))
        # files outside the library folder that no sync may ever touch
        self.outside = [self.dev / 'sdcard/Books/Other Library/Someone/Book (1)/Book.epub', self.dev / 'sdcard/DCIM/photo.jpg']
        for f in self.outside:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b'keep me')
        self.cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self.cwd)
        self.env.stop()
        self.tmp.cleanup()

    def sync(self, *args, lib=None):
        return cli('--library', str(lib or self.lib), 'sync', *args)

    def put(self, rel, data=b'x'):
        f = self.remote / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
        return f

    def device(self):
        return {f.relative_to(self.remote).as_posix() for f in self.remote.rglob('*') if f.is_file()}

    def assert_outside_kept(self):
        self.assertEqual([f.read_bytes() for f in self.outside], [b'keep me'] * 2)

    def test_local_files_skip_trash_and_root(self):
        self.assertTrue(self.loc)
        self.assertFalse([p for p in self.loc if p.startswith('.caltrash') or '/' not in p])

    def test_count_only(self):
        changed = sorted(self.loc)[0]
        self.put(changed, b'other size')
        self.put(STALE)
        rc, out, err = self.sync()
        self.assertEqual(rc, 0, err)
        self.assertIn(f'new {len(self.loc) - 1} ', out)
        self.assertIn('changed 1 ', out)
        self.assertIn('stale 1;', out)
        self.assertEqual(out.splitlines()[1:], [STALE])  # stale paths, one per line, after the numbers
        self.assertEqual(self.device(), {changed, STALE})  # nothing pushed or deleted
        self.assertNotIn("'push'", self.log.read_text())

    def test_same_size_other_time_is_changed(self):
        p = sorted(self.loc)[0]
        f = self.put(p, (self.lib / p).read_bytes())
        os.utime(f, (self.loc[p][1] - 100,) * 2)
        rc, out, _ = self.sync()
        self.assertIn('changed 1 ', out)

    def test_control_chars_in_stale_names_escaped(self):
        self.put('Gone/a\nb.epub')
        rc, out, _ = self.sync()
        self.assertEqual(out.splitlines()[1:], ['Gone/a\\nb.epub'])

    def test_add_deletes_nothing_and_marks_folder(self):
        self.put(STALE)
        rc, out, err = self.sync('--add')
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(self.device(), set(self.loc) | {STALE} | ROOT_PUSHED)
        for p, (size, _) in self.loc.items():
            self.assertEqual((self.remote / p).stat().st_size, size)
        self.assertNotIn('full-text-search.db', self.device())
        rc, out, _ = self.sync()
        self.assertIn('new 0 (0 MB), changed 0 (0 MB), stale 1;', out)

    def test_mirror_needs_yes(self):
        self.put(STALE)
        rc, out, err = self.sync('--mirror')
        self.assertEqual(rc, 2)
        self.assertIn('--yes', err)
        self.assertEqual(out.splitlines()[1:], [STALE])  # shown before the refusal
        self.assertEqual(self.device(), {STALE})

    def test_mirror_yes_deletes_stale(self):
        self.sync('--add')
        self.put(STALE)
        rc, out, err = self.sync('--mirror', '--yes')
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(self.device(), set(self.loc) | ROOT_PUSHED)
        self.assertFalse((self.remote / 'Gone Author').exists())  # the emptied folder goes too
        self.assertIn('deleted 1;', out)
        self.assert_outside_kept()

    def test_mirror_needs_marker(self):
        self.put(STALE)
        for p in self.loc:  # a full copy made by something else: no marker
            self.put(p, (self.lib / p).read_bytes())
        rc, _, err = self.sync('--mirror', '--yes')
        self.assertEqual(rc, 2)
        self.assertIn(MARKER, err)
        self.assertIn(STALE, self.device())

    def test_mirror_refuses_empty_library(self):
        self.sync('--add')
        n = len(self.device())
        for d in list(self.lib.iterdir()):  # book folders gone, metadata.db stays
            if d.is_dir() and d.name != '.caltrash':
                d.rename(self.lib.parent / ('moved-' + d.name))
        rc, _, err = self.sync('--mirror', '--yes', '--force')
        self.assertEqual(rc, 2)
        self.assertIn('no book files', err)
        self.assertEqual(len(self.device()), n)

    def test_mirror_more_than_half_stale_needs_force(self):
        self.sync('--add')
        n = len(self.device())
        moved = sorted({p.split('/')[1] for p in self.loc})[:-2]  # most book folders gone
        for b in moved:
            d = self.lib / 'Fixture Author' / b
            d.rename(self.lib.parent / ('moved-' + b))
        rc, _, err = self.sync('--mirror', '--yes')
        self.assertEqual(rc, 2)
        self.assertIn('--force', err)
        self.assertEqual(len(self.device()), n)
        rc, out, err = self.sync('--mirror', '--yes', '--force')
        self.assertEqual(rc, 0, out + err)
        self.assertLess(len(self.device()), n)

    def test_library_dot_and_dotdot(self):
        self.sync('--add')
        os.chdir(self.lib)
        rc, out, err = self.sync('--mirror', '--yes', lib='.')  # the folder name comes from the resolved path
        self.assertEqual(rc, 0, out + err)
        self.assertIn('/sdcard/Books/Fixture Library on', out)
        (self.lib / 'sub').mkdir()
        rc, out, _ = self.sync('--mirror', '--yes', lib=str(self.lib / 'sub') + '/..')
        self.assertIn('/sdcard/Books/Fixture Library on', out)
        self.assert_outside_kept()

    def test_library_at_root_refused(self):
        with mock.patch('ebook_repair.sync.open_db'):
            rc, _, err = self.sync(lib='/')
        self.assertEqual(rc, 2)
        self.assertIn('no folder name', err)

    def test_bad_device_dir(self):
        for d in ('/', '//', '', 'sdcard/Books', '/sdcard/Books/..', '/sdcard/./Books'):
            with self.subTest(device_dir=d):
                rc, _, err = self.sync('--device-dir', d, '--mirror', '--yes')
                self.assertEqual(rc, 2)
                self.assertIn('--device-dir must be', err)
        self.assert_outside_kept()

    def test_device_dir_sdcard_is_not_cleaned(self):
        rc, _, err = self.sync('--device-dir', '/sdcard', '--mirror', '--yes', '--force')
        self.assertEqual(rc, 2)
        self.assertIn('named like the library', err)
        self.assert_outside_kept()

    def test_device_dir(self):
        rc, out, _ = self.sync('--device-dir', '/sdcard/Other', '--add')
        self.assertEqual(rc, 0)
        self.assertTrue((self.dev / 'sdcard' / 'Other' / 'metadata.db').exists())
        self.assertFalse((self.dev / 'sdcard' / 'Other' / MARKER).exists())  # not the library's name: no marker
        self.assertIn('marker not written', out)
        rc, _, err = self.sync('--device-dir', '/sdcard/Other', '--mirror', '--yes', '--force')
        self.assertEqual(rc, 2)
        self.assertIn('named like the library', err)

    def test_device_dir_parent_of_other_library(self):
        args = ('--device-dir', '/sdcard/Books')  # the parent of every library folder, the other library included
        rc, out, err = self.sync(*args, '--add')
        self.assertEqual(rc, 0, out + err)
        self.assertFalse((self.dev / 'sdcard/Books' / MARKER).exists())
        for extra in ((), ('--force',)):
            rc, _, err = self.sync(*args, '--mirror', '--yes', *extra)
            self.assertEqual(rc, 2)
            self.assertIn('named like the library', err)
        self.assert_outside_kept()

    def test_device_dir_with_library_name_is_marked(self):
        d = '/sdcard/Elsewhere/fixture library'  # the same name up to case
        rc, out, err = self.sync('--device-dir', d, '--add')
        self.assertEqual(rc, 0, out + err)
        marker = self.dev / d.lstrip('/') / MARKER
        self.assertEqual(marker.read_text(encoding='utf-8').strip(), 'Fixture Library')
        rc, out, err = self.sync('--device-dir', d, '--mirror', '--yes')
        self.assertEqual(rc, 0, out + err)

    def test_mirror_refuses_marker_of_other_library(self):
        self.sync('--add')
        self.put(STALE)
        for text in ('Other Library\n', 'calibre library copied by ebook-repair\n', ''):
            with self.subTest(marker=text):
                (self.remote / MARKER).write_text(text, encoding='utf-8')
                rc, _, err = self.sync('--mirror', '--yes', '--force')
                self.assertEqual(rc, 2)
                self.assertIn('not this library', err)
                self.assertIn(STALE, self.device())

    @unittest.skipIf(hasattr(os, 'geteuid') and os.geteuid() == 0, 'root reads any folder')
    def test_unreadable_library_folder_stops(self):
        self.sync('--add')
        self.put(STALE)
        n = len(self.device())
        d = self.lib / 'Fixture Author' / 'Clean (1)'
        d.chmod(0)
        try:
            rc, _, err = self.sync('--mirror', '--yes', '--force')
        finally:
            d.chmod(0o755)
        self.assertEqual(rc, 1)
        self.assertIn('cannot be read', err)
        self.assertNotIn('Traceback', err)
        self.assertEqual(len(self.device()), n)

    def test_case_and_unicode_form_are_the_same_file(self):
        local = unicodedata.normalize('NFC', 'Fixture Author/Café (1)/note.txt')
        (self.lib / local).parent.mkdir(parents=True)
        (self.lib / local).write_bytes(b'note')
        self.put(unicodedata.normalize('NFD', local).upper(), b'note')
        rc, out, _ = self.sync()
        self.assertEqual(rc, 0)
        self.assertIn('changed 1 ', out)
        self.assertIn('stale 0;', out)
        self.assertIn('letter case only 1', out)

    def test_key_folds_composed_and_decomposed(self):
        self.assertEqual(key('ΐ'), key('Ϊ́'))  # ΐ and Ϊ́
        self.assertEqual(key('Straße'), key('STRASSE'))

    def test_case_twins_in_library_refused(self):
        with self.assertRaises(SystemExit):
            compare({'A/x.epub': (1, 1), 'a/X.EPUB': (1, 1)}, {}, [], [])

    def test_file_and_folder_with_one_path_refused(self):
        with self.assertRaises(SystemExit):
            compare({'A/b/c.epub': (1, 1)}, {'A/b': (1, 1)}, [], [])
        with self.assertRaises(SystemExit):
            compare({'A/b': (1, 1)}, {'A/b/c.epub': (1, 1)}, [], [])

    def test_device_path_outside_folder_refused(self):
        for p in ('../x.epub', 'A/../../x.epub', '/sdcard/x.epub', ''):
            with self.subTest(path=p), self.assertRaises(SystemExit):
                device_files(lambda *a: f'-|1|1.0|{p}\0'.encode(), '/sdcard/Books/L')

    def test_two_devices(self):
        with mock.patch.dict(os.environ, {'FAKE_ADB_DEVICES': 'AAA,BBB'}):
            rc, _, err = self.sync()
            self.assertEqual(rc, 2)
            self.assertIn('several devices connected', err)
            self.assertIn('AAA, BBB', err)
            rc, out, _ = self.sync('--serial', 'BBB')
            self.assertEqual(rc, 0)
            self.assertIn('on BBB', out)
            rc, _, err = self.sync('--serial', 'CCC')
            self.assertEqual(rc, 2)
            self.assertIn('CCC is not connected', err)

    def test_no_device(self):
        with mock.patch.dict(os.environ, {'FAKE_ADB_DEVICES': ''}):
            rc, _, err = self.sync()
        self.assertEqual(rc, 2)
        self.assertIn('no device', err)


if __name__ == '__main__':
    unittest.main()
