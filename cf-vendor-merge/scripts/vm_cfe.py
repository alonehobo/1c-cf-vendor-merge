"""Совместимость расширений с обновлённой конфигурацией (рабочий каталог репозитория = результат).

python vm_cfe.py --work <рабочий каталог> --cfe <каталог XML расширения> [--cfe ...]

Проверяет:
  adopted     заимствованные объекты и подобъекты: есть ли в результате объект с тем же UUID и именем
              (переименование поставщиком в Удалить*/...БУ, удаление); заимствованные формы и объекты
              без ExtendedConfigurationObject (--fix-eco / --fix-eco-all вписывает UUID из конфигурации);
  intercept   &Перед/&После/&Вместо/&ИзменениеИКонтроль: есть ли метод, совпадает ли число параметров;
              для &ИзменениеИКонтроль — совпадает ли восстановленный оригинал с методом результата
              (иначе платформа не подключит расширение: «метод изменён»), с diff поставщика;
              для &Вместо без ПродолжитьВызов — изменил ли поставщик оригинал (его изменения потеряются);
  calls       вызовы ОбщийМодуль.Метод( конфигурации: метод есть, экспортный, число аргументов подходит;
              помечает, что сломало именно обновление;
  baseforms   заимствованные формы, у которых изменилась форма конфигурации (базовую форму нужно обновить).
Результат: <work>/cfe_compat.tsv и сводка в консоли.
"""
import argparse
import difflib
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _vm_common import load_state, show, write_lines  # noqa: E402

NS = '{http://v8.1c.ru/8.3/MDClasses}'
ANN_RE = re.compile(r'^\s*&(Перед|После|Вместо|ИзменениеИКонтроль)\s*\(\s*"([^"]+)"\s*\)', re.I)
DECL_RE = re.compile(r'^\s*(Асинх\s+)?(Процедура|Функция)\s+([A-Za-zА-Яа-яЁё_0-9]+)\s*\(', re.I)
END_RE = re.compile(r'^\s*(КонецПроцедуры|КонецФункции)\b', re.I)
CALL_RE = re.compile(r'(?<![\w.])([A-Za-zА-Яа-яЁё_]\w*)\.([A-Za-zА-Яа-яЁё_]\w*)\s*\(')


def strip_comment(line):
    instr = False
    for i, ch in enumerate(line):
        if ch == '"':
            instr = not instr
        if not instr and line[i:i + 2] == '//':
            return line[:i]
    return line


def split_params(s):
    depth, cur, out, instr = 0, '', [], False
    for ch in s:
        if ch == '"':
            instr = not instr
        if not instr:
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
            elif ch == ',' and depth == 0:
                out.append(cur.strip())
                cur = ''
                continue
        cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def methods(text):
    res = {}
    if text is None:
        return res
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = DECL_RE.match(lines[i])
        if not m:
            i += 1
            continue
        hdr, j, depth, buf, done = strip_comment(lines[i])[m.end():], i, 1, '', False
        while True:
            for ch in hdr:
                if ch == '(':
                    depth += 1
                elif ch == ')':
                    depth -= 1
                    if depth == 0:
                        done = True
                        break
                buf += ch
            if done or j + 1 >= len(lines):
                break
            j += 1
            buf += ' '
            hdr = strip_comment(lines[j])
        rest = strip_comment(lines[j])
        nxt = strip_comment(lines[j + 1]) if j + 1 < len(lines) else ''
        export = bool(re.search(r'\)\s*Экспорт', rest, re.I) or re.match(r'^\s*Экспорт', nxt, re.I))
        params = split_params(buf)
        k = j
        while k < len(lines) and not END_RE.match(lines[k]):
            k += 1
        res[m.group(3).lower()] = dict(name=m.group(3), params=params, lines=lines[i:k + 1], export=export,
                                       required=sum(1 for p in params if '=' not in p))
        i = k + 1
    return res


