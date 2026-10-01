"""Workflow transitions and loopback boundaries; synthetic replies only."""
import contextlib
import http.client
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from roc.common import RocError, read_json, write_json
from roc.interface import make_server
from roc.pacer import Session
from roc.store import RunStore
from roc.workspace import Workspace, search_config
from tests.test_live_workflow import record
from tests.test_roc import report, party, count


def form(cap=1000):
    return {"firstName": "Jordan", "lastName": "Lawyer", "budgetCents": cap}


def response(rows, page=0, last=True, total=None):
    return json.dumps({"content": rows, "pageInfo": {"number": page, "totalElements": len(rows) if total is None else total,
        "numberOfElements": len(rows), "last": last}, "receipt": {"searchFee": ".10"}}), {}


def retrieval_values(ws, identifier, keys, cap=None):
    quote = ws.quote(identifier, keys)
    return {"keys": keys, "quoteId": quote["quoteId"],
            "docketBudgetCents": quote["maximumAdditionalCents"] if cap is None else cap}


class FakeCourt:
    bought = []
    fail_key = None

    def __init__(self, session, store, **kwargs):
        self.store = store

    def retrieve(self, case):
        if case["key"] == self.fail_key:
            raise RocError("Fictional unsupported form. No report submitted.")
        parameters = {"court": case["courtId"], "caseNumber": case["caseNumber"],
                      "scope": "all-defendants", "partiesAndCounsel": True}
        t = self.store.reserve("docket", parameters, 300)
        self.bought.append(case["key"])
        raw = report(party("Defendant", "Example Client", "Jordan Lawyer", count("18:1343.F FRAUD BY WIRE", "1")))
        raw = raw.replace("Example District", "Southern District of New York").replace("1:24-cr-00001", case["caseNumber"])
        return self.store.finish(t, raw)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.stdout = contextlib.redirect_stdout(io.StringIO())
        self.stdout.__enter__()
        self.addCleanup(self.stdout.__exit__, None, None, None)
        self.requests = []
        def request(url, payload, headers):
            self.requests.append((url, payload))
            return response([record(seq=1), record(seq=2)])
        self.session = Session("test-secret-token", requester=request)
        self.ws = Workspace(self.temp.name, lambda: self.session)
        self.addCleanup(self.ws.close)
        FakeCourt.bought = []
        FakeCourt.fail_key = None
        self.browser = patch("roc.cli.CourtRetriever", FakeCourt)
        self.browser.start()
        self.addCleanup(self.browser.stop)

    def wait(self):
        self.ws.future.result(timeout=15)

    def new(self, cap=1000):
        identifier = self.ws.new(form(cap))
        self.wait()
        return identifier

    def test_search_stops_at_preview_then_exact_selection_and_cumulative_exports(self):
        identifier = self.new()
        summary = self.ws.summary(identifier, True)
        self.assertEqual(summary["caseCount"], 2)
        self.assertEqual(summary["enrichedCount"], 0)
        self.assertEqual(FakeCourt.bought, [])
        self.assertEqual(len(self.requests), 1)
        keys = [c["key"] for c in summary["cases"]]
        quote = self.ws.quote(identifier, [keys[0]])
        self.assertEqual(quote["maximumAdditionalCents"], 300)
        self.ws.act(identifier, "retrieve", retrieval_values(self.ws, identifier, [keys[0]]))
        self.wait()
        self.assertEqual(FakeCourt.bought, [keys[0]])
        self.ws.act(identifier, "retrieve", retrieval_values(self.ws, identifier, [keys[1]]))
        self.wait()
        self.assertEqual(self.ws.summary(identifier)["enrichedCount"], 2)
        self.assertEqual(len(self.requests), 1)  # Never re-search for enrichment.
        self.assertEqual(self.ws.quote(identifier, keys)["maximumAdditionalCents"], 0)
        self.ws.act(identifier, "export")
        self.wait()
        self.assertEqual(len(FakeCourt.bought), 2)
        self.assertEqual(len(self.requests), 1)
        self.assertTrue(self.ws.download(identifier, "case-index.xlsx").is_file())
        files = "".join(p.read_text(encoding="utf-8") for p in self.ws.folder(identifier).rglob("*.json"))
        self.assertNotIn("test-secret-token", files)
        self.assertEqual(read_json(self.ws.folder(identifier) / "output/evidence.json")["cases"][0]["team"], "Criminal Defense")

    def test_fresh_docket_cap_is_separate_from_search_and_cannot_reset_ledger(self):
        identifier = self.new(1000)
        keys = [c["key"] for c in self.ws.cases(identifier)]
        original = self.ws.manifest(identifier)
        request = retrieval_values(self.ws, identifier, keys, 300)
        # A large old cap is not permission for another docket selection.
        for invalid in ({"keys": keys}, request, request | {"docketBudgetCents": True},
                        request | {"docketBudgetCents": "600"}, request | {"docketBudgetCents": -1}):
            with self.assertRaises(RocError):
                self.ws.act(identifier, "retrieve", invalid)
            self.assertEqual(self.ws.manifest(identifier), original)
        self.assertEqual(FakeCourt.bought, [])
        self.ws.act(identifier, "retrieve", retrieval_values(self.ws, identifier, keys[:1]))
        self.wait()
        self.assertEqual(self.ws.summary(identifier)["budgetCents"], 310)  # Unused old allowance is replaced.
        with self.assertRaises(RocError):
            self.ws.act(identifier, "budget", {"budgetCents": 10})
        self.assertEqual(self.ws.receipts(identifier)["spentCents"], 310)
        # A fresh docket allowance works even when the prior cap is exhausted.
        second = retrieval_values(self.ws, identifier, keys[1:])
        self.ws.act(identifier, "retrieve", second)
        self.wait()
        self.assertEqual(self.ws.receipts(identifier)["spentCents"], 610)
        self.assertEqual(self.ws.summary(identifier)["budgetCents"], 610)
        self.assertEqual(FakeCourt.bought, keys)
        with self.assertRaisesRegex(RocError, "out of date"):
            self.ws.act(identifier, "retrieve", second)
        self.ws.act(identifier, "retrieve", retrieval_values(self.ws, identifier, keys, 0))
        self.wait()
        self.assertEqual(len(FakeCourt.bought), 2)  # Cached rerun adds no charges or authentication.
        self.assertEqual(len(self.requests), 1)

    def test_preview_is_read_only_and_rejects_changed_cap_or_selection(self):
        identifier = self.new(10)
        keys = [c["key"] for c in self.ws.cases(identifier)]
        manifest, ledger = self.ws.manifest(identifier), self.ws.ledger(identifier)
        request = retrieval_values(self.ws, identifier, keys[:1])
        self.assertEqual(self.ws.manifest(identifier), manifest)
        self.assertEqual(self.ws.ledger(identifier), ledger)
        with self.assertRaisesRegex(RocError, "out of date"):
            self.ws.act(identifier, "retrieve", request | {"keys": keys[1:]})
        self.ws.act(identifier, "budget", {"budgetCents": 500})
        with self.assertRaisesRegex(RocError, "out of date"):
            self.ws.act(identifier, "retrieve", request)
        self.assertEqual(FakeCourt.bought, [])
        self.assertEqual(self.ws.receipts(identifier)["spentCents"], 10)

    def test_partial_retrieval_resume_only_remaining_selected_reports(self):
        identifier = self.new()
        keys = [c["key"] for c in self.ws.cases(identifier)]
        FakeCourt.fail_key = keys[1]
        self.ws.act(identifier, "retrieve", retrieval_values(self.ws, identifier, keys))
        self.wait()
        self.assertEqual(self.ws.summary(identifier)["state"], "stopped")
        self.assertEqual(self.ws.summary(identifier)["enrichedCount"], 1)
        self.assertEqual(self.ws.summary(identifier)["budgetCents"], 610)
        FakeCourt.fail_key = None
        self.ws.act(identifier, "resume")
        self.wait()
        self.assertEqual(FakeCourt.bought, keys)
        self.assertEqual(self.ws.summary(identifier)["enrichedCount"], 2)
        self.assertEqual(self.ws.summary(identifier)["budgetCents"], 610)

    def test_pending_receipts_never_retry_and_offline_reconciliation_is_explicit(self):
        identifier = self.new()
        folder = self.ws.folder(identifier)
        with RunStore(folder, 1000) as store:
            tx = store.reserve("pcl", {"fictional": "pending"}, 10)
        keys = [self.ws.cases(identifier)[0]["key"]]
        with self.assertRaises(RocError):
            self.ws.quote(identifier, keys)
        self.ws.act(identifier, "reconcile")
        self.wait()
        self.assertEqual(self.ws.summary(identifier)["state"], "stopped")
        self.assertEqual(len(self.requests), 1)
        with RunStore(folder, 1000) as store:
            store.save_response(tx, response([])[0])
        self.ws.act(identifier, "reconcile")
        self.wait()
        self.assertEqual(self.ws.summary(identifier)["pendingCount"], 0)
        self.assertEqual(len(self.requests), 1)

    def test_pause_waits_for_inflight_receipt_then_blocks_next_page_and_resume_uses_cache(self):
        started, release = threading.Event(), threading.Event()
        calls = []
        def request(url, payload, headers):
            calls.append(url)
            if len(calls) == 1:
                started.set()
                self.assertTrue(release.wait(10))
                return response([record(seq=1)], 0, False, 2)
            return response([record(seq=2)], 1, True, 2)
        self.session.requester = request
        identifier = self.ws.new(form())
        self.assertTrue(started.wait(10))
        with self.assertRaises(RocError):
            self.ws.new(form())
        self.ws.act(identifier, "pause")
        release.set()
        self.wait()
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.ws.receipts(identifier)["spentCents"], 10)
        self.assertEqual(self.ws.receipts(identifier)["pendingCount"], 0)
        with patch("roc.pacer.time.sleep"):
            self.ws.act(identifier, "resume")
            self.wait()
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.ws.summary(identifier)["caseCount"], 2)

    def test_missing_purchased_report_refuses_to_rebuy(self):
        identifier = self.new()
        key = self.ws.cases(identifier)[0]["key"]
        self.ws.act(identifier, "retrieve", retrieval_values(self.ws, identifier, [key]))
        self.wait()
        Path(self.ws.completed_reports(identifier)[key]["path"]).unlink()
        with self.assertRaisesRegex(RocError, "missing"):
            self.ws.quote(identifier, [key])
        self.assertEqual(len(FakeCourt.bought), 1)

    def test_selection_rejects_outside_cases_duplicate_keys_and_unverified_territories(self):
        identifier = self.new()
        key = self.ws.cases(identifier)[0]["key"]
        for keys in ([], [key,key], ["nysdc|1:24-cr-99999"], "all", [True]):
            with self.assertRaises(RocError):
                self.ws.quote(identifier, keys)
        self.session.requester = lambda *args: response([record("gudc")])
        other = self.new()
        with self.assertRaisesRegex(RocError, "not live-verified"):
            self.ws.quote(other, [self.ws.cases(other)[0]["key"]])
        self.assertFalse(self.ws.summary(other, True)["cases"][0]["eligible"])

    def test_free_demo_cannot_authenticate_or_make_remote_requests(self):
        with patch("roc.pacer.Session.prompt", side_effect=AssertionError("no login")), patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("no network")):
            identifier = self.ws.new(demo=True)
            self.wait()
            self.ws.act(identifier, "retrieve", retrieval_values(self.ws, identifier, ["nysdc|1:24-cr-00001"]))
            self.wait()
            summary = self.ws.summary(identifier, True)
            self.assertEqual(summary["spentCents"], 0)
            self.assertEqual(summary["enrichedCount"], 1)
            self.assertIn("FRAUD BY WIRE", summary["cases"][0]["nature"])
            self.assertEqual(FakeCourt.bought, [])

    def test_reopening_workspace_never_starts_work_and_interruption_is_visible(self):
        identifier = self.new()
        manifest = self.ws.manifest(identifier)
        manifest["state"] = "running"
        self.ws.save(identifier, manifest)
        with self.assertRaises(RocError):
            Workspace(self.temp.name)
        self.ws.close()
        restarted = Workspace(self.temp.name, lambda: self.fail("No authentication on open"))
        try:
            self.assertIsNone(restarted.future)
            self.assertEqual(restarted.summary(identifier)["state"], "interrupted")
            self.assertEqual(len(self.requests), 1)
        finally:
            restarted.close()

    def test_search_form_validation_and_aliases_never_expand_api_search(self):
        config = search_config(form() | {"aliases": ["Jordan A. Lawyer"], "courts": ["nysdc"], "dateFiledFrom": "2020-01-01"})
        self.assertEqual(config["search"], {"courtId": ["nysdc"], "dateFiledFrom": "2020-01-01"})
        for bad in ({"budgetCents":True}, {"budgetCents":0}, {"courts":"nysdc"}, {"indexFile":"secrets.json"},
                    {"dateFiledFrom":"bad"}, {"firstName":""}, {"aliases":[""]},
                    {"dateFiledFrom":"2025-01-01","dateFiledTo":"2024-01-01"}):
            with self.assertRaises(RocError):
                search_config(form() | bad)


class InterfaceTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows file sharing regression")
    def test_status_polling_does_not_interrupt_atomic_receipt_save(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ledger.json"
            write_json(path, {"transactions": []})
            reader = path.open("r")
            release = threading.Timer(0.09, reader.close)
            release.start()
            try:
                write_json(path, {"transactions": [{"state":"complete", "chargedCents":10}]})
            finally:
                reader.close()
                release.join()
            self.assertEqual(read_json(path)["transactions"][0]["chargedCents"], 10)

    def test_loopback_token_origin_and_path_boundaries(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            ws = Workspace(folder)
            server, url = make_server(ws)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            token = urlsplit(url).fragment
            def request(path, method="GET", body=None, headers=None):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
                connection.request(method, path, json.dumps(body) if body is not None else None,
                    {"X-ROC-Token": token, "Content-Type": "application/json", **(headers or {})})
                reply = connection.getresponse()
                result = reply.status, reply.read(), dict(reply.getheaders())
                connection.close()
                return result
            try:
                self.assertEqual(server.server_address[0], "127.0.0.1")
                self.assertEqual(request("/api/runs", headers={"X-ROC-Token":""})[0], 400)
                self.assertEqual(request("/api/runs", headers={"Origin":"https://foreign.example"})[0], 400)
                self.assertEqual(request("/api/runs", headers={"Host":"foreign.example"})[0], 400)
                self.assertEqual(request("/api/demo", "POST", {}, {"Content-Type":"text/plain"})[0], 400)
                status, body, headers = request("/")
                self.assertEqual(status, 200)
                self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
                self.assertNotIn(token.encode(), body)
                identifier = json.loads(request("/api/demo", "POST", {})[1])["id"]
                ws.future.result(10)
                self.assertEqual(request(f"/api/runs/{identifier}/download/case-index.csv")[0], 200)
                self.assertEqual(request(f"/api/runs/{identifier}/download/config.json")[0], 400)
                self.assertEqual(request("/api/runs/../../secrets")[0], 404)
                self.assertEqual(request(f"/api/runs/{identifier}/retrieve", "POST", {"keys":[]} )[0], 400)
                self.assertEqual(request("/api/runs")[0], 200)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(10)
                ws.close()


if __name__ == "__main__":
    unittest.main()
