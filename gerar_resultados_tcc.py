"""
Consolida os testes anotados (tela /tests) nas tabelas e figuras do TCC.

Uso (dentro da pasta do projeto, com o venv ativo):

    python gerar_resultados_tcc.py --listar
    python gerar_resultados_tcc.py <id1> <id2> <id3> --rotulos "Vídeo 1" "Vídeo 2" "Vídeo 3"

O <id> é o código de 12 caracteres do teste (aparece no final do nome da
pasta test_results/<ambiente>_<id> e na URL da revisão /tests/<id>/review).

Saída em resultados_tcc/<data_hora>/:
    tabela1_videos.csv                características dos vídeos (Tabela 1)
    tabela4_ambiente.csv              hardware e software (Tabela 4)
    tabela5_resultados_yolo.csv       TP/TN/FP/FN e métricas por vídeo (Tabela 5)
    tabela8_comparacao_rocha.csv      comparação com Rocha (2025) (Tabela 8)
    metricas_por_vaga.csv             métricas por vaga em cada vídeo
    tabelas_tcc.tex                   as mesmas tabelas em LaTeX
    matriz_confusao_<rótulo>.png      matriz no mesmo formato da Figura 8 de Rocha (2025)
    matriz_confusao_consolidada.png
    matriz_confusao_comparativa.png   este trabalho x Rocha (2025), lado a lado

Convenção: classe positiva = vaga ocupada. As colunas *_livre trazem as
métricas com vaga livre como positiva, que é a convenção que reproduz a
precisão de 60,45% publicada por Rocha (2025).
"""

import argparse
import csv
import datetime as dt
import json
import os
import sys
import unicodedata

import test_runner

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_ROOT = os.path.join(BASE_DIR, "resultados_tcc")


# ---------------------------------------------------------------------------
# utilidades
# ---------------------------------------------------------------------------

def pct(value, digits=2):
    if value is None:
        return "N/D"
    return f"{value * 100:.{digits}f}".replace(".", ",") + "%"


def num(value, digits=2):
    if value is None or value == "":
        return "N/D"
    return f"{float(value):.{digits}f}".replace(".", ",")