def reconstruct(lines):
    out, mode = [], None
    for l in lines:
        s = l.strip()
        if re.match(r'^#Вставка\b', s, re.I):
            mode = 'ins'
        elif re.match(r'^#КонецВставки\b', s, re.I):
            mode = None
        elif re.match(r'^#Удаление\b', s, re.I):
            mode = 'del'
        elif re.match(r'^#КонецУдаления\b', s, re.I):
            mode = None
        elif mode != 'ins':
            out.append(l)
    return out


def norm_body(lines, ws=True):
    res = []
    for idx, l in enumerate(lines):
        if idx == 0:
            l = DECL_RE.sub(lambda m: m.group(2) + ' X(', l)
        l = re.sub(r'\s+', '', l) if ws else l.rstrip()
        if l or not ws:
            res.append(l)
    return res


class Conf:
    def __init__(self, st):
        self.repo, c = st['repo'], st['commits']
        self.revs = {'vold': c['base'], 'old': c['ours'], 'vnew': c['theirs']}
        self.cache = {}

    def text(self, rev, rel):
        rel = rel.replace('\\', '/')
        if rev == 'head':
            p = os.path.join(self.repo, rel)
            return open(p, encoding='utf-8-sig', errors='replace').read() if os.path.exists(p) else None
        b = show(self.repo, f'{self.revs[rev]}:{rel}')
        return b.decode('utf-8-sig', errors='replace') if b is not None else None

    def methods(self, rev, rel):
        k = (rev, rel)
        if k not in self.cache:
            self.cache[k] = methods(self.text(rev, rel))
        return self.cache[k]

    def common_modules(self, rev):
        if rev == 'head':
            base = os.path.join(self.repo, 'CommonModules')
            out = {}
            for m in os.listdir(base):
                f = os.path.join(base, m, 'Ext', 'Module.bsl')
                if os.path.exists(f):
                    out[m.lower()] = methods(open(f, encoding='utf-8-sig', errors='replace').read())
            return out
        r = subprocess.run(['git', '-C', self.repo, '-c', 'core.quotepath=false', 'ls-tree', '-r',
                            '--format=%(objectname) %(path)', self.revs[rev], 'CommonModules'], capture_output=True)
        lst = [l.split(' ', 1) for l in r.stdout.decode('utf-8').splitlines() if l.endswith('/Ext/Module.bsl')]
        p = subprocess.Popen(['git', '-C', self.repo, 'cat-file', '--batch'], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        out = {}
        for oid, path in lst:
            p.stdin.write((oid + '\n').encode())
            p.stdin.flush()
            size = int(p.stdout.readline().split()[2])
            data = p.stdout.read(size)
            p.stdout.read(1)
            out[path.split('/')[1].lower()] = methods(data.decode('utf-8-sig', errors='replace'))
        p.stdin.close()
        p.wait()
        return out


def walk(root, chain=()):
    for e in root:
        t = e.tag.split('}')[-1]
        props = e.find(NS + 'Properties')
        if props is not None:
            n = props.find(NS + 'Name')
            ob = props.find(NS + 'ObjectBelonging')
            eco = props.find(NS + 'ExtendedConfigurationObject')
            yield t, (n.text if n is not None else None), e.get('uuid'), (eco.text if eco is not None else None), \
                chain, (ob.text if ob is not None else 'Own')
        co = e.find(NS + 'ChildObjects')
        if co is not None:
            yield from walk(co, chain + ((t, n.text if props is not None and n is not None else None),))


def check_adopted(conf, ext, base, out, fix=None, fix_all=False):
    n = 0
    idx_cache = {}
    for dp, _, fn in os.walk(base):
        if os.sep + 'Ext' + os.sep in dp[len(base):] + os.sep:
            continue
        for f in fn:
            if not f.endswith('.xml') or f in ('ConfigDumpInfo.xml', 'Configuration.xml'):
                continue
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, base)
            try:
                items = [x for x in walk(ET.parse(p).getroot()) if x[5] == 'Adopted']
            except ET.ParseError:
                continue
            if not items:
                continue
            if rel not in idx_cache:
                hp = os.path.join(conf.repo, rel)
                if os.path.exists(hp):
                    allh = list(walk(ET.parse(hp).getroot()))
                    idx_cache[rel] = ({u: (t, nm) for t, nm, u, _, _, _ in allh if u},
                                      {(t, tuple(c[1] for c in ch[1:]), nm) for t, nm, _, _, ch, _ in allh},
                                      allh[0][2] if allh else None)
                else:
                    idx_cache[rel] = None
            idx = idx_cache[rel]
            byuuid, bykey, root_uuid = idx if idx else (None, None, None)
            for t, name, u, eco, chain, _ in items:
                n += 1
                where = '.'.join([c[1] or '' for c in chain] + [name or ''])
                if byuuid is None:
                    out.append((ext, rel, f'{t} {where}', 'объекта нет в конфигурации (удалён/переименован поставщиком)',
                                'снять заимствование и ссылки на объект'))
                elif eco and eco not in byuuid:
                    out.append((ext, rel, f'{t} {where}', f'UUID {eco} не найден (удалён поставщиком)',
                                'снять заимствование, убрать из форм/кода расширения'))
                elif not eco and len(chain) > 0 and (t, tuple(c[1] for c in chain[1:]), name) not in bykey:
                    alt = next((p2 + name for p2 in ('Удалить',) if (t, tuple(c[1] for c in chain[1:]), p2 + name) in bykey), None)
                    out.append((ext, rel, f'{t} {where}', f'переименован: {name} -> {alt}' if alt else 'не найден в конфигурации по имени (удалён/переименован)',
                                'снять заимствование, убрать из форм/кода расширения'))
                elif eco and byuuid[eco][1] != name:
                    out.append((ext, rel, f'{t} {where}', f'переименован: {name} -> {byuuid[eco][1]}',
                                'перезаимствовать под новым именем, поправить формы/код'))
                elif not eco and not chain and root_uuid:
                    sev = 'форма: при загрузке из XML привязка к форме конфигурации молча теряется' if t in ('Form', 'CommonForm') \
                        else 'привязка к объекту конфигурации только по имени'
                    out.append((ext, rel, f'{t} {where}', f'нет ExtendedConfigurationObject ({sev}); UUID в конфигурации {root_uuid}',
                                'восстановить ссылку: vm_cfe.py --fix-eco'))
                    if fix is not None and (t in ('Form', 'CommonForm') or fix_all):
                        fix.append((p, root_uuid))
    return n


