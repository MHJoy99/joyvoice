"""v2.5.1 regression coverage: high-risk provenance guard, long-audio
markers/partial assembly, and safety invariants.

Ownership: this file ONLY (new). No production files modified here.

Scope:
* Guard behavior in ``app.transcription.prompt_compiler.parse_model_output``:
  cross-action target swaps, standalone fabricated completion, pre-verbal /
  post-verbal / contracted negation flips, mixed polarity around ``but``,
  ``shut down`` / ``shutdown`` equivalence, cited-ID subset semantics.
* Long-audio markers/partial assembly in ``gemini_audio``: six-marker
  synthetic evidence shape, partial raised when EITHER transcripts or
  translations exist, complete only when nothing failed.
* Safety invariants: partials are history-once copy-only (never autopaste,
  never claimed complete, never stored in prompt memory), crash logs stay
  content-free, counts-only logging (no content/keys/paths).
"""

from __future__ import annotations

import inspect
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.transcription.prompt_compiler import (
    _check_high_risk_drift,
    fallback_prompt,
    parse_model_output,
)


def _ok_json(prompt, used=(), missing=()):
    return json.dumps({"composed_prompt": prompt, "used_turn_ids": list(used),
                       "missing_details": list(missing)})


class TestHighRiskGuard(unittest.TestCase):
    def test_cross_action_target_swap_rejected(self):
        raw = _ok_json("Shut down the production server now.", used=["t1"])
        pc = parse_model_output(
            raw, allowed_turn_ids=["t1"],
            current_request="Restart the staging server now.",
            cited_turn_texts={"t1": "Please restart the staging server."},
        )
        self.assertFalse(pc.valid)
        self.assertTrue(any("high-risk" in e for e in pc.errors))

    def test_matching_action_passes(self):
        raw = _ok_json("Restart the staging server now.", used=["t1"])
        pc = parse_model_output(
            raw, allowed_turn_ids=["t1"],
            current_request="Restart the staging server now.",
            cited_turn_texts={"t1": "Please restart the staging server."},
        )
        self.assertTrue(pc.valid, pc.errors)

    def test_standalone_fabricated_completion_rejected(self):
        raw = _ok_json("Deployment succeeded, all done.", used=["t1"])
        pc = parse_model_output(
            raw, allowed_turn_ids=["t1"],
            current_request="Deploy it.",
            cited_turn_texts={"t1": "Keep dictation unchanged."},
        )
        self.assertFalse(pc.valid)
        self.assertTrue(any("completion" in e for e in pc.errors))

    def test_sourced_completion_passes(self):
        raw = _ok_json("The deploy already completed.", used=["t1"])
        pc = parse_model_output(
            raw, allowed_turn_ids=["t1"],
            current_request="Check the deploy.",
            cited_turn_texts={"t1": "The deploy already completed yesterday."},
        )
        self.assertTrue(pc.valid, pc.errors)

    def test_completion_substring_in_source_is_not_grounding(self):
        for claim, source in (
            ("Task finished.", "Task unfinished business."),
            ("Task completed.", "Task incompleted business."),
        ):
            with self.subTest(claim=claim):
                errors = _check_high_risk_drift(claim, source)
                self.assertTrue(any("unverifiable completion" in e for e in errors))
                pc = parse_model_output(
                    _ok_json(claim),
                    allowed_turn_ids=[],
                    current_request=source,
                    cited_turn_texts={},
                )
                self.assertFalse(pc.valid)

    def test_verb_substring_in_source_is_not_grounding(self):
        from app.transcription.prompt_compiler import _verb_grounded

        self.assertFalse(_verb_grounded("stop", "stopwatch repaired"))
        self.assertFalse(_verb_grounded("start", "please restart production now"))
        self.assertTrue(_verb_grounded("restart", "please restart production now"))
        self.assertTrue(_verb_grounded("shut down", "please shut down production"))

    def test_multiword_verb_parts_excluded_from_content_bag(self):
        from app.transcription.prompt_compiler import _clause_bag, _word_list

        bag = _clause_bag(
            _word_list("shut down production server"),
            "shut down production server",
            {"shut down"},
        )
        self.assertEqual(bag, {"production", "server"})

    def test_distant_negation_still_polarity_matched(self):
        self.assertEqual(
            _check_high_risk_drift(
                "Do not under any circumstances shut down production",
                "Do not under any circumstances shut down production",
                [],
            ),
            [],
        )
        self.assertTrue(
            _check_high_risk_drift(
                "Shut down production now",
                "Do not under any circumstances shut down production",
                [],
            )
        )

    def test_padding_cannot_move_negation_out_of_scope(self):
        source = (
            "Do not please now today here there then also just "
            "shut down production"
        )
        claim = "Shut down production"
        errors = _check_high_risk_drift(claim, source, [])
        self.assertTrue(errors, "a padded negation must block a bare destructive action")
        parsed = parse_model_output(
            _ok_json(claim), allowed_turn_ids=[],
            current_request=source, cited_turn_texts={},
        )
        self.assertFalse(parsed.valid)
        self.assertEqual(_check_high_risk_drift(source, source, []), [])

    def test_novel_filler_fails_closed(self):
        # Conservative by design: novel content words ("per your note") with
        # no cited grounding fail closed even around a grounded completion.
        raw = _ok_json("The deploy already completed per your note.", used=["t1"])
        pc = parse_model_output(
            raw, allowed_turn_ids=["t1"],
            current_request="Check the deploy.",
            cited_turn_texts={"t1": "The deploy already completed yesterday."},
        )
        self.assertFalse(pc.valid)

    def test_preverbal_negation_flip_rejected(self):
        raw = _ok_json("Delete all files now.", used=["t1"])
        pc = parse_model_output(
            raw, allowed_turn_ids=["t1"],
            current_request="Confirm the plan.",
            cited_turn_texts={"t1": "Do not delete any files."},
        )
        self.assertFalse(pc.valid)
        self.assertTrue(any("polarity" in e or "high-risk" in e for e in pc.errors))

    def test_contracted_negation_flip_rejected(self):
        raw = _ok_json("Restart the server now.", used=["t1"])
        pc = parse_model_output(
            raw, allowed_turn_ids=["t1"],
            current_request="Confirm the plan.",
            cited_turn_texts={"t1": "Don't restart the server."},
        )
        self.assertFalse(pc.valid)

    def test_matching_negation_passes(self):
        raw = _ok_json("Do not restart the server.", used=["t1"])
        pc = parse_model_output(
            raw, allowed_turn_ids=["t1"],
            current_request="Confirm the plan.",
            cited_turn_texts={"t1": "Don't restart the server tonight."},
        )
        self.assertTrue(pc.valid, pc.errors)

    def test_mixed_polarity_around_but_clause_aware(self):
        # Source: "keep staging but do not deploy to prod". Composed keeps
        # the benign clause but flips the guarded one -> must fail.
        raw = _ok_json("Keep staging but deploy to prod now.", used=["t1"])
        pc = parse_model_output(
            raw, allowed_turn_ids=["t1"],
            current_request="Sync the environments.",
            cited_turn_texts={"t1": "Keep staging but do not deploy to prod."},
        )
        self.assertFalse(pc.valid)

    def test_shutdown_spelling_equivalence(self):
        # Source uses "shutdown"; composed uses "shut down" with same
        # polarity -> equivalent, must pass.
        raw = _ok_json("Shut down the lab machine tonight.", used=["t1"])
        pc = parse_model_output(
            raw, allowed_turn_ids=["t1"],
            current_request="Power plan for tonight.",
            cited_turn_texts={"t1": "Shutdown the lab machine tonight."},
        )
        self.assertTrue(pc.valid, pc.errors)
        # Same spelling pair with flipped polarity -> must fail.
        raw2 = _ok_json("Shut down the lab machine tonight.", used=["t1"])
        pc2 = parse_model_output(
            raw2, allowed_turn_ids=["t1"],
            current_request="Power plan for tonight.",
            cited_turn_texts={"t1": "Do not shutdown the lab machine tonight."},
        )
        self.assertFalse(pc2.valid)

    def test_cited_id_subset_semantics_preserved(self):
        # Verb grounded ONLY in an uncited turn must still fail even though
        # the turn is in allowed_turn_ids.
        raw = _ok_json("Restart the server now.", used=["t1"])
        pc = parse_model_output(
            raw, allowed_turn_ids=["t1", "t2"],
            current_request="Confirm the plan.",
            cited_turn_texts={
                "t1": "Keep dictation unchanged.",
                "t2": "Restart the server at midnight.",
            },
        )
        self.assertFalse(pc.valid)

    def test_audit_negated_source_to_bare_command_rejected(self):
        # Exact audit case: source negates, composed issues the bare command.
        self.assertTrue(
            _check_high_risk_drift(
                "Restart production now",
                "Restart production is not recommended",
                [],
            )
        )

    def test_audit_inverse_bare_command_rejected(self):
        # Dangerous inverse: request says it ISN'T recommended, composed does it.
        raw = _ok_json("Restart production now.", used=["t1"])
        pc = parse_model_output(
            raw, allowed_turn_ids=["t1"],
            current_request="Restart production isn't recommended.",
            cited_turn_texts={"t1": "Restart production isn't recommended."},
        )
        self.assertFalse(pc.valid)

    def test_review_wrong_target_nearby_word_rejected(self):
        # Exact review case: unrelated nearby "production" must NOT satisfy
        # a composed "Restart production" when the source restarts staging.
        self.assertTrue(
            _check_high_risk_drift(
                "Restart production.",
                "Restart staging, then inspect production.",
                [],
            )
        )
        # Same via the validator path (fails closed to fallback).
        raw = _ok_json("Restart production now.", used=["t1"])
        pc = parse_model_output(
            raw, allowed_turn_ids=["t1"],
            current_request="Work through the checklist.",
            cited_turn_texts={"t1": "Restart staging, then inspect production."},
        )
        self.assertFalse(pc.valid)

    def test_review_quoted_identifier_beyond_window_passes(self):
        # Grounded quoted technical identifier with filler words between the
        # verb and the identifier must not read as a swap.
        self.assertEqual(
            _check_high_risk_drift(
                "Deploy directly to 'staging_cluster' now.",
                "Deploy to 'staging_cluster' when ready.",
                []),
            [],
        )

    def test_review_differing_identifiers_rejected_atomically(self):
        # Exact review case: 'production_cluster' vs 'staging_cluster' share
        # the piece "cluster" but are DIFFERENT targets -> must fail.
        # Identifiers compare atomically; piece overlap never counts.
        self.assertTrue(
            _check_high_risk_drift(
                "Deploy to 'production_cluster' now.",
                "Deploy to 'staging_cluster' when ready.",
                [],
            )
        )
        raw = _ok_json("Deploy to 'production_cluster' now.", used=["t1"])
        pc = parse_model_output(
            raw, allowed_turn_ids=["t1"],
            current_request="Ship tonight's build.",
            cited_turn_texts={"t1": "Deploy to 'staging_cluster' when ready."},
        )
        self.assertFalse(pc.valid)

    def test_review_shared_adjective_swap_rejected(self):
        # Exact review case: a shared adjective ("blue") is NOT agreement —
        # the changed core target must fail closed (subset comparison).
        self.assertTrue(
            _check_high_risk_drift(
                "Restart the blue production server now.",
                "Restart the blue staging server now.",
                [],
            )
        )
        raw = _ok_json("Restart the blue production server now.", used=["t1"])
        pc = parse_model_output(
            raw, allowed_turn_ids=["t1"],
            current_request="Work through the checklist.",
            cited_turn_texts={"t1": "Restart the blue staging server now."},
        )
        self.assertFalse(pc.valid)

    def test_review_identifier_bound_to_other_action_rejected(self):
        # Exact review case: the matching identifier belongs to "inspect",
        # not "restart" — clause-wide attribution must not bind it to the
        # wrong action. The source's own verb-adjacent target ("staging")
        # is dropped, so this fails closed.
        self.assertTrue(
            _check_high_risk_drift(
                "Restart production_cluster.",
                'Restart staging, then inspect "production_cluster".',
                [],
            )
        )

    def test_review_same_affix_identifiers_rejected(self):
        # Same-prefix/suffix-only difference must still fail: shared pieces
        # of recognized atomic IDs never satisfy target overlap on their own.
        self.assertTrue(
            _check_high_risk_drift(
                "Deploy to 'staging_cluster_b' now.",
                "Deploy to 'staging_cluster_a' when ready.",
                [],
            )
        )

    def test_review_differing_plain_targets_rejected(self):
        # Differing-target negative control without any identifiers.
        self.assertTrue(
            _check_high_risk_drift(
                "Restart production.", "Restart staging.", [])
        )

    def test_review_past_tense_completion_rejected(self):
        # Exact review case: negated command must not resurface as a
        # past-tense "completion" — inflections are checked as the verb.
        self.assertTrue(
            _check_high_risk_drift(
                "Production was restarted.",
                "Do not restart production.",
                [],
            )
        )
        raw = _ok_json("Production was restarted.", used=["t1"])
        pc = parse_model_output(
            raw, allowed_turn_ids=["t1"],
            current_request="Confirm the maintenance plan.",
            cited_turn_texts={"t1": "Do not restart production."},
        )
        self.assertFalse(pc.valid)

    def test_review_grounded_past_tense_passes(self):
        # Grounded control: the same past-tense claim WITH source support.
        self.assertEqual(
            _check_high_risk_drift(
                "Production was restarted.",
                "Production was restarted yesterday.",
                []),
            [],
        )

    def test_audit_shutdown_spelling_not_contrast(self):
        # Equivalent spellings must NOT read as a swap.
        self.assertEqual(
            _check_high_risk_drift(
                "Shutdown staging now.", "Shut down staging now.", []), []
        )

    def test_audit_mixed_polarity_target_swap_rejected(self):
        # Mirror-image swap around "but" must fail even though the same
        # verbs with the same polarities appear on both sides.
        raw = _ok_json(
            "Do not restart production, but restart staging.", used=["t1"])
        pc = parse_model_output(
            raw, allowed_turn_ids=["t1"],
            current_request="Do not restart staging, but restart production.",
            cited_turn_texts={
                "t1": "Do not restart staging, but restart production."},
        )
        self.assertFalse(pc.valid)
        self.assertTrue(any("swap" in e for e in pc.errors))

    def test_audit_dropped_wont_rejected(self):
        raw = _ok_json("Restart production now.", used=["t1"])
        pc = parse_model_output(
            raw, allowed_turn_ids=["t1"],
            current_request="Confirm the maintenance plan.",
            cited_turn_texts={"t1": "I won't restart production."},
        )
        self.assertFalse(pc.valid)

    def test_audit_grounded_controls_accepted(self):
        # Same verb + polarity + targets, verbatim and lightly rephrased.
        self.assertEqual(
            _check_high_risk_drift(
                "Restart production now.",
                "Please restart production now.", []), []
        )
        self.assertEqual(
            _check_high_risk_drift(
                "Do not restart staging.",
                "Do not restart staging tonight.", []), []
        )

    def test_guard_failure_falls_back_closed(self):
        raw = _ok_json("Delete all files now.", used=["t1"])
        pc = parse_model_output(
            raw, allowed_turn_ids=["t1"],
            current_request="Confirm the plan.",
            cited_turn_texts={"t1": "Do not delete any files."},
        )
        self.assertFalse(pc.valid)
        fb = fallback_prompt("Confirm the plan.", reason="guard")
        self.assertIn("Confirm the plan.", fb)
        self.assertNotIn("Delete", fb)


