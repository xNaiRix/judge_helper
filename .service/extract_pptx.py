# -*- coding: utf-8 -*-
"""Служебный скрипт: извлечение текста слайдов из docs/Тестирование.pptx
в .service/лекции_текст.txt (без внешних зависимостей, только stdlib)."""
import glob
import os
import re
import zipfile

WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(WORKDIR, ".service", "лекции_текст.txt")

pptx_files = glob.glob(os.path.join(WORKDIR, "docs", "*.pptx"))
assert pptx_files, "Не найден .pptx в docs/"
path = pptx_files[0]

z = zipfile.ZipFile(path)
slide_names = [
    n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)
]
slide_names.sort(key=lambda n: int(re.search(r"\d+", n).group()))

lines = []
for i, name in enumerate(slide_names, 1):
    xml = z.read(name).decode("utf-8")
    texts = re.findall(r"<a:t>([^<]*)</a:t>", xml)
    lines.append("=" * 20 + f" СЛАЙД {i} ({name}) " + "=" * 20)
    lines.append(" | ".join(t for t in texts if t.strip()))
    lines.append("")

# notes слайдов (если есть) — часто там тезисы лектора
note_names = [
    n for n in z.namelist() if re.match(r"ppt/notesSlides/notesSlide\d+\.xml$", n)
]
note_names.sort(key=lambda n: int(re.search(r"\d+", n).group()))
for name in note_names:
    xml = z.read(name).decode("utf-8")
    texts = re.findall(r"<a:t>([^<]*)</a:t>", xml)
    joined = " ".join(t for t in texts if t.strip())
    if joined.strip():
        lines.append("=" * 20 + f" ЗАМЕТКИ ({name}) " + "=" * 20)
        lines.append(joined[:2000])
        lines.append("")
z.close()

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print(f"OK: {len(slide_names)} слайдов -> {OUT}")