def add_eco(path, uuid):
    """Вставляет <ExtendedConfigurationObject> в первый <Properties> после Comment (или Name), байт-точно."""
    with open(path, 'rb') as f:
        data = f.read()
    end = data.find(b'</Properties>')
    m = None
    for tag in (rb'<Comment/>', rb'</Comment>', rb'</Name>'):
        m = re.search(rb'(?m)^([ \t]*)[^\n]*' + tag + rb'(\r?\n)', data[:end])
        if m:
            break
    if not m or b'<ExtendedConfigurationObject>' in data[:end]:
        return False
    ins = m.group(1) + b'<ExtendedConfigurationObject>' + uuid.encode() + b'</ExtendedConfigurationObject>' + m.group(2)
    with open(path, 'wb') as f:
        f.write(data[:m.end()] + ins + data[m.end():])
    return True


def check_intercepts(conf, ext, base, out):
    cnt = {'Перед': 0, 'После': 0, 'Вместо': 0, 'ИзменениеИКонтроль': 0}
    for dp, _, fn in os.walk(base):
        for f in fn:
            if not f.endswith('.bsl'):
                continue
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, base)
            text = open(p, encoding='utf-8-sig', errors='replace').read()
            lines = text.splitlines()
            ints = []
            for i, l in enumerate(lines):
                m = ANN_RE.match(l)
                if m:
                    j = i + 1
                    while j < len(lines) and not DECL_RE.match(lines[j]):
                        j += 1
                    ints.append((m.group(1), m.group(2), j))
            if not ints:
                continue
            extm = methods(text)
            head = conf.methods('head', rel)
            for kind, target, decl in ints:
                cnt[kind] += 1
                dm = DECL_RE.match(lines[decl]) if decl < len(lines) else None
                im = extm.get(dm.group(3).lower()) if dm else None
                obj = f'&{kind}("{target}")'
                hm = head.get(target.lower())
                if not head and conf.text('head', rel) is None:
                    out.append((ext, rel, obj, 'модуля нет в конфигурации', 'удалить перехватчик или перенести'))
                    continue
                if hm is None:
                    was = 'был' if conf.methods('old', rel).get(target.lower()) else 'не было и до обновления'
                    moved = find_method(conf, target) if was == 'был' else []
                    out.append((ext, rel, obj, f'метода нет в модуле конфигурации ({was})',
                                ('перенесён поставщиком в: ' + ', '.join(moved)) if moved else ''))
                    continue
                if im and len(im['params']) != len(hm['params']):
                    out.append((ext, rel, obj, f'параметры: перехватчик {len(im["params"])}, конфигурация {len(hm["params"])} '
                                f'({", ".join(hm["params"])})', 'привести объявление к конфигурации'))
                if kind == 'ИзменениеИКонтроль' and im:
                    if norm_body(reconstruct(im['lines'])) != norm_body(hm['lines']):
                        o = conf.methods('vold', rel).get(target.lower())
                        nw = conf.methods('vnew', rel).get(target.lower())
                        d = ''
                        if o and nw:
                            d = ' | '.join(l for l in difflib.unified_diff(norm_body(o['lines'], False), norm_body(nw['lines'], False),
                                                                           lineterm='', n=0) if l[:1] in '+-' and l[:3] not in ('+++', '---'))[:400]
                        out.append((ext, rel, obj, 'метод изменён в конфигурации — расширение не подключится',
                                    'актуализировать перехватчик по тексту конфигурации (cfe-patch-method); поставщик: ' + d))
                if kind == 'Вместо' and im and not any('ПродолжитьВызов' in l for l in im['lines']):
                    o = conf.methods('vold', rel).get(target.lower())
                    nw = conf.methods('vnew', rel).get(target.lower())
                    if o and nw and norm_body(o['lines']) != norm_body(nw['lines']):
                        out.append((ext, rel, obj, 'предупреждение: поставщик изменил метод, а &Вместо без ПродолжитьВызов',
                                    'перенести изменения поставщика в тело перехватчика'))
    return cnt


