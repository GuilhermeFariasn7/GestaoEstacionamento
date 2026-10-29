#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Avaliação experimental do TCC (mesmo método de Rocha, 2025).

Fluxo completo, em 4 passos, por vídeo:

  1) amostrar   roda o YOLO a cada N segundos (padrão 5 s, como o João) e salva
                as previsões + os quadros para o gabarito manual.
  2) (manual)   abre o arquivo  revisar.html  no navegador e marca o que
                realmente aparece em cada vaga (sem ver o resultado do YOLO).
  3) metricas   cruza previsões x gabarito: matriz de confusão, acurácia,
                precisão, recall, FPR, tempo por quadro, imagens dos erros e
                as tabelas LaTeX prontas para colar no artigo.
  4) sistema    (opcional) imprime as versões/hardware para a tabela de
                configuração computacional.

Exemplos (rode dentro da pasta do projeto, com o venv ativado):

  python avaliacao_tcc.py amostrar --ambiente MEU_AMBIENTE --video D:/videos/v1.mp4 --rotulo v1 --camera "Convencional" --periodo manha
  python avaliacao_tcc.py metricas
  python avaliacao_tcc.py sistema

Tudo é gravado em  avaliacao/<rotulo>/  e  avaliacao/resultado/.
"""
import argparse
import csv
import glob
import json
import math
import os
import platform
import re
import shutil
import sys
import time
from datetime import datetime

try:  # console do Windows (cp1252) não aceita alguns caracteres
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PASTA = os.path.join(BASE_DIR, "avaliacao")
PASTA_RESULTADO = os.path.join(PASTA, "resultado")
LARGURA_MAX_IMAGEM = 1600

# Matriz de confusão publicada por Rocha (2025), Figura 8.
# Linhas = real, colunas = previsto. Positivo = vaga OCUPADA.
ROCHA = {"TP": 16141, "FN": 2330, "FP": 0, "TN": 3561,
         "acc_publicada": 0.8946, "prec_publicada": 0.6045}


# ----------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------
def erro(msg):
    print("\n[ERRO] " + msg + "\n")
    sys.exit(1)


def slug(texto):
    texto = re.sub(r"[^A-Za-z0-9_-]+", "-", str(texto)).strip("-")
    return texto or "video"


def fmt_hms(seg):
    seg = int(round(float(seg or 0)))
    return "%02d:%02d:%02d" % (seg // 3600, (seg % 3600) // 60, seg % 60)


def safe_div(a, b):
    return (a / b) if b else None


def pct_tex(x):
    return "---" if x is None else ("%.2f" % (x * 100)).replace(".", ",") + "\\%"


def pct_txt(x):
    return "n/d" if x is None else ("%.2f%%" % (x * 100)).replace(".", ",")


def tex(s):
    return (str(s).replace("\\", "\\textbackslash{}").replace("_", "\\_")
            .replace("%", "\\%").replace("&", "\\&").replace("#", "\\#"))


def versao_pacote(nome):
    try:
        from importlib.metadata import version
        return version(nome)
    except Exception:
        return "n/d"


def calcular(tp, tn, fp, fn):
    total = tp + tn + fp + fn
    return {
        "TP": tp, "TN": tn, "FP": fp, "FN": fn, "observacoes": total,
        "acuracia": safe_div(tp + tn, total),
        "precisao": safe_div(tp, tp + fp),            # classe ocupada
        "recall": safe_div(tp, tp + fn),              # classe ocupada
        "fpr": safe_div(fp, fp + tn),
        # Rocha (2025) publicou 60,45%, que equivale à precisão da classe LIVRE
        "precisao_livre": safe_div(tn, tn + fn),
        "recall_livre": safe_div(tn, tn + fp),
    }


# ----------------------------------------------------------------------
# Passo 1 - amostrar
# ----------------------------------------------------------------------
def criar_contador(coordenadas, modelo, confianca, estabilizador):
    """Mesma configuração usada pelo monitoramento e pela tela de testes."""
    from regionCounterLive import RegionCounterLive  # importa o ultralytics aqui
    return RegionCounterLive(region=coordenadas, model=modelo, classes=[2],
                             conf=confianca, stabilizer=estabilizador)


def _partes_poligono(geometria):
    """Lista de contornos [(x,y),...] de um polígono shapely (ou lista de pontos)."""
    if isinstance(geometria, (list, tuple)):
        return [list(geometria)]
    partes = []
    for g in getattr(geometria, "geoms", [geometria]):
        ext = getattr(g, "exterior", None)
        if ext is not None and len(ext.coords) >= 3:
            partes.append([(p[0], p[1]) for p in ext.coords])
    return partes


def _desenhar_vagas(imagem, contador, coordenadas, escala):
    """Desenha SÓ o contorno e o nome das vagas (sem o resultado do YOLO)."""
    import cv2
    import numpy as np
    regioes = getattr(contador, "counting_regions", None)
    if regioes:
        itens = [(r["name"], r.get("polygon", r.get("points"))) for r in regioes]
    else:
        itens = list(coordenadas.items())
    fonte = max(0.5, imagem.shape[1] / 1700.0)
    esp = max(1, int(round(fonte * 2)))
    for nome, geom in itens:
        for parte in _partes_poligono(geom):
            pts = np.array([[p[0] * escala, p[1] * escala] for p in parte], dtype=np.int32)
            cv2.polylines(imagem, [pts], True, (0, 255, 255), esp + 1, cv2.LINE_AA)
            x, y = int(pts[:, 0].min()), int(pts[:, 1].min())
            y = max(y, int(24 * fonte))
            cv2.putText(imagem, str(nome), (x + 2, y - 4), cv2.FONT_HERSHEY_SIMPLEX,
                        fonte, (0, 0, 0), esp + 2, cv2.LINE_AA)
            cv2.putText(imagem, str(nome), (x + 2, y - 4), cv2.FONT_HERSHEY_SIMPLEX,
                        fonte, (0, 255, 255), esp, cv2.LINE_AA)


def _info_ambiente_python():
    info = {"python": platform.python_version(), "opencv": None,
            "ultralytics": versao_pacote("ultralytics"), "torch": versao_pacote("torch")}
    try:
        import cv2
        info["opencv"] = cv2.__version__
    except Exception:
        pass
    try:
        import torch
        info["cuda"] = bool(torch.cuda.is_available())
        if info["cuda"]:
            info["gpu"] = torch.cuda.get_device_name(0)
    except Exception:
        info["cuda"] = False
    return info


def cmd_amostrar(a):
    import cv2
    import regions_store as store
    from roi_stabilizer import build_stabilizer_for_environment

    ambiente = a.ambiente
    if store.get_environment(ambiente) is None:
        disp = ", ".join(store.list_environments()) or "(nenhum)"
        erro("Ambiente '%s' não existe. Ambientes salvos: %s" % (ambiente, disp))
    try:
        coordenadas = store.get_regions_for_program(ambiente)
    except ValueError as e:
        erro(str(e))
    if not coordenadas:
        erro("O ambiente '%s' não tem nenhuma vaga configurada (abra /roi e desenhe as vagas)." % ambiente)

    video = str(a.video).strip().strip('"')
    if not video.isdigit() and not os.path.exists(video):
        erro("Vídeo não encontrado: %s" % video)

    rotulo = slug(a.rotulo or os.path.splitext(os.path.basename(video))[0])
    pasta = os.path.join(PASTA, rotulo)
    if os.path.exists(os.path.join(pasta, "previsoes.csv")) and not a.continuar:
        if a.refazer:
            destino = pasta + "_antigo_" + datetime.now().strftime("%Y%m%d_%H%M%S")
            shutil.move(pasta, destino)
            print("[OK] Resultado anterior movido para: %s" % destino)
        else:
            erro("Já existe uma avaliação '%s'.\n"
                 "  --continuar  retoma de onde parou\n"
                 "  --refazer    guarda a antiga numa pasta *_antigo_* e começa de novo\n"
                 "  ou use outro --rotulo." % rotulo)
    os.makedirs(os.path.join(pasta, "para_anotar"), exist_ok=True)

    config = store.get_detection_config(ambiente) or {}
    confianca = a.conf if a.conf is not None else float(config.get("confidence", 0.4))
    estab = build_stabilizer_for_environment(
        ambiente, coordenadas, enabled=True if a.estabilizar else None,
        log=lambda m: print("[ESTAB] " + str(m)))

    cap = cv2.VideoCapture(int(video) if video.isdigit() else video)
    if not cap.isOpened():
        erro("Não foi possível abrir o vídeo: %s" % video)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
    if fps <= 0 or fps > 240:
        print("[AVISO] FPS do vídeo não informado; assumindo 30.")
        fps = 30.0
    largura = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    altura = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    duracao = total / fps if total > 0 else None

    passo = a.intervalo * fps                      # frames entre duas amostras
    n_total = (int((total - 1) // passo) + 1) if total > 0 else None
    if a.max_amostras and (n_total is None or a.max_amostras < n_total):
        n_total = a.max_amostras

    caminho_prev = os.path.join(pasta, "previsoes.csv")
    caminho_det = os.path.join(pasta, "deteccoes.jsonl")
    feitas = set()
    if os.path.exists(caminho_prev):
        with open(caminho_prev, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                feitas.add(int(r["amostra"]))
    feitas_idx = {x - 1 for x in feitas}
    novo_csv = not os.path.exists(caminho_prev)

    if not os.path.exists(os.path.join(BASE_DIR, a.modelo)):
        print("[AVISO] %s não está na pasta do projeto; o ultralytics tentará baixar (precisa de internet)." % a.modelo)
    print("Carregando o modelo %s ..." % a.modelo)
    contador = criar_contador(coordenadas, a.modelo, confianca, estab)

    meta_path = os.path.join(pasta, "metadata.json")
    meta = {
        "rotulo": rotulo, "ambiente": ambiente, "video": video,
        "camera": a.camera or "", "periodo": a.periodo or "",
        "fps": fps, "frames_total": total, "duracao_s": duracao,
        "largura": largura, "altura": altura,
        "intervalo_s": a.intervalo, "aquecimento_s": a.aquecimento,
        "confianca": confianca, "modelo": a.modelo, "classes": [2],
        "estabilizacao": bool(estab is not None),
        "projecao": getattr(estab, "projection", None) if estab is not None else
        (config.get("projection") or "perspective"),
        "vagas": list(coordenadas.keys()),
        "iniciado_em": datetime.now().isoformat(timespec="seconds"),
        "software": _info_ambiente_python(),
    }
    if os.path.exists(meta_path):                   # --continuar: preserva os dados originais
        try:
            with open(meta_path, encoding="utf-8") as f:
                antigo = json.load(f)
            meta["iniciado_em"] = antigo.get("iniciado_em", meta["iniciado_em"])
        except Exception:
            pass
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print("\nVídeo: %s  (%dx%d, %.2f fps, %s)" % (os.path.basename(video), largura, altura, fps,
                                                  fmt_hms(duracao) if duracao else "duração desconhecida"))
    print("Ambiente: %s | %d vagas | confiança %.2f | uma amostra a cada %g s" %
          (ambiente, len(coordenadas), confianca, a.intervalo))
    print("Amostras previstas: %s\n" % (n_total if n_total else "?"))

    pos = [0]                                       # próximo frame que a captura vai ler

    def ler(alvo):
        while pos[0] < alvo:
            if not cap.grab():
                return None
            pos[0] += 1
        ok, quadro = cap.read()
        if not ok:
            return None
        pos[0] += 1
        return quadro

    f_prev = open(caminho_prev, "a", newline="", encoding="utf-8-sig")
    f_det = open(caminho_det, "a", encoding="utf-8")
    w = csv.writer(f_prev)
    if novo_csv:
        w.writerow(["amostra", "tempo_s", "frame", "vaga", "previsao", "visivel", "tempo_ms", "imagem"])

    inicio = time.time()
    tempos = []
    k = 0
    frame_anterior = -1
    processadas = 0
    terminou_antes = False
    try:
        while n_total is None or k < n_total:
            alvo = int(round(k * passo))
            if total > 0 and alvo >= total:
                break
            aq = int(round(alvo - a.aquecimento * fps)) if a.aquecimento > 0 else None
            if k in feitas_idx:                     # já processada numa execução anterior
                avanc = ler(alvo)
                if avanc is None:
                    terminou_antes = True
                    break
                frame_anterior = alvo
                k += 1
                continue
            # Quadro de aquecimento: o rastreador do YOLO só "confirma" um carro novo no
            # 2º quadro em que ele aparece; sem isso o 1º quadro de cada chegada falharia.
            if aq is not None and aq > frame_anterior and aq < alvo and k > 0:
                q_aq = ler(aq)
                if q_aq is not None:
                    contador.process(q_aq)
            quadro = ler(alvo)
            if quadro is None:
                terminou_antes = True
                break
            frame_anterior = alvo
            limpo = quadro.copy()
            t0 = time.perf_counter()
            contador.process(quadro)
            dt_ms = (time.perf_counter() - t0) * 1000.0
            tempos.append(dt_ms)

            contagens = getattr(contador, "region_counts", {}) or {}
            visib = getattr(contador, "region_visibility", {}) or {}
            sid = k + 1
            tempo_s = alvo / fps
            nome_img = "amostra_%05d.jpg" % sid

            escala = 1.0
            vista = limpo
            if limpo.shape[1] > LARGURA_MAX_IMAGEM:
                escala = LARGURA_MAX_IMAGEM / float(limpo.shape[1])
                vista = cv2.resize(limpo, (LARGURA_MAX_IMAGEM, int(round(limpo.shape[0] * escala))),
                                   interpolation=cv2.INTER_AREA)
            _desenhar_vagas(vista, contador, coordenadas, escala)
            cv2.imwrite(os.path.join(pasta, "para_anotar", nome_img), vista, [cv2.IMWRITE_JPEG_QUALITY, 82])

            caixas = []
            try:
                for b in (getattr(contador, "boxes", None) if getattr(contador, "boxes", None) is not None else []):
                    caixas.append([round(float(v), 1) for v in list(b)[:4]])
            except Exception:
                caixas = []
            f_det.write(json.dumps({"amostra": sid, "escala": escala, "caixas": caixas}) + "\n")
            f_det.flush()

            ocupadas = 0
            for nome in coordenadas:
                visivel = bool(visib.get(nome, True))
                if not visivel:
                    prev = "fora_de_vista"
                elif int(contagens.get(nome, 0) or 0) > 0:
                    prev = "ocupada"
                    ocupadas += 1
                else:
                    prev = "livre"
                w.writerow([sid, "%.3f" % tempo_s, alvo, nome, prev, int(visivel), "%.1f" % dt_ms, nome_img])
            f_prev.flush()

            processadas += 1
            decorrido = time.time() - inicio
            restante = ""
            if n_total:
                eta = decorrido / processadas * (n_total - k - 1)
                restante = " | faltam ~%s" % fmt_hms(eta)
            print("[%d/%s] t=%s  ocupadas %d/%d  (%.0f ms)%s" % (
                sid, n_total or "?", fmt_hms(tempo_s), ocupadas, len(coordenadas), dt_ms, restante))
            k += 1
    except KeyboardInterrupt:
        print("\n[!] Interrompido. O que já foi processado está salvo; use --continuar para retomar.")
    finally:
        f_prev.close()
        f_det.close()
        cap.release()

    with open(caminho_prev, newline="", encoding="utf-8-sig") as f:
        todas = {int(r["amostra"]): float(r["tempo_ms"]) for r in csv.DictReader(f)}
    meta["amostras"] = len(todas)
    meta["tempo_medio_ms"] = (sum(todas.values()) / len(todas)) if todas else None
    meta["terminou_antes_do_fim_do_video"] = terminou_antes
    meta["finalizado_em"] = datetime.now().isoformat(timespec="seconds")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    gerar_revisao(pasta)
    print("\nConcluído: %d amostras em %s." % (len(todas), fmt_hms(time.time() - inicio)))
    print("PRÓXIMO PASSO: abra no navegador (duplo clique)\n   %s" % os.path.join(pasta, "revisar.html"))
    print("e marque o gabarito. Depois rode:  python avaliacao_tcc.py metricas")


# ----------------------------------------------------------------------
# Passo 2 - página de gabarito manual (offline, sem servidor)
# ----------------------------------------------------------------------
HTML_REVISAR = r"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gabarito - __ROTULO__</title>
<style>
*{box-sizing:border-box}body{margin:0;font-family:Arial,Helvetica,sans-serif;background:#12131a;color:#e8e9ee}
header{display:flex;gap:16px;align-items:center;padding:10px 16px;background:#1b1d29;border-bottom:1px solid #2c2f40;flex-wrap:wrap}
header h1{font-size:16px;margin:0}#info{font-size:15px;font-weight:bold}#msg{font-size:13px;color:#9aa0b5}
main{display:flex;gap:14px;padding:14px;align-items:flex-start}
#palco{flex:1;min-width:0;background:#000;border-radius:8px;text-align:center}
#img{max-width:100%;max-height:calc(100vh - 150px);cursor:zoom-in}
aside{width:340px;flex:none}
.vaga{display:flex;align-items:center;gap:6px;background:#1b1d29;border:1px solid #2c2f40;border-radius:8px;padding:6px 8px;margin-bottom:6px}
.vaga .n{width:18px;color:#9aa0b5;font-size:12px}.vaga .nome{flex:1;font-weight:bold;font-size:14px;overflow:hidden;text-overflow:ellipsis}
button{font:inherit;border:1px solid #3a3e55;background:#262a3c;color:#e8e9ee;border-radius:6px;padding:5px 9px;cursor:pointer}
button:hover{background:#31364d}
.btn-o.on{background:#c62839;border-color:#ff5468}.btn-l.on{background:#12805a;border-color:#2fd9a0}
.vaga .ev{width:12px;color:#ffd43b;font-size:14px;text-align:center}
.x{padding:3px 7px;color:#9aa0b5}
.nav{display:flex;gap:6px;margin:10px 0;flex-wrap:wrap}.nav button{flex:1}
#linha{width:100%;background:#1b1d29;border-radius:6px;cursor:pointer;margin-top:6px}
.dica{font-size:12px;color:#9aa0b5;line-height:1.5;margin-top:10px}.dica b{color:#e8e9ee}
.baixar{background:#2b6fe0;border-color:#5b95ff;width:100%;margin-top:8px;padding:9px}
#faltam{font-size:13px;margin-top:8px}.warn{color:#ffb454}
</style></head><body>
<header><h1>Gabarito manual - __ROTULO__</h1><span id="info"></span><span id="msg"></span></header>
<main>
 <div id="palco"><img id="img" alt="quadro"></div>
 <aside>
  <div class="nav">
   <button id="bPrev">&larr; Anterior</button><button id="bNext">Próximo &rarr;</button>
   <button id="bMenos">-10</button><button id="bMais">+10</button>
  </div>
  <div id="vagas"></div>
  <canvas id="linha" height="10"></canvas>
  <div id="faltam"></div>
  <button class="baixar" id="bBaixar">Baixar gabarito (gabarito.csv)</button>
  <button id="bImportar" style="width:100%;margin-top:6px">Importar um gabarito.csv salvo antes</button>
  <input type="file" id="arq" accept=".csv" style="display:none">
  <div class="dica">
   <b>Como marcar:</b> o estado vale <b>deste quadro em diante</b> até você marcar outro. Então basta marcar
   no <b>primeiro quadro</b> o estado de todas as vagas e depois só mexer nos quadros em que um carro
   <b>chega ou sai</b>. Errou? Volte ao quadro e marque de novo (ou clique no &times;).<br><br>
   <b>Atalhos:</b> &larr; &rarr; navegam | <b>1-9</b> alterna a vaga de mesmo número (ocupada/livre) |
   Z desfaz | clique na imagem para abrir em tamanho original.<br><br>
   Salva sozinho neste navegador. Ao terminar, baixe o CSV e coloque-o nesta pasta com o nome
   <b>gabarito.csv</b>.
  </div>
 </aside>
</main>
<script>
// ==LOGICA==
function preencher(eventos, n){
  const out = new Array(n).fill(null); let atual = null;
  for (let i = 0; i < n; i++){ if (eventos[i] !== undefined) atual = eventos[i]; out[i] = atual; }
  return out;
}
function eventosDeEstados(estados){
  const ev = {}; let prev = null;
  for (let i = 0; i < estados.length; i++){
    const s = estados[i];
    if (s){ if (s !== prev) ev[i] = s; prev = s; }
  }
  return ev;
}
function aspas(v){ v = String(v); return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; }
function montarCSV(amostras, vagas, estados){
  const linhas = ["amostra,tempo_s,vaga,estado_real"];
  for (let i = 0; i < amostras.length; i++){
    for (const v of vagas){
      linhas.push([amostras[i].id, amostras[i].t, aspas(v), estados[v][i] || ""].join(","));
    }
  }
  return linhas.join("\n") + "\n";
}
function linhaCSV(l){
  const out = []; let cur = "", q = false;
  for (let i = 0; i < l.length; i++){
    const c = l[i];
    if (q){ if (c === '"'){ if (l[i+1] === '"'){ cur += '"'; i++; } else q = false; } else cur += c; }
    else if (c === '"') q = true;
    else if (c === ","){ out.push(cur); cur = ""; }
    else cur += c;
  }
  out.push(cur); return out;
}
function lerCSV(texto, amostras, vagas){
  const idxPorId = {}; amostras.forEach((a, i) => idxPorId[a.id] = i);
  const estados = {}; vagas.forEach(v => estados[v] = new Array(amostras.length).fill(null));
  const linhas = texto.replace(/^\uFEFF/, "").split(/\r?\n/).filter(x => x.trim() !== "");
  const cab = linhaCSV(linhas[0]).map(s => s.trim());
  const ia = cab.indexOf("amostra"), iv = cab.indexOf("vaga"), ie = cab.indexOf("estado_real");
  if (ia < 0 || iv < 0 || ie < 0) throw new Error("CSV sem as colunas amostra, vaga, estado_real.");
  for (let j = 1; j < linhas.length; j++){
    const c = linhaCSV(linhas[j]);
    const i = idxPorId[parseInt(c[ia], 10)], v = c[iv], e = (c[ie] || "").trim().toLowerCase();
    if (i === undefined || !estados[v]) continue;
    if (e === "ocupada" || e === "livre") estados[v][i] = e;
  }
  const eventos = {}; vagas.forEach(v => eventos[v] = eventosDeEstados(estados[v]));
  return eventos;
}
// ==FIM==

if (typeof document !== "undefined"){
const D = __DADOS__;
const N = D.amostras.length, KEY = "gabarito:" + D.rotulo;
let idx = 0, eventos = {}, estado = {}, pilha = [];
D.vagas.forEach(v => eventos[v] = {});
const $ = id => document.getElementById(id);

function recomputar(v){ estado[v] = preencher(eventos[v], N); }
function recomputarTodos(){ D.vagas.forEach(recomputar); }
function salvar(){ try{ localStorage.setItem(KEY, JSON.stringify({idx: idx, eventos: eventos})); }catch(e){} }
function carregar(){
  try{
    const s = JSON.parse(localStorage.getItem(KEY) || "null");
    if (s && s.eventos){ D.vagas.forEach(v => { if (s.eventos[v]) eventos[v] = s.eventos[v]; }); idx = Math.min(Math.max(0, s.idx|0), N-1); }
  }catch(e){}
}
function hms(t){ t = Math.round(t); const h = Math.floor(t/3600), m = Math.floor(t%3600/60), s = t%60;
  return (h?String(h).padStart(2,"0")+":":"") + String(m).padStart(2,"0") + ":" + String(s).padStart(2,"0"); }

function definir(v, valor){
  const atual = estado[v][idx], tinha = eventos[v][idx];
  if (atual === valor && tinha === undefined) return;      // já é esse estado
  pilha.push({v: v, i: idx, antes: tinha});
  eventos[v][idx] = valor; recomputar(v); salvar(); desenhar();
}
function limparEvento(v){
  const tinha = eventos[v][idx]; if (tinha === undefined) return;
  pilha.push({v: v, i: idx, antes: tinha}); delete eventos[v][idx]; recomputar(v); salvar(); desenhar();
}
function desfazer(){
  const a = pilha.pop(); if (!a) return;
  if (a.antes === undefined) delete eventos[a.v][a.i]; else eventos[a.v][a.i] = a.antes;
  recomputar(a.v); idx = a.i; salvar(); desenhar();
}
function ir(i){ idx = Math.max(0, Math.min(N-1, i)); salvar(); desenhar(); }

function montarVagas(){
  const box = $("vagas"); box.innerHTML = "";
  D.vagas.forEach((v, k) => {
    const d = document.createElement("div"); d.className = "vaga"; d.id = "v" + k;
    d.innerHTML = '<span class="n">' + (k < 9 ? k+1 : "") + '</span><span class="nome"></span><span class="ev" title="marcação feita neste quadro"></span>' +
      '<button class="btn-o">Ocupada</button><button class="btn-l">Livre</button><button class="x" title="remover a marcação deste quadro">&times;</button>';
    d.querySelector(".nome").textContent = v;
    d.querySelector(".btn-o").onclick = () => definir(v, "ocupada");
    d.querySelector(".btn-l").onclick = () => definir(v, "livre");
    d.querySelector(".x").onclick = () => limparEvento(v);
    box.appendChild(d);
  });
}
function desenhar(){
  const a = D.amostras[idx];
  $("img").src = a.img;
  $("info").textContent = "Quadro " + (idx+1) + " de " + N + "  |  tempo " + hms(a.t);
  D.vagas.forEach((v, k) => {
    const d = $("v" + k), s = estado[v][idx];
    d.querySelector(".btn-o").classList.toggle("on", s === "ocupada");
    d.querySelector(".btn-l").classList.toggle("on", s === "livre");
    d.querySelector(".ev").textContent = eventos[v][idx] !== undefined ? "\u25CF" : "";
  });
  let faltam = 0; D.vagas.forEach(v => { estado[v].forEach(s => { if (!s) faltam++; }); });
  $("faltam").innerHTML = faltam ? '<span class="warn">Observações ainda sem estado: ' + faltam + ' (defina o estado no quadro 1)</span>' : "Todas as observações têm estado definido.";
  linha();
  [idx+1, idx-1].forEach(j => { if (j >= 0 && j < N){ const p = new Image(); p.src = D.amostras[j].img; } });
}
function linha(){
  const c = $("linha"), W = c.clientWidth || 340, h = 9, g = 2;
  c.width = W; c.height = D.vagas.length * (h+g) + 2;
  const x = c.getContext("2d"); x.clearRect(0, 0, c.width, c.height);
  const cor = {ocupada: "#e5384b", livre: "#1fb184"};
  D.vagas.forEach((v, k) => {
    let ini = 0;
    for (let i = 1; i <= N; i++){
      if (i === N || estado[v][i] !== estado[v][ini]){
        x.fillStyle = cor[estado[v][ini]] || "#444"; 
        x.fillRect(ini/N*W, k*(h+g)+1, Math.max(1, (i-ini)/N*W), h); ini = i;
      }
    }
  });
  x.fillStyle = "#fff"; x.fillRect(idx/N*W, 0, 2, c.height);
}
function baixar(){
  let faltam = 0; D.vagas.forEach(v => estado[v].forEach(s => { if (!s) faltam++; }));
  if (faltam && !confirm(faltam + " observações estão sem estado e serão ignoradas nas métricas. Baixar mesmo assim?")) return;
  const blob = new Blob(["\uFEFF" + montarCSV(D.amostras, D.vagas, estado)], {type: "text/csv;charset=utf-8"});
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
  a.download = "gabarito_" + D.rotulo + ".csv"; document.body.appendChild(a); a.click(); a.remove();
  $("msg").textContent = "Baixado. Mova o arquivo para esta pasta e renomeie para gabarito.csv (ou deixe em Downloads: o passo 'metricas' também procura lá).";
}
$("bPrev").onclick = () => ir(idx-1); $("bNext").onclick = () => ir(idx+1);
$("bMenos").onclick = () => ir(idx-10); $("bMais").onclick = () => ir(idx+10);
$("bBaixar").onclick = baixar;
$("img").onclick = () => window.open(D.amostras[idx].img, "_blank");
$("linha").onclick = ev => { const r = $("linha").getBoundingClientRect(); ir(Math.round((ev.clientX - r.left)/r.width*(N-1))); };
$("bImportar").onclick = () => $("arq").click();
$("arq").onchange = ev => {
  const f = ev.target.files[0]; if (!f) return;
  const r = new FileReader();
  r.onload = () => { try{ eventos = lerCSV(r.result, D.amostras, D.vagas); recomputarTodos(); pilha = []; salvar(); desenhar(); $("msg").textContent = "Gabarito importado."; }
    catch(e){ alert(e.message); } };
  r.readAsText(f, "utf-8");
};
document.addEventListener("keydown", ev => {
  if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
  if (ev.key === "ArrowRight" || ev.key === "d") ir(idx+1);
  else if (ev.key === "ArrowLeft" || ev.key === "a") ir(idx-1);
  else if (ev.key === "PageDown") ir(idx+10); else if (ev.key === "PageUp") ir(idx-10);
  else if (ev.key === "Home") ir(0); else if (ev.key === "End") ir(N-1);
  else if (ev.key === "z" || ev.key === "Z") desfazer();
  else if (/^[1-9]$/.test(ev.key)){
    const v = D.vagas[parseInt(ev.key, 10) - 1]; if (!v) return;
    const s = estado[v][idx]; definir(v, s === "ocupada" ? "livre" : "ocupada");
  } else return;
  ev.preventDefault();
});
window.addEventListener("resize", linha);
montarVagas(); carregar(); recomputarTodos(); desenhar();
}
</script></body></html>
"""


