"""Contrato de captions: jornada real da CLI, borda precoce e falas concorrentes."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from editor.contracts import CliError
from editor.dependencies import resolve_executable
from editor.plan.canonical import canonical_json, canonical_sha256
from editor.plan.schema import DEFAULT_CAPTION_BOXES
from editor.plan.validation import load_validated_plan, validate_plan
from tests.test_plan_validation import run_module, workspace_tempdir, write_plan


def caption(start: float, end: float, source: str = "main", text: str = "Tradução\nconferida") -> dict:
    """Cria uma fala em tempos da fonte, sem antecipar os tempos de timeline esperados."""
    return {"source_id": source, "in_s": start, "out_s": end, "text": text}


def segment(start: float, end: float, *, source: str = "main", mode: str = "keep", loop: bool = False) -> dict:
    """Segmento linear com áudio explícito, que o teste pode substituir pela dublagem."""
    return {
        "video": {"source_id": "main", "in_s": start, "out_s": end, "fit": "contain"},
        "audio": {"mode": mode, "tracks": [] if mode == "mute" else [
            {"source_id": source, "in_s": start, "out_s": end, "gain_db": 0, "loop": loop}
        ]},
    }


class CaptionsPlanTests(unittest.TestCase):
    """Valida o JSON observável com mídia real e isola recusas estruturais sem FFprobe."""

    @classmethod
    def setUpClass(cls) -> None:
        """Gera mídia longa e uma fonte sem áudio para provar duração e compatibilidade reais."""
        cls.workspace = workspace_tempdir()
        cls.root = cls.workspace.__enter__()
        ffmpeg = resolve_executable("ffmpeg")
        if ffmpeg is None:
            raise RuntimeError("FFmpeg local obrigatório para a prova de captions.")
        for name, audio in (("main.mp4", True), ("silent.mp4", False)):
            command = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x180:r=2:d=40"]
            if audio:
                command += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=40"]
            command += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
            if audio:
                command += ["-c:a", "aac", "-shortest"]
            command += [str(cls.root / name)]
            subprocess.run(command, capture_output=True, check=True)

    @classmethod
    def tearDownClass(cls) -> None:
        """Remove o trabalho descartável pelos mesmos controles da suíte vigente."""
        cls.workspace.__exit__(None, None, None)

    def plan(self) -> dict:
        """Plano pequeno válido; fontes distintas compartilham mídia sem compartilhar o ID."""
        return {
            "schema_version": 2, "profile": {"aspect_ratio": "16:9"},
            "sources": [
                {"id": "main", "kind": "video", "path": str(self.root / "main.mp4")},
                {"id": "other", "kind": "video", "path": str(self.root / "main.mp4")},
                {"id": "silent", "kind": "video", "path": str(self.root / "silent.mp4")},
            ],
            "segments": [segment(0, 2)], "cuts": [], "layers": [],
            "captions": {"style": {"font": "Arial", "size_px": 32, "color": "#FFFFFF"}, "items": [caption(0.2, 1.2)]},
        }

    def test_cli_audio_journey_and_private_render_contract(self) -> None:
        """Jump cut, gancho, reordenação, J/L, replace e loop têm tempos calculados à parte."""
        plan = self.plan()
        plan["segments"] = [segment(0, 2), segment(3, 5), segment(10, 12), segment(0, 2),
                            segment(20, 22, source="other", mode="replace"), segment(20, 22),
                            segment(30, 32), segment(0, 2, loop=True)]
        # S6 ouve 20..23 na timeline 9.5..12.5: antecipação J e extensão L.
        plan["segments"][5]["audio"]["tracks"][0]["out_s"] = 23
        plan["cuts"] = [{"after_segment": 5, "type": "j", "duration_s": 0.5},
                        {"after_segment": 6, "type": "l", "duration_s": 0.5}]
        plan["captions"]["items"] = [caption(1.5, 3.5), caption(10.2, 11.2), caption(20.1, 20.6),
                                    caption(22.1, 22.6), caption(4.8, 5.1), caption(25, 26), caption(0.2, 1.2)]
        plan["captions"]["style"].update({"shadow": {"offset_x_px": 2, "offset_y_px": 3, "blur_px": 0}, "outline": {"width_px": 2}})
        expected = {"items": 7, "occurrences": [
            {"item": 7, "start_s": 0.2, "end_s": 1.2},
            {"item": 1, "start_s": 1.5, "end_s": 2.5},
            {"item": 2, "start_s": 4.2, "end_s": 5.2},
            {"item": 7, "start_s": 6.2, "end_s": 7.2},
            {"item": 1, "start_s": 7.5, "end_s": 8.0},
            {"item": 3, "start_s": 9.6, "end_s": 10.1},
            {"item": 4, "start_s": 11.6, "end_s": 12.1},
        ], "unplaced": [5, 6]}
        write_plan(self.root, plan)
        response = run_module("plan", "validate", "--workdir", str(self.root), "--json")
        self.assertEqual(response.returncode, 0, response.stdout + response.stderr)
        data = json.loads(response.stdout)["data"]
        self.assertEqual(data["captions"], expected)
        document, private = load_validated_plan(self.root)
        self.assertEqual(document, plan)
        self.assertEqual(private["_captions"]["occurrences"], expected["occurrences"])
        style = private["_captions"]["style"]
        self.assertEqual({key: style[key] for key in DEFAULT_CAPTION_BOXES["16:9"]}, DEFAULT_CAPTION_BOXES["16:9"])
        self.assertEqual(style["align"], "center")
        self.assertTrue(style["wrap"])
        self.assertEqual(style["shadow"]["opacity"], 0.5)
        self.assertEqual(style["outline"]["color"], "#000000")
        self.assertTrue(Path(private["_resolved_font_files"]["Arial"]).is_file())
        self.assertFalse(any(key.startswith("_") for key in data))
        self.assertEqual(data["canonical_json"], canonical_json(plan))
        self.assertEqual(data["plan_sha256"], canonical_sha256(plan))

    def test_merge_before_minimum_and_exact_threshold(self) -> None:
        """Duas sobras de 0,2 s contíguas são legíveis juntas; 0,3 s é incluído."""
        plan = self.plan()
        plan["segments"] = [segment(0, 1), segment(2, 3), segment(4, 5)]
        plan["captions"]["items"] = [caption(0.8, 2.2), caption(4, 4.3)]
        write_plan(self.root, plan)
        result = validate_plan(self.root)["captions"]
        self.assertEqual(result["occurrences"], [{"item": 1, "start_s": 0.8, "end_s": 1.2}, {"item": 2, "start_s": 2, "end_s": 2.3}])
        self.assertEqual(result["unplaced"], [])

    def test_without_captions_is_identical_and_boxes_follow_profile(self) -> None:
        """Remover captions restaura envelope/hash; normalização não reescreve o canônico."""
        plan = self.plan()
        del plan["captions"]
        write_plan(self.root, plan)
        baseline = validate_plan(self.root)
        self.assertNotIn("captions", baseline)
        self.assertEqual(baseline["canonical_json"], canonical_json(plan))
        self.assertEqual(baseline["plan_sha256"], canonical_sha256(plan))
        for ratio, box in DEFAULT_CAPTION_BOXES.items():
            with self.subTest(ratio=ratio):
                modified = self.plan()
                modified["profile"]["aspect_ratio"] = ratio
                modified["captions"]["style"]["x"] = 0.04
                write_plan(self.root, modified)
                _, private = load_validated_plan(self.root)
                self.assertEqual({key: private["_captions"]["style"][key] for key in box}, {**box, "x": 0.04})
        write_plan(self.root, plan)
        self.assertEqual(validate_plan(self.root), baseline)

    def test_structural_refusals_before_media_and_without_echo(self) -> None:
        """Variações de A3 atravessam a borda real do plano, sem consultar FFprobe."""
        mutations = [
            ("block type", lambda p: p.update(captions=[])),
            ("block unknown", lambda p: p["captions"].update(secret="MARCADOR")),
            ("style type", lambda p: p["captions"].update(style=[])),
            ("style unknown", lambda p: p["captions"]["style"].update(wrap=False)),
            ("items type", lambda p: p["captions"].update(items={})),
            ("items empty", lambda p: p["captions"].update(items=[])),
            ("items limit", lambda p: p["captions"].update(items=[caption(0, 1)] * 2001)),
            ("item type", lambda p: p["captions"].update(items=[[]])),
            ("item unknown", lambda p: p["captions"]["items"][0].update(secret="MARCADOR")),
        ]
        for field, value in [("text", ""), ("text", "  \n"), ("text", "MARCADOR" * 26), ("text", "MARCADOR\x00"), ("text", False),
                             ("source_id", "MARCADOR/invalid"), ("source_id", []), ("in_s", -1), ("in_s", True),
                             ("in_s", float("nan")), ("out_s", float("inf")), ("out_s", 0), ("out_s", 31)]:
            mutations.append((f"item {field} {value!r}", lambda p, f=field, v=value: p["captions"]["items"][0].update({f: v})))
        for field, value in [("font", "MARCADOR/path"), ("font", ""), ("font", "x" * 129), ("font", "MARCADOR\x00"),
                             ("size_px", True), ("size_px", 513), ("size_px", 1.5), ("color", "MARCADOR"),
                             ("align", "MARCADOR"), ("x", True), ("y", float("inf")), ("width", 0), ("height", 1.0),
                             ("shadow", None), ("shadow", {"secret": "MARCADOR"}),
                             ("shadow", {"offset_x_px": 101, "offset_y_px": 0, "blur_px": 0}),
                             ("shadow", {"offset_x_px": 0, "offset_y_px": 0, "blur_px": 51}),
                             ("shadow", {"offset_x_px": 0, "offset_y_px": 0, "blur_px": 0, "opacity": 2}),
                             ("outline", {"width_px": 0}), ("outline", {"width_px": 2, "color": "MARCADOR"})]:
            mutations.append((f"style {field} {value!r}", lambda p, f=field, v=value: p["captions"]["style"].update({f: v})))
        mutations += [(f"missing {field}", lambda p, f=field: p["captions"]["style"].pop(f)) for field in ("font", "size_px", "color")]
        for name, mutate in mutations:
            with self.subTest(case=name):
                plan = self.plan()
                mutate(plan)
                write_plan(self.root, plan)
                with patch("editor.plan.validation.media.inspect_source") as inspect:
                    with self.assertRaises(CliError) as raised:
                        validate_plan(self.root)
                    self.assertEqual(raised.exception.code, "PLAN_INVALID")
                    self.assertNotIn("MARCADOR", raised.exception.message)
                    inspect.assert_not_called()

    def test_source_timeline_refusals_and_overlap_tolerance(self) -> None:
        """Fonte, duração e sobreposição usam metadados reais e tolerância fechada de 1 ms."""
        cases = [
            ("missing", [caption(0, 1, "missing")], None),
            ("no audio", [caption(0, 1, "silent")], None),
            ("duration", [caption(39.5, 40.1)], None),
            ("source overlap", [caption(0, 1), caption(0.9989, 1.8)], None),
            ("timeline overlap", [caption(0.2, 1), caption(0.3, 1.2, "other")], "mix"),
        ]
        for name, items, mode in cases:
            with self.subTest(case=name):
                plan = self.plan()
                plan["captions"]["items"] = items
                if mode:
                    plan["segments"][0]["audio"] = {"mode": mode, "tracks": [
                        {"source_id": source, "in_s": 0, "out_s": 2, "gain_db": 0}
                        for source in ("main", "other")
                    ]}
                write_plan(self.root, plan)
                response = run_module("plan", "validate", "--workdir", str(self.root), "--json")
                self.assertEqual(response.returncode, 5, response.stdout + response.stderr)
                error = json.loads(response.stdout)["error"]
                self.assertEqual(error["code"], "PLAN_INVALID")
                self.assertNotIn("missing", error["message"])
                if "overlap" in name:
                    self.assertIn("1 e 2", error["message"])
        # Duas falas de fontes diferentes concorrentes em J/L também devem ser recusadas.
        for cut in ("j", "l"):
            plan = self.plan()
            plan["segments"] = [segment(0, 2), segment(0, 2, source="other", mode="replace")]
            plan["cuts"] = [{"after_segment": 1, "type": cut, "duration_s": 0.5}]
            target = 1 if cut == "j" else 0
            plan["segments"][target]["audio"]["tracks"][0]["out_s"] = 2.5
            plan["captions"]["items"] = (
                [caption(1.5, 2), caption(0, 0.5, "other")] if cut == "j"
                else [caption(2, 2.5), caption(0, 0.5, "other")]
            )
            write_plan(self.root, plan)
            with self.assertRaises(CliError) as raised:
                validate_plan(self.root)
            self.assertEqual(raised.exception.code, "PLAN_INVALID")
            self.assertIn("timeline", raised.exception.message)
        for source in ("main", "other"):
            plan = self.plan()
            plan["captions"]["items"] = [caption(0, 1), caption(0.999, 1.8, source)]
            plan["segments"][0]["audio"] = {"mode": "mix", "tracks": [
                {"source_id": sid, "in_s": 0, "out_s": 2, "gain_db": 0} for sid in ("main", "other")
            ]}
            write_plan(self.root, plan)
            self.assertEqual(len(validate_plan(self.root)["captions"]["occurrences"]), 2)


if __name__ == "__main__":
    unittest.main()
