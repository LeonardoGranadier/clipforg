class ClipForgeError(Exception):
    """Erro esperado, com mensagem pronta para mostrar ao usuário (em português)."""


class Cancelled(ClipForgeError):
    """A tarefa foi cancelada pelo usuário."""

    def __init__(self, message: str = "Cancelado pelo usuário.") -> None:
        super().__init__(message)


class NotFound(ClipForgeError):
    """Projeto, corte ou arquivo inexistente (HTTP 404 na interface)."""
