"""Нормализация «шума выгрузки» — отличий, которые не являются доработками.

Найдено на реальном обновлении (ERP 2.5.22 -> 2.5.27, доработанная конфигурация):
- из конфигурации заказчика когда-то удалили язык en: пропали <v8:item> с lang=en и <languageInfo> en в макетах;
- отсутствуют блоки <StandardAttributes>/<StandardTabularSections> со значениями по умолчанию;
- служебные подэлементы форм названы по-английски (СписокExtendedTooltip вместо СписокРасширеннаяПодсказка);
- в ролях нет объекта Configuration.<Имя> с правами MainWindowMode* и т.п.;
- ссылки характеристик выгружены как -1;
- пустой модуль (только BOM) отсутствует как файл;
- в BSL отличаются только комментарии и пробелы.
"""
import re

EN_PATTERNS = [
    re.compile(rb'<v8:item>\s*<v8:lang>en</v8:lang>\s*<v8:content>.*?</v8:content>\s*</v8:item>', re.S),
    re.compile(rb'<v8:item>\s*<v8:lang>en</v8:lang>\s*<v8:content/>\s*</v8:item>', re.S),
    re.compile(rb'<v8:item>\s*<v8:lang>en</v8:lang>.*?</v8:item>', re.S),
    re.compile(rb'<languageInfo>\s*<id>en</id>.*?</languageInfo>', re.S),
]
STD_ATTR = re.compile(rb'<StandardAttributes>.*?</StandardAttributes>|<StandardTabularSections>.*?</StandardTabularSections>', re.S)
SUB_NAMES = re.compile(rb'(<(ExtendedTooltip|ContextMenu|SearchStringAddition|ViewStatusAddition|SearchControlAddition|AutoCommandBar) name=")[^"]*(")')
ROLE_CFG = re.compile(rb'<object>\s*<name>Configuration\.[^<]*</name>.*?</object>', re.S)
CHAR_REF = re.compile(rb'(<xr:(?:KeyField|TypesFilterField|ObjectField|TypeField|ValueField)>)[^<]*(</xr:)')
WS = re.compile(rb'\s+')


def norm_xml(b):
    if b is None:
        return None
    b = b.replace(b'\r\n', b'\n')
    if b.startswith(b'\xef\xbb\xbf'):
        b = b[3:]
    for rx in EN_PATTERNS:
        b = rx.sub(b'', b)
    b = STD_ATTR.sub(b'', b)
    b = SUB_NAMES.sub(rb'\1~\3', b)
    b = ROLE_CFG.sub(b'', b)
    b = CHAR_REF.sub(rb'\1~\2', b)
    return WS.sub(b'', b)


def _strip_bsl_comments(t):
    out = []
    for line in t.split('\n'):
        instr = False
        cut = None
        for i, ch in enumerate(line):
            if ch == '"':
                instr = not instr
            elif not instr and line.startswith('//', i):
                cut = i
                break
        out.append(line if cut is None else line[:cut])
    return '\n'.join(out)


def norm_bsl(b):
    if b is None:
        return None
    t = b.decode('utf-8-sig', errors='replace').replace('\r\n', '\n')
    t = _strip_bsl_comments(t)
    return re.sub(r'\s+', '', t)


def norm(path, b):
    if b is None:
        return None
    if path.endswith('.bsl'):
        return norm_bsl(b)
    if path.endswith('.xml'):
        return norm_xml(b)
    return b


def is_empty(path, b):
    return b is None or norm(path, b) in (b'', '')