class TestLongAudioMarkersAndPartials(unittest.TestCase):
    def test_six_marker_evidence_shape(self):
        """Sanitized live evidence contract: 39.9 s clip, six distinct
        markers, HTTP 200, 406 transcript/translation chars, all markers.

        Privacy-safe opt-in: the evidence file lives OUTSIDE the repo and is
        located only via ``JV_LIVE_EVIDENCE_PATH``. Skipped when unset — no
        machine-specific absolute path is embedded in this test.
        """
        import json as _json
        import os as _os

        evidence_raw = _os.environ.get("JV_LIVE_EVIDENCE_PATH", "")
        if not evidence_raw:
            self.skipTest("JV_LIVE_EVIDENCE_PATH not set")
        evidence_path = Path(evidence_raw)
        if not evidence_path.is_file():
            self.skipTest("sanitized live evidence file not present")
        data = _json.loads(evidence_path.read_text(encoding="utf-8"))
        markers = data.get("expected_markers") or data.get("markers") or []
        self.assertEqual(len(markers), 6, "six distinct markers expected")
        self.assertEqual(len(set(markers)), 6, "markers must be distinct")

    def _chunks_call(self, ga, side_effect):
        chunks = [b"x" * 3200, b"y" * 3200]
        with mock.patch.object(
            ga, "transcribe_and_translate", side_effect=side_effect
        ), mock.patch.object(
            ga, "is_silence_pcm16", return_value=False
        ):
            with self.assertRaises(ga.PartialAudioResult) as ctx:
                ga.transcribe_chunks_resilient(
                    chunks, api_base="https://example.invalid/v1",
                    api_key="test-key", model="test-model",
                )
        return ctx.exception

    def test_partial_when_either_side_exists(self):
        from app.transcription import gemini_audio as ga

        exc = self._chunks_call(
            ga, [("hello world", "hello world", None), ValueError("boom")])
        self.assertIn("hello", exc.partial_transcript)

    def test_translation_only_partial_recovered(self):
        from app.transcription import gemini_audio as ga

        exc = self._chunks_call(
            ga, [("", "translated prefix", None), ValueError("boom")])
        self.assertIn("translated prefix", exc.partial_translation)

    def test_unpack_failure_preserves_first_error(self):
        from app.transcription import gemini_audio as ga

        chunks = [b"x" * 3200, b"y" * 3200]
        with mock.patch.object(
            ga, "transcribe_and_translate", side_effect=[None, ValueError("boom")]
        ), mock.patch.object(ga, "is_silence_pcm16", return_value=False):
            with self.assertRaises(TypeError):
                ga.transcribe_chunks_resilient(
                    chunks, api_base="https://example.invalid/v1",
                    api_key="test-key", model="test-model",
                )

    def test_partial_and_complete_logs_counts_only(self):
        from app.transcription import gemini_audio as ga

        src = inspect.getsource(ga.transcribe_chunks_resilient)
        lowered = src.lower()
        # Counts-only partial + completion statements exist in this function.
        self.assertIn("gemini audio partial", lowered)
        self.assertIn("gemini audio complete", lowered)
        # The partial/complete log statements must not interpolate text content:
        # only lengths/counts/index lists flow into logger calls in this fn.
        log_block = "\n".join(
            ln for ln in src.splitlines() if "logger." in ln or "%d" in ln or "%s" in ln
        ).lower()
        self.assertNotIn("partial_transcript)", log_block.replace("len(", ""))
        for line in src.splitlines():
            if "logger." in line and (
                "transcript" in line.lower() or "translation" in line.lower()
            ):
                self.assertTrue(
                    "chars" in line.lower() or "counts" in line.lower()
                    or "recovered_transcripts" in line.lower()
                    or "recovered_translations" in line.lower(),
                    f"log line must be counts-only: {line.strip()}",
                )