def find_method(conf, name):
    r = subprocess.run(['git', '-C', conf.repo, '-c', 'core.quotepath=false', 'grep', '-l', '-E', '-i',
                        rf'^\s*(Процедура|Функция)\s+{re.escape(name)}\s*\(', conf.revs['vnew'], '--', '*.bsl'],
                       capture_output=True, text=True, encoding='utf-8')
    return [l.split(':', 1)[1] for l in r.stdout.splitlines()][:3]


def count_args(text, pos):
    depth, n, nonempty, instr, i = 0, 0, False, False, pos
    while i < len(text):
        ch = text[i]
        if instr:
            if ch == '"':
                instr = False
        elif ch == '"':
            instr = nonempty = True
        elif ch == '(':
            depth += 1
            nonempty = nonempty or depth > 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                return n + (1 if nonempty or n > 0 else 0)
        elif ch == ',' and depth == 1:
            n += 1
        elif not ch.isspace() and depth >= 1:
            nonempty = True
        i += 1
    return -1


def check_calls(conf, ext, base, out, mods):
    old, head = mods
    own = {}
    cm = os.path.join(base, 'CommonModules')
    if os.path.isdir(cm):
        for m in os.listdir(cm):
            f = os.path.join(cm, m, 'Ext', 'Module.bsl')
            if os.path.exists(f):
                own[m.lower()] = methods(open(f, encoding='utf-8-sig', errors='replace').read())
    n = 0
    for dp, _, fn in os.walk(base):
        for f in fn:
            if not f.endswith('.bsl'):
                continue
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, base)
            clean = '\n'.join(strip_comment(l) for l in open(p, encoding='utf-8-sig', errors='replace').read().splitlines())
            for m in CALL_RE.finditer(clean):
                mod, meth = m.group(1).lower(), m.group(2).lower()
                if mod not in head and mod not in old:
                    continue
                if meth in own.get(mod, {}):
                    continue
                n += 1
                line = clean.count('\n', 0, m.start()) + 1
                hm = head.get(mod, {}).get(meth)
                om = old.get(mod, {}).get(meth)
                new = ' — сломано обновлением' if om and om['export'] else ' (и до обновления)'
                call = f'{m.group(1)}.{m.group(2)}'
                if hm is None:
                    if mod in head or om:
                        out.append((ext, f'{rel}:{line}', call, 'метода нет' + new, ''))
                    continue
                if not hm['export']:
                    out.append((ext, f'{rel}:{line}', call, 'метод не экспортный' + new, ''))
                    continue
                na = count_args(clean, m.end() - 1)
                if na >= 0 and (na < hm['required'] or na > len(hm['params'])):
                    out.append((ext, f'{rel}:{line}', call,
                                f'аргументов {na}, нужно {hm["required"]}..{len(hm["params"])}'
                                + (new if om and (om['required'] != hm['required'] or len(om['params']) != len(hm['params'])) else ' (и до обновления)'),
                                ', '.join(hm['params'])))
    return n


