"""Общие функции skill cf-vendor-merge: git, состояние, чтение/запись файлов выгрузки 1С."""
import json
import os
import re
import subprocess
import sys

STATE_NAME = 'vendor-merge.json'

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')


# ---------- состояние ----------

def state_path(work):
    return os.path.join(work, STATE_NAME)


def load_state(work):
    p = state_path(work)
    if not os.path.exists(p):
        sys.exit(f'Нет файла состояния {p}. Сначала выполни vm_branches.py.')
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def save_state(work, st):
    os.makedirs(work, exist_ok=True)
    with open(state_path(work), 'w', encoding='utf-8') as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


# ---------- git ----------

def git(repo, *args, check=True, binary=False, env=None, input=None):
    cmd = ['git', '-C', repo, '-c', 'core.quotepath=false', *args]
    r = subprocess.run(cmd, capture_output=True, env=env, input=input)
    if check and r.returncode != 0:
        raise RuntimeError(f'git {" ".join(args)}: {r.stderr.decode("utf-8", "replace")}')
    if binary:
        return r.stdout
    return r.stdout.decode('utf-8', 'replace')


def git_rc(repo, *args):
    cmd = ['git', '-C', repo, '-c', 'core.quotepath=false', *args]
    return subprocess.run(cmd, capture_output=True).returncode


def show(repo, spec):
    """Содержимое blob (rev:path или :N:path) либо None."""
    r = subprocess.run(['git', '-C', repo, 'show', spec], capture_output=True)
    return r.stdout if r.returncode == 0 else None


def names(repo, *args):
    return [l for l in git(repo, *args).split('\n') if l]


def changed_files(repo, a, b):
    return set(names(repo, 'diff', '--name-only', '--no-renames', a, b))


def unmerged(repo):
    return names(repo, 'diff', '--name-only', '--diff-filter=U')


# ---------- файлы выгрузки ----------

BOM = b'\xef\xbb\xbf'


def read_text(path):
    with open(path, 'rb') as f:
        raw = f.read()
    return raw.decode('utf-8-sig', errors='replace')


def write_text(path, text, crlf=True, bom=True):
    """Пишет текст в формате выгрузки 1С: UTF-8 с BOM, CRLF."""
    t = text.replace('\r\n', '\n')
    if crlf:
        t = t.replace('\n', '\r\n')
    data = t.encode('utf-8')
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'wb') as f:
        f.write((BOM if bom else b'') + data)


def write_lines(path, items):
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(items) + ('\n' if items else ''))


def read_list(path):
    with open(path, encoding='utf-8-sig') as f:
        return [l.rstrip('\r\n') for l in f if l.strip()]


def config_version(xml_dir):
    """(Имя, Версия, Поставщик, версия формата выгрузки) из Configuration.xml."""
    p = os.path.join(xml_dir, 'Configuration.xml')
    t = read_text(p)
    fmt = re.search(r'<MetaDataObject[^>]*\sversion="([^"]+)"', t)
    def tag(n):
        m = re.search(rf'<{n}>([^<]*)</{n}>', t)
        return m.group(1) if m else None
    return tag('Name'), tag('Version'), tag('Vendor'), fmt.group(1) if fmt else None


# ---------- XML ----------

def xml_serialize(root, nl='\r\n'):
    """Сериализация lxml-дерева в формат 1С (проверено: байт-в-байт для неизменённых файлов)."""
    from lxml import etree
    body = etree.tostring(root, encoding='unicode')
    text = '<?xml version="1.0" encoding="UTF-8"?>\n' + body
    text = text.replace('\r\n', '\n')
    if nl == '\r\n':
        text = text.replace('\n', '\r\n')
    return BOM + text.encode('utf-8')


def xml_remove(e):
    """Удаляет элемент, сохраняя форматирование соседей."""
    prev = e.getprevious()
    par = e.getparent()
    if prev is not None:
        prev.tail = e.tail
    else:
        par.text = e.tail
    par.remove(e)


def local(e):
    from lxml import etree
    return etree.QName(e).localname if isinstance(e.tag, str) else None


# ---------- каталоги метаданных ----------

