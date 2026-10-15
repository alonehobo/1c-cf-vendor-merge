"""Проверки результата слияния (рабочий каталог репозитория).

python vm_check.py --work <рабочий каталог> <команда> [опции]

Команды:
  automerged   файлы, изменённые обеими сторонами и слитые git без конфликта: XML разбирается, уникальность id в
               формах, дубли uuid, баланс Процедура/Функция/#Область/#Если, дубли методов, маркеры конфликта;
  refs         ссылки на объекты верхнего уровня (cfg:XxxRef.Имя, Тип.Имя в xr:Ref/MDObjectRef/правах/составе),
               ссылки на реквизиты/ТЧ/измерения/ресурсы, согласованность состава Configuration.xml и файлов;
               подсказывает переименования поставщика по UUID (Удалить*, ...До2020);
  roles-clean  [--apply] права на переименованные поставщиком объекты и поля, сменившие вид
               (измерение/ресурс/реквизит), переименовать; на отсутствующие — убрать;
  ours-renamed наши объекты, переименованные в Удалить* при разборе add/add: где на них ссылались по имени
               (определяемые типы, типы реквизитов/измерений, подписки, планы обмена, права) и есть ли там Удалить*;
"""
import argparse
import collections
import os
import re
import sys

from lxml import etree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _vm_common import (git, load_state, names, changed_files, unmerged, read_text, write_lines, xml_serialize,  # noqa: E402
                        xml_remove, local, TYPE_DIRS, REF_TYPES, objects_on_disk, vendor_renames, uuid_map)

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUB_TYPES = {'Catalog', 'Document', 'InformationRegister', 'AccumulationRegister', 'AccountingRegister',
             'CalculationRegister', 'ChartOfCharacteristicTypes', 'ChartOfAccounts', 'ChartOfCalculationTypes',
             'BusinessProcess', 'Task', 'ExchangePlan', 'DataProcessor', 'Report', 'DocumentJournal'}


# ---------- automerged ----------

def cmd_automerged(st, a):
    repo, c = st['repo'], st['commits']
    both = sorted((changed_files(repo, c['base'], c['ours']) & changed_files(repo, c['base'], c['theirs']))
                  - set(unmerged(repo)))
    problems, cnt = [], collections.Counter()
    for p in both:
        fp = os.path.join(repo, p)
        if not os.path.exists(fp):
            cnt['deleted'] += 1
            continue
        data = open(fp, 'rb').read()
        if re.search(rb'^(<<<<<<<|>>>>>>>) ', data, re.M):
            problems.append((p, 'маркеры конфликта'))
            continue
        if p.endswith('.xml'):
            cnt['xml'] += 1
            try:
                root = etree.fromstring(data)
            except Exception as e:
                problems.append((p, f'XML не разбирается: {e}'))
                continue
            if p.endswith('Form.xml'):
                problems += [(p, m) for m in form_problems(root)]
            else:
                u = collections.Counter(e.get('uuid') for e in root.iter() if isinstance(e.tag, str) and e.get('uuid'))
                d = [k for k, v in u.items() if v > 1]
                if d:
                    problems.append((p, f'дубли uuid: {d[:3]}'))
        elif p.endswith('.bsl'):
            cnt['bsl'] += 1
            problems += [(p, m) for m in bsl_problems(data)]
    print(f'изменены обеими сторонами и слиты автоматически: {len(both)} {dict(cnt)}')
    print(f'проблем: {len(problems)}')
    for p, m in problems:
        print(f'{p}\t{m}')


def form_problems(root):
    out = []
    ids, nm = collections.Counter(), collections.Counter()
    for top in root:
        if not isinstance(top.tag, str):
            continue
        if local(top) in ('Attributes', 'Commands', 'Parameters', 'CommandInterface'):
            c2 = collections.Counter(e.get('id') for e in top if isinstance(e.tag, str) and e.get('id'))
            d = [k for k, v in c2.items() if v > 1]
            if d:
                out.append(f'дубли id в {local(top)}: {d[:5]}')
            continue
        for e in top.iter():
            if isinstance(e.tag, str) and e.get('id'):
                if not e.get('id').startswith('-'):
                    ids[e.get('id')] += 1
                if e.get('name'):
                    nm[e.get('name')] += 1
    d = [k for k, v in ids.items() if v > 1]
    if d:
        out.append(f'дубли id элементов: {d[:5]}')
    d = [k for k, v in nm.items() if v > 1]
    if d:
        out.append(f'дубли имён элементов: {d[:5]}')
    return out