def check_baseforms(conf, ext, base, out):
    n = 0
    for dp, _, fn in os.walk(base):
        if 'Form.xml' not in fn or not dp.endswith(os.sep + 'Ext'):
            continue
        p = os.path.join(dp, 'Form.xml')
        if '<BaseForm' not in open(p, encoding='utf-8-sig', errors='replace').read():
            continue
        n += 1
        rel = os.path.relpath(p, base).replace('\\', '/')
        o, h = conf.text('old', rel), conf.text('head', rel)
        if h is None:
            out.append((ext, rel, 'BaseForm', 'формы нет в конфигурации', 'снять заимствование формы'))
        elif o is not None and o != h:
            out.append((ext, rel, 'BaseForm', 'форма конфигурации изменилась', 'обновить базовую форму в расширении (конфигуратор: «Обновить заимствованную форму»)'))
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', required=True)
    ap.add_argument('--cfe', action='append', required=True)
    ap.add_argument('--fix-eco', action='store_true',
                    help='вписать недостающий ExtendedConfigurationObject заимствованным формам (UUID формы конфигурации)')
    ap.add_argument('--fix-eco-all', action='store_true', help='то же для всех заимствованных объектов верхнего уровня')
    a = ap.parse_args()
    st = load_state(a.work)
    conf = Conf(st)
    mods = (conf.common_modules('old'), conf.common_modules('head'))
    rows = []
    for base in a.cfe:
        ext = os.path.basename(os.path.normpath(base))
        out = []
        fix = [] if (a.fix_eco or a.fix_eco_all) else None
        na = check_adopted(conf, ext, base, out, fix, a.fix_eco_all)
        if fix:
            done = sum(add_eco(p, u) for p, u in fix)
            print(f'== {ext}: вписан ExtendedConfigurationObject в {done} из {len(fix)} файлов')
        ci = check_intercepts(conf, ext, base, out)
        nc = check_calls(conf, ext, base, out, mods)
        nf = check_baseforms(conf, ext, base, out)
        print(f'== {ext}: заимствовано {na}, перехватчики {ci}, вызовов общих модулей {nc}, форм с BaseForm {nf}; замечаний {len(out)}')
        for r in out:
            if 'и до обновления' in r[3] or 'не было и до обновления' in r[3]:
                continue
            print('  ' + ' | '.join(x for x in r[1:] if x))
        rows += out
    write_lines(os.path.join(a.work, 'cfe_compat.tsv'), ['\t'.join(r) for r in rows])
    print(f'Полный список (включая проблемы, бывшие до обновления): {os.path.join(a.work, "cfe_compat.tsv")}')


if __name__ == '__main__':
    main()
