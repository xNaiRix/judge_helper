"""Отчёты: статистика по судьям, гистограмма (CSV/PNG), PDF-протокол.

Доступно судьям соревнования после его завершения (ТЗ, разделы
«Завершение соревнований» и «Формирование протокола»)."""

from __future__ import annotations

import csv
import io
import statistics
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import get_current_user, get_db
from ..models import (
    Competition,
    CompetitionStatus,
    Participant,
    Score,
    User,
)
from ..schemas import JudgeStatEntry, JudgeStatsRow
from ..services import (
    comp_judge_users,
    fio_display,
    participant_scores,
    require_admin,
    require_member,
)
from ..utils import mean, round2

router = APIRouter(prefix="/competitions/{competition_id}", tags=["Отчёты и статистика"])

INDICATORS = (
    ("Артистичность", "artistry"),
    ("Техника", "technique"),
    ("Соревновательный балл", "total"),
)


@dataclass
class FinalRow:
    participant: Participant
    artistry: float | None
    technique: float | None
    competition_score: float | None
    scores: list[Score]


def _require_finished(db: Session, competition_id: int, user: User) -> Competition:
    comp = require_member(db, competition_id, user)
    if comp.status != CompetitionStatus.finished.value:
        raise HTTPException(
            status_code=409, detail="Отчёты доступны только после завершения соревнования"
        )
    return comp


def final_rows(db: Session, competition_id: int) -> list[FinalRow]:
    """Итоговые показатели участников (средние по оценившим судьям)."""
    rows: list[FinalRow] = []
    for p in db.execute(
        select(Participant)
        .where(Participant.competition_id == competition_id)
        .order_by(Participant.id)
    ).scalars():
        scores = participant_scores(db, competition_id, p.id)
        if scores:
            rows.append(
                FinalRow(
                    participant=p,
                    artistry=round2(mean([s.artistry for s in scores])),
                    technique=round2(mean([s.technique for s in scores])),
                    competition_score=round2(mean([s.artistry + s.technique for s in scores])),
                    scores=scores,
                )
            )
        else:
            rows.append(FinalRow(p, None, None, None, []))
    return rows


def _category_stats(rows: list[FinalRow]) -> dict[str, dict[str, dict[str, float]]]:
    """По каждой категории: среднее/макс/мин/медиана по каждому показателю."""
    stats: dict[str, dict[str, dict[str, float]]] = {}
    for row in rows:
        cat = row.participant.category
        stats.setdefault(cat, {"artistry": [], "technique": [], "total": []})
        if row.artistry is not None:
            stats[cat]["artistry"].append(row.artistry)
            stats[cat]["technique"].append(row.technique)
            stats[cat]["total"].append(row.competition_score)
    result: dict[str, dict[str, dict[str, float]]] = {}
    for cat, values in stats.items():
        result[cat] = {}
        for _, key in INDICATORS:
            data = values[key]
            if data:
                result[cat][key] = {
                    "mean": round2(mean(data)),
                    "max": round2(max(data)),
                    "min": round2(min(data)),
                    "median": round2(statistics.median(data)),
                }
    return result


# --------------------------- Статистика по судьям ---------------------------


