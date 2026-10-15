"""Разбор конфликтов modify/delete.

python vm_resolve_md.py --work <рабочий каталог> [--apply]

Удалено поставщиком, изменено у нас:
  trivial — наши изменения только шум выгрузки (см. _vm_noise.py) -> принять удаление (git rm);
  real    — настоящие доработки в удалённом поставщиком файле -> в отчёт, решает человек/агент.
Удалено у нас, изменено поставщиком:
  trivial — у нас удалён пустой/шумовой файл (например, пустой модуль) -> взять версию поставщика;
  real    -> в отчёт.
Дополнительно ищет «сирот»: файлы, добавленные только у нас, внутри объекта, который поставщик удалил
(например, своя форма в удалённой обработке) — их тоже нужно решить.

Без --apply только печатает и пишет <work>/md_classes.tsv.
"""
import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _vm_common import git, load_state, show, write_lines, names  # noqa: E402
from _vm_noise import norm, is_empty  # noqa: E402


def object_root(path):
    """Каталог/Имя объекта верхнего уровня для пути выгрузки."""
    parts = path.split('/')
    if len(parts) >= 2:
        return parts[0] + '/' + parts[1].removesuffix('.xml')
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', required=True)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--from-revs', action='store_true', help='ours из коммита состояния, а не из индекса (проверка без слияния)')
    a = ap.parse_args()
    if a.from_revs:
        a.apply = False
    st = load_state(a.work)
    repo, base = st['repo'], st['commits']['base']
    conf = json.load(open(os.path.join(a.work, 'conflicts.json'), encoding='utf-8'))

    rows = []
    for p in conf['deleted_in_theirs']:
        b = show(repo, f'{base}:{p}')
        o = show(repo, f'{st["commits"]["ours"]}:{p}') if a.from_revs else show(repo, f':2:{p}')
        cls = 'trivial' if norm(p, b) == norm(p, o) else 'real'
        rows.append(('deleted_in_theirs', cls, p))
    for p in conf['deleted_in_ours']:
        b = show(repo, f'{base}:{p}')
        cls = 'trivial' if is_empty(p, b) else 'real'
        rows.append(('deleted_in_ours', cls, p))

    # сироты: наши новые файлы внутри объектов, которые поставщик удалил целиком
    vendor_deleted = names(repo, 'diff', '--name-only', '--no-renames', '--diff-filter=D', base, st['commits']['theirs'])
    deleted_objects = {object_root(p) for p in vendor_deleted if p.count('/') == 1 and p.endswith('.xml')}
    ours_added = names(repo, 'diff', '--name-only', '--diff-filter=A', base, st['commits']['ours'])
    orphans = [p for p in ours_added if object_root(p) in deleted_objects and p.count('/') > 1]

    out = os.path.join(a.work, 'md_classes.tsv')
    write_lines(out, [f'{k}\t{c}\t{p}' for k, c, p in rows] + [f'orphan\treal\t{p}' for p in orphans])
    cnt = collections.Counter((k, c) for k, c, _ in rows)
    for (k, c), n in sorted(cnt.items()):
        print(f'{k:18} {c:8} {n}')
    print(f'сироты (наши файлы в удалённых поставщиком объектах): {len(orphans)}')
    for k, c, p in rows:
        if c == 'real':
            print(f'  REAL {k}: {p}')
    for p in orphans:
        print(f'  ORPHAN: {p}')

    if not a.apply:
        print(f'\nСписок: {out}. Повтори с --apply, чтобы применить trivial.')
        return
    rm = [p for k, c, p in rows if k == 'deleted_in_theirs' and c == 'trivial']
    take = [p for k, c, p in rows if k == 'deleted_in_ours' and c == 'trivial']
    if rm:
        lst = os.path.join(a.work, 'md_rm.txt')
        write_lines(lst, rm)
        git(repo, 'rm', '-q', f'--pathspec-from-file={lst}')
    for p in take:
        git(repo, 'checkout', '--theirs', '--', p)
        git(repo, 'add', '--', p)
    print(f'Применено: принято удаление поставщика {len(rm)}, взята версия поставщика {len(take)}')


if __name__ == '__main__':
    main()
