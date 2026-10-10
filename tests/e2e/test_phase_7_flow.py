"""Prova E2E da Phase 7: jornada da interface de revisão, da abertura à full aceita pela aprovação v2.

Percorre, pelos pontos de entrada reais (CLI `python -m editor` e HTTP contra o servidor que o `ui start`
sobe em segundo plano) e com uma fonte 1080p sintética gerada por `ffmpeg -f lavfi`: `init`, `capabilities`,
`plan validate`, `proxy build`, `ui start` (duas vezes), sessão por token, stream ao vivo e quadro de scrub,
texto da seleção, plano alterado por fora (a "IA") com recarga do estado, aprovação pela API com cookie e
`Origin`, `render full` aceitando a aprovação sem nenhuma preview, mutação posterior que a invalida e, por fim,
`ui stop` sem processo órfão (A1 a A10 da spec, C1 e C4).

As expectativas (formato do envelope, estados, resoluções, campos do registro de aprovação) vêm de
`capabilities --json` e das constantes de `ui/limits.py` e `render/live.py`, não de literais copiados do código
que as gera. O que o inventário não sabe (hash do plano, dimensões do canvas, duração) vem de `plan validate`.
"""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path
from typing import Any

from editor.render import live
from editor.ui import limits as ui_limits
from tests.e2e.test_mvp_flow import (
    assert_json_error,
    assert_json_success,
    run_cli,
    sha256_file,
    workspace_tempdir,
)
from tests.e2e.test_phase_5_flow import make_source
from tests.test_ui_server import WINDOWS, Client, ffmpeg_pids_using, process_alive, wait_until


SOURCE_ID = "main"
FIRST_SEGMENT = (1.0, 4.0)
SECOND_SEGMENT = (8.0, 11.0)
J_CUT_S = 0.4
"""Corte J depois do primeiro segmento: o áudio do segundo entra 0,4 s antes da imagem."""
TIMELINE_DURATION_S = 6.0
STREAM_START_S = 2.52
"""Instante do play e do quadro de scrub, dentro do primeiro segmento e na grade de 25 quadros (63 quadros)."""
SELECTION = (1.0, 4.5)
"""Intervalo da seleção: atravessa o corte entre os dois segmentos e cruza o texto."""
FULL_SIZE = (1920, 1080)
TEXT_BEFORE = "Primeira versão do letreiro"
TEXT_AFTER = "Letreiro revisado pela IA"
APPROVAL_PATH = "approval.json"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def segment(window: tuple[float, float], audio_in_s: float | None = None) -> dict[str, Any]:
    """Trecho da fonte com a faixa de áudio do mesmo intervalo (ou de `audio_in_s`, para o corte J)."""

    in_s, out_s = window
    return {
        "video": {"source_id": SOURCE_ID, "in_s": in_s, "out_s": out_s, "fit": "preserve"},
        "audio": {
            "mode": "keep",
            "tracks": [
                {"source_id": SOURCE_ID, "in_s": in_s if audio_in_s is None else audio_in_s, "out_s": out_s, "gain_db": 0}
            ],
        },
    }


def plan_document(text: str) -> dict[str, Any]:
    """Plano 16:9 de dois trechos de 3 s com um corte J e um letreiro; só o `text` muda entre as versões."""

    return {
        "schema_version": 2,
        "profile": {"aspect_ratio": "16:9"},
        "sources": [{"id": SOURCE_ID, "kind": "video", "path": "sources/main.mp4"}],
        "segments": [segment(FIRST_SEGMENT), segment(SECOND_SEGMENT, audio_in_s=SECOND_SEGMENT[0] - J_CUT_S)],
        "cuts": [{"after_segment": 1, "type": "j", "duration_s": J_CUT_S}],
        "layers": [{
            "type": "text", "start_s": 1.0, "end_s": 4.0, "x": 0.08, "y": 0.72, "width": 0.84, "height": 0.1,
            "text": text, "font": "Arial", "size_px": 64, "color": "#FFFFFF", "align": "center", "wrap": True,
        }],
        "audio_processing": {
            "normalize_loudness": False,
            "target_lufs": -16,
            "limiter": {"enabled": False, "ceiling_dbfs": -1},
            "ducking": False,
        },
    }