@router.get(
    "/judges/stats",
    response_model=list[JudgeStatsRow],
    summary="Список по судьям: средние оценки и развёрнутые баллы",
)
def judges_stats(
    competition_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Список по судьям доступен только администратору соревнования (ТЗ)
    comp = _require_finished(db, competition_id, user)
    require_admin(comp, user)
    finals = {r.participant.id: r for r in final_rows(db, competition_id)}

    result: list[JudgeStatsRow] = []
    for judge in comp_judge_users(db, competition_id):
        scores = list(
            db.execute(
                select(Score)
                .where(Score.competition_id == competition_id, Score.judge_id == judge.id)
                .order_by(Score.participant_id)
            ).scalars()
        )
        entries = []
        for s in scores:
            final = finals.get(s.participant_id)
            p = final.participant if final else db.get(Participant, s.participant_id)
            entries.append(
                JudgeStatEntry(
                    participant=fio_display(p),
                    category=p.category,
                    artistry=s.artistry,
                    artistry_final=final.artistry if final else None,
                    technique=s.technique,
                    technique_final=final.technique if final else None,
                    total=s.artistry + s.technique,
                    competition_score=final.competition_score if final else None,
                )
            )
        result.append(
            JudgeStatsRow(
                judge_id=judge.id,
                full_name=judge.full_name,
                email=judge.email,
                avg_artistry=round2(mean([s.artistry for s in scores])),
                avg_technique=round2(mean([s.technique for s in scores])),
                avg_total=round2(mean([s.artistry + s.technique for s in scores])),
                entries=entries,
            )
        )
    return result


# --------------------------------- CSV --------------------------------------


@router.get("/reports/chart.csv", summary="Данные гистограммы в CSV", response_class=Response)
def chart_csv(
    competition_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_finished(db, competition_id, user)
    stats = _category_stats(final_rows(db, competition_id))

    def num(value: float) -> str:
        """Число с десятичной ЗАПЯТОЙ: иначе Excel в русской локали
        принимает «8.00» за дату (8 мая), а не балл."""
        return f"{value:.2f}".replace(".", ",")

    buffer = io.StringIO()
    buffer.write("\ufeff")  # BOM для корректного отображения кириллицы в Excel
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(["Категория", "Показатель", "Среднее", "Максимум", "Минимум", "Медиана"])
    for cat in sorted(stats):
        for label, key in INDICATORS:
            if key in stats[cat]:
                v = stats[cat][key]
                writer.writerow(
                    [
                        cat,
                        label,
                        num(v["mean"]),
                        num(v["max"]),
                        num(v["min"]),
                        num(v["median"]),
                    ]
                )
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="chart_{competition_id}.csv"'},
    )


# --------------------------------- PNG --------------------------------------


def _load_pil_font(size: int = 13):
    """Шрифт с поддержкой кириллицы (обязательна русская локализация, ТЗ).

    Стандартный bitmap-шрифт PIL кириллицу не содержит, поэтому ищем
    системный TTF (Windows: Arial; Linux: DejaVu/Liberation)."""
    from PIL import ImageFont

    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


@router.get("/reports/chart.png", summary="Гистограмма по категориям в PNG")
def chart_png(
    competition_id: int,
    category: str | None = Query(None, description="Одна категория; без параметра — все вместе"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from PIL import Image, ImageDraw

    comp = _require_finished(db, competition_id, user)
    stats = _category_stats(final_rows(db, competition_id))
    if category is not None:
        if category not in stats:
            raise HTTPException(status_code=422, detail="Нет данных для указанной категории")
        stats = {category: stats[category]}

    cats = sorted(stats)
    if not cats:
        raise HTTPException(status_code=409, detail="Нет оценок для построения графика")

    width, height = 980, 560
    margin_left, margin_bottom, top = 90, 90, 70
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    # Значения и шкала
    all_values = [
        stats[cat][k][m]
        for cat in cats
        for k in ("artistry", "technique", "total")
        for m in ("mean", "max", "min", "median")
        if k in stats[cat]
    ]
    scale_max = max(all_values + [1]) * 1.1
    plot_h = height - margin_bottom - top

    def y_of(value: float) -> float:
        return height - margin_bottom - (value / scale_max) * plot_h

    # Оси
    draw.line([(margin_left, top), (margin_left, height - margin_bottom)], fill="black", width=2)
    draw.line(
        [(margin_left, height - margin_bottom), (width - 20, height - margin_bottom)],
        fill="black",
        width=2,
    )

    bar_group_w = (width - margin_left - 40) / len(cats)
    bar_w = min(52, bar_group_w / 3.6)
    colors = {"artistry": (66, 133, 244), "technique": (52, 168, 83), "total": (251, 146, 60)}
    labels = {
        "artistry": "Артистичность",
        "technique": "Техника",
        "total": "Соревновательный балл",
    }
    font = _load_pil_font(13)
    font_small = _load_pil_font(11)
    font_title = _load_pil_font(17)

    for gi, cat in enumerate(cats):
        x0 = margin_left + gi * bar_group_w + bar_group_w / 2 - 1.5 * bar_w
        for bi, key in enumerate(("artistry", "technique", "total")):
            if key not in stats[cat]:
                continue
            v = stats[cat][key]
            bx = x0 + bi * (bar_w + 6)
            top_y = y_of(v["mean"])
            # Столбец (среднее)
            draw.rectangle(
                [bx, top_y, bx + bar_w, height - margin_bottom], fill=colors[key], outline="black"
            )
            # Линии: зелёная — макс, красная — мин, жёлтая — медиана (ТЗ)
            for value, color in (
                (v["max"], (0, 160, 0)),
                (v["min"], (220, 0, 0)),
                (v["median"], (240, 190, 0)),
            ):
                ly = y_of(value)
                draw.line([bx - 3, ly, bx + bar_w + 3, ly], fill=color, width=3)
            draw.text(
                (bx, top_y - 16), f"{v['mean']:.2f}", fill="black", anchor="ma", font=font_small
            )
        # Подпись категории
        draw.text(
            (x0 + 1.5 * bar_w, height - margin_bottom + 12),
            cat,
            fill="black",
            anchor="ma",
            font=font,
        )

    # Заголовок и легенда
    if len(cats) > 1:
        title = f"Гистограмма: {comp.name}"
    else:
        title = f"Гистограмма: {comp.name} ({cats[0]})"
    draw.text((width // 2, 16), title, fill="black", anchor="ma", font=font_title)
    legend_x = margin_left
    for key in ("artistry", "technique", "total"):
        draw.rectangle([legend_x, 44, legend_x + 14, 56], fill=colors[key], outline="black")
        draw.text((legend_x + 20, 44), labels[key], fill="black", font=font_small)
        legend_x += 200
    draw.line([legend_x, 50, legend_x + 22, 50], fill=(0, 160, 0), width=3)
    draw.text((legend_x + 28, 44), "макс", fill="black", font=font_small)
    draw.line([legend_x + 90, 50, legend_x + 112, 50], fill=(220, 0, 0), width=3)
    draw.text((legend_x + 118, 44), "мин", fill="black", font=font_small)
    draw.line([legend_x + 170, 50, legend_x + 192, 50], fill=(240, 190, 0), width=3)
    draw.text((legend_x + 198, 44), "медиана", fill="black", font=font_small)

    # Шкала Y
    step = scale_max / 5
    for i in range(6):
        value = i * step
        draw.text(
            (margin_left - 8, y_of(value)),
            f"{value:.1f}",
            fill="black",
            anchor="rm",
            font=font_small,
        )

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return Response(
        content=buffer.getvalue(),
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="chart_{competition_id}.png"'},
    )


# --------------------------------- PDF ---------------------------------------

_FONT_CANDIDATES = (
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
)

_FONT_BOLD_CANDIDATES = (
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/Arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
)


def _register_font() -> tuple[str | None, str | None]:
    """Ищет системные шрифты с поддержкой кириллицы: (обычный, жирный)."""
    import os

    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    regular = None
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont("AppFont", path))
                regular = "AppFont"
                break
            except Exception:
                continue
    bold = None
    if regular:
        for path in _FONT_BOLD_CANDIDATES:
            if os.path.exists(path):
                try:
                    pdfmetrics.registerFont(TTFont("AppFontBold", path))
                    bold = "AppFontBold"
                    break
                except Exception:
                    continue
    return regular, bold


@router.get("/protocol.pdf", summary="PDF-протокол соревнования (детерминированный)")
def protocol_pdf(
    competition_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as pdf_canvas

    comp = _require_finished(db, competition_id, user)
    rows = final_rows(db, competition_id)

    buffer = io.BytesIO()
    # invariant=1 — детерминированный вывод: повторная генерация идентична (ТЗ)
    pdf = pdf_canvas.Canvas(buffer, pagesize=A4, invariant=1)
    width, height = A4
    font, font_bold = _register_font()
    font_name = font or "Helvetica"

    y = height - 50

    def ensure_space(needed: float) -> None:
        nonlocal y
        if y - needed < 60:
            pdf.showPage()
            y = height - 50

    def text(x: float, yy: float, s: str, size: int = 10, bold: bool = False) -> None:
        name = font_bold if (bold and font_bold) else font_name
        pdf.setFont(name, size)
        pdf.drawString(x, yy, s)

    text(width / 2, y, "ПРОТОКОЛ", 18, bold=True)
    y -= 26
    text(width / 2, y, comp.name, 14, bold=True)
    y -= 18
    text(width / 2, y, f"Даты: {comp.dates}    Место: {comp.place}", 10)
    y -= 24

    # Участники по категориям, отсортированы по невозрастанию соревновательного балла
    by_cat: dict[str, list[FinalRow]] = {}
    for r in rows:
        by_cat.setdefault(r.participant.category, []).append(r)

    for cat in sorted(by_cat):
        cat_rows = sorted(
            by_cat[cat],
            key=lambda r: r.competition_score if r.competition_score is not None else -1,
            reverse=True,
        )
        ensure_space(60)
        text(50, y, f"Категория: {cat}", 12, bold=True)
        y -= 18
        text(50, y, "№", 9, bold=True)
        text(80, y, "ФИО участника", 9, bold=True)
        text(280, y, "Артистичность", 9, bold=True)
        text(380, y, "Техника", 9, bold=True)
        text(470, y, "Соревнов. балл", 9, bold=True)
        y -= 14
        for i, r in enumerate(cat_rows, 1):
            ensure_space(20)
            text(50, y, str(i), 9)
            text(80, y, fio_display(r.participant), 9)
            text(280, y, "—" if r.artistry is None else f"{r.artistry:.2f}", 9)
            text(380, y, "—" if r.technique is None else f"{r.technique:.2f}", 9)
            text(470, y, "—" if r.competition_score is None else f"{r.competition_score:.2f}", 9)
            y -= 14
        y -= 6

    # Победители: наивысший балл в своей категории (при равенстве — все)
    y -= 10
    ensure_space(60)
    text(50, y, "Победители", 12, bold=True)
    y -= 16
    for cat in sorted(by_cat):
        cat_rows = [r for r in by_cat[cat] if r.competition_score is not None]
        if not cat_rows:
            continue
        best = max(r.competition_score for r in cat_rows)
        winners = [fio_display(r.participant) for r in cat_rows if r.competition_score == best]
        ensure_space(18)
        text(50, y, f"{cat}: {', '.join(winners)} — {best:.2f}", 10)
        y -= 14

    # Судьи с местом для подписи (ТЗ)
    y -= 16
    ensure_space(80)
    text(50, y, "Судьи", 12, bold=True)
    y -= 16
    for j in comp_judge_users(db, competition_id):
        ensure_space(22)
        text(50, y, f"{j.full_name} ({j.email})", 10)
        text(330, y, "Подпись ____________  Расшифровка ____________  Дата ____________", 9)
        y -= 18

    pdf.save()
    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="protocol_{competition_id}.pdf"'},
    )
