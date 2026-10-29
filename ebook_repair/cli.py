"""python3 -m ebook_repair <command>: one entry point for all commands."""
import argparse
import sys
from pathlib import Path

from . import i18n
from .i18n import t


def flag_lang(argv):
    """--lang is read before argparse, so help texts are already in the chosen language; the last one wins, as in argparse."""
    lang = None
    for k, x in enumerate(argv):
        if x == '--lang' and k + 1 < len(argv):
            lang = argv[k + 1]
        elif x.startswith('--lang='):
            lang = x.split('=', 1)[1]
    return lang


def build():
    from .apply import run_apply
    from .check import SAMPLE_KINDS, run_bold, run_check, run_samples
    from .repair import run_repair
    from .sync import run_sync
    from .translit import run_build_dict
    from .verify import run_azw3_check, run_verify

    # global options work before and after the command; SUPPRESS keeps the subparser from resetting them
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--lang', choices=('en', 'ru'), default=argparse.SUPPRESS, help=t('h_lang'))
    common.add_argument('--library', default=argparse.SUPPRESS, help=t('h_library'))
    common.add_argument('--workdir', default=argparse.SUPPRESS, help=t('h_workdir'))
    p = argparse.ArgumentParser(prog='python3 -m ebook_repair', description=t('h_prog'), parents=[common])
    sub = p.add_subparsers(dest='command', metavar='COMMAND')
    sub.required = True

    def cmd(name, func):
        s = sub.add_parser(name, parents=[common], help=t('h_' + name), description=t('h_' + name))
        s.set_defaults(func=func)
        return s

    def books(s):
        s.add_argument('ids', nargs='*', type=int, metavar='ID', help=t('h_ids'))
        s.add_argument('--tag', help=t('h_tag'))

    books(cmd('check', run_check))
    s = cmd('samples', run_samples)
    s.add_argument('kind', choices=SAMPLE_KINDS, help=t('h_kind'))
    s.add_argument('id', type=int, help=t('h_id'))
    s.add_argument('count', type=int, nargs='?', default=3, help=t('h_count'))
    books(cmd('bold', run_bold))
    books(cmd('repair', run_repair))
    s = cmd('verify', run_verify)
    s.add_argument('ids', nargs='*', type=int, metavar='ID', help=t('h_ids_workdir'))
    s = cmd('apply', run_apply)
    s.add_argument('ids', nargs='*', type=int, metavar='ID', help=t('h_ids_workdir'))
    s = cmd('sync', run_sync)
    m = s.add_mutually_exclusive_group()
    m.add_argument('--add', action='store_true', help=t('h_add'))
    m.add_argument('--mirror', action='store_true', help=t('h_mirror'))
    s.add_argument('--yes', action='store_true', help=t('h_yes'))
    s.add_argument('--force', action='store_true', help=t('h_force'))
    s.add_argument('--serial', help=t('h_serial'))
    s.add_argument('--device-dir', help=t('h_device_dir'))
    s = cmd('azw3-check', run_azw3_check)
    s.add_argument('epub', help=t('h_epub'))
    books(cmd('build-dict', run_build_dict))
    return p


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    i18n.LANG = i18n.pick(flag_lang(argv))
    a = build().parse_args(argv)
    from .common import library_path
    a.library = library_path(getattr(a, 'library', None))
    a.workdir = Path(getattr(a, 'workdir', None) or 'ebook-repair-work')
    return a.func(a) or 0