def bsl_problems(data):
    t = data.decode('utf-8-sig', errors='replace')
    code = re.sub(r'//[^\n]*', '', t)
    code = re.sub(r'"[^"\n]*"', '""', code)
    out = []
    for nm, a, b in (('Процедура', r'^\s*(?:Асинх\s+)?Процедура\s', r'^\s*КонецПроцедуры'),
                     ('Функция', r'^\s*(?:Асинх\s+)?Функция\s', r'^\s*КонецФункции'),
                     ('#Область', r'^\s*#Область', r'^\s*#КонецОбласти'),
                     ('#Если', r'^\s*#Если', r'^\s*#КонецЕсли')):
        x, y = len(re.findall(a, code, re.M | re.I)), len(re.findall(b, code, re.M | re.I))
        if x != y:
            out.append(f'не сбалансировано {nm}: {x} / {y}')
    ms = re.findall(r'^\s*(?:Асинх\s+)?(?:Процедура|Функция)\s+([\wА-Яа-яЁё]+)\s*\(', code, re.M | re.I)
    d = [k for k, v in collections.Counter(m.lower() for m in ms).items() if v > 1]
    if d:
        out.append(f'дубли методов: {d[:5]}')
    return out


# ---------- refs ----------

class Structs:
    def __init__(self, cf):
        self.cf, self.cache = cf, {}

    def get(self, kind, name):
        k = (kind, name)
        if k not in self.cache:
            self.cache[k] = self._load(kind, name)
        return self.cache[k]

    def _load(self, kind, name):
        p = os.path.join(self.cf, TYPE_DIRS[kind], name + '.xml')
        if not os.path.exists(p):
            return None
        root = etree.parse(p).getroot()
        obj = root[0] if len(root) else root
        s = collections.defaultdict(set)
        s['TabularSection'] = {}
        co = obj.find('{*}ChildObjects')
        if co is not None:
            for e in co:
                if not isinstance(e.tag, str):
                    continue
                t = local(e)
                n = e.findtext('{*}Properties/{*}Name')
                if t == 'TabularSection':
                    tco = e.find('{*}ChildObjects')
                    s['TabularSection'][n] = {x.findtext('{*}Properties/{*}Name') for x in tco
                                              if isinstance(x.tag, str)} if tco is not None else set()
                else:
                    s[t].add(n)
        return s


def missing_refs(cf):
    objs = objects_on_disk(cf)
    structs = Structs(cf)
    kinds = '|'.join(sorted(TYPE_DIRS, key=len, reverse=True))
    rx_type = re.compile(r'cfg:([A-Za-z]+)\.([\wА-Яа-яЁё]+)')
    rx_top = re.compile(r'>-?(' + kinds + r')\.([\wА-Яа-яЁё]+)(?=[.<])')
    rx_sub = re.compile(r'(?<![\wА-Яа-яЁё])(' + '|'.join(SUB_TYPES) + r')\.([\wА-Яа-яЁё]+)\.'
                        r'(Attribute|Dimension|Resource|TabularSection|AddressingAttribute|AccountingFlag)\.'
                        r'([\wА-Яа-яЁё]+)(?:\.Attribute\.([\wА-Яа-яЁё]+))?')
    miss = collections.defaultdict(set)
    for root_dir, dirs, files in os.walk(cf):
        dirs[:] = [d for d in dirs if d != '.git']
        for f in files:
            if not f.endswith('.xml') or f in ('Template.xml', 'ConfigDumpInfo.xml'):
                continue
            fp = os.path.join(root_dir, f)
            p = os.path.relpath(fp, cf).replace('\\', '/')
            t = read_text(fp)
            for m in rx_type.finditer(t):
                k = REF_TYPES.get(m.group(1))
                if k and m.group(2) not in objs[k]:
                    miss[f'{k}.{m.group(2)}'].add(p)
            for m in rx_top.finditer(t):
                if m.group(2) not in objs[m.group(1)]:
                    miss[f'{m.group(1)}.{m.group(2)}'].add(p)
            for m in rx_sub.finditer(t):
                kind, name, sk, sn, sa = m.groups()
                s = structs.get(kind, name)
                if s is None:
                    continue  # отсутствие объекта уже поймано выше
                if sk == 'TabularSection':
                    if sn not in s['TabularSection'] or (sa and sa not in s['TabularSection'][sn]):
                        miss[m.group(0)].add(p)
                elif sn not in s[sk]:
                    miss[m.group(0)].add(p)
    return miss


