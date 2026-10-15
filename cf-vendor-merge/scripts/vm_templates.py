"""Разрешение конфликтов в макетах (Template.xml, MXL). Поэлементно макеты не сливаются:
форматы ячеек ссылаются на позиции в списке форматов, объединения — на индексы строк/колонок.

python vm_templates.py --work <рабочий каталог> [--apply]

Правила (сравнение после нормализации шума, см. _vm_noise.py):
  у поставщика изменились только en-тексты/шум   -> берём наш макет (типичный случай: доработанная печатная форма);
  у нас изменился только шум                      -> берём макет поставщика;
  изменили обе стороны по существу               -> в отчёт: нужно решить вручную (обычно — наш макет и
                                                     перенос новых параметров поставщика через edit_template).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _vm_common import git, load_state, show, unmerged  # noqa: E402
from _vm_noise import norm_xml  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', required=True)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    st = load_state(a.work)
    repo = st['repo']
    files = [p for p in unmerged(repo) if p.endswith('Template.xml')]
    plan = []
    for p in files:
        b, o, t = (show(repo, f':{n}:{p}') for n in (1, 2, 3))
        if o is None or t is None:
            plan.append((p, 'manual', 'нет одной из сторон'))
            continue
        nb, no, nt = norm_xml(b), norm_xml(o), norm_xml(t)
        if nt == nb:
            plan.append((p, 'ours', 'поставщик менял только en/шум'))
        elif no == nb:
            plan.append((p, 'theirs', 'у нас только шум'))
        elif no == nt:
            plan.append((p, 'theirs', 'изменения совпадают'))
        else:
            plan.append((p, 'manual', 'изменены обеими сторонами'))
    for p, act, why in plan:
        print(f'{act:7} {p}  ({why})')
    if not a.apply:
        return
    for p, act, _ in plan:
        if act in ('ours', 'theirs'):
            git(repo, 'checkout', f'--{act}', '--', p)
            git(repo, 'add', '--', p)
    print('Применено (кроме manual).')


if __name__ == '__main__':
    main()
