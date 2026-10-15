"""Подготовка веток для трёхстороннего слияния обновления.

python vm_branches.py --repo <git-репозиторий выгрузки> --work <рабочий каталог слияния>
    --old-xml <XML старого типового релиза> --new-xml <XML нового типового релиза>
    (--custom-xml <XML текущей конфигурации> | --custom-rev <ревизия с текущей конфигурацией>)
    [--root <коммит-родитель для старого релиза>] [--old-branch ..] [--new-branch ..] [--custom-branch ..]

Создаёт:
    <old-branch>    = старый типовой релиз (родитель --root или корневой коммит);
    <new-branch>    = новый типовой релиз (родитель <old-branch>);
    <custom-branch> = дерево текущей конфигурации с родителем <old-branch>.
Тогда merge-base(custom, new) = old, и git merge даёт настоящее трёхстороннее слияние.
Коммиты делаются через временный индекс и commit-tree — рабочий каталог репозитория не трогается.
Состояние (ветки, коммиты, версии) пишется в <work>/vendor-merge.json.
"""
import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _vm_common import git, config_version, save_state  # noqa: E402

TRAILER = os.environ.get('VM_COMMIT_TRAILER', '')


def commit_dir(repo, work, xml_dir, parent, msg, gitattributes):
    if gitattributes:
        shutil.copyfile(gitattributes, os.path.join(xml_dir, '.gitattributes'))
    idx = os.path.join(work, f'index-{os.getpid()}')
    if os.path.exists(idx):
        os.remove(idx)
    env = dict(os.environ, GIT_INDEX_FILE=idx)
    if parent:
        git(repo, f'--work-tree={xml_dir}', 'read-tree', parent, env=env)
    git(repo, f'--work-tree={xml_dir}', 'add', '-A', '.', env=env)
    tree = git(repo, 'write-tree', env=env).strip()
    os.remove(idx)
    args = ['commit-tree', tree] + (['-p', parent] if parent else [])
    return git(repo, *args, input=(msg + TRAILER).encode('utf-8')).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', required=True)
    ap.add_argument('--work', required=True)
    ap.add_argument('--old-xml', required=True)
    ap.add_argument('--new-xml', required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--custom-xml')
    g.add_argument('--custom-rev')
    ap.add_argument('--root', help='коммит-родитель для старого релиза (например, коммит с .gitattributes)')
    ap.add_argument('--old-branch')
    ap.add_argument('--new-branch')
    ap.add_argument('--custom-branch', default='custom')
    ap.add_argument('--gitattributes', help='файл .gitattributes, который положить в каждое дерево')
    a = ap.parse_args()

    os.makedirs(a.work, exist_ok=True)
    if not os.path.isdir(os.path.join(a.repo, '.git')):
        os.makedirs(a.repo, exist_ok=True)
        git(a.repo, 'init', '-q')
        print(f'Создан репозиторий {a.repo}')

    vo, vn = config_version(a.old_xml), config_version(a.new_xml)
    vc = config_version(a.custom_xml) if a.custom_xml else None
    print('старый релиз :', vo)
    print('новый релиз  :', vn)
    if vc:
        print('текущая      :', vc)
        if vc[1] != vo[1]:
            print(f'ВНИМАНИЕ: версия текущей конфигурации {vc[1]} не совпадает со старым релизом {vo[1]}')
    fmts = {v[3] for v in (vo, vn, vc) if v}
    if len(fmts) > 1:
        sys.exit(f'Разные версии формата выгрузки {fmts}: выгрузи всё одной версией платформы')

    old_b = a.old_branch or f'vendor-{vo[1]}'
    new_b = a.new_branch or f'vendor-{vn[1]}'
    c_old = commit_dir(a.repo, a.work, a.old_xml, a.root, f'chore(vendor): типовая {vo[0]} {vo[1]}', a.gitattributes)
    git(a.repo, 'branch', '-f', old_b, c_old)
    c_new = commit_dir(a.repo, a.work, a.new_xml, c_old, f'chore(vendor): типовая {vn[0]} {vn[1]}', a.gitattributes)
    git(a.repo, 'branch', '-f', new_b, c_new)
    if a.custom_xml:
        c_cust = commit_dir(a.repo, a.work, a.custom_xml, c_old, f'feat: текущая конфигурация на базе {vo[1]}', a.gitattributes)
    else:
        tree = git(a.repo, 'rev-parse', f'{a.custom_rev}^{{tree}}').strip()
        c_cust = git(a.repo, 'commit-tree', tree, '-p', c_old,
                     input=(f'feat: текущая конфигурация на базе {vo[1]} (дерево {a.custom_rev})' + TRAILER).encode('utf-8')).strip()
    git(a.repo, 'branch', '-f', a.custom_branch, c_cust)

    st = {'repo': os.path.abspath(a.repo), 'work': os.path.abspath(a.work),
          'base': old_b, 'theirs': new_b, 'ours': a.custom_branch,
          'commits': {'base': c_old, 'theirs': c_new, 'ours': c_cust},
          'versions': {'base': vo[1], 'theirs': vn[1], 'name': vn[0]}}
    save_state(a.work, st)
    for b in (old_b, new_b, a.custom_branch):
        print(b, git(a.repo, 'rev-parse', '--short', b).strip())
    print('ours  vs base:', git(a.repo, 'diff', '--shortstat', '--no-renames', c_old, c_cust).strip())
    print('theirs vs base:', git(a.repo, 'diff', '--shortstat', '--no-renames', c_old, c_new).strip())


if __name__ == '__main__':
    main()
