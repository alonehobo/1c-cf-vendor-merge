"""Структурное трёхстороннее слияние XML-выгрузки 1С для конфликтных файлов.

python vm_xmlmerge.py --work <рабочий каталог> [--files <список>] [--apply]

Берёт base/ours/theirs из индекса (:1:/:2:/:3:) и сливает деревья, сопоставляя элементы по uuid, атрибуту name,
дочернему <name> (роли), тексту (элементы списков: типы, состав подсистем, RegisterRecords...), иначе по тегу.
Правила (из реального обновления):
  - ours == base или ours == theirs -> theirs; theirs == base -> ours;
  - добавленное у нас вставляется после того же соседа, что и у нас;
  - удалённое у нас, но не изменённое поставщиком — удаляется; кроме шума: <StandardAttributes>, en-строки,
    объект Configuration.* в ролях — остаются как у поставщика;
  - служебные подэлементы форм (ExtendedTooltip, ContextMenu, ...*Addition, AutoCommandBar) сопоставляются по тегу,
    а не по имени (у заказчика они могут называться по-английски);
  - позиционные настройки (диаграммы, настройки СКД внутри форм) не сливаются поэлементно: при изменении с обеих
    сторон берётся поставщик, событие пишется в лог;
  - конфликт листа -> поставщик, событие в лог; ссылка характеристик «-1» у нас -> поставщик;
  - в Form.xml после слияния проверяется уникальность id (элементы/реквизиты/команды/параметры), дубли у добавленных
    нами элементов перенумеровываются.
Template.xml не обрабатывается (см. vm_templates.py).
Без --apply пишет результат в <work>/xmlmerge_out/ для просмотра; лог — <work>/xmlmerge.log.
"""
import argparse
import collections
import copy
import os
import sys

from lxml import etree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _vm_common import git, load_state, show, unmerged, read_list, write_lines, xml_serialize, local  # noqa: E402

NOISE_KEEP = {'StandardAttributes', 'StandardTabularSections'}
UNIQUE_SUB = {'ExtendedTooltip', 'ContextMenu', 'AutoCommandBar', 'SearchStringAddition', 'ViewStatusAddition',
              'SearchControlAddition'}
ATOMIC_NS = ('http://v8.1c.ru/8.2/data/chart', 'http://v8.1c.ru/8.1/data-composition-system/settings',
             'http://v8.1c.ru/8.1/data-composition-system/core')
LOG = []


def canon(e):
    if e is None:
        return None
    return (e.tag, tuple(sorted(e.attrib.items())), (e.text or '').strip(),
            tuple(canon(c) for c in e if isinstance(c.tag, str)))


def is_en(e):
    ln = local(e)
    if ln == 'item':
        return any(local(c) == 'lang' and (c.text or '') == 'en' for c in e)
    if ln == 'languageInfo':
        return any(local(c) == 'id' and (c.text or '') == 'en' for c in e)
    return False


def is_atomic(e):
    if not isinstance(e.tag, str):
        return False
    ns = etree.QName(e).namespace or ''
    if ns.startswith(ATOMIC_NS):
        return True
    return any(isinstance(c.tag, str) and (etree.QName(c).namespace or '').startswith(ATOMIC_NS) for c in e)


def keys_for(parents):
    repeated = set()
    for p in parents:
        if p is None:
            continue
        seen = set()
        for c in p:
            if isinstance(c.tag, str):
                if c.tag in seen:
                    repeated.add(c.tag)
                seen.add(c.tag)

    def key(c):
        if local(c) in UNIQUE_SUB:
            return (c.tag,)
        if 'uuid' in c.attrib:
            return (c.tag, 'uuid', c.attrib['uuid'])
        if 'name' in c.attrib:
            return (c.tag, 'name', c.attrib['name'])
        if c.tag not in repeated:
            return (c.tag,)
        kids = [k for k in c if isinstance(k.tag, str)]
        for k in kids:
            if local(k) == 'name' and k.text:
                return (c.tag, 'cname', k.text.strip())
        if not kids:
            return (c.tag, 'text', (c.text or '').strip(), tuple(sorted(c.attrib.items())))
        return (c.tag, 'first', kids[0].tag, (kids[0].text or '').strip())
    return key


