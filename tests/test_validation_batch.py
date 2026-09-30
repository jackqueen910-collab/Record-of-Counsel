"""Batch acceptance tests with synthetic API replies/reports; no live traffic."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from roc.cli import main
from roc.common import RocError, fingerprint, read_json, write_json
from roc.courts import court_profile
from roc.index import build_index
from roc.pacer import Session
from roc.store import RunStore
from roc.validation import assess_report, pick_case, run_validation, save_plan, validation_plan
from tests.test_roc import party, count, report


def config(**changes):
    return {"courts": ["nyedc", "dcdc"], "caseTypes": ["Civil", "Criminal"],
            "allowUnverifiedCourts": True, "dateFiledFrom": "2024-01-01", "dateFiledTo": "2024-01-31",
            "runDirectory": "run", "budgetCents": 1240, "requestDelaySeconds": 1, **changes}


def record(code, kind):
    short = "cv" if kind == "Civil" else "cr"
    return {"courtId": code, "caseNumberFull": f"1:2024{short}00001", "jurisdictionType": short,
            "caseType": short, "caseTitle": "USA v. Fictional Client" if short == "cr" else "A v. B",
            "dateFiled": "2024-01-01", "natureOfSuit": "190" if short == "cv" else None,
            "caseLink": court_profile(code).origin + "/cgi-bin/iqquerymenu.pl?1"}


def response(code, kind):
    return {"pageInfo": {"number": 0, "numberOfElements": 1, "totalElements": 100, "last": False},
            "content": [record(code, kind)], "receipt": {"searchFee": ".10"}}


def html(case):
    if case["caseType"] == "Criminal":
        parties = party("Defendant", "Client", "Defense Lawyer", count("18:1343.F FRAUD BY WIRE", "2"))
        parties += party("Defendant", "Other", "Another Lawyer", count("18:111.F ASSAULT", "1"), "2")
        parties += party("Plaintiff", "USA", "Government Lawyer")
    else:
        parties = party("Plaintiff", "A", "Plaintiff Lawyer") + party("Defendant", "B", "Defense Lawyer")
    result = report(parties, case["caseType"].upper()).replace("Example District", case["district"])
    result = result.replace("1:24-cr-00001", case["caseNumber"])
    if case["caseType"] == "Civil":
        result = result.replace("</h3>", "</h3><p>Nature of Suit: 190 Contract: Other Jurisdiction: Diversity</p>")
    return result


class FakeCourt:
    calls = []
    def __init__(self, session, store, **kwargs):
        self.store = store
    def retrieve(self, case):
        parameters = {"case": case["key"]}
        saved = self.store.cached("docket", parameters)
        if saved:
            return saved
        t = self.store.reserve("docket", parameters, 300)
        self.calls.append(case["key"])
        return self.store.finish(t, html(case))


class BatchValidationTests(unittest.TestCase):
    def test_plan_has_exactly_one_slot_per_court_type(self):
        result = validation_plan(config())
        self.assertEqual((result["maximumSearchPages"], result["maximumDockets"], result["maximumPlannedCents"]), (4, 4, 1240))
        self.assertEqual(result["slots"][0]["criteria"]["courtId"], ["nyedc"])
        self.assertEqual(result["slots"][0]["criteria"]["caseType"], ["cv"])
        for changes in ({"courts": ["nyedc", "nyedc"]}, {"courts": ["ca2"]},
                        {"caseTypes": ["Criminal", "Criminal"]}, {"allowUnverifiedCourts": False, "courts": ["gudc"]},
                        {"requestDelaySeconds": 0}, {"budgetCents": True}, {"oops": True}):
            with self.subTest(changes=changes), self.assertRaises(RocError):
                validation_plan(config(**changes))

    def test_preview_never_authenticates_or_creates_run(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            write_json(path, config())
            with patch("roc.validation.Session.prompt") as login, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["validate-courts", str(path)]), 0)
                login.assert_not_called()
            self.assertFalse((Path(folder) / "run").exists())

    def test_wrong_court_type_date_and_untrusted_host_are_stops(self):
        slot = validation_plan(config())["slots"][0]
        for changed in ({"courtId": "dcdc"}, {"jurisdictionType": "cr"},
                        {"dateFiled": "2020-01-01"}, {"caseLink": "https://example.com"}):
            r = response("nyedc", "Civil")
            r["content"][0].update(changed)
            with self.subTest(changed=changed), self.assertRaises(RocError):
                pick_case(r, slot)

    def test_first_page_selection_does_not_claim_complete_collection(self):
        slot = validation_plan(config())["slots"][0]
        chosen, evidence = pick_case(response("nyedc", "Civil"), slot)
        self.assertEqual(chosen["caseType"], "Civil")
        self.assertFalse(evidence["completeSearch"])
        empty = {"content": [], "pageInfo": {"number": 0, "numberOfElements": 0, "totalElements": 0, "last": True}}
        self.assertIsNone(pick_case(empty, slot)[0])

    def test_attorney_trials_keep_other_defendant_counts_separate(self):
        case = build_index([record("nyedc", "Criminal")])[0]
        result = assess_report(html(case), case)
        defense = next(t for t in result["attorneyTrials"] if t["attorney"] == "Defense Lawyer")
        self.assertEqual(defense["nature"], "Wire fraud (count 2).")
        self.assertEqual(defense["representedParties"], ["Client"])
        self.assertFalse(result["independentlyReviewed"])
        self.assertIn("Prosecution", result["rolesExercised"])
        missing = html(case).replace(count("18:1343.F FRAUD BY WIRE", "2"), "").replace(count("18:111.F ASSAULT", "1"), "")
        self.assertEqual(assess_report(missing, case)["automatedAssessment"], "review-needed")

    def test_complete_batch_and_resume_charge_each_request_once(self):
        requests = []
        def requester(url, payload, headers):
            requests.append(url)
            self.assertTrue(url.endswith("/cases/find?page=0"))
            kind = "Civil" if payload["jurisdictionType"] == "cv" else "Criminal"
            return json.dumps(response(payload["courtId"][0], kind)), {"X-NEXT-GEN-CSO": "rotated-fictional"}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            write_json(path, config())
            FakeCourt.calls = []
            with patch("roc.validation.CourtRetriever", FakeCourt), patch("roc.validation.time.sleep"), contextlib.redirect_stdout(io.StringIO()):
                for _ in range(2):
                    self.assertEqual(run_validation(path, True, lambda: Session("fictional", requester=requester)), 0)
            self.assertEqual(len(requests), 4)
            self.assertEqual(len(FakeCourt.calls), 4)
            result = read_json(Path(folder) / "run/validation-results.json")
            self.assertEqual(result["chargedCents"], 1240)
            self.assertEqual(result["downloadedDockets"], 4)
            self.assertFalse(result["registryPromoted"])
            self.assertTrue((Path(folder) / "run/validation-report.html").exists())
            self.assertNotIn("rotated-fictional", (Path(folder) / "run/validation-results.json").read_text())
            root = Path(folder) / "run"
            before = {name: (root / name).read_bytes() for name in
                      ("validation-plan.json", "validation-results.json", "validation-report.html", "ledger.json")}
            write_json(path, config(courts=["nyedc"]))
            with patch("roc.validation.CourtRetriever", FakeCourt), patch("roc.validation.time.sleep"), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run_validation(path, True, lambda: Session("fictional", requester=requester)), 0)
            self.assertEqual((len(requests), len(FakeCourt.calls)), (4, 4))
            result = read_json(root / "validation-results.json")
            self.assertEqual((result["chargedCents"], result["downloadedDockets"]), (1240, 2))
            self.assertEqual((root / "ledger.json").read_bytes(), before["ledger.json"])
            history = list((root / "scope-history").iterdir())
            self.assertEqual(len(history), 1)
            for name in ("validation-plan.json", "validation-results.json", "validation-report.html"):
                self.assertEqual((history[0] / name).read_bytes(), before[name])
            change = read_json(history[0] / "scope-change.json")
            self.assertEqual({s["courtId"] for s in change["removedSlots"]}, {"dcdc"})
            self.assertEqual(len(read_json(root / "validation-plan.json")["slots"]), 2)

    def test_api_failure_does_not_fall_back_to_court_retrieval(self):
        def requester(*_):
            raise RocError("API unavailable")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            write_json(path, config())
            with patch("roc.validation.CourtRetriever") as court, patch("roc.validation.time.sleep"), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run_validation(path, True, lambda: Session("fake", requester=requester)), 2)
                court.return_value.retrieve.assert_not_called()
            result = read_json(Path(folder) / "run/validation-results.json")
            self.assertEqual(result["downloadedDockets"], 0)
            self.assertEqual(read_json(Path(folder) / "run/ledger.json")["transactions"][0]["state"], "pending")

    def test_budget_blocks_next_search_and_all_reports(self):
        requests = []
        def requester(url, payload, headers):
            requests.append(url)
            return json.dumps(response("nyedc", "Civil")), {}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            write_json(path, config(budgetCents=10))
            with patch("roc.validation.CourtRetriever") as court, patch("roc.validation.time.sleep"), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run_validation(path, True, lambda: Session("fake", requester=requester)), 2)
                court.return_value.retrieve.assert_not_called()
            self.assertEqual(len(requests), 1)

    def test_case_and_party_search_caches_are_distinct(self):
        calls = []
        def requester(url, *_):
            calls.append(url)
            return '{"receipt":{"searchFee":".10"}}', {}
        with tempfile.TemporaryDirectory() as folder, RunStore(folder, 20) as store:
            session = Session("fake", requester=requester)
            session.search_cases_page({"courtId": ["nyedc"]}, 0, store)
            session.search_page({"courtId": ["nyedc"]}, 0, store)
            session.search_cases_page({"courtId": ["nyedc"]}, 0, store)
            self.assertEqual(len(calls), 2)
            self.assertEqual(store.spent, 20)

    def test_changed_scope_refused_before_sign_in(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            write_json(path, config())
            write_json(Path(folder) / "run/validation-plan.json", {"identity": "different"})
            with patch("roc.validation.Session.prompt") as login, self.assertRaisesRegex(RocError, "scope differs"):
                run_validation(path, True)
            login.assert_not_called()

    def test_scope_expansion_replacement_and_date_changes_refused_before_sign_in(self):
        for changes in ({"courts": ["nyedc", "dcdc", "gudc"]}, {"courts": ["nyedc", "gudc"]},
                        {"dateFiledTo": "2024-01-30"}, {"courts": ["nyedc"], "dateFiledFrom": "2024-01-02"}):
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as folder:
                path, root = Path(folder) / "config.json", Path(folder) / "run"
                save_plan(root, validation_plan(config()))
                before = (root / "validation-plan.json").read_bytes()
                write_json(path, config(**changes))
                with patch("roc.validation.Session.prompt") as login, self.assertRaisesRegex(RocError, "scope differs"):
                    run_validation(path, True)
                login.assert_not_called()
                self.assertEqual((root / "validation-plan.json").read_bytes(), before)
                self.assertFalse((root / "scope-history").exists())

    def test_reduction_refuses_modified_methods_or_corrupt_saved_metadata(self):
        for change in ("method", "criteria", "identity"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                save_plan(root, validation_plan(config()))
                saved = read_json(root / "validation-plan.json")
                if change == "method":
                    saved["methods"]["discovery"] = "a different method"
                elif change == "criteria":
                    saved["slots"][1]["criteria"]["dateFiledTo"] = "2024-01-02"
                saved["identity"] = fingerprint({"slots": saved["slots"], "methods": saved["methods"]})
                if change == "identity":
                    saved["identity"] = "incorrect"
                write_json(root / "validation-plan.json", saved)
                with self.assertRaisesRegex(RocError, "scope differs"):
                    save_plan(root, validation_plan(config(courts=["nyedc"])))
                self.assertFalse((root / "scope-history").exists())

    def test_pending_confirmation_blocks_scope_reduction_before_sign_in(self):
        with tempfile.TemporaryDirectory() as folder:
            path, root = Path(folder) / "config.json", Path(folder) / "run"
            save_plan(root, validation_plan(config()))
            write_json(path, config(courts=["nyedc"]))
            with patch("roc.validation.pending_confirmation", return_value={"parameters": {"court": "dcdc"}}), \
                    patch("roc.validation.Session.prompt") as login, \
                    self.assertRaisesRegex(RocError, "pending report confirmation"):
                run_validation(path, True)
            login.assert_not_called()
            self.assertEqual(len(read_json(root / "validation-plan.json")["slots"]), 4)

    def test_case_type_reduction_preserves_original_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            save_plan(root, validation_plan(config()))
            save_plan(root, validation_plan(config(caseTypes=["Civil"])))
            self.assertEqual(read_json(root / "validation-plan.json")["caseTypes"], ["Civil"])
            self.assertEqual(len(list((root / "scope-history").iterdir())), 1)
