"""Offline result sorting/facets: source names, pagination and purchase boundaries."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from roc.exports import output_directory
from roc.common import read_json, write_json
from roc.interface import make_server
from roc.workspace import Workspace


def case(number, title, court, filed, names, nature, status="Open"):
    case_number = f"1:24-cv-{number:05d}"
    row = {"key": court+"|"+case_number,"courtId":court,"caseNumber":case_number,"caseTitle":title,
        "pacerLink":f"https://ecf.{'njd' if court == 'njdc' else 'nysd'}.uscourts.gov/cgi-bin/iqquerymenu.pl?{number}",
        "caseType":"Civil","court":"U.S. District Court","district":"District of New Jersey" if court == "njdc" else "Southern District of New York",
        "dateFiled":filed,"status":status,"role":"Civil Defense" if names else "","nature":nature,"warnings":[],"sourceRows":[]}
    if names:
        row["enrichment"] = {"partyDetails":[{"name":name,"role":"Defendant","matchedCounsel":["Jordan Lawyer"]} for name in names]}
    return row


@contextlib.contextmanager
def browser_fixture(cases):
    from playwright.sync_api import sync_playwright
    with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()), \
            patch('roc.pacer.Session.prompt',side_effect=AssertionError('No login')), \
            patch('roc.pacer.request_json',side_effect=AssertionError('No PACER')):
        ws = Workspace(folder)
        identifier = ws.new(demo=True); ws.future.result(10)
        path = output_directory(ws.folder(identifier))/'evidence.json'
        evidence = read_json(path); evidence['cases'] = cases; write_json(path,evidence)
        server,url = make_server(ws)
        thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width':1440,'height':1100})
                errors,blocked,mutations = [],[],[]
                def route(request):
                    if urlsplit(request.request.url).netloc != urlsplit(url).netloc:
                        blocked.append(request.request.url);request.abort()
                    else:
                        if request.request.method == 'POST': mutations.append(urlsplit(request.request.url).path)
                        request.continue_()
                page.route('**/*',route);page.on('pageerror',lambda e: errors.append(str(e)))
                page.goto(url);page.get_by_role('button',name='Jordan Lawyer').click()
                yield page,ws,identifier,path,evidence,errors,blocked,mutations
                browser.close()
        finally:
            server.shutdown();server.server_close();thread.join(10);ws.close()


@unittest.skipUnless(os.environ.get('ROC_BROWSER_TESTS') == '1','Set ROC_BROWSER_TESTS=1')
class ResultFilterBrowserTests(unittest.TestCase):
    def test_party_name_and_role_match_same_appearance_with_counts_and_groups(self):
        from playwright.sync_api import expect
        rows=[case(i,f'Case {i}','nysdc','2024-01-01',['3M Company'],'Contract') for i in range(1,6)]
        rows[0]['role']='Civil Plaintiff'  # Attorney role must not supply the party's role.
        rows[0]['enrichment']['partyDetails'].append({'name':'Acme','role':'Plaintiff','matchedCounsel':[]})
        rows[1]['enrichment']['partyDetails'][0]['role']='Plaintiff'
        rows[1]['enrichment']['partyDetails'].append({'name':'Acme','role':'Defendant','matchedCounsel':[]})
        rows[2]['enrichment']['partyDetails'] *= 2  # Repeated source block counts once.
        rows[3]['enrichment']['partyDetails']=[{'name':'3M Subsidiary','role':'Counter Defendant','matchedCounsel':['Jordan Lawyer']}]
        rows[4]['enrichment']['partyDetails'][0]['role']=''
        rows.append(case(6,'3M Company v. Acme','nysdc','2024-01-01',[],'Contract'))  # Caption is not evidence.
        with browser_fixture(rows) as (page,ws,identifier,path,evidence,errors,blocked,mutations):
            numbers=page.locator('#case-rows td:nth-child(2)>button')
            page.locator('#result-filters>summary').click()
            page.locator('[data-facet="party"]>summary').click()
            query=page.get_by_role('searchbox',name='Find Party name options')
            role=page.get_by_role('combobox',name='Party role',exact=True)
            query.fill('3m')
            company=page.get_by_role('checkbox',name='Party name: 3M Company',exact=True)
            expect(company.locator('..').locator('small')).to_have_text('4')
            # Typing only finds options; it does not silently change the case list.
            expect(numbers).to_have_count(6)
            company.check()
            role.select_option(label='Defendant')
            expect(numbers).to_have_text(['1:24-cv-00001','1:24-cv-00003'])
            expect(company.locator('..').locator('small')).to_have_text('2')
            expect(page.locator('#active-filters')).to_contain_text('Party role: Defendant')
            query.fill('Acme')
            expect(company).to_be_visible()  # Selection stays visible outside typed suggestions.
            page.get_by_role('checkbox',name='Party name: Acme',exact=True).check()
            expect(numbers).to_have_text(['1:24-cv-00001','1:24-cv-00002','1:24-cv-00003'])
            page.get_by_role('button',name='Remove Party name: Acme',exact=True).click()
            role.select_option(label='Plaintiff')
            expect(numbers).to_have_text(['1:24-cv-00002'])
            role.select_option(label='Not recorded')
            expect(numbers).to_have_text(['1:24-cv-00005'])
            role.select_option(label='Counter Defendant')
            expect(numbers).to_have_count(0)  # A subsidiary is not silently the same entity.
            page.get_by_role('button',name='Remove Party name: 3M Company',exact=True).click()
            expect(numbers).to_have_text(['1:24-cv-00004'])  # Role alone matches any named party.
            page.get_by_role('button',name='Remove Party role: Counter Defendant',exact=True).click()
            expect(role).to_have_value('')
            expect(numbers).to_have_count(6)
            query.fill('3M')
            company.check()
            request={'revision':0,'operation':'save','rule':{'label':'3M Group','kind':'organization-group','names':['3M Company','3M Subsidiary']}}
            _,proposed=ws.name_rules.propose(request);ws.name_rules.commit(request,proposed)
            group=page.get_by_role('checkbox',name='Party name: 3M Group',exact=True)
            expect(group).to_be_visible(timeout=10000)
            expect(numbers).to_have_count(0)  # Retain old choice at zero instead of broadening.
            expect(company.locator('..').locator('small')).to_have_text('0')
            page.locator('#clear-filters').click()
            query.fill('subsidiary')
            expect(group).to_be_visible()
            expect(group.locator('..')).to_contain_text('Includes: 3M Subsidiary')
            group.check()
            role.select_option(label='Plaintiff')
            expect(numbers).to_have_text(['1:24-cv-00002'])
            role.select_option(label='Defendant')
            expect(numbers).to_have_text(['1:24-cv-00001','1:24-cv-00003'])
            role.select_option(label='Counter Defendant')
            expect(numbers).to_have_text(['1:24-cv-00004'])
            capture=os.environ.get('ROC_UI_SCREENSHOTS')
            if capture:
                Path(capture).mkdir(parents=True,exist_ok=True)
                page.screenshot(path=str(Path(capture)/'party-picker.png'),full_page=True)
                page.set_viewport_size({'width':480,'height':900})
                page.screenshot(path=str(Path(capture)/'party-picker-mobile.png'),full_page=True)
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),480)
            page.locator('#clear-filters').click()
            expect(role).to_have_value('')
            expect(query).to_have_value('')
            expect(numbers).to_have_count(6)
            self.assertEqual(ws.receipts(identifier)['spentCents'],0)
            self.assertEqual((errors,blocked,mutations),([],[],[]))

    def test_pcl_roles_unknown_codes_and_multiple_roles_stay_attached_to_names(self):
        from playwright.sync_api import expect
        rows=[case(i,f'Case {i}','nysdc','2024-01-01',[],'Contract') for i in range(1,6)]
        for row,role in zip(rows,['dft','pla','pla; dft','custom-code','']):
            row['indexedParties']=[{'name':'Acme','role':role},{'name':'Other Entity','role':'dft'}]
        with browser_fixture(rows) as (page,ws,identifier,path,evidence,errors,blocked,mutations):
            page.locator('#result-filters>summary').click()
            page.locator('[data-facet="party"]>summary').click()
            page.get_by_role('searchbox',name='Find Party name options').fill('Acme')
            page.get_by_role('checkbox',name='Party name: Acme',exact=True).check()
            numbers=page.locator('#case-rows td:nth-child(2)>button')
            role=page.get_by_role('combobox',name='Party role',exact=True)
            for label,expected in [('Defendant',[1,3]),('Plaintiff',[2,3]),('PCL: custom-code',[4]),('Not recorded',[5])]:
                role.select_option(label=label)
                expect(numbers).to_have_text([f'1:24-cv-{i:05d}' for i in expected])
            page.get_by_role('button',name='1:24-cv-00005',exact=True).click()
            expect(page.locator('#detail-parties')).to_contain_text('Acme · PCL party role: Not supplied')
            self.assertEqual(read_json(path)['cases'],rows)  # Display labels never rewrite evidence.
            self.assertEqual((errors,blocked,mutations),([],[],[]))

    def test_activity_tracks_current_operation_and_stops_when_paused_or_interrupted(self):
        from playwright.sync_api import expect
        rows=[case(1,'A case','nysdc','2024-01-01',[],'Not supplied by PCL')]
        with browser_fixture(rows) as (page,ws,identifier,path,evidence,errors,blocked,mutations):
            override={}
            active={'id':None}
            def detail(route):
                response=route.fetch(); data=response.json(); data.update(override)
                route.fulfill(response=response,json=data)
            def listing(route):
                response=route.fetch(); data=response.json(); data['active']=active['id']
                route.fulfill(response=response,json=data)
            page.route(f'**/api/runs/{identifier}',detail)
            page.route('**/api/runs',listing)
            override.update(busy=True,state='running',lastAction='search',indexReady=False)
            active['id']=identifier
            expect(page.locator('#results-progress-title')).to_have_text('Preparing your results…',timeout=10000)
            expect(page.locator('#case-rows tr')).to_have_count(1)  # Partial rows stay visible.
            expect(page.locator('#run-help')).to_contain_text('remaining searches are still running')
            expect(page.locator('#case-rows input[type=checkbox]')).to_be_disabled()
            override['pauseRequested']=True
            expect(page.locator('#results-progress-title')).to_have_text('Pausing after the current request…',timeout=10000)
            expect(page.locator('#live-progress')).to_be_visible()  # Request still settling.
            for state in ('stopped','interrupted'):
                override.update(busy=False,state=state,pauseRequested=False)
                active['id']=None
                expect(page.locator('#run-status')).to_have_text(state.upper(),timeout=10000)
                expect(page.locator('#results-progress')).not_to_be_visible()
                expect(page.locator('#live-progress')).not_to_be_visible()
                expect(page.locator('#select-visible')).to_be_disabled()  # Incomplete search.
                expect(page.locator('#run-help')).to_contain_text('Resume to finish')
            override.update(busy=True,state='running',lastAction='retrieve',indexReady=True)
            active['id']=identifier
            expect(page.locator('#results-progress-title')).to_have_text('Preparing your results…',timeout=10000)
            override.update(busy=False,state='ready')
            active['id']='another-run'
            expect(page.locator('#run-status')).to_have_text('READY',timeout=10000)
            expect(page.locator('#results-progress')).not_to_be_visible()
            expect(page.locator('#live-progress')).not_to_be_visible()
            expect(page).to_have_title('Record of Counsel')
            self.assertEqual((errors,blocked,mutations),([],[],[]))

    def test_simpler_filters_retain_case_notes_and_source_downloads(self):
        from playwright.sync_api import expect
        rows=[case(1,'A case','nysdc','2024-01-01',[],'Not supplied by PCL')]
        rows[0]['warnings']=['Conflicting counsel association needs review.']
        with browser_fixture(rows) as (page,ws,identifier,path,evidence,errors,blocked,mutations):
            expect(page.locator('[data-facet="review"], [data-facet="docket"]')).to_have_count(0)
            page.get_by_role('button',name='1:24-cv-00001',exact=True).click()
            expect(page.get_by_role('heading',name='Data notes',exact=True)).to_be_visible()
            expect(page.locator('#detail-issues')).to_contain_text('Conflicting counsel association needs review.')
            page.get_by_role('button',name='Close case').click()
            for label,filename in [('Source data ↓','evidence.json'),('Data notes ↓','review.json')]:
                button=page.get_by_role('button',name=label,exact=True)
                self.assertTrue(button.get_attribute('title').startswith('JSON:'))
                with page.expect_download() as captured:
                    button.click()
                download=captured.value
                self.assertEqual(download.suggested_filename,filename)
                data=read_json(Path(download.path()))
                if filename == 'evidence.json':
                    self.assertEqual(data['cases'],rows)
                else:
                    self.assertIsInstance(data,list)
            self.assertEqual((errors,blocked,mutations),([],[],[]))

    def test_client_types_vary_by_case_and_old_server_requires_export_restart(self):
        from playwright.sync_api import expect
        rows=[case(1,'First case','nysdc','2024-01-01',['Shared Client'],'Contract'),
              case(2,'Second case','njdc','2023-01-01',['Shared Client'],'Contract')]
        rows[0]['enrichment']['partyDetails'][0]['role']='Plaintiff'
        rows[0]['role']='Civil Plaintiff'
        with browser_fixture(rows) as (page,ws,identifier,path,evidence,errors,blocked,mutations):
            page.locator('#view-clients').click()
            expect(page.locator('#party-rows tr')).to_have_count(1)
            expect(page.locator('#party-rows td').last).to_have_text('2')
            page.get_by_role('button',name='Shared Client',exact=True).click()
            expect(page.locator('.party-appearance').nth(0)).to_contain_text('Client type: Plaintiff')
            expect(page.locator('.party-appearance').nth(1)).to_contain_text('Client type: Defendant')
            page.get_by_role('button',name='Close party').click()
            def old_server(route):
                response=route.fetch(); data=response.json(); data.pop('clientReportVersion')
                route.fulfill(response=response,json=data)
            page.route('**/api/runs',old_server)
            expect(page.locator('#exports-stale-message')).to_contain_text('Restarting ends the current PACER sign-in',timeout=10000)
            expect(page.get_by_role('button',name='Excel ↓',exact=True)).to_be_disabled()
            expect(page.locator('#refresh-reports')).to_be_disabled()
            expect(page.locator('#party-rows tr')).to_have_count(1)
            page.unroute('**/api/runs',old_server)
            expect(page.locator('#exports-stale')).not_to_be_visible(timeout=10000)
            expect(page.get_by_role('button',name='Excel ↓',exact=True)).to_be_enabled()
            self.assertEqual((errors,blocked,mutations),([],[],[]))

    def test_sort_directions_multivalue_filters_missing_names_and_name_rules(self):
        from playwright.sync_api import expect
        rows = [case(1,'Zulu','nysdc','2024-01-01',['Zeta','Beta'],'Fraud'),
                case(2,'Alpha','njdc','2020-01-01',['Alpha'],'Contract','Closed'),
                case(3,'Bravo','njdc','2023-01-01',['Omega'],'Antitrust'),
                case(4,'Delta','nysdc','',[],'Not supplied by PCL','Closed')]
        with browser_fixture(rows) as (page,ws,identifier,path,evidence,errors,blocked,mutations):
            numbers = page.locator('#case-rows td:nth-child(2)>button')
            expected = {'newest':[1,3,2,4],'oldest':[2,3,1,4], 'party-asc':[2,1,3,4], 'party-desc':[3,1,2,4],
                'title-asc':[2,3,4,1],'title-desc':[1,4,3,2], 'court-asc':[2,3,1,4],'court-desc':[1,4,2,3],
                'nature-asc':[3,2,1,4],'nature-desc':[1,2,3,4]}
            for value,order in expected.items():
                page.locator('#sort').select_option(value)
                expect(numbers).to_have_text([f'1:24-cv-{n:05d}' for n in order])
            page.locator('#sort').select_option('party-asc')
            expect(page.locator('#sort-help')).to_be_visible()
            expect(page.locator('#case-rows tr').last).to_contain_text('No saved party names')
            page.get_by_role('checkbox',name='Select 1:24-cv-00001',exact=True).check()
            page.locator('#result-filters>summary').click()
            page.locator('[data-facet="court"]>summary').click()
            nj = page.get_by_role('checkbox',name='Court: District of New Jersey',exact=True)
            ny = page.get_by_role('checkbox',name='Court: Southern District of New York',exact=True)
            expect(nj.locator('..').locator('small')).to_have_text('2')
            expect(ny.locator('..').locator('small')).to_have_text('2')  # Not three despite two parties in case one.
            nj.check();expect(numbers).to_have_count(2)
            expect(page.locator('#selection-hidden')).to_contain_text('1 outside this filter')
            ny.check();expect(numbers).to_have_count(4)  # OR within Court.
            page.locator('[data-facet="status"]>summary').click()
            page.get_by_role('checkbox',name='Case status: Closed',exact=True).check()
            expect(numbers).to_have_text(['1:24-cv-00002','1:24-cv-00004'])
            ny.uncheck();expect(numbers).to_have_text(['1:24-cv-00002'])  # AND across categories.
            page.locator('#clear-filters').click()
            page.locator('[data-facet="party"]>summary').click()
            page.get_by_role('searchbox',name='Find Party name options').fill('Zeta')
            page.get_by_role('checkbox',name='Party name: Zeta',exact=True).check()
            expect(numbers).to_have_text(['1:24-cv-00001'])  # Not just the first alphabetical party.
            page.get_by_role('searchbox',name='Find Party name options').fill('Alpha')
            page.get_by_role('checkbox',name='Party name: Alpha',exact=True).check()
            expect(numbers).to_have_text(['1:24-cv-00002','1:24-cv-00001'])
            page.locator('[data-facet="nature"]>summary').click()
            page.get_by_role('checkbox',name='Nature of Case: Antitrust',exact=True).check()
            expect(numbers).to_have_count(0);expect(page.locator('#empty')).to_contain_text('No cases match')
            page.locator('#clear-filters').click()
            page.get_by_role('checkbox',name='Party name: No saved party names',exact=True).check()
            expect(numbers).to_have_text(['1:24-cv-00004'])
            page.locator('#clear-filters').click()
            page.get_by_role('searchbox',name='Find Party name options').fill('Beta')
            page.get_by_role('checkbox',name='Party name: Beta',exact=True).check()
            # New saved evidence/rules refresh choices, without silently clearing
            # an active filter whose old value no longer exists.
            request={'revision':0,'operation':'save','rule':{'label':'Aardvark Group','kind':'organization-group','names':['Beta']}}
            _,proposed=ws.name_rules.propose(request);ws.name_rules.commit(request,proposed)
            expect(page.get_by_role('checkbox',name='Party name: Aardvark Group',exact=True)).to_be_visible(timeout=10000)
            expect(numbers).to_have_count(0)
            expect(page.get_by_role('checkbox',name='Party name: Beta',exact=True).locator('..').locator('small')).to_have_text('0')
            page.locator('#clear-filters').click()
            expect(numbers).to_have_text(['1:24-cv-00001','1:24-cv-00002','1:24-cv-00003','1:24-cv-00004'])
            page.locator('#filter').fill('Beta')  # Original spellings stay searchable.
            expect(numbers).to_have_text(['1:24-cv-00001'])
            expect(page.locator('.case-parties')).to_contain_text('Aardvark Group')
            page.locator('#clear-filters').click()
            page.locator('#view-clients').click()
            page.locator('#party-sort').select_option('name-desc')
            expect(page.locator('#party-rows td:first-child>button:first-of-type')).to_have_text(['Zeta','Omega','Alpha','Aardvark Group'])
            page.locator('#party-sort').select_option('name-asc')
            expect(page.locator('#party-rows td:first-child>button:first-of-type')).to_have_text(['Aardvark Group','Alpha','Omega','Zeta'])
            self.assertEqual(ws.receipts(identifier)['spentCents'],0)
            self.assertEqual((errors,blocked,mutations),([],[],[]))

    def test_pagination_searchable_options_refresh_reset_and_selected_quote(self):
        from playwright.sync_api import expect
        rows=[case(i,f'Filing {i:03d}','nysdc','2024-01-01',[f'Party {i:03d}'],'Contract') for i in range(1,106)]
        with browser_fixture(rows) as (page,ws,identifier,path,evidence,errors,blocked,mutations):
            page.locator('#sort').select_option('title-desc')
            expect(page.locator('#page-summary')).to_have_text('1–50 of 105 cases')
            expect(page.locator('#case-rows td:nth-child(2)>button').first).to_have_text('1:24-cv-00105')
            page.locator('#next').click();expect(page.locator('#page-summary')).to_have_text('51–100 of 105 cases')
            page.get_by_role('checkbox',name='Select 1:24-cv-00055',exact=True).check()
            page.locator('#result-filters>summary').click()
            page.locator('[data-facet="party"]>summary').click()
            expect(page.locator('[data-facet="party"] input[type=checkbox]')).to_have_count(0)
            page.get_by_role('searchbox',name='Find Party name options').fill('Party')
            expect(page.locator('[data-facet="party"] input[type=checkbox]')).to_have_count(20)
            expect(page.locator('#party-picker-hint')).to_contain_text('20 of 105 matching names')
            page.get_by_role('searchbox',name='Find Party name options').fill('105')
            page.get_by_role('checkbox',name='Party name: Party 105',exact=True).check()
            expect(page.locator('#page-summary')).to_have_text('1–1 of 1 cases')
            expect(page.locator('#selection-hidden')).to_contain_text('1 outside this filter')
            page.locator('#select-visible').click();expect(page.locator('#selection-count')).to_have_text('2')
            page.locator('#preview').click();expect(page.locator('#quote-cases p')).to_have_count(2)
            expect(page.locator('#quote-cases')).to_contain_text('1:24-cv-00055')
            expect(page.locator('#quote-cases')).to_contain_text('1:24-cv-00105')
            page.locator('#revise-dockets').click()
            self.assertEqual(mutations,[f'/api/runs/{identifier}/quote'])  # Never a purchase.
            page.locator('#clear-filters').click();page.locator('#clear-selection').click()
            page.get_by_role('searchbox',name='Find Party name options').fill('')
            page.locator('[data-facet="court"]>summary').click()
            page.locator('[data-facet="status"]>summary').click()
            capture=os.environ.get('ROC_UI_SCREENSHOTS')
            if capture:
                Path(capture).mkdir(parents=True,exist_ok=True)
                page.screenshot(path=str(Path(capture)/'result-filters.png'),full_page=True)
                page.set_viewport_size({'width':480,'height':900})
                page.screenshot(path=str(Path(capture)/'result-filters-mobile.png'),full_page=True)
                page.set_viewport_size({'width':1440,'height':1100})
            # Source options remain literal text and refresh when evidence changes.
            evil='<img src="https://foreign.example/x" onerror="alert(1)">'
            evidence['cases'].append(case(106,evil,'njdc','2025-01-01',[evil],'Antitrust'))
            write_json(path,evidence)
            expect(page.get_by_role('checkbox',name='Court: District of New Jersey',exact=True)).to_be_visible(timeout=10000)
            page.get_by_role('searchbox',name='Find Party name options').fill('<img')
            expect(page.get_by_role('checkbox',name='Party name: '+evil,exact=True)).to_be_visible()
            self.assertEqual(page.locator('#facet-groups img').count(),0)
            page.get_by_role('checkbox',name='Party name: '+evil,exact=True).check()
            page.get_by_role('button',name='Jordan Lawyer').click()  # Run navigation resets filters.
            expect(page.locator('#page-summary')).to_have_text('1–50 of 106 cases')
            expect(page.locator('#facet-count')).to_have_text('All results')
            self.assertEqual(ws.receipts(identifier)['spentCents'],0)
            self.assertEqual((errors,blocked),([],[]))
