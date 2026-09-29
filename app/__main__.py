"""Inicia o ClipForge e abre o navegador: python -m app"""
import threading
import webbrowser

import uvicorn

HOST, PORT = "127.0.0.1", 8000


def main() -> None:
    url = f"http://{HOST}:{PORT}"
    print(f"ClipForge rodando em {url}  (feche esta janela ou aperte Ctrl+C para parar)")
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("app.main:app", host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
