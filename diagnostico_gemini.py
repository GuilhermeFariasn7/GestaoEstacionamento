"""
Diagnóstico do Gemini, fora do sistema.

Mostra, em 3 passos, onde está o problema:
  1) a chave é aceita e a internet chega no Google?
  2) o modelo escolhido responde a uma pergunta simples (só texto)?
  3) o modelo responde com uma imagem de uma amostra do teste?

Uso (no terminal, com o venv ativo):
    python diagnostico_gemini.py                      # pergunta chave e modelo
    python diagnostico_gemini.py --model gemini-2.5-flash
A chave pode vir de GEMINI_API_KEY ou ser digitada (não aparece na tela).
"""
import argparse
import base64
import getpass
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.request

API_BASE = os.environ.get("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta")
TIMEOUT = 60


def call(method, url, key, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "x-goog-api-key": key, "Content-Type": "application/json"})
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, json.loads(r.read().decode()), time.perf_counter() - t
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode()).get("error", {}).get("message", str(e))
        except Exception:
            msg = str(e)
        return e.code, msg, time.perf_counter() - t
    except Exception as e:  # sem rede, DNS, timeout...
        return None, f"{type(e).__name__}: {getattr(e, 'reason', e)}", time.perf_counter() - t


def verdict(code, detail):
    if code is None:
        return ("SEM CONEXÃO/RESPOSTA: o pedido não chegou ou o Google não respondeu em "
                f"{TIMEOUT} s ({detail}). Verifique internet, proxy, antivírus/firewall.")
    return {
        400: "PEDIDO RECUSADO (400): chave inválida para este recurso ou requisição mal formada.",
        401: "CHAVE RECUSADA (401).",
        403: "SEM PERMISSÃO (403): chave sem acesso à API/modelo, ou API não habilitada no projeto, "
             "ou região não suportada.",
        404: "MODELO NÃO EXISTE para esta chave (404): escolha outro modelo na lista.",
        429: "LIMITE DE COTA (429): muitas requisições ou cota gratuita esgotada. Aumente a pausa "
             "entre requisições ou espere.",
        500: "ERRO INTERNO DO GOOGLE (500): tente de novo mais tarde.",
        503: "MODELO SOBRECARREGADO (503): problema temporário do lado do Google. Tente outro "
             "modelo estável ou espere alguns minutos.",
    }.get(code, f"ERRO {code}.") + f"\n    Mensagem do Google: {str(detail)[:300]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="")
    args = ap.parse_args()

    key = os.environ.get("GEMINI_API_KEY", "").strip() or getpass.getpass("Chave da API do Gemini: ").strip()
    if not key:
        sys.exit("Sem chave.")

    print("\n[1/3] Testando chave e conexão (lista de modelos)...")
    code, data, dt = call("GET", f"{API_BASE}/models?pageSize=1000", key)
    if code != 200:
        print(f"  FALHOU em {dt:.1f} s -> {verdict(code, data)}")
        return
    names = sorted(m["name"].replace("models/", "") for m in data.get("models", [])
                   if "generateContent" in m.get("supportedGenerationMethods", [])
                   and m["name"].startswith("models/gemini"))
    print(f"  OK em {dt:.1f} s. A chave funciona. {len(names)} modelos com generateContent.")

    model = args.model or input("\nModelo a testar (ex.: o que você usa no sistema): ").strip()
    if model not in names:
        print(f"  ATENÇÃO: '{model}' não está na lista desta chave. Alguns disponíveis:")
        for n in [n for n in names if "preview" not in n and "exp" not in n][:15]:
            print("   -", n)

    url = f"{API_BASE}/models/{model}:generateContent"

    print(f"\n[2/3] {model}: pergunta simples só com texto...")
    code, data, dt = call("POST", url, key, {"contents": [{"parts": [{"text": "Responda apenas: ok"}]}]})
    if code == 200:
        print(f"  OK em {dt:.1f} s.")
    else:
        print(f"  FALHOU em {dt:.1f} s -> {verdict(code, data)}")
        return

    print(f"\n[3/3] {model}: com uma imagem de amostra...")
    imgs = sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "test_results", "*", "samples", "*.jpg")))
    if not imgs:
        print("  Nenhuma imagem em test_results; passo ignorado.")
        return
    with open(imgs[0], "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    body = {"contents": [{"parts": [
        {"text": "Quantos carros vermelhos existem? Responda só com o número."},
        {"inline_data": {"mime_type": "image/jpeg", "data": b64}}]}]}
    code, data, dt = call("POST", url, key, body)
    if code == 200:
        print(f"  OK em {dt:.1f} s. O Gemini está funcionando com imagens; o problema não é a chave nem o modelo.")
    else:
        print(f"  FALHOU em {dt:.1f} s -> {verdict(code, data)}")


if __name__ == "__main__":
    main()
