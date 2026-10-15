"""План переноса доработок в конфликтных модулях BSL для параллельных агентов.

python vm_bsl_plan.py --work <рабочий каталог> [--groups 4]

Для каждого неразрешённого .bsl считает объём наших изменений (base->ours) и изменений поставщика (base->theirs),
делит файлы на группы с примерно равным объёмом наших изменений, пишет:
  <work>/bsl_instructions.md   — инструкция агенту (из references/bsl-merge-agent.md с подставленными путями);
  <work>/bsl_group_<N>.txt     — списки файлов;
и печатает готовые тексты заданий для агентов.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _vm_common import git, load_state, unmerged, write_lines  # noqa: E402

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def numstat(repo, a, b, p):
    out = git(repo, 'diff', '--numstat', '--no-renames', a, b, '--', p).strip()
    if not out:
        return 0
    x = out.split('\t')
    return sum(int(v) for v in x[:2] if v.isdigit())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', required=True)
    ap.add_argument('--groups', type=int, default=4)
    a = ap.parse_args()
    st = load_state(a.work)
    repo, c = st['repo'], st['commits']
    files = [p for p in unmerged(repo) if p.endswith('.bsl')]
    rows = sorted(((numstat(repo, c['base'], c['ours'], p), numstat(repo, c['base'], c['theirs'], p), p) for p in files),
                  reverse=True)
    groups = [[] for _ in range(max(1, min(a.groups, len(rows))))]
    load = [0] * len(groups)
    for ours, theirs, p in rows:
        i = load.index(min(load))
        groups[i].append((ours, theirs, p))
        load[i] += ours + theirs // 10

    tpl = open(os.path.join(SKILL_DIR, 'references', 'bsl-merge-agent.md'), encoding='utf-8').read()
    instr = (tpl.replace('{{REPO}}', repo).replace('{{BASE}}', c['base']).replace('{{OURS}}', c['ours'])
             .replace('{{THEIRS}}', c['theirs']))
    ipath = os.path.join(a.work, 'bsl_instructions.md')
    with open(ipath, 'w', encoding='utf-8') as f:
        f.write(instr)

    print(f'Модулей BSL с конфликтами: {len(rows)}')
    for i, g in enumerate(groups, 1):
        lst = os.path.join(a.work, f'bsl_group_{i}.txt')
        write_lines(lst, [p for _, _, p in g])
        print(f'\n--- группа {i}: {len(g)} файлов, наших строк {sum(x[0] for x in g)}')
        for ours, theirs, p in g:
            print(f'   ours {ours:5}  theirs {theirs:6}  {p}')
        print(f'Задание агенту: «Прочитай и строго выполни инструкцию `{ipath}` для файлов из списка `{lst}` '
              f'(пути относительно `{repo}`). Отчёт — по-русски.»')


if __name__ == '__main__':
    main()
