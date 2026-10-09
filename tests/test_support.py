"""Testes da limpeza compartilhada de pastas descartáveis (`tests/support.py::remove_tree`).

Prova que a pasta `tmp-*` de um teste não fica para trás: remoção direta, repetição enquanto um processo
externo (simulado com um handle de diretório do Windows) a mantém aberta, e aviso visível, sem exceção,
quando ela nunca sai.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, redirect_stderr
import ctypes
import io
import os
from pathlib import Path
import shutil
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from tests.support import REMOVE_TREE_ATTEMPTS, remove_tree


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def directory_held_open(path: Path, release_after_s: float) -> Iterator[None]:
    """Mantém a pasta aberta por um processo externo simulado (Windows) e a solta depois de `release_after_s`.

    O handle é aberto sem `FILE_SHARE_DELETE`, como faz um indexador, antivírus ou observador de arquivos:
    o conteúdo da pasta ainda pode ser apagado, mas o `rmdir` dela falha com violação de compartilhamento
    até o handle ser fechado. É exatamente o sintoma das pastas `tmp-*` vazias que sobravam na raiz.
    """

    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    file_list_directory, share_read_write, open_existing, backup_semantics = 0x1, 0x3, 3, 0x02000000
    handle = kernel32.CreateFileW(
        str(path), file_list_directory, share_read_write, None, open_existing, backup_semantics, None
    )
    if handle in (None, ctypes.c_void_p(-1).value):
        raise OSError(ctypes.get_last_error(), "não foi possível abrir a pasta para a simulação")

    released = threading.Event()

    def release() -> None:
        """Fecha o handle uma única vez, seja pelo temporizador ou ao sair do contexto."""

        if not released.is_set():
            released.set()
            kernel32.CloseHandle(handle)

    timer = threading.Timer(release_after_s, release)
    timer.start()
    try:
        yield
    finally:
        timer.cancel()
        release()


class RemoveTreeTests(unittest.TestCase):
    """`remove_tree` não deixa a pasta de teste para trás e não esconde nem promove a falha quando ela persiste."""

    def new_tree(self) -> Path:
        """Cria `tmp-remove-tree-*` com um arquivo aninhado e garante a remoção ao fim do teste."""

        path = PROJECT_ROOT / f"tmp-remove-tree-{uuid4().hex}"
        (path / "sub").mkdir(parents=True)
        (path / "sub" / "preview.mp4").write_bytes(b"x" * 1024)
        # Rede de segurança: se uma asserção falhar antes da remoção, a pasta não fica no repositório.
        self.addCleanup(remove_tree, path)
        return path

    def test_removes_the_folder_on_the_first_attempt_without_waiting(self) -> None:
        """Caminho normal: uma tentativa basta e nenhuma espera é gasta."""

        path = self.new_tree()

        with patch.object(time, "sleep") as sleep:
            remove_tree(path)

        self.assertFalse(path.exists())
        sleep.assert_not_called()

    def test_removing_a_folder_that_is_already_gone_is_not_an_error(self) -> None:
        """A limpeza é idempotente: uma segunda chamada sobre a pasta já removida não falha."""

        path = self.new_tree()
        remove_tree(path)

        remove_tree(path)

        self.assertFalse(path.exists())

    @unittest.skipUnless(os.name == "nt", "a violação de compartilhamento da pasta é comportamento do Windows")
    def test_waits_for_an_external_process_to_release_the_folder(self) -> None:
        """Reproduz o defeito (pasta vazia sobrando com `ignore_errors=True`) e prova que `remove_tree` o resolve."""

        path = self.new_tree()

        with directory_held_open(path, release_after_s=1.0):
            shutil.rmtree(path, ignore_errors=True)
            # O padrão antigo: o conteúdo some, a pasta fica, e nenhum erro é reportado.
            self.assertTrue(path.is_dir(), "a simulação deveria impedir o rmdir da pasta")
            self.assertEqual(list(path.iterdir()), [])

            remove_tree(path)

        self.assertFalse(path.exists())

    def test_warns_instead_of_failing_when_the_folder_never_goes_away(self) -> None:
        """Pasta que resiste a todas as tentativas vira aviso visível com o nome dela, nunca exceção nem silêncio."""

        path = self.new_tree()
        stderr = io.StringIO()

        # `rmtree` que não remove nada simula a pasta que resiste a todas as tentativas.
        with patch.object(shutil, "rmtree"), patch.object(time, "sleep") as sleep, redirect_stderr(stderr):
            with self.assertWarns(ResourceWarning) as warned:
                remove_tree(path)  # não levanta: o desfecho é só o aviso

        self.assertIn(path.name, str(warned.warning))
        self.assertNotIn(str(PROJECT_ROOT), str(warned.warning))
        # A linha em stderr sobrevive a um filtro de avisos e também cita a pasta pelo nome.
        self.assertIn(path.name, stderr.getvalue())
        self.assertEqual(sleep.call_count, REMOVE_TREE_ATTEMPTS)
        self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
