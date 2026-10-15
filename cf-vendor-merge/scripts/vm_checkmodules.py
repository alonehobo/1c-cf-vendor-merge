"""Синтаксический контроль модулей платформой (DESIGNER /CheckModules) на проверочной базе.

python vm_checkmodules.py --work <рабочий каталог> --ibcmd <ibcmd.exe> [--v8 <1cv8.exe>] [--db <каталог базы>]
                          [--cfe <каталог XML расширения>]... [--modes ThinClient,Server,ExternalConnection]

База — результат `vm_import.py --keep-db` (по умолчанию <work>/importcheck_db). Расширения загружаются в неё из XML
(`/LoadConfigFromFiles -Extension`) и проверяются вместе с конфигурацией. Рабочую базу скрипт не трогает.
Ошибки группируются по модулям: <work>/checkmodules.tsv (модуль, строка, текст, наш/типовой), полный вывод —
<work>/checkmodules.log. «наш» — модуль добавлен или изменён относительно нового релиза, такие ошибки — первыми.
"""
import argparse
import collections
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _vm_common import load_state, names, write_lines  # noqa: E402

ERR_RE = re.compile(r'^\{(?P<mod>[^}(]+)\((?P<line>\d+),(?P<col>\d+)\)\}:\s*(?P<text>.*)$')


def run_designer(v8, db, args, log):
    if os.path.exists(log):
        os.remove(log)
    cmd = [v8, 'DESIGNER', '/F', db, '/DisableStartupDialogs', '/DisableStartupMessages', *args, '/Out', log]
    r = subprocess.run(cmd)
    text = open(log, encoding='utf-8-sig', errors='replace').read() if os.path.exists(log) else ''
    if not text and os.path.exists(log):
        text = open(log, encoding='cp1251', errors='replace').read()
    return r.returncode, text


def module_key(path):
    """'Documents/X/Ext/ObjectModule.bsl' -> ключ для сопоставления с именем модуля в выводе платформы."""
    p = path.replace('\\', '/').split('/')
    return p[1].lower() if len(p) > 1 else path.lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', required=True)
    ap.add_argument('--ibcmd', required=True, help='ibcmd.exe; 1cv8.exe ищется рядом, если не задан --v8')
    ap.add_argument('--v8')
    ap.add_argument('--db')
    ap.add_argument('--cfe', action='append', default=[])
    ap.add_argument('--modes', default='ThinClient,Server,ExternalConnection')
    a = ap.parse_args()
    st = load_state(a.work)
    v8 = a.v8 or os.path.join(os.path.dirname(a.ibcmd), '1cv8.exe')
    db = a.db or os.path.join(a.work, 'importcheck_db')
    if not os.path.exists(os.path.join(db, '1Cv8.1CD')):
        sys.exit(f'Нет проверочной базы {db}: сначала vm_import.py --keep-db')
    t0 = time.time()
    for ext in a.cfe:
        name = os.path.basename(os.path.normpath(ext))
        rc, text = run_designer(v8, db, ['/LoadConfigFromFiles', ext, '-Extension', name],
                                os.path.join(a.work, f'checkmodules_load_{name}.log'))
        if rc != 0:
            print(f'Расширение {name} не загрузилось (код {rc}):\n{text.strip()[:2000]}')
            sys.exit(1)
        print(f'Расширение {name} загружено')
    flags = ['-' + m.strip() for m in a.modes.split(',') if m.strip()]
    if a.cfe:
        flags.append('-AllExtensions')
    log = os.path.join(a.work, 'checkmodules.log')
    rc, text = run_designer(v8, db, ['/CheckModules', *flags], log)
    print(f'CheckModules: код {rc}, {int(time.time() - t0) // 60} мин')

    repo, theirs = st['repo'], st['commits']['theirs']
    ours = {module_key(f) for f in names(repo, 'diff', '--name-only', '--no-renames', '--diff-filter=AM',
                                         theirs, '--', '*.bsl')}
    rows, by_mod = [], collections.defaultdict(list)
    for line in text.splitlines():
        m = ERR_RE.match(line.strip())
        if not m:
            continue
        mod = m.group('mod').strip()
        parts = mod.split('.')
        own = 'наш' if len(parts) > 1 and parts[1].lower() in ours else ''
        if a.cfe and not own and len(parts) > 1 and any(os.path.isdir(os.path.join(e, d, parts[1]))
                                                        for e in a.cfe for d in os.listdir(e)
                                                        if os.path.isdir(os.path.join(e, d))):
            own = 'расширение'
        by_mod[(own, mod)].append((int(m.group('line')), m.group('text')))
        rows.append(f'{mod}\t{m.group("line")}\t{m.group("text")}\t{own}')
    write_lines(os.path.join(a.work, 'checkmodules.tsv'), sorted(rows, key=lambda r: (r.split('\t')[3] == '', r)))
    total = sum(len(v) for v in by_mod.values())
    print(f'ошибок: {total} в {len(by_mod)} модулях')
    for (own, mod), items in sorted(by_mod.items(), key=lambda kv: (kv[0][0] == '', kv[0][1]))[:40]:
        print(f'  [{own or "типовой"}] {mod}: {len(items)}; {items[0][0]}: {items[0][1][:120]}')
    if total == 0 and rc != 0:
        print('Код возврата ненулевой, но ошибок в формате {Модуль(строка,колонка)} нет — смотри', log)
    sys.exit(0 if total == 0 and rc == 0 else 1)


if __name__ == '__main__':
    main()