TYPE_DIRS = {
    'Catalog': 'Catalogs', 'Document': 'Documents', 'Enum': 'Enums', 'InformationRegister': 'InformationRegisters',
    'AccumulationRegister': 'AccumulationRegisters', 'AccountingRegister': 'AccountingRegisters',
    'CalculationRegister': 'CalculationRegisters', 'ChartOfCharacteristicTypes': 'ChartsOfCharacteristicTypes',
    'ChartOfAccounts': 'ChartsOfAccounts', 'ChartOfCalculationTypes': 'ChartsOfCalculationTypes',
    'BusinessProcess': 'BusinessProcesses', 'Task': 'Tasks', 'ExchangePlan': 'ExchangePlans',
    'DataProcessor': 'DataProcessors', 'Report': 'Reports', 'DocumentJournal': 'DocumentJournals',
    'Constant': 'Constants', 'CommonModule': 'CommonModules', 'CommonForm': 'CommonForms',
    'CommonCommand': 'CommonCommands', 'CommonTemplate': 'CommonTemplates', 'CommonPicture': 'CommonPictures',
    'CommonAttribute': 'CommonAttributes', 'Role': 'Roles', 'Subsystem': 'Subsystems', 'DefinedType': 'DefinedTypes',
    'FunctionalOption': 'FunctionalOptions', 'FunctionalOptionsParameter': 'FunctionalOptionsParameters',
    'SessionParameter': 'SessionParameters', 'EventSubscription': 'EventSubscriptions', 'ScheduledJob': 'ScheduledJobs',
    'StyleItem': 'StyleItems', 'Style': 'Styles', 'Language': 'Languages', 'WebService': 'WebServices',
    'HTTPService': 'HTTPServices', 'XDTOPackage': 'XDTOPackages', 'FilterCriterion': 'FilterCriteria',
    'SettingsStorage': 'SettingsStorages', 'CommandGroup': 'CommandGroups', 'DocumentNumerator': 'DocumentNumerators',
    'Sequence': 'Sequences', 'WSReference': 'WSReferences', 'ExternalDataSource': 'ExternalDataSources',
    'IntegrationService': 'IntegrationServices', 'Bot': 'Bots', 'PaletteColor': 'PaletteColors',
    'WebSocketClient': 'WebSocketClients', 'Interface': 'Interfaces',
}

REF_TYPES = {
    'CatalogRef': 'Catalog', 'CatalogObject': 'Catalog', 'DocumentRef': 'Document', 'DocumentObject': 'Document',
    'EnumRef': 'Enum', 'ChartOfCharacteristicTypesRef': 'ChartOfCharacteristicTypes',
    'ChartOfAccountsRef': 'ChartOfAccounts', 'ChartOfCalculationTypesRef': 'ChartOfCalculationTypes',
    'BusinessProcessRef': 'BusinessProcess', 'TaskRef': 'Task', 'ExchangePlanRef': 'ExchangePlan',
    'DefinedType': 'DefinedType', 'InformationRegisterRecordKey': 'InformationRegister',
    'InformationRegisterRecordSet': 'InformationRegister', 'AccumulationRegisterRecordSet': 'AccumulationRegister',
    'AccountingRegisterRecordSet': 'AccountingRegister', 'CalculationRegisterRecordSet': 'CalculationRegister',
    'DataProcessorObject': 'DataProcessor', 'ReportObject': 'Report', 'BusinessProcessRoutePointRef': 'BusinessProcess',
    'ConstantValueManager': 'Constant',
}


def objects_on_disk(cf_dir):
    """{Тип: {имена}} по файлам <Каталог>/<Имя>.xml."""
    res = {}
    for t, d in TYPE_DIRS.items():
        p = os.path.join(cf_dir, d)
        res[t] = {f[:-4] for f in os.listdir(p) if f.endswith('.xml')} if os.path.isdir(p) else set()
    return res


def uuid_map(repo, rev):
    """{uuid: 'Каталог/Имя'} для объектов верхнего уровня в ревизии."""
    out = names(repo, 'grep', '-E', '-o', '-m1', r'^\s*<[A-Za-z]+ uuid="[0-9a-f-]+"', rev, '--',
                *[f'{d}/*.xml' for d in TYPE_DIRS.values()])
    m = {}
    for line in out:
        try:
            _, path, rest = line.split(':', 2)
        except ValueError:
            continue
        if path.count('/') != 1:
            continue
        u = re.search(r'uuid="([^"]+)"', rest)
        if u:
            m[u.group(1)] = path[:-4]
    return m


def vendor_renames(repo, old_rev, new_rev):
    """{'Каталог/СтароеИмя': 'Каталог/НовоеИмя'} — переименования поставщика по UUID."""
    old, new = uuid_map(repo, old_rev), uuid_map(repo, new_rev)
    return {old[u]: new[u] for u in old if u in new and old[u] != new[u]}
