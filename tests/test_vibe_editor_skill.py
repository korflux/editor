"""Provas estáticas do gate editorial e visual da skill de edição local."""

from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = PROJECT_ROOT / "skills" / "vibe-editor" / "SKILL.md"
YOUTUBE_REFERENCE_PATH = (
    PROJECT_ROOT / "skills" / "vibe-editor" / "references" / "youtube-editing.md"
)


def read_skill_document(path: Path) -> str:
    """Lê uma instrução da skill em UTF-8 para testar o contrato que o agente recebe."""

    return path.read_text(encoding="utf-8")


class VibeEditorSkillTests(unittest.TestCase):
    """Garante que nenhuma aprovação seja solicitada sem prova estrutural e visual."""

    @classmethod
    def setUpClass(cls) -> None:
        """Carrega os documentos uma vez para manter os testes focados no contrato publicado."""

        cls.skill = read_skill_document(SKILL_PATH)
        cls.youtube_reference = read_skill_document(YOUTUBE_REFERENCE_PATH)

    def test_skill_orders_editorial_gate_before_approval_command(self) -> None:
        """Confirma que o gate aparece antes do comando que registra a aprovação humana."""

        gate_start = self.skill.index("### 7. Auditar a preview antes de pedir aprovação")
        approval_start = self.skill.index("### 8. Registrar a aprovação e gerar a full")
        approval_command = self.skill.index("editor approve", approval_start)

        self.assertLess(gate_start, approval_start)
        self.assertLess(approval_start, approval_command)
        self.assertIn("ainda não peça aprovação", self.skill[:gate_start].lower())
        gate = self.skill[gate_start:approval_start]
        self.assertIn("validação estrutural", gate)
        self.assertIn("inspeção editorial/visual", gate)
        self.assertIn("não peça aprovação", gate)

    def test_skill_documents_temporal_visual_and_lettering_evidence(self) -> None:
        """Confirma comandos e evidências para fronteiras, movimento, fim e lettering."""

        gate_start = self.skill.index("### 7. Auditar a preview antes de pedir aprovação")
        approval_start = self.skill.index("### 8. Registrar a aprovação e gerar a full")
        gate = self.skill[gate_start:approval_start]

        for marker in (
            r"analysis\transcription\<source-id>.json",
            "words[]",
            "extract frames",
            "movimento",
            "último segundo",
            "safe area",
            "fonte real",
            "plan validate",
            "render preview",
        ):
            self.assertIn(marker, gate)

    def test_skill_requires_human_decisions_and_rejects_automation(self) -> None:
        """Confirma registro de tradução, exclusões e falas removidas sem decisão automática."""

        gate_start = self.skill.index("### 7. Auditar a preview antes de pedir aprovação")
        approval_start = self.skill.index("### 8. Registrar a aprovação e gerar a full")
        gate = self.skill[gate_start:approval_start]

        gate_lower = gate.lower()
        for marker in (
            "manter o original, traduzir ou legendar",
            "arquivos excluídos",
            "falas removidas intencionalmente",
            "decisão humana",
            "não se aplica",
        ):
            self.assertIn(marker, gate_lower)
        self.assertIn("não traduza, escolha cortes", gate_lower)

    def test_youtube_reference_repeats_the_approval_gate(self) -> None:
        """Mantém a referência de YouTube alinhada ao fluxo obrigatório da skill principal."""

        audit_start = self.youtube_reference.index("### 1.6 Auditar a preview antes da aprovação")
        approval_start = self.youtube_reference.index("### 1.7 Aprovar e gerar a versão final")
        audit = self.youtube_reference[audit_start:approval_start]

        audit_lower = audit.lower()
        for marker in (
            "validação estrutural",
            "inspeção visual",
            "extract frames",
            "words[]",
            "safe area",
            "fala estrangeira",
            "arquivo excluído",
            "fala removida intencionalmente",
            "não pedir aprovação",
        ):
            self.assertIn(marker, audit_lower)


if __name__ == "__main__":
    unittest.main()
