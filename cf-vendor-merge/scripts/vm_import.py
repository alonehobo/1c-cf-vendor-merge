"""Проверка результата загрузкой в новую пустую файловую базу через ibcmd.

python vm_import.py --work <рабочий каталог> --ibcmd <ibcmd.exe> [--db <каталог базы>] [--keep-db]

Платформа валидирует XML в два прохода:
  1) чтение файлов (XDTO): останавливается на ПЕРВОМ сломанном файле — чини и запускай снова;
  2) семантика: выдаёт полный список ошибок (неверные пути к данным, неизвестные объекты, команды).
Ошибки группируются и пишутся в <work>/import_errors.tsv; полный вывод — <work>/import.log.
Время одного прогона на ERP — 5–10 минут.
"""
import argparse
import collections
import os
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _vm_common import load_state, write_lines  # noqa: E402

PATTERNS = [
    ('xdto', re.compile(r'Исключение XDTO произошло при чтении файла- (\S+?)\. (.*)')),
    ('datapath', re.compile(r'Файл - (\S+?): Неверный путь к данным: "([^"]+)"')),
    ('unknown', re.compile(r'Неизвестный объект метаданных - (\S+)')),
    ('command', re.compile(r'Неверное имя команды элемента формы\. - (\S+)')),
    ('helplink', re.compile(r'Файл - (\S+?): Возможно неверная ссылка')),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', required=True)
    ap.add_argument('--ibcmd', required=True)
    ap.add_argument('--db', help='каталог проверочной базы (по умолчанию <work>/importcheck_db)')
    ap.add_argument('--keep-db', action='store_true')
    a = ap.parse_args()
    st = load_state(a.work)
    db = a.db or os.path.join(a.work, 'importcheck_db')
    if os.path.exists(db):
        shutil.rmtree(db)
    log_path = os.path.join(a.work, 'import.log')
    t0 = time.time()
    with open(log_path, 'wb') as f:
        r = subprocess.run([a.ibcmd, 'infobase', 'create', f'--db-path={db}', '--create-database',
                            f'--import={st["repo"]}', '--force'], stdout=f, stderr=subprocess.STDOUT)
    log = open(log_path, encoding='utf-8', errors='replace').read()
    print(f'exit {r.returncode}, {int(time.time() - t0) // 60} мин')
    rows, groups = [], collections.defaultdict(list)
    for line in log.splitlines():
        lvl = 'ERROR' if '[ERROR]' in line else 'WARN' if '[WARN]' in line else None
        if not lvl:
            continue
        for kind, rx in PATTERNS:
            m = rx.search(line)
            if m:
                groups[(lvl, kind)].append(m.groups())
                rows.append(f'{lvl}\t{kind}\t' + '\t'.join(m.groups()))
                break
        else:
            if 'завершен с ошибкой' not in line and 'критичные ошибки' not in line:
                groups[(lvl, 'other')].append((line.strip(),))
                rows.append(f'{lvl}\tother\t{line.strip()}')
    write_lines(os.path.join(a.work, 'import_errors.tsv'), rows)
    for (lvl, kind), items in sorted(groups.items()):
        print(f'{lvl:5} {kind:9} {len(items)}')
        for it in items[:8]:
            print('       ', ' | '.join(it))
    if r.returncode == 0:
        print('Импорт успешен: конфигурация загружается в платформу.')
    if not a.keep_db:
        shutil.rmtree(db, ignore_errors=True)
    sys.exit(0 if r.returncode == 0 else 1)


if __name__ == '__main__':
    main()
