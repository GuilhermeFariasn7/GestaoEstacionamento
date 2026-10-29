#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Janela de botões para os testes do TCC (usa o avaliacao_tcc.py por trás)."""
import os
import queue
import subprocess
import sys
import threading
import webbrowser
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PASTA = os.path.join(BASE_DIR, "avaliacao")
RESULTADO = os.path.join(PASTA, "resultado")
SCRIPT = os.path.join(BASE_DIR, "avaliacao_tcc.py")


def slug(txt):
    import re
    return re.sub(r"[^A-Za-z0-9_-]+", "-", str(txt)).strip("-") or "video"


def montar_comando_amostrar(ambiente, video, rotulo, camera, periodo, intervalo,
                            rapido, modo):
    """modo: 'novo' | 'continuar' | 'refazer'."""
    cmd = [sys.executable, "-u", SCRIPT, "amostrar", "--ambiente", ambiente,
           "--video", video, "--rotulo", rotulo, "--intervalo", str(intervalo)]
    if camera:
        cmd += ["--camera", camera]
    if periodo:
        cmd += ["--periodo", periodo]
    if rapido:
        cmd += ["--max-amostras", "5"]
    if modo == "continuar":
        cmd.append("--continuar")
    elif modo == "refazer":
        cmd.append("--refazer")
    return cmd


def listar_ambientes():
    try:
        sys.path.insert(0, BASE_DIR)
        import regions_store as store
        return store.list_environments()
    except Exception:
        return []