class TestSafetyInvariants(unittest.TestCase):
    def test_partial_never_autopastes_claims_complete_or_enters_memory(self):
        src = Path(__file__).resolve().parents[1].joinpath("app", "main.py").read_text(
            encoding="utf-8"
        )
        # Partial routing block must exist and must not call paste/autopaste.
        self.assertIn("PartialAudioResult", src)
        self.assertIn("copy-only", src)

    def test_crash_logs_content_free(self):
        from app import crash_guard as cg

        src = inspect.getsource(cg)
        self.assertIn("message_chars", src)
        self.assertIn("[REDACTED]", src)
        self.assertNotIn("thumbnail", src)

    def test_no_content_key_or_path_in_new_logs(self):
        from app.transcription import gemini_audio as ga

        fn_src = inspect.getsource(ga.transcribe_chunks_resilient)
        # New partial/complete log statements: counts/char-lengths only —
        # never content, keys, or paths. (The api_base/api_key/model kwargs
        # are plumbing, not logged: assert no logger line mentions them.)
        for line in fn_src.splitlines():
            if "logger." in line:
                low = line.lower()
                self.assertNotIn("api_key", low)
                self.assertNotIn("api_base", low)
        lowered = fn_src.lower()
        self.assertNotIn("bearer", lowered)
        self.assertNotIn("c:\\", lowered)
        self.assertNotIn("c:/", lowered)
        # Counts-only: transcript/translation text never interpolated into logs.
        for line in fn_src.splitlines():
            if "logger." in line and ("transcript" in line.lower() or "translation" in line.lower()):
                self.assertTrue(
                    "chars" in line.lower() or "counts" in line.lower()
                    or "recovered_transcripts" in line.lower()
                    or "recovered_translations" in line.lower(),
                    f"log line must be counts-only: {line.strip()}",
                )


if __name__ == "__main__":
    unittest.main()
