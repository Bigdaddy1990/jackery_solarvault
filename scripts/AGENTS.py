#!/usr/bin/env python3
"""
BOTTEST.py - Home Assistant Workflow & Compliance Matrix Enforcer
Kompatibel mit Windows und Linux.
Dieses Skript erzwingt zwingend die Prüf- und Fixworkflows basierend auf
den Richtlinien für die Jahre 2025/2026.
"""

import os
import sys
import re
import time
import argparse
from pathlib import Path

# Plattformübergreifende ANSI-Farbzuweisungen
class TerminalColors:
    HEADER = '\033
    try:
        with open(filepath, 'r', encoding='utf-8') as file_obj:
            content = file_obj.read()
            for rule_name, regex_pattern in patterns.items():
                match = re.search(regex_pattern, content)
                if not require_match and match:
                    detected_issues.append(f"Verbotenes Muster detektiert: [{rule_name}]")
                elif require_match and not match:
                    detected_issues.append(f"Erzwungenes Muster fehlt: [{rule_name}]")
    except Exception as read_error:
        detected_issues.append(f"Dateizugriffsfehler: {str(read_error)}")
    return detected_issues

def enforce_repository_compliance(repo_directory: Path) -> int:
    """Durchläuft das Repository rekursiv zur Durchsetzung der Code-Richtlinien."""
    print(f"{TerminalColors.OKBLUE}[*] Starte statische Code-Analyse im Verzeichnis: {repo_directory}{TerminalColors.ENDC}")
    python_files = list(repo_directory.rglob("*.py"))
    yaml_files = list(repo_directory.rglob("*.yaml"))

    violation_count = 0

    print(f"\n{TerminalColors.BOLD}--- Überprüfung der Python-Integrationen ---{TerminalColors.ENDC}")
    for script_file in python_files:
        if "venv" in str(script_file) or ".tox" in str(script_file):
            continue

        deprecation_issues = analyze_file_content(script_file, RESTRICTED_PATTERNS, require_match=False)
        mandatory_issues = analyze_file_content(script_file, MANDATORY_PATTERNS, require_match=True)

        combined_issues = deprecation_issues + mandatory_issues
        if combined_issues:
            print(f"{TerminalColors.FAIL} In Datei: {script_file.name}{TerminalColors.ENDC}")
            for issue in combined_issues:
                print(f"    -> {issue}")
                violation_count += 1

    print(f"\n{TerminalColors.BOLD}--- Überprüfung der YAML-Konfigurationen ---{TerminalColors.ENDC}")
    for config_file in yaml_files:
        yaml_issues = analyze_file_content(config_file, {"Legacy Template": r"platform:\s*template"}, require_match=False)
        if yaml_issues:
            print(f"{TerminalColors.WARNING} In Datei: {config_file.name}{TerminalColors.ENDC}")
            for issue in yaml_issues:
                print(f"    -> {issue} (Wird in Release 2026.6 zu Systemausfällen führen)")
                violation_count += 1

    return violation_count

def parse_runtime_logs(log_file_path: Path) -> int:
    """Parst die Home Assistant Logs auf Laufzeit-Deprecations und Fehler."""
    print(f"\n{TerminalColors.OKBLUE}[*] Initiiere Protokoll-Analyse: {log_file_path}{TerminalColors.ENDC}")
    if not log_file_path.exists():
        print(f"{TerminalColors.WARNING}[-] Logdatei nicht auffindbar. Überspringe dynamische Laufzeitanalyse.{TerminalColors.ENDC}")
        return 0

    critical_runtime_errors = 0
    with open(log_file_path, 'r', encoding='utf-8') as log_data:
        for line_index, log_line in enumerate(log_data, 1):
            if "homeassistant.helpers.frame" in log_line and "deprecated" in log_line:
                print(f"{TerminalColors.FAIL}Laufzeit-Deprecation [Zeile {line_index}]: {log_line.strip()[:110]}...{TerminalColors.ENDC}")
                critical_runtime_errors += 1
            elif "AttributeError" in log_line and "async_subscribe" in log_line:
                print(f"{TerminalColors.FAIL}MQTT Namespace-Verletzung [Zeile {line_index}]: {log_line.strip()[:110]}...{TerminalColors.ENDC}")
                critical_runtime_errors += 1

    return critical_runtime_errors

def execute_matrix(repo_path: Path, log_path: Path):
    """Hauptausführungslogik für den Prüf- und Fixworkflow."""
    start_timestamp = time.time()
    clear_terminal()
    render_matrix_banner()

    repo_errors = enforce_repository_compliance(repo_path)
    log_errors = parse_runtime_logs(log_path)

    total_violations = repo_errors + log_errors

    print(f"\n{TerminalColors.BOLD}{'='*70}{TerminalColors.ENDC}")
    print(f"{TerminalColors.BOLD} ERGEBNISSE DER WORKFLOW-MATRIX {TerminalColors.ENDC}")
    print(f"{TerminalColors.BOLD}{'='*70}{TerminalColors.ENDC}")

    execution_duration = time.time() - start_timestamp
    print(f" Ausführungsdauer     : {execution_duration:.2f} Sekunden")
    print(f" Statische Verstöße   : {repo_errors}")
    print(f" Laufzeit-Fehler      : {log_errors}")

    if total_violations > 0:
        print(f"\n{TerminalColors.FAIL} Die Codebasis verletzt die Home Assistant Matrix für 2025/2026.{TerminalColors.ENDC}")
        print("Konsultieren Sie zwingend TOFIX.html und FAHRPLAN.html für die erforderlichen Sanierungsschritte.")
        sys.exit(1)
    else:
        print(f"\n{TerminalColors.OKGREEN} Die Systemarchitektur erfüllt alle strikten Vorgaben.{TerminalColors.ENDC}")
        sys.exit(0)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HA Workflow Matrix Enforcer")
    parser.add_argument("--repo", default=".", help="Pfad zum custom_components Verzeichnis")
    parser.add_argument("--log", default="home-assistant.log", help="Pfad zur System-Logdatei")
    args = parser.parse_args()

    execute_matrix(Path(args.repo), Path(args.log))