def probe(path: Path) -> dict[str, Any]:
    """Streams (codec e dimensões) e duração de um arquivo, pelo FFprobe."""

    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height:format=duration",
         "-of", "json", str(path)],
        capture_output=True, check=True,
    )
    return json.loads(result.stdout)


def reduced(size: int, divisor: int) -> int:
    """Lado do canvas numa fração do player: dividido e arredondado para baixo a par (a regra do spec, suposição 3)."""

    return max(2, size // divisor // 2 * 2)


class ReviewInterfaceFlowTests(unittest.TestCase):
    """Jornada completa da interface e o contrato de aprovação v2 conferido pela CLI."""

    def command(self, *arguments: str, workdir: Path | None = None) -> dict[str, Any]:
        """Executa a CLI com `--workdir` e devolve o envelope de sucesso (falha o teste se o comando falhar)."""

        extra = ["--workdir", str(workdir)] if workdir is not None else []
        result = run_cli(*arguments, *extra, "--json")
        # O `command` do envelope é o grupo e a operação (`ui start`, `plan validate`), sem as opções.
        return assert_json_success(self, result, " ".join(item for item in arguments if not item.startswith("--")))

    def capabilities(self) -> dict[str, Any]:
        """`data` de `capabilities --json` pelo ponto de entrada real."""

        return assert_json_success(self, run_cli("capabilities", "--json"), "capabilities")["data"]

    def prepare(self, workdir: Path, text: str) -> None:
        """`init`, a fonte 1080p e o plano da primeira versão."""

        self.command("init", workdir=workdir)
        (workdir / "sources").mkdir()
        make_source(workdir / "sources" / "main.mp4")
        (workdir / "plan.json").write_text(json.dumps(plan_document(text)), encoding="utf-8")

    def stop_interface(self, workdir: Path) -> None:
        """Encerra a instância do trabalho se ainda existir (limpeza que roda mesmo se o teste falhar)."""

        run_cli("ui", "stop", "--workdir", str(workdir), "--json")

    def state(self, client: Client) -> dict[str, Any]:
        """`data` de `GET /api/state`."""

        response = client.get("/api/state")
        self.assertEqual(response.status, 200, response.body)
        return response.json["data"]

    def post_approval(self, client: Client, port: int, shown: dict[str, str]) -> Any:
        """`POST /api/approve` como o navegador: cookie da sessão, `Origin` própria e o corpo JSON dos dois valores."""

        return client.request(
            "POST", "/api/approve", body=json.dumps(shown).encode("utf-8"),
            headers={"Origin": f"http://127.0.0.1:{port}", "Content-Type": "application/json"},
        )

    # --- A jornada ---

    def test_journey_from_ui_start_to_the_full_accepted_by_the_interface_approval(self) -> None:
        capabilities = self.capabilities()
        interface = capabilities["operations"]["ui"]
        approve_contract = capabilities["operations"]["approve"]
        self.assertEqual(capabilities["cli_version"], "2.0.0")

        with workspace_tempdir("phase-7") as workdir:
            self.addCleanup(self.stop_interface, workdir)
            self.prepare(workdir, TEXT_BEFORE)

            # Plano válido e proxy construído antes da revisão: a interface nunca constrói proxy.
            validation = self.command("plan", "validate", workdir=workdir)["data"]
            first_hash = validation["plan_sha256"]
            self.assertEqual(validation["timeline"]["duration_seconds"], TIMELINE_DURATION_S)
            profile = validation["profile"]
            self.assertEqual((profile["full_width"], profile["full_height"]), FULL_SIZE)
            proxies = self.command("proxy", "build", workdir=workdir)["data"]["proxies"]
            self.assertEqual([item["status"] for item in proxies], ["created"])

            # --- ui start, duas vezes: a segunda reaproveita a instância ---
            started = self.command("ui", "start", "--no-open", workdir=workdir)["data"]
            self.assertEqual(sorted(started), sorted(interface["commands"]["start"]["envelope_fields"]))
            self.assertEqual(started["state"], interface["commands"]["start"]["states"][0])
            self.assertRegex(started["url"], rf"^http://{re.escape(ui_limits.HOST)}:{started['port']}/\?token=[A-Za-z0-9_-]{{43}}$")
            self.assertTrue(process_alive(started["pid"]))
            again = self.command("ui", "start", "--no-open", workdir=workdir)["data"]
            self.assertEqual(again, {**started, "state": interface["commands"]["start"]["states"][1]})

            # --- sessão: o token vira cookie e sai da URL ---
            token = started["url"].split("token=", 1)[1]
            client = Client(started["port"])
            login = client.login(token)
            self.assertEqual(login.status, 302)
            self.assertEqual(login.headers["location"], "/")
            self.assertTrue(client.cookie.startswith(f"{ui_limits.COOKIE_PREFIX}{started['port']}="))
            self.assertNotIn(token, login.headers["location"])
            anonymous = Client(started["port"]).get("/api/state")
            self.assertEqual((anonymous.status, anonymous.code), (401, "UI_TOKEN_INVALID"))

            # --- estado: o mesmo hash do `plan validate`, proxy válido e nenhuma aprovação ainda ---
            shown = wait_until(
                lambda: (lambda data: data if data["state"] == "ready" else None)(self.state(client)),
                message="o plano não foi carregado",
            )
            self.assertEqual(shown["resolutions"], interface["resolutions"]["allowed"])
            self.assertEqual(shown["workdir_name"], workdir.name)
            self.assertEqual(shown["plan"]["plan_sha256"], first_hash)
            self.assertEqual(shown["plan"]["duration_s"], TIMELINE_DURATION_S)
            self.assertTrue(shown["plan"]["sources"][0]["proxy"])
            self.assertEqual(shown["approval"], {"state": "none", "approved_at": None})

            # --- stream ao vivo e quadro de scrub, nas frações (leem o proxy) ---
            for resolution, divisor in (("1/2", 2), ("1/4", 4)):
                with self.subTest(resolution=resolution):
                    canvas = (reduced(FULL_SIZE[0], divisor), reduced(FULL_SIZE[1], divisor))
                    self.assertEqual(live.RESOLUTION_DIVISORS[resolution], divisor)
                    response = client.get(f"/api/stream?t={STREAM_START_S}&res={resolution}")
                    self.assertEqual((response.status, response.headers["content-type"]), (200, "video/mp4"))
                    self.assertEqual(float(response.headers["x-editor-start-s"]), STREAM_START_S)
                    streamed = workdir / f"stream-{resolution.replace('/', '-')}.mp4"
                    streamed.write_bytes(response.body)
                    info = probe(streamed)
                    streams = {item["codec_type"]: item for item in info["streams"]}
                    self.assertEqual((streams["video"]["codec_name"], streams["audio"]["codec_name"]), ("h264", "aac"))
                    self.assertEqual((streams["video"]["width"], streams["video"]["height"]), canvas)
                    self.assertAlmostEqual(
                        float(info["format"]["duration"]), TIMELINE_DURATION_S - STREAM_START_S, delta=0.3
                    )
            frame = client.get(f"/api/frame?t={STREAM_START_S}&res=1/4")
            self.assertEqual((frame.status, frame.headers["content-type"]), (200, "image/jpeg"))
            self.assertEqual(frame.body[:3], b"\xff\xd8\xff")

            # --- seleção: o bloco "Copiar para IA" confere com o plano e não vaza caminho nem token ---
            start_s, end_s = SELECTION
            selection = client.get(f"/api/selection?start={start_s}&end={end_s}")
            self.assertEqual(selection.status, 200, selection.body)
            chosen = selection.json["data"]
            self.assertEqual(chosen["plan_sha256"], first_hash)
            self.assertEqual([item["index"] for item in chosen["segments"]], [1, 2])
            self.assertEqual([(item["video"]["source_in_s"], item["video"]["source_out_s"]) for item in chosen["segments"]],
                             [(2.0, 4.0), (8.0, 9.5)])
            self.assertEqual([item["audio_cut"] for item in chosen["segments"]], [None, None])
            self.assertEqual([(item["index"], item["type"], item["text"]) for item in chosen["layers"]], [(1, "text", TEXT_BEFORE)])
            self.assertEqual(
                (chosen["start_s"], chosen["end_s"], chosen["duration_s"]), (start_s, end_s, end_s - start_s)
            )
            for required in (workdir.name, first_hash, "Pedido:", TEXT_BEFORE):
                self.assertIn(required, chosen["text"])
            for secret in (str(workdir), str(workdir.parent), token, "\\\\"):
                self.assertNotIn(secret, chosen["text"])

            # --- a "IA" altera o plano por fora: o estado recarrega com o hash novo ---
            (workdir / "plan.json").write_text(json.dumps(plan_document(TEXT_AFTER)), encoding="utf-8")
            reloaded = wait_until(
                lambda: (lambda data: data if data["plan"]["plan_sha256"] != first_hash else None)(self.state(client)),
                timeout_s=10, message="o estado não recarregou depois da edição do plano",
            )
            self.assertEqual(reloaded["state"], "ready")
            second_hash = self.command("plan", "validate", workdir=workdir)["data"]["plan_sha256"]
            self.assertNotEqual(second_hash, first_hash)
            self.assertEqual(reloaded["plan"]["plan_sha256"], second_hash)
            self.assertEqual(reloaded["plan"]["sources_fingerprint"], shown["plan"]["sources_fingerprint"])
            new_selection = client.get(f"/api/selection?start={start_s}&end={end_s}").json["data"]
            self.assertEqual(new_selection["plan_sha256"], second_hash)
            self.assertIn(TEXT_AFTER, new_selection["text"])

            # --- aprovação pela API: a versão antiga é recusada sem gravar; a atual é aprovada ---
            old_version = {name: shown["plan"][name] for name in approve_contract["ui"]["binds_loaded_version"]}
            refused = self.post_approval(client, started["port"], old_version)
            self.assertEqual((refused.status, refused.code), (409, approve_contract["ui"]["changed"]))
            self.assertFalse((workdir / APPROVAL_PATH).exists())
            current_version = {name: reloaded["plan"][name] for name in approve_contract["ui"]["binds_loaded_version"]}
            approved = self.post_approval(client, started["port"], current_version)
            self.assertEqual(approved.status, 200, approved.body)
            self.assertEqual(approved.json["data"]["plan_sha256"], second_hash)
            self.assertFalse((workdir / "preview.mp4").exists(), "a aprovação v2 não depende de preview")
            record = json.loads((workdir / APPROVAL_PATH).read_text(encoding="utf-8"))
            self.assertEqual(sorted(record), approve_contract["record_fields"])
            self.assertEqual(record["schema_version"], approve_contract["schema_version"])
            self.assertEqual(record["plan_sha256"], second_hash)
            self.assertEqual(record["approved_at"], approved.json["data"]["approved_at"])
            self.assertEqual(
                record["sources"], [{"source_id": SOURCE_ID, "sha256": sha256_file(workdir / "sources" / "main.mp4")}]
            )
            self.assertTrue(all(SHA256_PATTERN.match(item["sha256"]) for item in record["sources"]))
            self.assertEqual(
                wait_until(
                    lambda: (lambda value: value if value["state"] == "current" else None)(self.state(client)["approval"]),
                    message="a aprovação não ficou em dia",
                ),
                {"state": "current", "approved_at": record["approved_at"]},
            )

            # --- render full aceita a aprovação (sem preview) e publica a full ---
            full = self.command("render", "full", workdir=workdir)["data"]
            self.assertEqual(sorted(full["approval"]), ["approved_at", "plan_sha256"])
            self.assertEqual(full["approval"], {"approved_at": record["approved_at"], "plan_sha256": second_hash})
            self.assertNotIn("preview_sha256", json.dumps(full))
            full_path = workdir / "full.mp4"
            info = probe(full_path)
            video = next(item for item in info["streams"] if item["codec_type"] == "video")
            self.assertEqual((video["width"], video["height"]), FULL_SIZE)
            self.assertAlmostEqual(float(info["format"]["duration"]), TIMELINE_DURATION_S, delta=0.3)
            self.assertFalse((workdir / "preview.mp4").exists())

            # --- plano alterado depois de aprovado: selo desatualizado e a full recusa, preservando a vigente ---
            full_hash = sha256_file(full_path)
            (workdir / "plan.json").write_text(json.dumps(plan_document(TEXT_BEFORE)), encoding="utf-8")
            wait_until(
                lambda: self.state(client)["approval"]["state"] == "stale",
                timeout_s=20, message="a aprovação não ficou desatualizada",
            )
            refused_full = run_cli("render", "full", "--workdir", str(workdir), "--json")
            payload = assert_json_error(self, refused_full)
            self.assertEqual(payload["error"]["code"], approve_contract["release_gate"]["stale"])
            self.assertEqual(sha256_file(full_path), full_hash)

            # --- ui stop: sem processo órfão e sem registro, e o segundo stop informa not_running ---
            stopped = self.command("ui", "stop", workdir=workdir)["data"]
            self.assertEqual(stopped, {"state": interface["commands"]["stop"]["states"][0]})
            wait_until(lambda: not process_alive(started["pid"]), message="o servidor continua vivo")
            self.assertFalse((workdir / ".tmp" / ui_limits.REGISTRY_NAME).exists())
            self.assertEqual(list((workdir / ".tmp").glob(f"{ui_limits.LIVE_FILTER_PREFIX}*")), [])
            if WINDOWS:
                self.assertEqual(ffmpeg_pids_using(workdir.name), [])
            with self.assertRaises((ConnectionError, OSError)):
                client.get("/api/state")
            nothing = self.command("ui", "stop", workdir=workdir)["data"]
            self.assertEqual(nothing, {"state": interface["commands"]["stop"]["states"][1]})

    def test_cli_approve_without_preview_matches_the_capabilities_contract(self) -> None:
        """`editor approve` grava o registro v2 sem preview, e o v1 (com `preview_sha256`) é obsoleto para a full."""

        approve_contract = self.capabilities()["operations"]["approve"]
        with workspace_tempdir("phase-7-approve") as workdir:
            self.prepare(workdir, TEXT_BEFORE)
            approved = self.command("approve", workdir=workdir)["data"]
            self.assertEqual(sorted(approved), sorted(approve_contract["envelope_fields"]))
            self.assertNotIn("preview_sha256", approved)
            self.assertFalse((workdir / "preview.mp4").exists())
            record = json.loads((workdir / APPROVAL_PATH).read_text(encoding="utf-8"))
            self.assertEqual(sorted(record), approve_contract["record_fields"])
            self.assertEqual(record["schema_version"], approve_contract["schema_version"])
            self.assertEqual(sorted(record["sources"][0]), approve_contract["source_fields"])

            # Registro de versão anterior (com `preview_sha256`): a full o recusa sem renderizar nada.
            obsolete = {**record, "schema_version": approve_contract["obsolete_versions"][0], "preview_sha256": "0" * 64}
            (workdir / APPROVAL_PATH).write_text(json.dumps(obsolete), encoding="utf-8")
            payload = assert_json_error(self, run_cli("render", "full", "--workdir", str(workdir), "--json"))
            self.assertEqual(payload["error"]["code"], approve_contract["release_gate"]["stale"])
            self.assertFalse((workdir / "full.mp4").exists())


if __name__ == "__main__":
    unittest.main()