def cmd_refs(st, a):
    repo, c = st['repo'], st['commits']
    miss = missing_refs(repo)
    ren_by_name = renames_by_name(repo, c)
    ren = ren_by_name
    cfg = etree.parse(os.path.join(repo, 'Configuration.xml')).getroot()
    co = cfg.find('.//{*}ChildObjects')
    listed = collections.defaultdict(set)
    for e in co:
        if isinstance(e.tag, str):
            listed[local(e)].add(e.text)
    objs = objects_on_disk(repo)
    no_file = [(k, n) for k, s in listed.items() if k in objs for n in s if n not in objs[k]]
    not_listed = [(k, n) for k, s in objs.items() for n in s if n not in listed.get(k, set())]
    print(f'переименований поставщика по UUID: {len(ren)}')
    print(f'ссылок на отсутствующее: {len(miss)}')
    rows = []
    for k, ps in sorted(miss.items()):
        top = '.'.join(k.split('.')[:2])
        hint = f' -> переименован в {ren_by_name[top]}' if top in ren_by_name else ''
        print(f'  {k}{hint}  <- {len(ps)}: {sorted(ps)[:3]}')
        rows += [f'{k}\t{p}\t{hint.strip()}' for p in sorted(ps)]
    write_lines(os.path.join(st['work'], 'missing_refs.tsv'), rows)
    print(f'в Configuration.xml без файла: {len(no_file)} {no_file[:20]}')
    print(f'файл есть, в Configuration.xml нет: {len(not_listed)} {not_listed[:20]}')


def renames_by_name(repo, c):
    """{'Kind.СтароеИмя': 'НовоеИмя'} — объекты, переименованные поставщиком с тем же UUID."""
    out = {}
    for old, new in vendor_renames(repo, c['base'], c['theirs']).items():
        d, n = old.split('/')
        kind = next((k for k, v in TYPE_DIRS.items() if v == d), d)
        out[f'{kind}.{n}'] = new.split('/')[1]
    return out


FIELD_KINDS = ('Dimension', 'Resource', 'Attribute')


def moved_name(nm, structs, ren_by_name, own_role, top_missing):
    """Новое имя права, если объект переименован поставщиком или поле сменило вид
    (измерение <-> ресурс <-> реквизит регистра); иначе None.
    В собственной роли заказчика права идут за объектом по UUID, как при обновлении
    конфигуратором, даже если поставщик создал новый объект со старым именем.
    В типовой роли (слита с поставщиком) — только когда старого имени больше нет."""
    parts = nm.split('.')
    if len(parts) < 2:
        return None
    top = '.'.join(parts[:2])
    if top in ren_by_name and (own_role or top_missing):
        parts[1] = ren_by_name[top]
    if len(parts) == 4 and parts[2] in FIELD_KINDS and parts[0] in TYPE_DIRS:
        s = structs.get(parts[0], parts[1])
        if s is not None and parts[3] not in s[parts[2]]:
            alt = [k for k in FIELD_KINDS if parts[3] in s[k]]
            if len(alt) == 1:
                parts[2] = alt[0]
    new = '.'.join(parts)
    return new if new != nm else None


def cmd_roles_clean(st, a):
    repo, c = st['repo'], st['commits']
    miss = missing_refs(repo)
    bad_full = set(miss)
    bad_top = {k for k in miss if k.count('.') == 1}
    structs = Structs(repo)
    ren_by_name = renames_by_name(repo, c)
    total = renamed = 0
    vendor_roles = {l.split('/')[-1] for r in (c['base'], c['theirs'])
                    for l in names(repo, 'ls-tree', '--name-only', r, 'Roles/')}
    for f in sorted(os.listdir(os.path.join(repo, 'Roles'))):
        p = os.path.join(repo, 'Roles', f, 'Ext', 'Rights.xml')
        if not os.path.exists(p):
            continue
        root = etree.fromstring(open(p, 'rb').read())
        rm, mv = [], []
        objs = [o for o in root if isinstance(o.tag, str) and local(o) == 'object']
        have = {o.findtext('{*}name') or '' for o in objs}
        for o in objs:
            nm = o.findtext('{*}name') or ''
            parts = nm.split('.')
            new = moved_name(nm, structs, ren_by_name, f not in vendor_roles,
                             '.'.join(parts[:2]) in bad_top)
            if new and new != nm:
                if new in have:
                    rm.append(o)
                else:
                    mv.append((o, new))
                    have.add(new)
            elif nm in bad_full or (len(parts) >= 2 and '.'.join(parts[:2]) in bad_top):
                rm.append(o)
        if mv:
            renamed += len(mv)
            print(f'{f}: переименовать {len(mv)}  {[(o.findtext("{*}name"), n) for o, n in mv[:3]]}')
            if a.apply:
                for o, n in mv:
                    o.find('{*}name').text = n
                if not rm:
                    with open(p, 'wb') as fh:
                        fh.write(xml_serialize(root))
        if rm:
            total += len(rm)
            print(f'{f}: {len(rm)}  {[o.findtext("{*}name") for o in rm[:3]]}')
            if a.apply:
                for o in rm:
                    xml_remove(o)
                with open(p, 'wb') as fh:
                    fh.write(xml_serialize(root))
    print(f'всего прав на отсутствующее: {total}, переименовано: {renamed}'
          + ('' if a.apply else ' (без --apply ничего не изменено)'))


