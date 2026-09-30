"""District expansion, policy boundaries and offline planning; no live traffic."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from roc.cli import main, plan_run, run
from roc.common import RocError, write_json
from roc.courts import DISTRICT_COURTS, court_profile, profile_for_case, profile_for_url, registry_summary
from roc.index import build_index
from roc.pacer import Session
from roc.retrieve import CourtRetriever, DistrictCMECFAdapter, court_cookies
from roc.select import select_dockets, validate_options
from roc.store import RunStore
from tests.test_roc import report, party, count


def record(code, sequence=1, date="2024-01-01"):
    origin = DISTRICT_COURTS[code].origin if code in DISTRICT_COURTS else "https://ecf.nysb.uscourts.gov"
    return {"courtId": code, "caseNumberFull": f"1:2024cr{sequence:05}", "caseTitle": "USA v. Fictional Client",
            "jurisdictionType": "Criminal", "dateFiled": date,
            "caseLink": origin + f"/cgi-bin/iqquerymenu.pl?{sequence}"}


class RegistryTests(unittest.TestCase):
    def test_all_primary_districts_and_only_two_verified_samples(self):
        expected = set("""akdc almdc alndc alsdc aredc arwdc azdc cacdc caedc candc casdc
            codc ctdc dcdc dedc flmdc flndc flsdc gamdc gandc gasdc gudc hidc iandc iasdc
            iddc ilcdc ilndc ilsdc inndc insdc ksdc kyedc kywdc laedc lamdc lawdc madc mddc
            medc miedc miwdc mndc moedc mowdc msndc mssdc mtdc ncedc ncmdc ncwdc nddc
            nedc nhdc njdc nmdc nmidc nvdc nyedc nyndc nysdc nywdc ohndc ohsdc okedc okndc
            okwdc ordc paedc pamdc pawdc prdc ridc scdc sddc tnedc tnmdc tnwdc txedc txndc
            txsdc txwdc utdc vaedc vawdc vidc vtdc waedc wawdc wiedc wiwdc wvndc wvsdc wydc""".split())
        self.assertEqual(set(DISTRICT_COURTS), expected)
        self.assertEqual(len(expected), 94)
        self.assertEqual({c.court_id for c in DISTRICT_COURTS.values() if c.sample_verified}, {"nysdc", "njdc"})
        for profile in DISTRICT_COURTS.values():
            self.assertEqual(profile_for_url(profile.origin), profile)
            cookies = court_cookies(Session("fictional-token"), profile.origin)
            self.assertEqual(cookies[0]["url"], profile.origin)
            self.assertNotIn("domain", cookies[0])

    def test_all_districts_receive_labels_and_remain_distinct(self):
        cases = build_index([record(code) for code in DISTRICT_COURTS])
        self.assertEqual(len(cases), 94)
        self.assertEqual(len({c["key"] for c in cases}), 94)
        self.assertTrue(all(c["court"] == "U.S. District Court" and not c["warnings"] for c in cases))
        self.assertEqual(court_profile("DCDC").district, "District of Columbia")
        self.assertEqual(court_profile("nmidc").district, "District of the Northern Mariana Islands")

    def test_court_ids_and_websites_must_agree(self):
        case = build_index([record("nyedc")])[0]
        case["pacerLink"] = DISTRICT_COURTS["nysdc"].origin
        with self.assertRaisesRegex(RocError, "disagree"):
            profile_for_case(case)

    def test_non_district_and_lookalike_hosts_rejected(self):
        for code in ("nysbk", "ca2", "jpml", "cofc", "cit", "00pcl", "ohndad", "madeupdc"):
            with self.subTest(code=code), self.assertRaises(RocError):
                court_profile(code)
        urls = ["https://ecf.nysb.uscourts.gov", "https://ecf.ca2.uscourts.gov", "https://pcl.uscourts.gov",
                "https://ecf.nysd.uscourts.gov.evil.example", "https://ecf.fake.uscourts.gov",
                "http://ecf.nysd.uscourts.gov", "https://ecf.nysd.uscourts.gov:443", "https://ecf.nysd.uscourts.gov:bad",
                "https://user@ecf.nysd.uscourts.gov", "https://ecf.nysd.uscourts.gov@evil.example", "https://[invalid"]
        for url in urls:
            with self.subTest(url=url), self.assertRaises(RocError):
                profile_for_url(url)

    def test_registry_listing_is_offline(self):
        with patch("socket.socket.connect", side_effect=AssertionError("Network prohibited")):
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                self.assertEqual(main(["courts", "--json"]), 0)
            value = json.loads(stream.getvalue())
            self.assertEqual(value["registeredDistrictCourts"], 94)
            self.assertEqual(value["sampleVerifiedCourts"], 2)


class DistrictSelectionTests(unittest.TestCase):
    def test_default_preserves_verified_courts_and_explains_skips(self):
        cases = build_index([record("nysdc"), record("nyedc"), record("gudc"), record("nysbk")])
        selected, plan = select_dockets(cases, {"dockets": {"limit": 10}})
        self.assertEqual([c["courtId"] for c in selected], ["nysdc"])
        self.assertEqual(plan["validationPolicy"], "sample-verified-only")
        self.assertEqual(len(plan["skipped"]), 3)
        self.assertTrue(any("not live-verified" in item["reason"] for item in plan["skipped"]))

    def test_unverified_opt_in_enables_all_districts_but_no_other_systems(self):
        cases = build_index([record(code) for code in DISTRICT_COURTS] + [record("nysbk")])
        selected, plan = select_dockets(cases, {"allowUnverifiedCourts": True, "dockets": {"limit": 100}})
        self.assertEqual(len(selected), 94)
        self.assertEqual(len(plan["courtCoverage"]), 94)
        self.assertEqual(len(plan["skipped"]), 1)
        self.assertEqual(plan["courtCoverage"]["nyedc"]["validationStatus"], "unverified")

    def test_per_court_cap_and_total_limit(self):
        cases = build_index([record("nyedc", 1, "2024-04-01"), record("nyedc", 2, "2024-03-01"),
                             record("gudc", 3, "2024-02-01"), record("dcdc", 4)])
        selected, _ = select_dockets(cases, {"allowUnverifiedCourts": True, "dockets": {"limit": 2, "maxPerCourt": 1}})
        self.assertEqual([c["courtId"] for c in selected], ["nyedc", "gudc"])

    def test_explicit_unverified_selection_requires_opt_in(self):
        cases = build_index([record("nyedc")])
        config = {"retrieveDockets": [{"courtId": "nyedc", "caseNumber": "1:24-cr-1"}]}
        with self.assertRaisesRegex(RocError, "not live-verified"):
            select_dockets(cases, config)
        selected, plan = select_dockets(cases, config | {"allowUnverifiedCourts": True})
        self.assertEqual(len(selected), 1)
        self.assertEqual(plan["courtCoverage"]["nyedc"]["verifiedSamples"], 0)

    def test_invalid_options_fail_before_authentication(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            invalids = [{"allowUnverifiedCourts": "false"}, {"dockets": {"limit": 1, "courts": ["nysbk"]}},
                        {"dockets": {"limit": 1, "courts": ["typo"]}}, {"dockets": {"limit": 1, "maxPerCourt": 0}},
                        {"dockets": {"limit": 1, "maxPerCourt": True}}, {"dockets": {"limit": 1, "courts": [None]}},
                        {"retrieveDockets": [{"courtId": "ca2", "caseNumber": "1:24-cr-1"}]}]
            for invalid in invalids:
                write_json(path, {"lawyer": {"firstName": "Jordan", "lastName": "Lawyer"},
                                  "runDirectory": "run", "budgetCents": 100} | invalid)
                with self.subTest(config=invalid), patch("roc.cli.Session.prompt") as login:
                    with self.assertRaises(RocError):
                        run(path, live=True)
                    login.assert_not_called()

    def test_mismatched_court_link_never_enters_selection(self):
        cases = build_index([record("nyedc")])
        cases[0]["pacerLink"] = DISTRICT_COURTS["gudc"].origin
        selected, plan = select_dockets(cases, {"allowUnverifiedCourts": True, "dockets": {"limit": 1}})
        self.assertEqual(selected, [])
        self.assertIn("disagree", plan["skipped"][0]["reason"])


class OfflinePlanAndAdapterTests(unittest.TestCase):
    def test_plan_reads_run_cache_without_login_purchases_or_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            config = base / "config.json"
            write_json(config, {"runDirectory": "run", "allowUnverifiedCourts": True, "dockets": {"limit": 1}})
            write_json(base / "run/pcl-records.json", [record("nyedc")])
            before = {str(p): p.read_bytes() for p in base.rglob("*") if p.is_file()}
            with patch("roc.cli.Session.prompt") as login, patch("socket.socket.connect", side_effect=AssertionError("Network")):
                result = plan_run(config)
                login.assert_not_called()
            self.assertEqual(result["networkRequests"], 0)
            self.assertEqual(result["docketSelection"]["selected"], ["nyedc|1:24-cr-00001"])
            self.assertEqual(before, {str(p): p.read_bytes() for p in base.rglob("*") if p.is_file()})

    def test_missing_plan_input_stops_without_search(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            write_json(path, {"runDirectory": "missing"})
            with patch("roc.cli.Session.prompt") as login, self.assertRaisesRegex(RocError, "No PACER request"):
                plan_run(path)
            login.assert_not_called()

    def test_retriever_policy_and_court_identity_enforced_before_browser(self):
        with tempfile.TemporaryDirectory() as folder, RunStore(folder, 300) as store:
            case = build_index([record("nyedc")])[0]
            with patch("roc.retrieve.court_cookies") as cookies:
                with self.assertRaisesRegex(RocError, "not live-verified"):
                    CourtRetriever(Session("fake"), store).retrieve(case)
                case["pacerLink"] = DISTRICT_COURTS["gudc"].origin
                with self.assertRaisesRegex(RocError, "disagree"):
                    CourtRetriever(Session("fake"), store, allow_unverified=True).retrieve(case)
                cookies.assert_not_called()
            self.assertEqual(store.ledger["transactions"], [])

    def test_report_validation_requires_same_court_and_case(self):
        profile = court_profile("nyedc")
        case = build_index([record("nyedc")])[0]
        raw = report(party("Defendant", "Fictional Client", "Jordan Lawyer", count("18:1343.F FRAUD BY WIRE", "1")))
        adapter = DistrictCMECFAdapter()
        self.assertEqual(adapter.validate_report(raw.replace("Example District", profile.district), case, profile)["caseNumber"], case["caseNumber"])
        for altered in (raw.replace("Example District", "Southern District of New York"),
                        raw.replace("Example District", profile.district).replace("1:24-cr-00001", "1:24-cr-99999")):
            with self.assertRaises(RocError):
                adapter.validate_report(altered, case, profile)

    def test_territorial_heading_aliases_do_not_accept_other_districts(self):
        profile = court_profile("nmidc")
        self.assertTrue(profile.matches_heading("District Court for the Northern Mariana Islands CRIMINAL DOCKET"))
        self.assertFalse(profile.matches_heading("District of Guam CRIMINAL DOCKET"))

    def test_state_first_heading_keeps_district_identity(self):
        heading = "U.S. District Court California Northern District (San Francisco) CIVIL DOCKET FOR CASE #: 3:25-cv-00001"
        self.assertTrue(court_profile("candc").matches_heading(heading))
        for code, profile in DISTRICT_COURTS.items():
            if code != "candc":
                with self.subTest(court=code):
                    self.assertFalse(profile.matches_heading(heading))
        for wrong in ("California Southern District", "California Northern Districtish", "North California District"):
            self.assertFalse(court_profile("candc").matches_heading(wrong))


if __name__ == "__main__":
    unittest.main()
