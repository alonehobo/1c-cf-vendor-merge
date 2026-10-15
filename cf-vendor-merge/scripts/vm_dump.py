"""Выгрузка релиза конфигурации (CF или DT) в XML через временную файловую базу.

python vm_dump.py --ibcmd <ibcmd.exe> --src <file.cf|file.dt> --out <каталог XML> [--tmp <каталог временных баз>] [--keep-db]

CF загружается через `infobase create --load`, DT — через `--restore`. Затем `infobase config export`.
В конце печатает имя, версию и версию формата выгрузки — сверяй их у всех выгрузок одного слияния.
"""
import argparse
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _vm_common import config_version  # noqa: E402


def run(cmd):
    print('>', ' '.join(f'"{c}"' if ' ' in c else c for c in cmd), flush=True)
    t0 = time.time()
    r = subprocess.run(cmd)
    print(f'  exit {r.returncode}, {int(time.time() - t0)} с', flush=True)
    if r.returncode != 0:
        sys.exit(f'Команда завершилась с кодом {r.returncode}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ibcmd', required=True)
    ap.add_argument('--src', required=True, help='файл .cf или .dt релиза')
    ap.add_argument('--out', required=True, help='каталог для XML (должен быть пустым или отсутствовать)')
    ap.add_argument('--tmp', help='каталог для временной базы (по умолчанию рядом с --out)')
    ap.add_argument('--keep-db', action='store_true', help='не удалять временную базу (пригодится для сравнения в конфигураторе)')
    a = ap.parse_args()

    if not os.path.isfile(a.src):
        sys.exit(f'Нет файла {a.src}')
    if os.path.isdir(a.out) and os.listdir(a.out):
        sys.exit(f'Каталог {a.out} не пуст')
    ext = os.path.splitext(a.src)[1].lower()
    if ext not in ('.cf', '.dt'):
        sys.exit('Ожидается .cf или .dt (файл .cfu — обновление, не полная конфигурация)')
    tmp = a.tmp or os.path.dirname(os.path.abspath(a.out))
    db = os.path.join(tmp, os.path.basename(os.path.normpath(a.out)) + '_db')
    if os.path.exists(db):
        sys.exit(f'Временная база {db} уже существует — удали её или укажи другой --tmp')

    load = f'--load={a.src}' if ext == '.cf' else f'--restore={a.src}'
    run([a.ibcmd, 'infobase', 'create', f'--db-path={db}', '--create-database', load, '--force'])
    run([a.ibcmd, 'infobase', 'config', 'export', f'--db-path={db}', a.out])

    name, ver, vendor, fmt = config_version(a.out)
    files = sum(len(f) for _, _, f in os.walk(a.out))
    print(f'Конфигурация: {name}, версия {ver}, поставщик {vendor}, формат выгрузки {fmt}, файлов {files}')
    if not a.keep_db:
        shutil.rmtree(db, ignore_errors=True)
    else:
        print(f'Временная база сохранена: {db}')


if __name__ == '__main__':
    main()
