"""
Ponto de entrada do Smart Parking.

Este é o único arquivo Python que o usuário precisa iniciar manualmente.
O iniciar.bat chama este arquivo, que sobe:
    - API principal / Dashboard na porta 5000
    - API de configuração de ambientes na porta 5001
"""

import os
import sys
import time
import threading
import webbrowser

from apiFlask_dev import app as dashboard_app, socketio, start_status_monitor
from roi_api import app as roi_app


HOST = "127.0.0.1"
DASHBOARD_PORT = 5000
ROI_PORT = 5001


def run_roi_api():
    """Inicia a API responsável pelos ambientes e captura de frames."""
    print("[ROI] Iniciando API de configuração...")
    roi_app.run(
        host=HOST,
        port=ROI_PORT,
        debug=False,
        use_reloader=False
    )


def run_dashboard():
    """Inicia a API principal e o dashboard."""
    print("[DASHBOARD] Iniciando dashboard...")

    socketio.run(
        dashboard_app,
        host=HOST,
        port=DASHBOARD_PORT,
        debug=False,
        use_reloader=False,
        allow_unsafe_werkzeug=True
    )


def open_browser():
    """Abre o dashboard e o editor de ROI automaticamente, cada um em
    uma aba, depois que os serviços iniciam.

    Repare que o editor de ROI é servido pela própria API do dashboard
    (rota /roi em apiFlask_dev.py, porta 5000) — a roi_api.py na porta
    5001 é só a API JSON de dados, não tem páginas HTML.
    """
    time.sleep(2)

    dashboard_url = f"http://{HOST}:{DASHBOARD_PORT}"
    roi_editor_url = f"http://{HOST}:{DASHBOARD_PORT}/roi"

    print()
    print("------------------------------------------------------------")
    print(f"Dashboard    : {dashboard_url}")
    print(f"Editor ROI   : {roi_editor_url}")
    print(f"ROI API (JSON): http://{HOST}:{ROI_PORT}")
    print("------------------------------------------------------------")
    print()
    print("Abrindo o navegador...")

    webbrowser.open(dashboard_url)
    time.sleep(0.5)
    webbrowser.open_new_tab(roi_editor_url)


def main():
    print()
    print("=" * 60)
    print("                    SMART PARKING")
    print("             Sistema de monitoramento")
    print("=" * 60)
    print()

    # Garante que os diretórios necessários existam.
    os.makedirs("reference_frames", exist_ok=True)
    os.makedirs("data", exist_ok=True)
    os.makedirs("templates", exist_ok=True)
    os.makedirs("static", exist_ok=True)

    # Inicia o monitoramento do arquivo de status.
    start_status_monitor()

    # API de configuração.
    roi_thread = threading.Thread(
        target=run_roi_api,
        daemon=True
    )
    roi_thread.start()

    # Dashboard/API principal.
    dashboard_thread = threading.Thread(
        target=run_dashboard,
        daemon=True
    )
    dashboard_thread.start()

    # Abre o navegador sem bloquear os servidores.
    browser_thread = threading.Thread(
        target=open_browser,
        daemon=True
    )
    browser_thread.start()

    print()
    print("[OK] Smart Parking iniciado.")
    print()
    print("Não feche esta janela enquanto estiver utilizando o sistema.")
    print("Para encerrar, pressione CTRL+C.")
    print()

    try:
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print()
        print("Encerrando Smart Parking...")
        sys.exit(0)


if __name__ == "__main__":
    main()