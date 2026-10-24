#!/usr/bin/env python3
"""Мини-скрипт: расписание приёма -> JSON-план напоминаний через LLM.

Использование:
  python remind.py --version v2 --schedule "Витамин D — 1 таблетка в 09:00 после еды"
  python remind.py --version v2 --schedule "..." --out results/v2_api_run1.json
  python remind.py --check results/v2_run1.json     # только проверить готовый ответ
  python remind.py --check results/v2_run1.json --ics plan.ics   # + календарный файл
"""
import argparse
import datetime
import json
import os
import re
import sys
import uuid

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d — .+")


def validate(data):
    """Возвращает список проблем; пустой список = ответ годится."""
    problems = []
    if not isinstance(data, dict):
        return ["корень JSON — не объект"]
    if set(data) != {"title", "steps", "notes"}:
        problems.append(f"ключи {sorted(data)} != ['notes', 'steps', 'title']")
    if not isinstance(data.get("title"), str) or not data.get("title"):
        problems.append("title должен быть непустой строкой")
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        problems.append("steps должен быть непустым списком")
    else:
        for s in steps:
            if not isinstance(s, str) or not TIME_RE.match(s):
                problems.append(f"шаг не в формате «ЧЧ:ММ — ...»: {s!r}")
    notes = data.get("notes")
    if not isinstance(notes, list) or len(notes) > 3:
        problems.append("notes должен быть списком не длиннее 3")
    return problems


def ics_escape(text):
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def to_ics(data, day=None):
    """Календарный файл: каждый шаг — ежедневное событие со звуковым напоминанием."""
    day = day or datetime.date.today()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//VPf03 remind//RU", "CALSCALE:GREGORIAN"]
    for step in data["steps"]:
        time_part, text = step.split(" — ", 1)
        hh, mm = time_part.split(":")
        start = datetime.datetime.combine(day, datetime.time(int(hh), int(mm)))
        end = start + datetime.timedelta(minutes=15)
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uuid.uuid4()}@vpf03",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{start:%Y%m%dT%H%M%S}",
            f"DTEND:{end:%Y%m%dT%H%M%S}",
            "RRULE:FREQ=DAILY",
            f"SUMMARY:{ics_escape('💊 ' + text)}",
            f"DESCRIPTION:{ics_escape(data['title'])}",
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{ics_escape(text)}",
            "TRIGGER:PT0M",
            "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def save_ics(data, path):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(to_ics(data))
    print(f"📅 Календарный файл: {path} (откройте на телефоне или в календаре)")


def check(path, ics=None):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"✗ {path}: невалидный JSON ({e})")
        return 1
    problems = validate(data)
    if problems:
        print(f"✗ {path}: " + "; ".join(problems))
        return 1
    print(f"✓ {path}: JSON валиден, steps={len(data['steps'])}, notes={len(data['notes'])}")
    if ics:
        save_ics(data, ics)
    return 0


def build_messages(version, schedule):
    with open(os.path.join(PROMPTS_DIR, f"{version}.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    user = cfg["user_template"].format(schedule=schedule)
    return cfg["messages"] + [{"role": "user", "content": user}]


def ask(version, schedule, model, temperature):
    from openai import OpenAI  # ключ берётся из OPENAI_API_KEY

    client = OpenAI()
    resp = client.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=build_messages(version, schedule),
    )
    return resp.choices[0].message.content


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", choices=["v1", "v2"], default="v2")
    p.add_argument("--schedule", help="расписание приёма своими словами")
    p.add_argument("--model", default="gpt-4o-mini")
    p.add_argument("--temperature", type=float, default=0.2)
    p.add_argument("--check", metavar="FILE", help="проверить готовый JSON-файл и выйти")
    p.add_argument("--ics", metavar="FILE", help="сохранить план как календарный файл .ics (ежедневные напоминания)")
    p.add_argument("--out", metavar="FILE", help="сохранить сырой ответ модели в файл")
    args = p.parse_args()

    if args.check:
        sys.exit(check(args.check, args.ics))
    if not args.schedule:
        p.error("нужен --schedule или --check")

    raw = ask(args.version, args.schedule, args.model, args.temperature)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(raw)
        print(f"Сырой ответ сохранён в {args.out}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print("✗ Модель вернула невалидный JSON:\n" + raw)
        sys.exit(1)
    problems = validate(data)
    if problems:
        print("✗ " + "; ".join(problems))
        sys.exit(1)

    print(f"\n💊 {data['title']}\n")
    for s in data["steps"]:
        print("  ⏰", s)
    for n in data["notes"]:
        print("  •", n)
    if args.ics:
        save_ics(data, args.ics)


if __name__ == "__main__":
    main()
