#!/usr/bin/env python3
"""
Конвертер PDF с двумя колонками в одноколоночный PDF.
Использование: python3 convert.py input.pdf output.pdf
"""

import sys
import os
import re
import subprocess
import tempfile

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_LEFT, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak, HRFlowable
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ─── Шрифты ──────────────────────────────────────────────────────────────────
# Ищем системный шрифт с поддержкой кириллицы
def find_font(names):
    dirs = [
        "/usr/share/fonts", "/usr/local/share/fonts",
        os.path.expanduser("~/.fonts"), os.path.expanduser("~/.local/share/fonts"),
    ]
    for name in names:
        for d in dirs:
            for root, _, files in os.walk(d):
                for f in files:
                    if f.lower() == name.lower():
                        return os.path.join(root, f)
    return None

def register_fonts():
    # Абсолютные пути для известных систем, затем fallback-поиск
    absolute_candidates = [
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"),
        ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Italic.ttf"),
        ("/usr/share/fonts/truetype/freefont/FreeSans.ttf",
         "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
         "/usr/share/fonts/truetype/freefont/FreeSansOblique.ttf"),
        ("/usr/share/fonts/truetype/crosextra/Carlito-Regular.ttf",
         "/usr/share/fonts/truetype/crosextra/Carlito-Bold.ttf",
         "/usr/share/fonts/truetype/crosextra/Carlito-Italic.ttf"),
    ]
    for r, b, i in absolute_candidates:
        if os.path.exists(r) and os.path.exists(b):
            pdfmetrics.registerFont(TTFont("Manual",        r))
            pdfmetrics.registerFont(TTFont("Manual-Bold",   b))
            pdfmetrics.registerFont(TTFont("Manual-Italic", i if os.path.exists(i) else r))
            print(f"  Шрифт: {os.path.basename(r)}")
            return "Manual"

    # Поиск по имени файла в системных директориях
    name_candidates = [
        ("DejaVuSans.ttf",             "DejaVuSans-Bold.ttf",        "DejaVuSans-Oblique.ttf"),
        ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf",    "LiberationSans-Italic.ttf"),
        ("FreeSans.ttf",               "FreeSansBold.ttf",            "FreeSansOblique.ttf"),
        ("Carlito-Regular.ttf",        "Carlito-Bold.ttf",            "Carlito-Italic.ttf"),
        ("Arial.ttf",                  "Arial_Bold.ttf",              "Arial_Italic.ttf"),
    ]
    for regular, bold, italic in name_candidates:
        r = find_font(regular)
        b = find_font(bold)
        if r and b:
            i = find_font(italic)
            pdfmetrics.registerFont(TTFont("Manual",        r))
            pdfmetrics.registerFont(TTFont("Manual-Bold",   b))
            pdfmetrics.registerFont(TTFont("Manual-Italic", i if i else r))
            print(f"  Шрифт: {regular}")
            return "Manual"

    print("  Предупреждение: кириллический шрифт не найден, используется Helvetica")
    return "Helvetica"

# ─── Извлечение текста ────────────────────────────────────────────────────────
PAGE_W_PT = 436.535   # ширина страницы оригинала в pt
COL_SPLIT  = PAGE_W_PT * 0.52  # граница между колонками (~227 pt)

def extract_page_text(pdf_path, page_num):
    """
    Извлекает текст страницы с учётом двух колонок:
    сначала левая, потом правая.
    page_num — 1-based.
    """
    def run(x, w):
        r = subprocess.run(
            ["pdftotext", "-f", str(page_num), "-l", str(page_num),
             "-x", str(int(x)), "-y", "0",
             "-W", str(int(w)), "-H", "595",
             pdf_path, "-"],
            capture_output=True, text=True
        )
        return r.stdout

    left  = run(0,          COL_SPLIT)
    right = run(COL_SPLIT,  PAGE_W_PT - COL_SPLIT)

    # Убираем дубли номеров страниц и колонтитулов
    left  = clean_raw(left)
    right = clean_raw(right)

    # Если правая колонка пустая — страница одноколоночная
    if not right.strip():
        return left
    return left + "\n" + right