def gerar_revisao(pasta):
    caminho = os.path.join(pasta, "previsoes.csv")
    if not os.path.exists(caminho):
        erro("Não há previsoes.csv em %s" % pasta)
    amostras, vagas, vistos = [], [], set()
    with open(caminho, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            sid = int(r["amostra"])
            if r["vaga"] not in vagas:
                vagas.append(r["vaga"])
            if sid not in vistos:
                vistos.add(sid)
                amostras.append({"id": sid, "t": round(float(r["tempo_s"]), 2),
                                 "img": "para_anotar/" + r["imagem"]})
    amostras.sort(key=lambda x: x["id"])
    rotulo = os.path.basename(os.path.normpath(pasta))
    dados = json.dumps({"rotulo": rotulo, "amostras": amostras, "vagas": vagas}, ensure_ascii=False)
    html = HTML_REVISAR.replace("__DADOS__", dados).replace("__ROTULO__", rotulo)
    with open(os.path.join(pasta, "revisar.html"), "w", encoding="utf-8") as f:
        f.write(html)
    return os.path.join(pasta, "revisar.html")


def cmd_revisar(a):
    pasta = os.path.join(PASTA, slug(a.rotulo))
    print("Gerado: " + gerar_revisao(pasta))


# ----------------------------------------------------------------------
# Passo 3 - métricas
# ----------------------------------------------------------------------
def _achar_gabarito(pasta, rotulo):
    destino = os.path.join(pasta, "gabarito.csv")
    if os.path.exists(destino):
        return destino
    candidatos = []
    for d in {os.path.join(os.path.expanduser("~"), "Downloads"), pasta}:
        candidatos += glob.glob(os.path.join(d, "gabarito_%s*.csv" % rotulo))
    if candidatos:
        novo = max(candidatos, key=os.path.getmtime)
        shutil.copy(novo, destino)
        print("[OK] Gabarito encontrado em %s e copiado para %s" % (novo, destino))
        return destino
    return None


def _carregar_deteccoes(pasta):
    res = {}
    caminho = os.path.join(pasta, "deteccoes.jsonl")
    if os.path.exists(caminho):
        with open(caminho, encoding="utf-8") as f:
            for linha in f:
                try:
                    d = json.loads(linha)
                    res[int(d["amostra"])] = d
                except Exception:
                    pass
    return res


def _salvar_erros(pasta, df_erros, destino, limite):
    import cv2
    os.makedirs(destino, exist_ok=True)
    det = _carregar_deteccoes(pasta)
    salvos = 0
    for sid, grupo in df_erros.groupby("amostra"):
        if salvos >= limite:
            break
        img = cv2.imread(os.path.join(pasta, "para_anotar", grupo.iloc[0]["imagem"]))
        if img is None:
            continue
        d = det.get(int(sid))
        if d:
            for x1, y1, x2, y2 in d["caixas"]:
                e = d["escala"]
                cv2.rectangle(img, (int(x1 * e), int(y1 * e)), (int(x2 * e), int(y2 * e)), (255, 200, 0), 2)
        rotulos, y = [], 30
        for _, r in grupo.iterrows():
            tipo = "FP" if r["previsao"] == "ocupada" else "FN"
            rotulos.append("%s-%s" % (tipo, r["vaga"]))
            txt = "%s %s (YOLO: %s | real: %s)" % (tipo, r["vaga"], r["previsao"], r["estado_real"])
            cv2.putText(img, txt, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(img, txt, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (60, 60, 255), 2, cv2.LINE_AA)
            y += 30
        nome = "amostra_%05d_%s.jpg" % (int(sid), "_".join(rotulos)[:80])
        cv2.imwrite(os.path.join(destino, nome), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        salvos += 1
    return salvos


def _plot_matriz(c, titulo, caminho):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    m = [[c["TP"], c["FN"]], [c["FP"], c["TN"]]]
    fig, ax = plt.subplots(figsize=(5.6, 4.6))
    im = ax.imshow(m, cmap="Blues")
    ax.set_xticks([0, 1]); ax.set_xticklabels(["Vaga Ocupada", "Vaga Livre"])
    ax.set_yticks([0, 1]); ax.set_yticklabels(["Vaga Ocupada", "Vaga Livre"], rotation=90, va="center")
    ax.set_xlabel("Previsto"); ax.set_ylabel("Real"); ax.set_title(titulo)
    vmax = max(max(l) for l in m) or 1
    for i in range(2):
        for j in range(2):
            ax.text(j, i, "{:,}".format(m[i][j]).replace(",", "."), ha="center", va="center",
                    color="white" if m[i][j] > vmax * 0.5 else "black", fontsize=13)
    fig.colorbar(im, ax=ax)
    fig.tight_layout()
    fig.savefig(caminho, dpi=200)
    plt.close(fig)


def _bloco_tabela(legenda, rotulo, colunas, linhas, rodape="Fonte: Do autor.", pequeno=False):
    s = ["\\begin{table}[H]", "\\centering", "\\caption{%s}" % legenda, "\\label{%s}" % rotulo,
         "\\vspace{-0.2cm}"]
    if pequeno:
        s += ["\\footnotesize", "\\setlength{\\tabcolsep}{3.5pt}"]
    s += ["\\begin{tabular}{%s}" % colunas, "\\hline"]
    s += linhas
    s += ["\\hline", "\\end{tabular}", "", "\\vspace{-0.2cm}", "\\flushleft",
          "\\hspace{0.9cm}\\footnotesize %s" % rodape, "\\end{table}", ""]
    return "\n".join(s)


def cmd_metricas(a):
    import pandas as pd
    if a.rotulos:
        rotulos = [slug(r) for r in a.rotulos]
    else:
        rotulos = sorted(d for d in os.listdir(PASTA)
                         if os.path.isfile(os.path.join(PASTA, d, "previsoes.csv"))
                         and "_antigo_" not in d) if os.path.isdir(PASTA) else []
    if not rotulos:
        erro("Nenhuma avaliação encontrada em %s. Rode o passo 'amostrar' primeiro." % PASTA)

    os.makedirs(PASTA_RESULTADO, exist_ok=True)
    por_video, por_vaga_todas, metas = [], [], {}
    total = {"TP": 0, "TN": 0, "FP": 0, "FN": 0}
    soma_quadros = 0
    tempos_todos = []
    ignorados = []

    for rot in rotulos:
        pasta = os.path.join(PASTA, rot)
        meta = {}
        if os.path.exists(os.path.join(pasta, "metadata.json")):
            with open(os.path.join(pasta, "metadata.json"), encoding="utf-8") as f:
                meta = json.load(f)
        metas[rot] = meta
        gab = _achar_gabarito(pasta, rot)
        if not gab:
            ignorados.append(rot)
            print("[PULADO] %s: ainda não há gabarito (abra %s e marque)." % (rot, os.path.join(pasta, "revisar.html")))
            continue
        prev = pd.read_csv(os.path.join(pasta, a.previsoes), encoding="utf-8-sig", dtype={"vaga": str})
        gt = pd.read_csv(gab, encoding="utf-8-sig", dtype={"vaga": str})
        for col in ("amostra", "vaga", "estado_real"):
            if col not in gt.columns:
                erro("%s precisa ter a coluna '%s'." % (gab, col))
        gt = gt[["amostra", "vaga", "estado_real"]].copy()
        m = prev.merge(gt, on=["amostra", "vaga"], how="left")
        m["estado_real"] = m["estado_real"].fillna("").astype(str).str.strip().str.lower()
        m["previsao"] = m["previsao"].fillna("").astype(str).str.strip().str.lower()
        valido = m[m["estado_real"].isin(["ocupada", "livre"]) & m["previsao"].isin(["ocupada", "livre"])].copy()
        n_fora = int((m["previsao"] == "fora_de_vista").sum())
        n_sem_gab = int((~m["estado_real"].isin(["ocupada", "livre"])).sum())
        if valido.empty:
            print("[PULADO] %s: nenhuma observação válida (gabarito vazio?)." % rot)
            ignorados.append(rot)
            continue

        def cont(df):
            return (int(((df.estado_real == "ocupada") & (df.previsao == "ocupada")).sum()),
                    int(((df.estado_real == "livre") & (df.previsao == "livre")).sum()),
                    int(((df.estado_real == "livre") & (df.previsao == "ocupada")).sum()),
                    int(((df.estado_real == "ocupada") & (df.previsao == "livre")).sum()))

        tp, tn, fp, fn = cont(valido)
        r = calcular(tp, tn, fp, fn)
        quadros = int(valido["amostra"].nunique())
        tempo = m.drop_duplicates("amostra")["tempo_ms"].astype(float)
        r.update({"video": rot, "quadros": quadros,
                  "tempo_medio_s": float(tempo.mean() / 1000.0), "tempo_mediano_s": float(tempo.median() / 1000.0),
                  "fora_de_vista_excluidas": n_fora, "sem_gabarito_excluidas": n_sem_gab})
        por_video.append(r)
        tempos_todos += list(tempo)
        for k in total:
            total[k] += r[k]
        soma_quadros += quadros

        for vaga, g in valido.groupby("vaga", sort=True):
            tpv, tnv, fpv, fnv = cont(g)
            d = calcular(tpv, tnv, fpv, fnv)
            d.update({"video": rot, "vaga": vaga})
            por_vaga_todas.append(d)

        erros_df = valido[valido["estado_real"] != valido["previsao"]]
        if not erros_df.empty:
            n = _salvar_erros(pasta, erros_df, os.path.join(PASTA_RESULTADO, "erros_" + rot), a.max_erros)
            print("   %s: %d imagens de erro (FP/FN) em resultado/erros_%s" % (rot, n, rot))
        _plot_matriz(r, "Matriz de confusão - %s" % rot,
                     os.path.join(PASTA_RESULTADO, "matriz_confusao_%s.png" % rot))
        if n_sem_gab:
            print("   [AVISO] %s: %d observações sem gabarito foram ignoradas." % (rot, n_sem_gab))
        if n_fora:
            print("   [AVISO] %s: %d observações 'fora de vista' foram excluídas." % (rot, n_fora))

    if not por_video:
        erro("Nenhum vídeo com gabarito para calcular. Marque o gabarito e rode de novo.")

    cons = calcular(total["TP"], total["TN"], total["FP"], total["FN"])
    cons.update({"video": "CONSOLIDADO", "quadros": soma_quadros,
                 "tempo_medio_s": (sum(tempos_todos) / len(tempos_todos) / 1000.0) if tempos_todos else None,
                 "tempo_mediano_s": None, "fora_de_vista_excluidas": "", "sem_gabarito_excluidas": ""})
    _plot_matriz(cons, "Matriz de confusão consolidada", os.path.join(PASTA_RESULTADO, "matriz_confusao_consolidada.png"))

    cols = ["video", "quadros", "observacoes", "TP", "TN", "FP", "FN", "acuracia", "precisao", "recall", "fpr",
            "precisao_livre", "recall_livre", "tempo_medio_s", "tempo_mediano_s",
            "fora_de_vista_excluidas", "sem_gabarito_excluidas"]
    df = pd.DataFrame(por_video + [cons])[cols]
    df.to_csv(os.path.join(PASTA_RESULTADO, "metricas_por_video.csv"), index=False, sep=";", decimal=",",
              encoding="utf-8-sig")
    pd.DataFrame(por_vaga_todas)[["video", "vaga", "observacoes", "TP", "TN", "FP", "FN", "acuracia", "precisao",
                                  "recall", "fpr"]].to_csv(os.path.join(PASTA_RESULTADO, "metricas_por_vaga.csv"),
                                                          index=False, sep=";", decimal=",", encoding="utf-8-sig")

    # ---------------- tabelas LaTeX ----------------
    L = []
    # Tabela de vídeos
    lin = ["\\textbf{Vídeo} & \\textbf{Câmera} & \\textbf{Duração} & \\textbf{Resolução} & \\textbf{Vagas} \\\\", "\\hline"]
    nome_curto = {}
    for i, r in enumerate([x for x in por_video], 1):
        mt = metas.get(r["video"], {})
        nome = "Vídeo %d" % i
        nome_curto[r["video"]] = nome
        if mt.get("periodo"):
            nome += " (%s)" % tex(mt["periodo"])
        lin.append("%s & %s & %s & %s & %d \\\\" % (
            nome, tex(mt.get("camera") or "[preencher]"), fmt_hms(mt.get("duracao_s")),
            "%dx%d" % (mt.get("largura", 0), mt.get("altura", 0)), len(mt.get("vagas", []))))
    L.append(_bloco_tabela("Características dos vídeos utilizados nos experimentos", "tab:videos", "l c c c c", lin))

    # Tabela de resultados YOLO
    lin = ["\\textbf{Vídeo} & \\textbf{Am.} & \\textbf{TP} & \\textbf{TN} & \\textbf{FP} & \\textbf{FN} & "
           "\\textbf{Acc.} & \\textbf{Prec.} & \\textbf{Rec.} & \\textbf{FPR} \\\\", "\\hline"]
    for r in por_video + [cons]:
        nome = nome_curto.get(r["video"], "\\textbf{Total}")
        lin.append("%s & %d & %d & %d & %d & %d & %s & %s & %s & %s \\\\" % (
            nome, r["quadros"], r["TP"], r["TN"], r["FP"], r["FN"], pct_tex(r["acuracia"]),
            pct_tex(r["precisao"]), pct_tex(r["recall"]), pct_tex(r["fpr"])))
        if r is por_video[-1]:
            lin.append("\\hline")
    L.append(_bloco_tabela("Resultados dos testes realizados com YOLO", "tab:resultados-yolo",
                           "l c c c c c c c c c", lin, pequeno=True))

    # Matriz consolidada
    lin = [" & \\textbf{Predito: Ocupada} & \\textbf{Predito: Livre} \\\\", "\\hline",
           "\\textbf{Real: Ocupada} & %d & %d \\\\" % (cons["TP"], cons["FN"]),
           "\\textbf{Real: Livre} & %d & %d \\\\" % (cons["FP"], cons["TN"])]
    L.append(_bloco_tabela("Matriz de confusão consolidada", "tab:confusao", "l c c", lin))

    # Comparação com Rocha
    rc = calcular(ROCHA["TP"], ROCHA["TN"], ROCHA["FP"], ROCHA["FN"])
    lin = ["\\textbf{Trabalho} & \\textbf{Acurácia} & \\textbf{Precisão} & \\textbf{Recall} & \\textbf{FPR} \\\\", "\\hline",
           "Rocha (2025), como publicado & %s & %s\\textsuperscript{*} & --- & --- \\\\" % (
               pct_tex(ROCHA["acc_publicada"]), pct_tex(ROCHA["prec_publicada"])),
           "Rocha (2025), recalculado da matriz\\textsuperscript{**} & %s & %s & %s & %s \\\\" % (
               pct_tex(rc["acuracia"]), pct_tex(rc["precisao"]), pct_tex(rc["recall"]), pct_tex(rc["fpr"])),
           "Este trabalho & %s & %s & %s & %s \\\\" % (
               pct_tex(cons["acuracia"]), pct_tex(cons["precisao"]), pct_tex(cons["recall"]), pct_tex(cons["fpr"]))]
    rod = ("Fonte: Do autor e \\citeonline{rocha2025}. \\textsuperscript{*}Corresponde à precisão da classe "
           "\\textit{livre} (%s neste trabalho). \\textsuperscript{**}Considerando vaga ocupada como classe "
           "positiva, como definido na seção de métricas." % pct_tex(cons["precisao_livre"]))
    L.append(_bloco_tabela("Comparação dos resultados com o trabalho de Rocha (2025)", "tab:comparacao-rocha",
                           "l c c c c", lin, rodape=rod))

    # Tempo
    lin = ["\\textbf{Vídeo} & \\textbf{Tempo médio por quadro (s)} \\\\", "\\hline"]
    for r in por_video:
        lin.append("%s & %s \\\\" % (nome_curto.get(r["video"]), ("%.2f" % r["tempo_medio_s"]).replace(".", ",")))
    lin.append("\\textbf{Média geral} & %s \\\\" % ("%.2f" % cons["tempo_medio_s"]).replace(".", ","))
    L.append(_bloco_tabela("Tempo de processamento do YOLO por quadro", "tab:tempo-yolo", "l c", lin))

    with open(os.path.join(PASTA_RESULTADO, "tabelas.tex"), "w", encoding="utf-8") as f:
        f.write("%% Gerado por avaliacao_tcc.py em %s\n%% Cole no artigo no lugar das tabelas com [preencher].\n\n" %
                datetime.now().strftime("%d/%m/%Y %H:%M"))
        f.write("\n".join(L))

    # ---------------- resumo no console ----------------
    print("\n" + "=" * 78)
    print("%-16s %6s %7s %6s %6s %6s %6s  %8s %8s %8s %7s" % (
        "Vídeo", "Quadr.", "Obs.", "TP", "TN", "FP", "FN", "Acur.", "Prec.", "Recall", "FPR"))
    for r in por_video + [cons]:
        print("%-16s %6d %7d %6d %6d %6d %6d  %8s %8s %8s %7s" % (
            r["video"][:16], r["quadros"], r["observacoes"], r["TP"], r["TN"], r["FP"], r["FN"],
            pct_txt(r["acuracia"]), pct_txt(r["precisao"]), pct_txt(r["recall"]), pct_txt(r["fpr"])))
    print("=" * 78)
    print("Precisão da classe LIVRE (a mesma definição do 60,45%% do João): %s" % pct_txt(cons["precisao_livre"]))
    print("Tempo médio por quadro: %.2f s" % (cons["tempo_medio_s"] or 0))
    if ignorados:
        print("Vídeos sem gabarito (fora da conta): %s" % ", ".join(ignorados))
    print("\nArquivos em %s:\n  tabelas.tex | metricas_por_video.csv | metricas_por_vaga.csv | matriz_confusao_*.png | erros_*/" %
          PASTA_RESULTADO)


# ----------------------------------------------------------------------
# Passo 4 - sistema (tabela de configuração computacional)
# ----------------------------------------------------------------------
def cmd_sistema(a):
    itens = []
    itens.append(("Sistema operacional", "%s %s" % (platform.system(), platform.release())))
    cpu = platform.processor() or "n/d"
    try:
        import cpuinfo
        cpu = cpuinfo.get_cpu_info().get("brand_raw", cpu)
    except Exception:
        pass
    itens.append(("Processador", cpu))
    try:
        import psutil
        itens.append(("Memória RAM", "%.0f GB" % (psutil.virtual_memory().total / 1024 ** 3)))
    except Exception:
        itens.append(("Memória RAM", "[preencher]"))
    gpu = "Sem GPU dedicada (CPU)"
    try:
        import torch
        if torch.cuda.is_available():
            gpu = torch.cuda.get_device_name(0)
    except Exception:
        gpu = "[preencher]"
    itens.append(("GPU", gpu))
    itens.append(("Python", platform.python_version()))
    try:
        import cv2
        itens.append(("OpenCV", cv2.__version__))
    except Exception:
        itens.append(("OpenCV", "[preencher]"))
    itens.append(("Ultralytics", versao_pacote("ultralytics")))
    itens.append(("Flask", versao_pacote("flask")))
    itens.append(("Socket.IO", "Flask-SocketIO %s" % versao_pacote("flask-socketio")))
    linhas = ["\\textbf{Item} & \\textbf{Configuração} \\\\", "\\hline"] + \
             ["%s & %s \\\\" % (tex(k), tex(v)) for k, v in itens]
    bloco = _bloco_tabela("Configuração computacional utilizada nos experimentos", "tab:hardware", "l p{8cm}", linhas)
    os.makedirs(PASTA_RESULTADO, exist_ok=True)
    with open(os.path.join(PASTA_RESULTADO, "tabela_sistema.tex"), "w", encoding="utf-8") as f:
        f.write(bloco)
    for k, v in itens:
        print("%-22s %s" % (k, v))
    print("\nTabela LaTeX salva em %s" % os.path.join(PASTA_RESULTADO, "tabela_sistema.tex"))


# ----------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("amostrar", help="roda o YOLO a cada N segundos de um vídeo")
    s.add_argument("--ambiente", required=True, help="nome do ambiente salvo no editor de vagas")
    s.add_argument("--video", required=True, help="caminho do vídeo")
    s.add_argument("--rotulo", help="nome curto desta avaliação (padrão: nome do arquivo)")
    s.add_argument("--intervalo", type=float, default=5.0, help="segundos entre amostras (padrão 5, como Rocha)")
    s.add_argument("--aquecimento", type=float, default=1.0,
                   help="segundos antes de cada amostra em que um quadro extra é analisado só para o rastreador "
                        "confirmar os carros (padrão 1; 0 desliga)")
    s.add_argument("--conf", type=float, help="confiança do YOLO (padrão: a salva no ambiente, senão 0,4)")
    s.add_argument("--modelo", default="yolo12x.pt")
    s.add_argument("--estabilizar", action="store_true", help="força a estabilização de ROI (câmera que se move)")
    s.add_argument("--camera", help="texto para a tabela de vídeos (ex.: 'Convencional', '360 equirretangular')")
    s.add_argument("--periodo", help="texto para a tabela de vídeos (ex.: manhã, tarde, noite)")
    s.add_argument("--max-amostras", type=int, help="processa só as N primeiras (para testar rápido)")
    s.add_argument("--continuar", action="store_true", help="retoma uma avaliação interrompida")
    s.add_argument("--refazer", action="store_true", help="guarda a avaliação antiga e começa de novo")
    s.set_defaults(func=cmd_amostrar)

    s = sub.add_parser("revisar", help="(re)gera o revisar.html de uma avaliação")
    s.add_argument("rotulo")
    s.set_defaults(func=cmd_revisar)

    s = sub.add_parser("metricas", help="calcula métricas, gráficos e tabelas LaTeX")
    s.add_argument("rotulos", nargs="*", help="quais avaliações (padrão: todas)")
    s.add_argument("--previsoes", default="previsoes.csv", help="arquivo de previsões (útil p/ comparar com outro modelo)")
    s.add_argument("--max-erros", type=int, default=150, help="máx. de imagens de erro salvas por vídeo")
    s.set_defaults(func=cmd_metricas)

    s = sub.add_parser("sistema", help="gera a tabela de hardware/software")
    s.set_defaults(func=cmd_sistema)

    args = p.parse_args()
    if not getattr(args, "func", None):
        p.print_help()
        return
    args.func(args)


if __name__ == "__main__":
    main()