# ---------- ours-renamed ----------

# Где ссылка по типу хранит данные: сужение типа удаляет записи/очищает значения при обновлении БД.
DATA_DIRS = ('DefinedTypes', 'InformationRegisters', 'AccumulationRegisters', 'AccountingRegisters',
             'CalculationRegisters', 'ChartsOfCharacteristicTypes', 'Catalogs', 'Documents', 'ExchangePlans',
             'Constants', 'CommonAttributes', 'BusinessProcesses', 'Tasks', 'ChartsOfAccounts',
             'ChartsOfCalculationTypes')


def disk_uuid_map(repo):
    """{uuid: 'Каталог/Имя'} для объектов верхнего уровня в рабочем каталоге."""
    rx = re.compile(r'<[A-Za-z]+ uuid="([0-9a-f-]+)"')
    m = {}
    for d in TYPE_DIRS.values():
        p = os.path.join(repo, d)
        if not os.path.isdir(p):
            continue
        for f in os.listdir(p):
            if f.endswith('.xml'):
                with open(os.path.join(p, f), 'rb') as fh:
                    u = rx.search(fh.read(4096).decode('utf-8', 'replace'))
                if u:
                    m[u.group(1)] = f'{d}/{f[:-4]}'
    return m


def cmd_ours_renamed(st, a):
    """Наши объекты, которые при разборе add/add переименованы в Удалить* (UUID сохранён, имя занял объект
    поставщика). Ссылки на них по имени (типы реквизитов, измерений, определяемые типы, подписки, состав планов
    обмена, права) теперь указывают на объект поставщика; объект Удалить* из них выпал."""
    repo, c = st['repo'], st['commits']
    ours, theirs, cur = uuid_map(repo, c['ours']), uuid_map(repo, c['theirs']), disk_uuid_map(repo)
    dir2kind = {v: k for k, v in TYPE_DIRS.items()}
    kind2refs = collections.defaultdict(list)
    for r, k in REF_TYPES.items():
        kind2refs[k].append(r)
    ren = [(ours[u], cur[u]) for u in ours if u in cur and cur[u] != ours[u] and theirs.get(u) != cur[u]]
    print(f'наших объектов, переименованных при слиянии: {len(ren)}')
    rows = []
    for old, new in sorted(ren):
        d, on = old.split('/')
        nn = new.split('/')[1]
        kind = dir2kind.get(d, d)
        alts = '|'.join([kind] + kind2refs.get(kind, []))
        rx_old = re.compile(rf'(?<![\wА-Яа-яЁё])(?:cfg:)?(?:{alts})\.{re.escape(on)}(?![\wА-Яа-яЁё])')
        files = [f for f in names(repo, 'grep', '-l', '-F', on, c['ours'], '--', '*.xml')]
        print(f'== {old} -> {nn}')
        for spec in files:
            rel = spec.split(':', 1)[1]
            if rel.startswith(f'{d}/{on}/') or rel == f'{d}/{on}.xml' or rel in ('Configuration.xml', 'ConfigDumpInfo.xml'):
                continue
            if not rx_old.search(git(repo, 'show', spec)):
                continue
            fp = os.path.join(repo, rel)
            now = read_text(fp) if os.path.exists(fp) else ''
            has_new = bool(re.search(rf'\.{re.escape(nn)}(?![\wА-Яа-яЁё])', now))
            data = rel.split('/')[0] in DATA_DIRS and not rel.split('/')[0] == 'Roles'
            mark = 'есть' if has_new else ('НЕТ — данные будут потеряны при обновлении БД' if data else 'нет')
            print(f'  {rel}: {nn} {mark}')
            rows.append(f'{old}\t{nn}\t{rel}\t{"есть" if has_new else "нет"}\t{"данные" if data else ""}')
    write_lines(os.path.join(st['work'], 'ours_renamed_refs.tsv'), rows)
    print('Решение по каждой ссылке: добавить тип Удалить* рядом с типом поставщика (данные сохраняются до переноса)'
          ' или перенести данные до обновления БД. Права и состав подсистем — по смыслу.')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', required=True)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('automerged')
    sub.add_parser('refs')
    sub.add_parser('ours-renamed')
    r = sub.add_parser('roles-clean')
    r.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    st = load_state(a.work)
    {'automerged': cmd_automerged, 'refs': cmd_refs, 'roles-clean': cmd_roles_clean, 'ours-renamed': cmd_ours_renamed}[a.cmd](st, a)


if __name__ == '__main__':
    main()