def clean_raw(text):
    """Убирает артефакты: номера страниц, цифры-разделители глав."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        # Пропускаем строки только из цифр (номера страниц)
        if re.fullmatch(r'\d+', stripped):
            continue
        # Пропускаем одиночные цифры (маркеры глав на боковой панели)
        if re.fullmatch(r'[1-8]', stripped):
            continue
        lines.append(line)
    return "\n".join(lines)


# ─── Разбор структуры текста ─────────────────────────────────────────────────
def detect_heading_level(line):
    """
    Возвращает (level, text) или None.
    Эвристика на основе наблюдённых паттернов документа.
    """
    s = line.strip()
    if not s:
        return None

    # Строки, написанные ТОЛЬКО заглавными → не заголовки (аббревиатуры)
    # Признаки заголовков в этом PDF:
    # H1: короткие строки, начало раздела, все слова с большой буквы, нет точки в конце
    # H2: синие подзаголовки — в тексте неотличимы, используем длину + структуру
    # Маркер «•» или цифра «N.» → элемент списка

    # Заголовок главы — длинный текст в начале страницы (определяется отдельно)
    return None


def parse_lines(raw_text):
    """
    Разбирает сырой текст в список элементов.

    Паттерн PDF: маркер '•' стоит на отдельной строке, затем идут строки
    текста пункта (до следующей пустой строки, '•', цифры или сноски).
    """
    elements = []
    lines = raw_text.split('\n')
    n = len(lines)
    i = 0

    def collect_text_lines(start):
        """Собирает непрерывный блок строк начиная со start."""
        parts = []
        j = start
        while j < n:
            s = lines[j].strip()
            if not s:
                break
            if s == '•':
                break
            if re.fullmatch(r'\d{1,2}\.', s):
                break
            if re.match(r'^\d{1,2}\.\s+\S', s):
                break
            if s.startswith('*') and 'функция' in s.lower():
                break
            parts.append(s)
            j += 1
        return ' '.join(parts), j

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # Пустая строка
        if not stripped:
            elements.append(('empty',))
            i += 1
            continue

        # Одиночный маркер '•' — текст идёт на следующих строках
        if stripped == '•':
            text, i = collect_text_lines(i + 1)
            if text:
                elements.append(('bullet', text))
            # иначе пропускаем пустой маркер
            continue

        # Маркер '•' с текстом на той же строке
        if stripped.startswith('•') and len(stripped) > 1:
            text = stripped.lstrip('•').strip()
            # Подхватываем продолжение
            j = i + 1
            while j < n:
                s = lines[j].strip()
                if not s or s == '•' or s.startswith('*'):
                    break
                text += ' ' + s
                j += 1
            elements.append(('bullet', text))
            i = j
            continue

        # Нумерованный пункт: "1." на своей строке, текст дальше
        if re.fullmatch(r'\d{1,2}\.', stripped):
            num = stripped.rstrip('.')
            text, i = collect_text_lines(i + 1)
            if text:
                elements.append(('numbered', num, text))
            continue

        # Нумерованный пункт: "1. Текст" на одной строке
        m = re.match(r'^(\d{1,2})\.\s+(.+)$', stripped)
        if m:
            num, text = m.group(1), m.group(2)
            j = i + 1
            while j < n:
                s = lines[j].strip()
                if not s or s == '•' or re.match(r'^\d{1,2}\.', s):
                    break
                text += ' ' + s
                j += 1
            elements.append(('numbered', num, text))
            i = j
            continue

        # Сноска
        if stripped.startswith('*') and 'функция' in stripped.lower():
            elements.append(('footnote', stripped))
            i += 1
            continue

        # Обычный параграф — склеиваем только строки, которые явно являются
        # продолжением (не выглядят как заголовок и не начинают новый блок)
        text = stripped
        j = i + 1
        while j < n:
            s = lines[j].strip()
            if not s:
                break
            if s == '•' or s.startswith('•'):
                break
            if re.match(r'^\d{1,2}\.', s):
                break
            if s.startswith('*') and 'функция' in s.lower():
                break
            # Не склеиваем если следующая строка выглядит как заголовок:
            # короткая, с заглавной, без точки в конце
            if (len(s.split()) <= 13 and s[0].isupper() and
                    s[-1] not in '.,:;)»◄' and
                    not any(x in s for x in ['–', ' и ', ' в ', ' на ', ' с '])):
                break
            text += ' ' + s
            j += 1
        elements.append(('para', text))
        i = j

    return elements


def classify_para(text):
    """
    Классифицирует параграф по эвристике:
    chapter_header, heading1, heading2, note, para
    """
    s = text.strip()
    if not s:
        return 'para'

    word_count = len(s.split())

    # Колонтитул — точное совпадение с известными названиями разделов
    CHAPTER_HEADERS = {
        "Общая информация",
        "Функции доступа и управления замками автомобиля",
        "Оборудование автомобиля",
        "Сиденья и защитные устройства",
        "Запуск двигателя и вождение автомобиля",
        "Ремонт и техническое обслуживание",
        "Действия в чрезвычайной ситуации",
        "Техническая информация",
        "Мультимедийная система",
    }
    if s in CHAPTER_HEADERS:
        return 'chapter_header'

    # Примечание — заканчивается на ◄
    if s.endswith('◄'):
        return 'note'

    # Не заголовок если заканчивается на знак конца предложения
    if s[-1] in '.,:;)»':
        return 'para'

    # Не заголовок если содержит типичные не-заголовочные паттерны
    NO_HEADING = ['км/ч', ' кг', ' мм', ' л.', 'A/C', 'USB', 'ABS', 'ESC',
                  'ACC', 'EPB', 'EPS', 'LKA', 'HDC', 'IHBC', 'CMSF', 'VIN',
                  ' OFF', ' ON', '→', '%', '«', 'Тип 1', 'Тип 2',
                  'функция', 'система', 'нажмите', 'автомобиль']
    s_lower = s.lower()
    if any(x.lower() in s_lower for x in NO_HEADING):
        return 'para'

    # Заголовок — короткий, с заглавной, без точки
    if 1 <= word_count <= 7 and s[0].isupper():
        return 'heading1'

    # Заголовок второго уровня — средней длины
    if 8 <= word_count <= 13 and s[0].isupper():
        return 'heading2'

    return 'para'


# ─── Построение PDF ───────────────────────────────────────────────────────────
def build_styles(font):
    bold   = font + "-Bold"
    italic = font + "-Italic"

    base = dict(fontName=font, fontSize=10, leading=14,
                leftIndent=0, rightIndent=0, spaceAfter=4,
                alignment=TA_JUSTIFY)

    styles = {
        'chapter': ParagraphStyle('chapter',
            fontName=bold, fontSize=14, leading=18,
            spaceBefore=18, spaceAfter=6,
            textColor='#1A5276', alignment=TA_LEFT),

        'h1': ParagraphStyle('h1',
            fontName=bold, fontSize=12, leading=16,
            spaceBefore=14, spaceAfter=4,
            textColor='#1A5276', alignment=TA_LEFT),

        'h2': ParagraphStyle('h2',
            fontName=bold, fontSize=11, leading=15,
            spaceBefore=10, spaceAfter=3,
            textColor='#2471A3', alignment=TA_LEFT),

        'para': ParagraphStyle('para', **base, spaceBefore=2),

        'bullet': ParagraphStyle('bullet',
            **{**base, 'leftIndent': 12, 'firstLineIndent': -8,
               'spaceBefore': 2, 'spaceAfter': 2}),

        'numbered': ParagraphStyle('numbered',
            **{**base, 'leftIndent': 20, 'firstLineIndent': -16,
               'spaceBefore': 2, 'spaceAfter': 2}),

        'note': ParagraphStyle('note',
            fontName=font, fontSize=9, leading=13,
            leftIndent=10, rightIndent=10,
            spaceBefore=4, spaceAfter=4,
            backColor='#EBF5FB', borderPadding=4,
            alignment=TA_JUSTIFY),

        'footnote': ParagraphStyle('footnote',
            fontName=font, fontSize=8, leading=11,
            textColor='#666666', spaceBefore=6, spaceAfter=2),
    }
    return styles


def safe_para(text, style):
    """Создаёт Paragraph, экранируя спецсимволы XML."""
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    try:
        return Paragraph(text, style)
    except Exception:
        return Paragraph(text.encode('ascii', 'replace').decode(), style)


def build_pdf(pages_text, output_path, font):
    styles = build_styles(font)

    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=20*mm, bottomMargin=20*mm,
        title="Руководство по эксплуатации Geely CityRay",
    )

    story = []
    seen_chapter_headers = set()

    for page_idx, raw_text in enumerate(pages_text):
        elements = parse_lines(raw_text)

        # Группируем последовательные ('empty',) в один
        prev_empty = False
        for el in elements:
            if el[0] == 'empty':
                if not prev_empty:
                    story.append(Spacer(1, 4))
                prev_empty = True
                continue
            prev_empty = False

            if el[0] == 'bullet':
                story.append(safe_para(u'\u2022\u2002' + el[1], styles['bullet']))

            elif el[0] == 'numbered':
                story.append(safe_para(f"{el[1]}.\u2002{el[2]}", styles['numbered']))

            elif el[0] == 'footnote':
                story.append(safe_para(el[1], styles['footnote']))

            elif el[0] == 'para':
                kind = classify_para(el[1])

                if kind == 'chapter_header':
                    # Показываем колонтитул только один раз
                    key = el[1].strip()
                    if key not in seen_chapter_headers:
                        seen_chapter_headers.add(key)
                        story.append(HRFlowable(width="100%", thickness=1,
                                                color='#2471A3', spaceAfter=4))
                        story.append(safe_para(el[1], styles['chapter']))
                    # иначе пропускаем

                elif kind == 'heading1':
                    story.append(safe_para(el[1], styles['h1']))

                elif kind == 'heading2':
                    story.append(safe_para(el[1], styles['h2']))

                elif kind == 'note':
                    story.append(safe_para(el[1], styles['note']))

                else:
                    story.append(safe_para(el[1], styles['para']))

    doc.build(story)


# ─── Главная функция ──────────────────────────────────────────────────────────
def main():
    if len(sys.argv) < 3:
        print(f"Использование: python3 {sys.argv[0]} input.pdf output.pdf")
        sys.exit(1)

    input_pdf  = sys.argv[1]
    output_pdf = sys.argv[2]

    if not os.path.exists(input_pdf):
        print(f"Ошибка: файл '{input_pdf}' не найден.")
        sys.exit(1)

    # Узнаём количество страниц
    result = subprocess.run(
        ["pdfinfo", input_pdf], capture_output=True, text=True
    )
    pages_match = re.search(r'Pages:\s+(\d+)', result.stdout)
    if not pages_match:
        print("Ошибка: не удалось определить количество страниц.")
        sys.exit(1)
    total_pages = int(pages_match.group(1))
    print(f"Страниц в документе: {total_pages}")

    # Регистрируем шрифт
    print("Поиск шрифта...")
    font = register_fonts()

    # Извлекаем текст со всех страниц
    print("Извлечение текста...")
    pages_text = []
    for page in range(1, total_pages + 1):
        if page % 20 == 0 or page == total_pages:
            print(f"  Страница {page}/{total_pages}")
        text = extract_page_text(input_pdf, page)
        pages_text.append(text)

    # Строим PDF
    print(f"Генерация PDF: {output_pdf}")
    build_pdf(pages_text, output_pdf, font)
    size_mb = os.path.getsize(output_pdf) / 1024 / 1024
    print(f"Готово! Размер файла: {size_mb:.1f} МБ")


if __name__ == '__main__':
    main()
