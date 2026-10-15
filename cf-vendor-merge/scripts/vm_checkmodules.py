"""Синтаксический контроль модулей платформой (DESIGNER /CheckModules) на проверочной базе.

python vm_checkmodules.py --work <рабочий каталог> --ibcmd <ibcmd.exe> [--v8 <1cv8.exe>] [--db <каталог базы>]
                          [--cfe <каталог XML расширения>]... [--modes ThinClient,Server,ExternalConnection]

База — результат `vm_import.py --keep-db` (по умолчанию <work>/importcheck_db). Расширения загружаются в неё из XML
(`/LoadConfigFromFiles -Extension`) и проверяются вместе с конфигурацией. Рабочую базу скрипт не трогает.
Ошибки группируются по модулям: <work>/checkmodules.tsv (модуль, строка, текст, контексты, наш/расширение/типовой), полный вывод —
<work>/checkmodules.log. «наш» — модуль конфигурации добавлен или изменён относительно нового релиза; такие ошибки и ошибки
расширений — первыми. Одна ошибка в нескольких контекстах выводится один раз.
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
    ext_names = {os.path.basename(os.path.normpath(e)) for e in a.cfe}
    rows, by_mod, seen = [], collections.defaultdict(list), {}
    lines = text.splitlines()
    for k, line in enumerate(lines):
        m = ERR_RE.match(line.strip())
        if not m:
            continue
        mod = m.group('mod').strip()
        ext = ''
        if ' ' in mod and mod.split(' ', 1)[0] in ext_names:  # «<Расширение> ОбщийМодуль.Имя.Модуль»
            ext, mod = mod.split(' ', 1)
        nxt = lines[k + 1].strip() if k + 1 < len(lines) else ''
        ctx = re.search(r'\(Проверка: ([^)]+)\)\s*$', nxt)
        key = (ext, mod, m.group('line'), m.group('col'), m.group('text'))
        if key in seen:  # та же ошибка в другом контексте
            if ctx:
                seen[key].append(ctx.group(1))
            continue
        seen[key] = [ctx.group(1)] if ctx else []
        parts = mod.split('.')
        own = f'расширение {ext}' if ext else ('наш' if len(parts) > 1 and parts[1].lower() in ours else '')
        full = (ext + ' ' if ext else '') + mod
        by_mod[(own, full)].append((int(m.group('line')), m.group('text')))
        rows.append((own, full, m.group('line'), m.group('text'), key))
    rows = ['\t'.join((full, ln, txt, ', '.join(seen[key]), own)) for own, full, ln, txt, key in
            sorted(rows, key=lambda r: (r[0] == '', r[1], int(r[2])))]
    write_lines(os.path.join(a.work, 'checkmodules.tsv'), rows)
    total = sum(len(v) for v in by_mod.values())
    print(f'ошибок: {total} в {len(by_mod)} модулях')
    for (own, mod), items in sorted(by_mod.items(), key=lambda kv: (kv[0][0] == '', kv[0][1]))[:40]:
        print(f'  [{own or "типовой"}] {mod}: {len(items)}; {items[0][0]}: {items[0][1][:120]}')
    if total == 0 and rc != 0:
        print('Код возврата ненулевой, но ошибок в формате {Модуль(строка,колонка)} нет — смотри', log)
    sys.exit(0 if total == 0 and rc == 0 else 1)


if __name__ == '__main__':
    main()