def index(p, key):
    d, order, cnt = {}, [], {}
    if p is None:
        return d, order
    for c in p:
        if not isinstance(c.tag, str):
            continue
        k = key(c)
        n = cnt.get(k, 0)
        cnt[k] = n + 1
        k = k + (n,)
        d[k] = c
        order.append(k)
    return d, order


def where(e):
    parts = []
    while e is not None and isinstance(e.tag, str):
        n = local(e)
        if 'name' in e.attrib:
            n += f'[{e.attrib["name"]}]'
        else:
            pr = next((c for c in e if local(c) == 'Properties'), None)
            if pr is not None:
                nm = next((c.text for c in pr if local(c) == 'Name'), None)
                if nm:
                    n += f'[{nm}]'
            else:
                nm = next((c.text for c in e if local(c) == 'name'), None)
                if nm:
                    n += f'[{nm}]'
        parts.append(n)
        e = e.getparent()
    return '/'.join(reversed(parts))


def merge(b, o, t, path):
    cb, co, ct = canon(b), canon(o), canon(t)
    if co == ct or co == cb:
        return copy.deepcopy(t)
    if ct == cb:
        return copy.deepcopy(o)
    if is_atomic(t) or is_atomic(o):
        LOG.append(f'{path}\tATOMIC-THEIRS\t{where(t)}')
        return copy.deepcopy(t)
    okids = [c for c in o if isinstance(c.tag, str)]
    tkids = [c for c in t if isinstance(c.tag, str)]
    if not okids and not tkids:
        ot, tt = (o.text or '').strip(), (t.text or '').strip()
        if ot == '-1':
            return copy.deepcopy(t)
        bt = (b.text or '').strip() if b is not None else None
        LOG.append(f'{path}\tLEAF\t{where(t)}\tours={ot!r} theirs={tt!r} base={bt!r} -> theirs')
        return copy.deepcopy(t)
    res = copy.deepcopy(t)
    for c in list(res):
        res.remove(c)
    res.text = t.text
    key = keys_for([b, o, t])
    bd, _ = index(b, key)
    od, oorder = index(o, key)
    td, torder = index(t, key)
    result = []
    for k in torder:
        tc = td[k]
        if k in od:
            result.append((k, merge(bd.get(k), od[k], tc, path)))
        elif k in bd:
            noise = is_en(tc) or local(tc) in NOISE_KEEP or (
                local(tc) == 'object' and (tc.findtext('{*}name') or '').startswith('Configuration.'))
            if noise:
                result.append((k, copy.deepcopy(tc)))
            elif canon(tc) == canon(bd[k]):
                LOG.append(f'{path}\tOURS-DELETED\t{where(tc)}')
            else:
                LOG.append(f'{path}\tCONFLICT ours-deleted/theirs-modified (kept theirs)\t{where(tc)}')
                result.append((k, copy.deepcopy(tc)))
        else:
            result.append((k, copy.deepcopy(tc)))
    for i, k in enumerate(oorder):
        if k in td:
            continue
        oc = od[k]
        if k in bd:
            if canon(oc) != canon(bd[k]):
                LOG.append(f'{path}\tCONFLICT theirs-deleted/ours-modified (dropped)\t{where(oc)}')
            continue
        pos = 0
        for j in range(i - 1, -1, -1):
            idx = next((n for n, (kk, _) in enumerate(result) if kk == oorder[j]), None)
            if idx is not None:
                pos = idx + 1
                break
        result.insert(pos, (k, copy.deepcopy(oc)))
        LOG.append(f'{path}\tOURS-ADDED\t{where(oc)}')
    inner = tkids[0].tail if tkids else (okids[0].tail if okids else None)
    last = tkids[-1].tail if tkids else (okids[-1].tail if okids else None)
    for n, (_, e) in enumerate(result):
        e.tail = inner if n < len(result) - 1 else last
        res.append(e)
    return res