def duration(seconds):
    if not seconds:
        return "N/D"
    seconds = int(round(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}h{m:02d}min{s:02d}s" if h else f"{m}min{s:02d}s"


def latex_escape(text):
    text = str(text)
    for old, new in (("\\", r"\textbackslash{}"), ("&", r"\&"), ("%", r"\%"),
                     ("_", r"\_"), ("#", r"\#"), ("$", r"\$")):
        text = text.replace(old, new)
    return text


def write_csv(path, rows):
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


def list_tests():
    jobs = test_runner.list_jobs(limit=1000)
    if not jobs:
        print("Nenhum teste encontrado em test_results/.")
        return
    print(f"{'ID':<14}{'Ambiente':<26}{'Vídeo':<34}{'Status':<11}{'Anotado':<10}Acurácia")
    for job in jobs:
        metrics = test_runner.get_metrics(job["id"]) if job["status"] == "completed" else None
        annotated = ""
        accuracy = ""
        if metrics:
            evaluable = metrics["total_cells"] - metrics.get("out_of_view_cells", 0)
            done = metrics["annotated_cells"] + metrics.get("ignored_cells", 0)
            annotated = f"{done / evaluable * 100:.0f}%" if evaluable else "-"
            accuracy = pct(metrics["overall"]["accuracy"])
        source = os.path.basename(job.get("source") or "")[:32]
        print(f"{job['id']:<14}{(job['environment'] or '')[:25]:<26}{source:<34}"
              f"{job['status']:<11}{annotated:<10}{accuracy}")


# ---------------------------------------------------------------------------
# figuras
# ---------------------------------------------------------------------------

def plot_confusion(cell, title, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import seaborn as sns

    matrix = [[cell["tp"], cell["fn"]], [cell["fp"], cell["tn"]]]
    labels = ["Vaga Ocupada", "Vaga Livre"]
    fig, ax = plt.subplots(figsize=(7, 5.6))
    sns.heatmap(matrix, annot=True, fmt="d", cmap="Blues", cbar=True,
                xticklabels=labels, yticklabels=labels, ax=ax,
                annot_kws={"size": 12})
    ax.set_xlabel("Previsto")
    ax.set_ylabel("Real")
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


def plot_side_by_side(ours, rocha, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import seaborn as sns

    labels = ["Vaga Ocupada", "Vaga Livre"]
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    for ax, (cell, title) in zip(axes, ((ours, "Este trabalho"), (rocha, "Rocha (2025)"))):
        total = sum(cell[k] for k in ("tp", "fn", "fp", "tn")) or 1
        matrix = [[cell["tp"], cell["fn"]], [cell["fp"], cell["tn"]]]
        # Percentual por linha (quanto de cada classe real foi acertado),
        # o que permite comparar trabalhos com quantidades diferentes de testes.
        row_pct = [[v / (sum(row) or 1) for v in row] for row in matrix]
        annot = [[f"{matrix[i][j]}\n({row_pct[i][j] * 100:.1f}%)".replace(".", ",")
                  for j in range(2)] for i in range(2)]
        sns.heatmap(row_pct, annot=annot, fmt="", cmap="Blues", vmin=0, vmax=1,
                    cbar=False, xticklabels=labels, yticklabels=labels, ax=ax)
        ax.set_xlabel("Previsto")
        ax.set_ylabel("Real")
        ax.set_title(f"{title} (n = {total})")
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


# ---------------------------------------------------------------------------
# principal
# ---------------------------------------------------------------------------

CAMERA_NAMES = {
    "perspective": "Convencional",
    "equirectangular": "360° (equirretangular)",
    "fisheye": "360° (olho de peixe)",
    "cylindrical": "Panorâmica (cilíndrica)",
}


def gerar(ids, rotulos=None, cameras=None, permitir_incompleto=False, log=print):
    """
    Gera tabelas e figuras para os testes informados e devolve
    {"out_dir": pasta, "tabela5": linhas}. Erros viram ValueError.
    """
    ids = list(ids)
    if not ids:
        raise ValueError("Informe pelo menos um teste.")
    if rotulos and len(rotulos) != len(ids):
        raise ValueError("A quantidade de rótulos precisa ser igual à de testes.")
    if cameras and len(cameras) != len(ids):
        raise ValueError("A quantidade de câmeras precisa ser igual à de testes.")

    tests = []
    for index, job_id in enumerate(ids):
        job_dir = test_runner._resolve_job_dir(job_id)
        if not job_dir:
            raise ValueError(f"Teste {job_id} não encontrado em test_results/.")
        metrics = test_runner.compute_metrics(job_id)
        if metrics is None:
            raise ValueError(f"Teste {job_id} sem resultado (data.csv).")
        metadata = test_runner._load_metadata(job_dir)
        label = (rotulos[index] if rotulos else None) or metadata.get("label") or f"Vídeo {index + 1}"
        camera = (cameras[index] if cameras else None) or CAMERA_NAMES.get(
            metadata.get("projection"), metadata.get("projection") or "N/D")

        evaluable = metrics["total_cells"] - metrics.get("out_of_view_cells", 0)
        done = metrics["annotated_cells"] + metrics.get("ignored_cells", 0)
        if done < evaluable:
            message = (f"{label}: {done} de {evaluable} observações anotadas. "
                       "As métricas só valem para a parte anotada.")
            if not permitir_incompleto:
                raise ValueError(message + " Termine o gabarito ou marque 'permitir anotação incompleta'.")
            log("[AVISO] " + message)
        if metrics["annotated_cells"] == 0:
            raise ValueError(f"{label}: nenhuma observação anotada ainda.")
        tests.append({"id": job_id, "label": label, "camera": camera,
                      "metrics": metrics, "metadata": metadata})

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(OUT_ROOT, stamp)
    os.makedirs(out_dir, exist_ok=True)

    # ---- Tabela 1: vídeos ----
    table1 = []
    for t in tests:
        md, m = t["metadata"], t["metrics"]
        criterion = {"sobreposicao": "sobreposição ≥ 15%", "centro": "centro da caixa"}.get(
            md.get("occupancy_criterion"), md.get("occupancy_criterion") or "sobreposição ≥ 15%")
        interval_real = md.get("interval_real_s")
        speed = md.get("speed_factor") or 1
        if interval_real:
            interval_text = f"{num(interval_real, 1)} s" + (f" (vídeo {num(speed, 1)}x)" if speed != 1 else "")
        elif md.get("interval_s"):
            interval_text = f"{num(md.get('interval_s'), 1)} s"
        else:
            interval_text = f"{md.get('frame_step')} quadros"
        table1.append({
            "Vídeo": t["label"],
            "Câmera": t["camera"],
            "Duração": duration(md.get("duration_s")),
            "Resolução": f"{md.get('frame_width')}x{md.get('frame_height')}" if md.get("frame_width") else "N/D",
            "FPS": num(md.get("video_fps")),
            "Vagas": len(md.get("regions", [])),
            "Intervalo": interval_text,
            "Amostras": m["total_samples"],
            "Observações": m["annotated_cells"],
            "Critério": criterion,
            "Estabilização": "sim" if md.get("stabilize") else "não",
            "ID do teste": t["id"],
        })
    write_csv(os.path.join(out_dir, "tabela1_videos.csv"), table1)

    # ---- Tabela 4: ambiente computacional (do primeiro teste) ----
    env_info = next((t["metadata"].get("ambiente_computacional") for t in tests
                     if t["metadata"].get("ambiente_computacional")), {}) or {}
    names = {
        "sistema_operacional": "Sistema operacional", "processador": "Processador",
        "nucleos_cpu": "Núcleos lógicos", "memoria_ram_gb": "Memória RAM (GB)", "gpu": "GPU",
        "cuda": "CUDA", "python": "Python", "opencv": "OpenCV", "ultralytics": "Ultralytics",
        "torch": "PyTorch", "flask": "Flask", "flask_socketio": "Flask-SocketIO",
    }
    table4 = [{"Item": label, "Configuração": env_info[key]} for key, label in names.items() if env_info.get(key)]
    write_csv(os.path.join(out_dir, "tabela4_ambiente.csv"), table4)

    # ---- Tabela 5: resultados por vídeo + consolidado ----
    total_cell = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    table5 = []
    for t in tests:
        o = t["metrics"]["overall"]
        for key in total_cell:
            total_cell[key] += o[key]
        time_stats = t["metadata"].get("tempo_por_quadro_ms") or {}
        table5.append({
            "Vídeo": t["label"], "Amostras": t["metrics"]["total_samples"], "Observações": o["total"],
            "TP": o["tp"], "TN": o["tn"], "FP": o["fp"], "FN": o["fn"],
            "Acurácia": pct(o["accuracy"]), "Precisão": pct(o["precision"]),
            "Recall": pct(o["recall"]), "FPR": pct(o["false_positive_rate"]), "F1": pct(o["f1"]),
            "Precisão (livre+)": pct(o["precision_livre"]), "Recall (livre+)": pct(o["recall_livre"]),
            "Tempo médio/quadro (ms)": num(time_stats.get("media")),
        })
    overall = test_runner._derive_metrics(total_cell)
    table5.append({
        "Vídeo": "Consolidado", "Amostras": sum(t["metrics"]["total_samples"] for t in tests),
        "Observações": overall["total"], "TP": overall["tp"], "TN": overall["tn"],
        "FP": overall["fp"], "FN": overall["fn"], "Acurácia": pct(overall["accuracy"]),
        "Precisão": pct(overall["precision"]), "Recall": pct(overall["recall"]),
        "FPR": pct(overall["false_positive_rate"]), "F1": pct(overall["f1"]),
        "Precisão (livre+)": pct(overall["precision_livre"]),
        "Recall (livre+)": pct(overall["recall_livre"]), "Tempo médio/quadro (ms)": "",
    })
    write_csv(os.path.join(out_dir, "tabela5_resultados_yolo.csv"), table5)

    # ---- métricas por vaga ----
    per_region = []
    for t in tests:
        for name, v in t["metrics"]["per_region"].items():
            per_region.append({
                "Vídeo": t["label"], "Vaga": name, "Observações": v["total"],
                "TP": v["tp"], "TN": v["tn"], "FP": v["fp"], "FN": v["fn"],
                "Acurácia": pct(v["accuracy"]), "Precisão": pct(v["precision"]),
                "Recall": pct(v["recall"]), "FPR": pct(v["false_positive_rate"]),
            })
    write_csv(os.path.join(out_dir, "metricas_por_vaga.csv"), per_region)

    # ---- Tabela 8: comparação com Rocha (2025) ----
    rocha = test_runner._derive_metrics(dict(test_runner.ROCHA_2025))
    table8 = []
    for label, m, n in (("Rocha (2025)", rocha, rocha["total"]), ("Este trabalho", overall, overall["total"])):
        table8.append({
            "Trabalho": label, "Observações": n, "Acurácia": pct(m["accuracy"]),
            "Precisão": pct(m["precision"]), "Recall": pct(m["recall"]),
            "FPR": pct(m["false_positive_rate"]), "F1": pct(m["f1"]),
            "Precisão (livre+)": pct(m["precision_livre"]), "Recall (livre+)": pct(m["recall_livre"]),
        })
    write_csv(os.path.join(out_dir, "tabela8_comparacao_rocha.csv"), table8)

    # ---- figuras ----
    figures = []
    for t in tests:
        ascii_label = unicodedata.normalize("NFKD", t["label"]).encode("ascii", "ignore").decode()
        safe = "".join(c if c.isalnum() else "_" for c in ascii_label).strip("_").lower()
        path = os.path.join(out_dir, f"matriz_confusao_{safe}.png")
        plot_confusion(t["metrics"]["overall"], f"Matriz de confusão - {t['label']}", path)
        figures.append(path)
    plot_confusion(overall, "Matriz de confusão - Detecção de vagas (consolidada)",
                   os.path.join(out_dir, "matriz_confusao_consolidada.png"))
    plot_side_by_side(overall, rocha, os.path.join(out_dir, "matriz_confusao_comparativa.png"))

    # ---- LaTeX ----
    tex = []

    def tabular(caption, label, header, rows, align):
        tex.append(r"\begin{table}[htbp]")
        tex.append(r"\centering")
        tex.append(r"\caption{" + caption + "}")
        tex.append(r"\label{" + label + "}")
        tex.append(r"\resizebox{\textwidth}{!}{%")
        tex.append(r"\begin{tabular}{" + align + "}")
        tex.append(r"\hline")
        tex.append(" & ".join(latex_escape(h) for h in header) + r" \\ \hline")
        for row in rows:
            tex.append(" & ".join(latex_escape(c) for c in row) + r" \\")
        tex.append(r"\hline")
        tex.append(r"\end{tabular}}")
        tex.append(r"\legend{Fonte: Do autor.}")
        tex.append(r"\end{table}")
        tex.append("")

    tabular("Características dos vídeos utilizados nos experimentos", "tab:videos",
            ["Vídeo", "Câmera", "Duração", "Resolução", "FPS", "Intervalo", "Vagas", "Amostras", "Observações"],
            [[r["Vídeo"], r["Câmera"], r["Duração"], r["Resolução"], r["FPS"], r["Intervalo"], r["Vagas"],
              r["Amostras"], r["Observações"]] for r in table1], "lllllcccc")
    tabular("Configuração computacional utilizada nos experimentos", "tab:ambiente",
            ["Item", "Configuração"], [[r["Item"], r["Configuração"]] for r in table4], "ll")
    tabular("Resultados dos testes realizados com YOLO", "tab:resultados-yolo",
            ["Vídeo", "Am.", "TP", "TN", "FP", "FN", "Acc.", "Prec.", "Rec.", "FPR"],
            [[r["Vídeo"], r["Amostras"], r["TP"], r["TN"], r["FP"], r["FN"], r["Acurácia"],
              r["Precisão"], r["Recall"], r["FPR"]] for r in table5], "lccccccccc")
    tex.append(r"% Tabela 6 - matriz de confusão consolidada")
    tabular("Matriz de confusão consolidada", "tab:matriz",
            ["", "Predito: Ocupada", "Predito: Livre"],
            [["Real: Ocupada", overall["tp"], overall["fn"]],
             ["Real: Livre", overall["fp"], overall["tn"]]], "lcc")
    tabular("Comparação dos resultados com o trabalho de Rocha (2025)", "tab:comparacao-rocha",
            ["Trabalho", "Acurácia", "Precisão", "Recall", "FPR", "Precisão (livre)"],
            [[r["Trabalho"], r["Acurácia"], r["Precisão"], r["Recall"], r["FPR"], r["Precisão (livre+)"]]
             for r in table8], "lccccc")
    with open(os.path.join(out_dir, "tabelas_tcc.tex"), "w", encoding="utf-8") as file:
        file.write("% Gerado por gerar_resultados_tcc.py em " + stamp + "\n")
        file.write("% Requer \\usepackage{graphicx} (resizebox). \\legend é do abnTeX2.\n\n")
        file.write("\n".join(tex))

    with open(os.path.join(out_dir, "resumo.json"), "w", encoding="utf-8") as file:
        json.dump({"testes": [{k: t[k] for k in ("id", "label", "camera")} for t in tests],
                   "consolidado": overall, "rocha_2025": rocha}, file, ensure_ascii=False, indent=2)

    log(f"Resultados gerados em: {out_dir}")
    return {"out_dir": out_dir, "tabela5": table5, "rocha": rocha}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ids", nargs="*", help="IDs dos testes, na ordem dos vídeos do TCC")
    parser.add_argument("--rotulos", nargs="*", help="Nomes para cada teste (padrão: rótulo dado na tela)")
    parser.add_argument("--cameras", nargs="*", help="Tipo de câmera de cada vídeo (padrão: do ambiente)")
    parser.add_argument("--listar", action="store_true", help="Lista os testes existentes e sai")
    parser.add_argument("--permitir-incompleto", action="store_true",
                        help="Gera mesmo que algum teste não esteja 100%% anotado")
    args = parser.parse_args()

    if args.listar or not args.ids:
        list_tests()
        if not args.ids:
            print("\nInforme os IDs dos testes. Ex.: python gerar_resultados_tcc.py abc123 def456")
        return
    try:
        result = gerar(args.ids, args.rotulos, args.cameras, args.permitir_incompleto)
    except ValueError as error:
        sys.exit(str(error))
    print()
    for row in result["tabela5"]:
        print(f"  {row['Vídeo']:<20} n={row['Observações']:<7} acc={row['Acurácia']:<8} "
              f"prec={row['Precisão']:<8} rec={row['Recall']:<8} FPR={row['FPR']}")
    rocha = result["rocha"]
    print(f"  {'Rocha (2025)':<20} n={rocha['total']:<7} acc={pct(rocha['accuracy']):<8} "
          f"prec={pct(rocha['precision']):<8} rec={pct(rocha['recall']):<8} FPR={pct(rocha['false_positive_rate'])}")


if __name__ == "__main__":
    main()
