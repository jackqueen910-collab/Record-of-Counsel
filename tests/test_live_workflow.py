"""Fresh-search orchestration tests. All remote replies are synthetic."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from roc.cli import run
from roc.common import RocError, read_json, write_json
from roc.index import build_index
from roc.pacer import Session
from roc.select import select_dockets, validate_options
from tests.test_roc import report, party, count


def record(court="nysdc", seq=1, filed="2024-01-01", kind="Criminal"):
    host = {"nysdc": "nysd", "njdc": "njd", "cacdc": "cacd"}[court]
    return {"courtId": court, "caseNumberFull": f"1:2024cr{seq:05d}", "caseTitle": "USA v. Client",
            "jurisdictionType": kind, "dateFiled": filed,
            "caseLink": f"https://ecf.{host}.uscourts.gov/cgi-bin/iqquerymenu.pl?{seq}"}


class SelectionTests(unittest.TestCase):
    def test_latest_limits_and_exclusions_with_unknown_court(self):
        cases = build_index([record(seq=1), record(seq=2, filed="2024-02-01"),
                             record(seq=3, filed="2024-03-01"), record("cacdc", 4, "2024-04-01")])
        selected, plan = select_dockets(cases, {"dockets": {"order": "latest", "limit": 1,
            "exclude": [{"courtId": "nysdc", "caseNumber": "1:24-cr-3"}]}})
        self.assertEqual(selected[0]["caseNumber"], "1:24-cr-00002")
        self.assertEqual(plan["eligibleCases"], 2)
        self.assertEqual(len(plan["skipped"]), 1)

    def test_court_type_date_and_oldest_filters(self):
        cases = build_index([record(), record("njdc", 2, "2024-02-01"), record("njdc", 3, "2024-03-01"),
                             record("njdc", 4, "2024-02-02", "Civil")])
        selected, _ = select_dockets(cases, {"dockets": {"order": "oldest", "limit": 2,
            "courts": ["njdc"], "caseTypes": ["Criminal"], "dateFiledTo": "2024-02-28"}})
        self.assertEqual([c["caseNumber"] for c in selected], ["1:24-cr-00002"])

    def test_unbounded_and_conflicting_options_fail(self):
        for config in ({"dockets": {}}, {"dockets": {"limit": True}}, {"dockets": {"limit": 0}},
                       {"dockets": {"limit": 1, "typo": True}},
                       {"dockets": {"limit": 1}, "retrieveDockets": [{"courtId": "nysdc"}]}):
            with self.assertRaises(RocError):
                validate_options(config)

    def test_no_docket_selection_by_default(self):
        self.assertEqual(select_dockets(build_index([record()]), {})[0], [])


class LiveWorkflowTests(unittest.TestCase):
    def test_fresh_search_to_selected_docket_to_output_without_saved_input(self):
        requests = []
        def request(url, payload, headers):
            requests.append((url, payload))
            return json.dumps({"content": [record()], "pageInfo": {"number": 0, "totalElements": 1,
                "numberOfElements": 1, "last": True}, "receipt": {"searchFee": ".10"}}), {}
        html = report(party("Defendant", "Client", "Jordan Lawyer", count("18:1343.F FRAUD BY WIRE", "1")))
        html = html.replace("Example District", "Southern District of New York")
        retrieved = []
        class FakeCourt:
            def __init__(self, session, store, **kwargs):
                self.store = store
            def retrieve(self, case):
                retrieved.append(case["key"])
                t = self.store.reserve("docket", {"case": case["key"]}, 300)
                return self.store.finish(t, html)
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "config.json"
            write_json(config, {"lawyer": {"firstName": "Jordan", "lastName": "Lawyer"},
                "runDirectory": "run", "budgetCents": 310, "dockets": {"limit": 1}})
            with patch("roc.cli.Session.prompt", return_value=Session("fake", requester=request)), \
                 patch("roc.cli.CourtRetriever", FakeCourt), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run(config, live=True), 0)
            result = read_json(Path(folder) / "run/result.json")
            self.assertEqual(result["chargedCentsThisRunFolder"], 310)
            self.assertEqual(result["resolvedTeamCases"], 1)
            self.assertEqual(retrieved, ["nysdc|1:24-cr-00001"])
            self.assertEqual(requests[0][1], {"firstName": "Jordan", "lastName": "Lawyer", "partyType": "aty"})
            self.assertTrue(Path(result["workbook"]).exists())

    def test_api_failure_stops_without_browser_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "config.json"
            write_json(config, {"lawyer": {"firstName": "Jordan", "lastName": "Lawyer"},
                "runDirectory": "run", "budgetCents": 1000, "dockets": {"limit": 2}})
            with patch("roc.cli.Session.prompt", side_effect=RocError("Sign-in failed")), \
                 patch("roc.cli.CourtRetriever") as browser, contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(RocError):
                    run(config, live=True)
                browser.assert_not_called()
