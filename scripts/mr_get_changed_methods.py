#!/usr/bin/env python3
"""Находит изменённые методы в .cs файлах между ветками Merge Request (GitLab CI).

Запуск: mr-get-changed-methods
Требуется: переменные окружения CI_MERGE_REQUEST_SOURCE_BRANCH_NAME и
CI_MERGE_REQUEST_TARGET_BRANCH_NAME, а также git-репозиторий в CWD.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

# Регулярка для сигнатуры метода (упрощённая, покрывает большинство случаев)
METHOD_SIGNATURE_PATTERN = re.compile(
    r'^\s*(?:public|private|internal|protected)\s+(?:static\s+)?'
    r'(?:[A-Za-z0-9_.<>[\]]+\s+)+'  # возвращаемый тип
    r'([A-Za-z0-9_]+)\s*\(',       # имя метода (группа 1)
    re.MULTILINE
)

def run_git(cmd: list[str], cwd: str | None = None) -> str:
    result = subprocess.run(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True
    )
    return result.stdout


def resolve_ref(branch: str) -> str:
    """
    Вернуть доступный git-ref для имени ветки.

    GitLab CI делает checkout только исходной ветки MR (она есть как локальная),
    а целевая ветка доступна лишь как origin/<name> (и/или FETCH_HEAD после
    git fetch). Поэтому git diff ...master падает с exit 128, если локальной
    ветки master нет. Пробуем по порядку: локальная ветка -> origin/<branch>
    -> FETCH_HEAD -> branch как есть.
    """
    for candidate in (branch, f"origin/{branch}", "FETCH_HEAD"):
        probe = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if probe.returncode == 0:
            return candidate
    # Ничего не резолвилось — вернём исходное имя, чтобы git сам выдал понятную ошибку
    return branch

def get_changed_cs_files(source_branch: str, target_branch: str) -> list[Path]:
    """Получить список изменённых .cs файлов.

    Используем две точки (..), а не три (...): для MR, где исходная ветка
    является потомком целевой, merge-base совпадает с целевой, и A...B
    вернёт пусто. Две точки показывают все различия между ветками.
    """
    source_ref = resolve_ref(source_branch)
    target_ref = resolve_ref(target_branch)

    output = run_git([
        "git", "diff", "--name-only", f"{source_ref}..{target_ref}"
    ])
    
    files = [Path(line.strip()) for line in output.splitlines() if line.strip()]
    return [f for f in files if f.suffix.lower() == ".cs"]

def get_file_content(branch: str, file_path: Path) -> str:
    """Получить содержимое файла на указанной ветке"""
    try:
        return run_git(["git", "show", f"{resolve_ref(branch)}:{file_path}"])
    except subprocess.CalledProcessError:
        # Если файла нет (например, удалён) — вернём пустую строку
        return ""

def find_methods_with_bodies(text: str):
    """
    Найти все методы с телами и их диапазоны строк.
    Возвращает список кортежей: (имя_метода, start_line, end_line, код_метода)
    """
    lines = text.splitlines(keepends=True)  # сохраняем \n для точного восстановления
    results = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        m = METHOD_SIGNATURE_PATTERN.match(stripped)
        if m:
            method_name = m.group(1)
            start_line = i + 1

            brace_depth = 0
            # Проверяем, есть ли '{' уже в этой строке
            if "{" in stripped:
                brace_depth += stripped.count("{")
                brace_depth -= stripped.count("}")

            j = i + 1
            # Случай: тело на одной строке "{ ... }"
            if brace_depth == 0 and "{" in stripped and "}" in stripped:
                end_line = start_line
                method_code = line
                results.append((method_name, start_line, end_line, method_code))
                i = j
                continue

            # Ищем конец тела
            while j < len(lines) and (brace_depth > 0 or j == i + 1):
                current_line = lines[j]
                brace_depth += current_line.count("{")
                brace_depth -= current_line.count("}")
                if brace_depth == 0:
                    end_line = j + 1
                    # Собираем код от start_line до end_line включительно
                    method_code = "".join(lines[i:j+1])
                    results.append((method_name, start_line, end_line, method_code))
                    i = j + 1
                    break
                j += 1
            else:
                # Не нашли закрывающую скобку (неполный файл или ошибка)
                i += 1
            continue

        i += 1

    return results

def get_changed_line_ranges(source_branch: str, target_branch: str, file_path: Path):
    """
    Получить диапазоны изменённых строк в новом файле (source_branch).
    Формат: список (start, end) в нумерации новой версии.
    Используем git diff -U0, чтобы получить только изменённые строки.
    """
    diff_output = run_git([
        "git", "diff", "-U0",
        resolve_ref(target_branch), resolve_ref(source_branch),
        "--", str(file_path)
    ])

    changed_ranges = []
    for line in diff_output.splitlines():
        if line.startswith("@@"):
            import re as regex
            m = regex.match(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', line)
            if not m:
                continue
            new_start = int(m.group(3))
            new_count = int(m.group(4)) if m.group(4) else 1
            new_end = new_start + new_count - 1
            changed_ranges.append((new_start, new_end))

    return changed_ranges

def ranges_overlap(a_start, a_end, b_start, b_end):
    """Проверить пересечение двух диапазонов [start, end] включительно"""
    return a_start <= b_end and b_start <= a_end

def main():
    source_branch = os.getenv("CI_MERGE_REQUEST_SOURCE_BRANCH_NAME")
    target_branch = os.getenv("CI_MERGE_REQUEST_TARGET_BRANCH_NAME")
    
    if not source_branch or not target_branch:
        print(
            "Ошибка: не найдены переменные CI_MERGE_REQUEST_SOURCE_BRANCH_NAME и/или CI_MERGE_REQUEST_TARGET_BRANCH_NAME",
            file=sys.stderr
        )
        print("Этот скрипт предназначен для запуска в GitLab CI при Merge Request.", file=sys.stderr)
        sys.exit(1)

    changed_files = get_changed_cs_files(source_branch, target_branch)
    if not changed_files:
        print("Изменённых .cs файлов не найдено.")
        return

    for file_path in changed_files:
        new_text = get_file_content(source_branch, file_path)
        if not new_text.strip():
            continue

        methods_new = find_methods_with_bodies(new_text)
        changed_ranges = get_changed_line_ranges(source_branch, target_branch, file_path)

        for method_name, m_start, m_end, method_code in methods_new:
            is_changed = False
            for c_start, c_end in changed_ranges:
                if ranges_overlap(m_start, m_end, c_start, c_end):
                    is_changed = True
                    break

            if is_changed:
                print(f"{file_path}, {method_name}(), {m_start}–{m_end}", end="\n")
                # Выводим полный код метода.
                # Чтобы вывод был аккуратнее, можно убрать лишние пустые строки сверху/снизу,
                # но здесь оставляем как есть — с сохранением отступов и переносов.
                print(method_code, end="\n\n")
                # Для визуального разделения между методами можно добавить пустую строку:
                print()

if __name__ == "__main__":
    main()
