#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Дамп содержимого docs/распределение_задач.xlsx (только стандартная библиотека)."""

import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
COL_RE = re.compile(r"^([A-Z]+)")


def col_index(ref: str) -> int:
    letters = COL_RE.match(ref).group(1)
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx


def cell_text(c, shared) -> str:
    t = c.get("t")
    if t == "s":
        v = c.find("m:v", NS)
        return shared[int(v.text)] if v is not None else ""
    if t == "inlineStr":
        return "".join(x.text or "" for x in c.findall(".//m:t", NS))
    v = c.find("m:v", NS)
    return v.text if v is not None else ""


def main(path: str) -> None:
    with zipfile.ZipFile(path) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", NS):
                shared.append("".join(t.text or "" for t in si.findall(".//m:t", NS)))
        # порядок листов из workbook
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        rel_map = {r.get("Id"): r.get("Target") for r in rels}
        sheets = []
        for sh in wb.findall(".//m:sheet", NS):
            rid = sh.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            target = rel_map[rid]
            if not target.startswith("xl/"):
                target = "xl/" + target.lstrip("/")
            sheets.append((sh.get("name"), target))

        for name, target in sheets:
            print(f"===== ЛИСТ: {name} =====")
            root = ET.fromstring(z.read(target))
            for row in root.findall(".//m:row", NS):
                cells = {}
                for c in row.findall("m:c", NS):
                    cells[col_index(c.get("r", "A1"))] = cell_text(c, shared)
                if not cells:
                    continue
                width = max(cells)
                line = " | ".join(
                    (cells.get(i, "") or "").replace("\n", "\\n") for i in range(1, width + 1)
                )
                print(line.rstrip(" |"))
            print()


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "docs/распределение_задач.xlsx"
    main(str(Path(target)))
