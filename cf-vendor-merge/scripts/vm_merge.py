"""Запуск слияния нового релиза в ветку текущей конфигурации и инвентаризация конфликтов.

python vm_merge.py --work <рабочий каталог>

- переключает репозиторий на ветку ours (рабочий каталог должен быть чистым);
- git merge --no-commit --no-ff -X no-renames <theirs>  (без поиска переименований: в выгрузке 1С много похожих XML);
- пересоздаёт конфликты в стиле diff3 (с секцией base);
- пишет <work>/merge.log и <work>/conflicts.json: content / modify-delete (кем удалено) / add-add, по расширениям.
"""
import argparse
import collections
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _vm_common import git, load_state, save_state, unmerged, write_lines  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', required=True)
    a = ap.parse_args()
    st = load_state(a.work)
    repo = st['repo']

    if git(repo, 'status', '--porcelain', '--untracked-files=no').strip():
        sys.exit('В репозитории есть незакоммиченные изменения — закоммить или убери их перед слиянием')
    git(repo, 'switch', '-q', st['ours'])
    git(repo, 'config', 'merge.renameLimit', '0')
    git(repo, 'config', 'diff.renameLimit', '0')

    log_path = os.path.join(a.work, 'merge.log')
    print(f'git merge {st["theirs"]} -> {st["ours"]} ...', flush=True)
    with open(log_path, 'wb') as f:
        r = subprocess.run(['git', '-C', repo, '-c', 'core.quotepath=false', 'merge', '--no-commit', '--no-ff',
                            '-X', 'no-renames', st['theirs']], stdout=f, stderr=subprocess.STDOUT)
    log = open(log_path, encoding='utf-8', errors='replace').read()

    conf = {'content': [], 'add/add': [], 'deleted_in_theirs': [], 'deleted_in_ours': [], 'other': []}
    for m in re.finditer(r'^CONFLICT \(([^)]+)\): (.*)$', log, re.M):
        kind, rest = m.group(1), m.group(2)
        if kind == 'content':
            conf['content'].append(rest.replace('Merge conflict in ', ''))
        elif kind == 'add/add':
            conf['add/add'].append(rest.replace('Merge conflict in ', ''))
        elif kind == 'modify/delete':
            mm = re.match(r'(.+?) deleted in (\S+) and modified in (\S+)', rest)
            if mm:
                who = 'deleted_in_theirs' if mm.group(2) == st['theirs'] else 'deleted_in_ours'
                conf[who].append(mm.group(1))
            else:
                conf['other'].append(rest)
        else:
            conf['other'].append(f'{kind}: {rest}')

    um = unmerged(repo)
    content = [p for p in um if p in set(conf['content']) | set(conf['add/add'])]
    if content:
        lst = os.path.join(a.work, 'content_conflicts.txt')
        write_lines(lst, content)
        git(repo, 'checkout', '--conflict=diff3', f'--pathspec-from-file={lst}', check=False)

    with open(os.path.join(a.work, 'conflicts.json'), 'w', encoding='utf-8') as f:
        json.dump(conf, f, ensure_ascii=False, indent=1)
    st['merge'] = {'exit': r.returncode, 'unmerged': len(um)}
    save_state(a.work, st)

    print(f'exit {r.returncode}; неразрешённых файлов: {len(um)}')
    for k, v in conf.items():
        if v:
            ext = collections.Counter(os.path.splitext(p)[1] or p for p in v)
            print(f'  {k:18} {len(v):5}  {dict(ext)}')
    tops = collections.Counter(p.split('/')[0] for p in um)
    print('  по каталогам:', dict(tops.most_common(12)))


if __name__ == '__main__':
    main()