SECTIONS = {'Attributes': 'attr', 'Commands': 'cmd', 'Parameters': 'par', 'CommandInterface': None}


def collect(root, space):
    out = []
    for top in root:
        if not isinstance(top.tag, str):
            continue
        sp = SECTIONS.get(local(top), 'items')
        if sp != space:
            continue
        if space == 'items':
            out += [e for e in top.iter() if isinstance(e.tag, str) and e.get('id') is not None]
        else:
            out += [e for e in top if isinstance(e.tag, str) and e.get('id') is not None]
    return out


def fix_form_ids(res, theirs, path):
    for space in ('items', 'attr', 'cmd', 'par'):
        els = collect(res, space)
        known = {(e.get('id'), e.get('name')) for e in collect(theirs, space)}
        seen, dups = {}, []
        for e in els:
            i = e.get('id')
            if i.startswith('-'):
                continue
            if i in seen:
                a, b = seen[i], e
                victim = b if (b.get('id'), b.get('name')) not in known else a
                dups.append(victim)
                if victim is a:
                    seen[i] = b
            else:
                seen[i] = e
        if dups:
            mx = max(int(x.get('id')) for x in els if x.get('id').lstrip('-').isdigit())
            for v in dups:
                mx += 1
                LOG.append(f'{path}\tFORM-ID\t{space}: {v.get("name")} id {v.get("id")} -> {mx}')
                v.set('id', str(mx))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', required=True)
    ap.add_argument('--files', help='список путей (по умолчанию — все неразрешённые *.xml, кроме Template.xml)')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--from-revs', action='store_true', help='брать base/ours/theirs из коммитов состояния, а не из индекса (проверка без слияния; только в xmlmerge_out)')
    a = ap.parse_args()
    st = load_state(a.work)
    repo = st['repo']
    if a.from_revs:
        a.apply = False
        specs = [st['commits'][k] + ':' for k in ('base', 'ours', 'theirs')]
    else:
        specs = [':1:', ':2:', ':3:']
    files = read_list(a.files) if a.files else [p for p in unmerged(repo)
                                                  if p.endswith('.xml') and not p.endswith('Template.xml')]
    parser = etree.XMLParser(remove_blank_text=False, strip_cdata=False, resolve_entities=False)
    out_dir = os.path.join(a.work, 'xmlmerge_out')
    done, skipped = [], []
    for path in files:
        b, o, t = (show(repo, s + path) for s in specs)
        if o is None or t is None:
            skipped.append(path)
            continue
        rb = etree.fromstring(b, parser) if b else None
        ro, rt = etree.fromstring(o, parser), etree.fromstring(t, parser)
        n0 = len(LOG)
        res = merge(rb, ro, rt, path)
        if path.endswith('Form.xml'):
            fix_form_ids(res, rt, path)
        data = xml_serialize(res)
        dst = os.path.join(repo if a.apply else out_dir, path)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, 'wb') as f:
            f.write(data)
        done.append(path)
        c = sum(1 for l in LOG[n0:] if '\tCONFLICT' in l or '\tLEAF' in l or '\tATOMIC' in l)
        if c:
            print(f'{c:3} спорных  {path}')
    write_lines(os.path.join(a.work, 'xmlmerge.log'), LOG)
    kinds = collections.Counter(l.split('\t')[1] for l in LOG)
    print(f'слито файлов: {len(done)}, пропущено (нет ours/theirs): {len(skipped)}')
    print('события:', dict(kinds))
    for p in skipped:
        print('  SKIP', p)
    if a.apply and done:
        lst = os.path.join(a.work, 'xmlmerge_done.txt')
        write_lines(lst, done)
        git(repo, 'add', f'--pathspec-from-file={lst}')
        print('Файлы записаны и добавлены в индекс.')
    elif done:
        print(f'Результат для просмотра: {out_dir}. Проверь лог, затем повтори с --apply.')


if __name__ == '__main__':
    main()