def listar_avaliacoes():
    itens = []
    if os.path.isdir(PASTA):
        for nome in sorted(os.listdir(PASTA)):
            p = os.path.join(PASTA, nome)
            if nome == "resultado" or "_antigo_" in nome or not os.path.isfile(os.path.join(p, "previsoes.csv")):
                continue
            gab = "OK" if os.path.exists(os.path.join(p, "gabarito.csv")) else "falta"
            itens.append((nome, "%s   [gabarito: %s]" % (nome, gab)))
    return itens


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Smart Parking - Testes do TCC")
        self.geometry("920x760")
        self.fila = queue.Queue()
        self.rodando = False
        self.botoes = []
        self._ui()
        self.atualizar_listas()
        self.after(100, self._drenar)

    # ------------------------------------------------------------ UI
    def _ui(self):
        pad = {"padx": 8, "pady": 4}
        f1 = ttk.LabelFrame(self, text="1. Processar um vídeo com o YOLO")
        f1.pack(fill="x", padx=10, pady=(10, 4))
        f1.columnconfigure(1, weight=1)

        ttk.Label(f1, text="Ambiente (vagas):").grid(row=0, column=0, sticky="e", **pad)
        self.v_amb = tk.StringVar()
        self.cb_amb = ttk.Combobox(f1, textvariable=self.v_amb, state="readonly")
        self.cb_amb.grid(row=0, column=1, sticky="ew", **pad)
        b = ttk.Button(f1, text="Abrir editor de vagas", command=self.abrir_editor)
        b.grid(row=0, column=2, **pad)
        self.botoes.append(b)

        ttk.Label(f1, text="Vídeo:").grid(row=1, column=0, sticky="e", **pad)
        self.v_video = tk.StringVar()
        ttk.Entry(f1, textvariable=self.v_video).grid(row=1, column=1, sticky="ew", **pad)
        b = ttk.Button(f1, text="Escolher...", command=self.escolher_video)
        b.grid(row=1, column=2, **pad)
        self.botoes.append(b)

        linha = ttk.Frame(f1)
        linha.grid(row=2, column=0, columnspan=3, sticky="ew")
        self.v_rot, self.v_cam, self.v_per, self.v_int = (tk.StringVar() for _ in range(4))
        self.v_int.set("5")
        for i, (rot, var, w) in enumerate([("Nome curto (v1):", self.v_rot, 10),
                                           ("Câmera:", self.v_cam, 16),
                                           ("Período (manhã...):", self.v_per, 10),
                                           ("Intervalo (s):", self.v_int, 4)]):
            ttk.Label(linha, text=rot).grid(row=0, column=i * 2, sticky="e", **pad)
            ttk.Entry(linha, textvariable=var, width=w).grid(row=0, column=i * 2 + 1, sticky="w", **pad)

        self.v_rapido = tk.BooleanVar(value=True)
        ttk.Checkbutton(f1, text="Teste rápido (só 5 amostras) - marque na primeira vez para ver se funciona",
                        variable=self.v_rapido).grid(row=3, column=0, columnspan=3, sticky="w", **pad)
        b = ttk.Button(f1, text="PROCESSAR VÍDEO", command=self.processar)
        b.grid(row=4, column=0, columnspan=3, sticky="ew", padx=8, pady=6)
        self.botoes.append(b)

        f2 = ttk.LabelFrame(self, text="2. Gabarito manual (marcar o que realmente aparece em cada vaga)")
        f2.pack(fill="x", padx=10, pady=4)
        f2.columnconfigure(0, weight=1)
        self.lista = tk.Listbox(f2, height=5, exportselection=False)
        self.lista.grid(row=0, column=0, sticky="ew", **pad)
        col = ttk.Frame(f2)
        col.grid(row=0, column=1, **pad)
        for txt, cmd in [("Abrir página do gabarito", self.abrir_gabarito),
                         ("Atualizar lista", self.atualizar_listas)]:
            b = ttk.Button(col, text=txt, command=cmd)
            b.pack(fill="x", pady=2)
            self.botoes.append(b)

        f3 = ttk.LabelFrame(self, text="3. Resultados")
        f3.pack(fill="x", padx=10, pady=4)
        for txt, cmd in [("CALCULAR MÉTRICAS E TABELAS (todos os vídeos com gabarito)", self.metricas),
                         ("Gerar tabela do computador (hardware)", self.sistema),
                         ("Abrir pasta de resultados", self.abrir_resultados)]:
            b = ttk.Button(f3, text=txt, command=cmd)
            b.pack(side="left", expand=True, fill="x", padx=6, pady=6)
            self.botoes.append(b)

        f4 = ttk.LabelFrame(self, text="Andamento")
        f4.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        self.log = tk.Text(f4, height=12, wrap="word", state="disabled", bg="#111", fg="#ddd")
        sb = ttk.Scrollbar(f4, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)

    # ------------------------------------------------------------ util
    def escrever(self, txt):
        self.log.configure(state="normal")
        self.log.insert("end", txt)
        self.log.see("end")
        self.log.configure(state="disabled")

    def atualizar_listas(self):
        ambs = listar_ambientes()
        self.cb_amb["values"] = ambs
        if ambs and self.v_amb.get() not in ambs:
            self.v_amb.set(ambs[0])
        self.itens = listar_avaliacoes()
        self.lista.delete(0, "end")
        for _, texto in self.itens:
            self.lista.insert("end", texto)

    def escolher_video(self):
        p = filedialog.askopenfilename(title="Escolha o vídeo",
                                       filetypes=[("Vídeos", "*.mp4 *.avi *.mov *.mkv *.mpg *.mpeg *.wmv"),
                                                  ("Todos", "*.*")])
        if p:
            self.v_video.set(p)
            if not self.v_rot.get():
                self.v_rot.set(slug(os.path.splitext(os.path.basename(p))[0])[:20])

    def _travar(self, travar):
        self.rodando = travar
        for b in self.botoes:
            b.configure(state="disabled" if travar else "normal")

    def executar(self, cmd, ao_terminar=None):
        if self.rodando:
            return
        self._travar(True)
        self.escrever("\n> " + " ".join('"%s"' % c if " " in c else c for c in cmd[2:]) + "\n")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        def trabalho():
            try:
                p = subprocess.Popen(cmd, cwd=BASE_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding="utf-8", errors="replace", env=env,
                                     creationflags=flags)
                for linha in p.stdout:
                    self.fila.put(("log", linha))
                p.wait()
                self.fila.put(("fim", p.returncode, ao_terminar))
            except Exception as e:
                self.fila.put(("log", "[ERRO] %s\n" % e))
                self.fila.put(("fim", 1, None))

        threading.Thread(target=trabalho, daemon=True).start()

    def _drenar(self):
        try:
            while True:
                item = self.fila.get_nowait()
                if item[0] == "log":
                    self.escrever(item[1])
                else:
                    self._travar(False)
                    self.atualizar_listas()
                    if item[1] == 0:
                        self.escrever("\n=== TERMINOU ===\n")
                        if item[2]:
                            item[2]()
                    else:
                        self.escrever("\n=== TERMINOU COM ERRO (leia as mensagens acima) ===\n")
        except queue.Empty:
            pass
        self.after(100, self._drenar)

    # ------------------------------------------------------------ ações
    def processar(self):
        amb, vid = self.v_amb.get().strip(), self.v_video.get().strip().strip('"')
        if not amb:
            return messagebox.showwarning("Falta o ambiente",
                                          "Nenhum ambiente salvo. Clique em 'Abrir editor de vagas', "
                                          "desenhe as vagas e salve o ambiente.")
        if not vid or not os.path.exists(vid):
            return messagebox.showwarning("Falta o vídeo", "Escolha o arquivo do vídeo.")
        rot = slug(self.v_rot.get() or os.path.splitext(os.path.basename(vid))[0])
        try:
            intervalo = float(self.v_int.get().replace(",", "."))
            assert intervalo > 0
        except Exception:
            return messagebox.showwarning("Intervalo inválido", "Use um número de segundos (ex.: 5).")
        modo = "novo"
        if os.path.exists(os.path.join(PASTA, rot, "previsoes.csv")):
            r = messagebox.askyesnocancel(
                "Já existe '%s'" % rot,
                "Já existe uma avaliação com o nome '%s'.\n\nSIM = continuar de onde parou\n"
                "NÃO = refazer do zero (a antiga é guardada, nada é apagado)\nCANCELAR = voltar" % rot)
            if r is None:
                return
            modo = "continuar" if r else "refazer"
        if not self.v_rapido.get():
            if not messagebox.askyesno("Processar o vídeo inteiro?",
                                       "Isso pode demorar bastante (dependendo do vídeo e do computador). "
                                       "Você pode interromper fechando a janela e retomar depois escolhendo "
                                       "'continuar'.\n\nIniciar?"):
                return
        cmd = montar_comando_amostrar(amb, vid, rot, self.v_cam.get().strip(), self.v_per.get().strip(),
                                      intervalo, self.v_rapido.get(), modo)
        self.executar(cmd)

    def abrir_gabarito(self):
        sel = self.lista.curselection()
        if not sel:
            return messagebox.showinfo("Escolha um vídeo", "Clique em um item da lista primeiro.")
        rot = self.itens[sel[0]][0]
        caminho = os.path.join(PASTA, rot, "revisar.html")
        if not os.path.exists(caminho):
            return messagebox.showwarning("Não encontrado", caminho)
        try:
            os.startfile(caminho)  # Windows
        except AttributeError:
            webbrowser.open("file://" + caminho)

    def metricas(self):
        self.executar([sys.executable, "-u", SCRIPT, "metricas"], self.abrir_resultados)

    def sistema(self):
        self.executar([sys.executable, "-u", SCRIPT, "sistema"])

    def abrir_resultados(self):
        os.makedirs(RESULTADO, exist_ok=True)
        try:
            os.startfile(RESULTADO)
        except AttributeError:
            webbrowser.open("file://" + RESULTADO)

    def abrir_editor(self):
        try:
            flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
            subprocess.Popen([sys.executable, os.path.join(BASE_DIR, "app.py")], cwd=BASE_DIR, creationflags=flags)
            self.escrever("\nServidor iniciando; o navegador vai abrir o editor de vagas (pode levar alguns "
                          "segundos). Depois de salvar as vagas, feche a janela preta do servidor e clique em "
                          "'Atualizar lista'.\n")
        except Exception as e:
            messagebox.showerror("Erro", str(e))


if __name__ == "__main__":
    App().mainloop()
