"""Chromium POC checks against the isolated offline launcher; no live quality claim."""
import json
from pathlib import Path
from time import monotonic

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'artifacts/20261028-docling-aml-cft-01/20261112-unified-assistant-poc'
URL = 'http://127.0.0.1:18517'
report = {'browser_plugin': 'absent', 'fallback_reason': 'Browser plugin not available',
          'url': URL, 'viewports': [[1440, 1000], [390, 844]], 'checks': [],
          'provider_calls': 0, 'language_and_audits': 'MOCKED; not live application verification',
          'application_database_writes': False, 'console_errors': [], 'console_warnings': []}


def record(name, **extra):
    report['checks'].append({'check': name, 'passed': True, **extra})
    (OUT / 'poc-browser-checks.json').write_text(json.dumps(report, indent=2) + '\n')


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True,
        executable_path='/home/dsly/.cache/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell')
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    page.set_default_timeout(30000)
    page.on('pageerror', lambda e: report['console_errors'].append(str(e)))
    page.on('console', lambda msg: report['console_errors' if msg.type == 'error' else 'console_warnings'].append(msg.text)
            if msg.type in ('error', 'warning') else None)

    def idle():
        page.get_by_test_id('stChatInputTextArea').wait_for(timeout=180000)
        page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'", timeout=180000)
        assert not page.get_by_test_id('stException').count()

    def ask(question, count):
        started = monotonic()
        box = page.get_by_test_id('stChatInputTextArea')
        box.fill(question); box.press('Enter')
        if count == 2:
            page.get_by_text('Checking the conversation’s pinned knowledge and source identities', exact=True).wait_for(timeout=30000)
            record('loading_feedback_visible')
        page.get_by_test_id('stChatMessage').nth(count - 1).wait_for(timeout=180000)
        page.get_by_text('Diagnostics', exact=True).nth(count // 2 - 1).wait_for(timeout=180000)
        idle()
        return round(monotonic() - started, 3)

    page.goto(URL, wait_until='domcontentloaded'); idle()
    assert page.title() == 'Knowledge Foundry · Assistant'
    assert page.get_by_role('heading', name='Assistant', exact=True).count()
    assert page.get_by_text('Experimental POC', exact=False).count()
    assert not page.get_by_text('Document assistant', exact=True).count()
    assert not page.get_by_text('Reasoning assistant', exact=True).count()
    record('identity_nonblank_no_overlay_single_assistant', title=page.title())
    seconds = ask("C'est quoi une COSI ?", 2)
    assert page.get_by_text('Une COSI est une communication', exact=False).count()
    links = page.get_by_test_id('stChatMessage').last.locator('a[target="_blank"]')
    assert links.count() >= 2
    assert any('eur' not in href and ('acpr' in href or 'banque-france' in href or 'economie' in href)
               for href in links.evaluate_all('(links) => links.map(a => a.href)'))
    page.screenshot(path='/tmp/assistant-poc-browser-desktop.png', full_page=False)
    page.get_by_text('Sources et extraits originaux', exact=True).click()
    page.screenshot(path='/tmp/assistant-poc-browser-answer-inspect.png', full_page=True)
    assert page.locator('pre').count() >= 2
    assert page.get_by_text('critères objectifs', exact=False).count()
    assert page.get_by_text('Diagnostics', exact=True).count()
    record('cosi_actual_citations_and_source_passages', fixture_seconds=seconds)
    assert page.get_by_role('button', name='Open source', exact=True).count() >= 2 or page.get_by_role('link', name='Open source', exact=True).count() >= 2
    record('clickable_source_citations_and_expandable_exact_passages')
    seconds = ask('Et la déclaration de soupçon ?', 4)
    record('french_followup_same_conversation', fixture_seconds=seconds)
    seconds = ask('Explain that distinction in English', 6)
    assert page.get_by_text('A COSI is a systematic', exact=False).count()
    record('english_followup_same_conversation', fixture_seconds=seconds)
    first_conversation_url = page.url
    page.get_by_role('button', name='New conversation', exact=True).click(); idle()
    assert page.get_by_test_id('stChatMessage').count() == 0
    second_conversation_url = page.url
    assert first_conversation_url != second_conversation_url
    page.goto(first_conversation_url); idle()
    assert page.get_by_test_id('stChatMessage').count() == 6
    page.goto(second_conversation_url); idle()
    assert page.get_by_test_id('stChatMessage').count() == 0
    record('creation_reopen_switch_without_duplicate_history')
    # A separate owner has no access to either conversation, even with its ID.
    other = browser.new_context().new_page()
    other.goto(URL + '?conversation_id=' + first_conversation_url.split('conversation_id=')[1].split('&')[0])
    other.get_by_test_id('stChatInputTextArea').wait_for(timeout=60000)
    assert other.get_by_test_id('stChatMessage').count() == 0
    record('owner_isolation_in_second_browser_context')
    page.get_by_role('button', name='Documents in corpus', exact=True).click(); idle()
    page.get_by_test_id('stDataFrame').wait_for(timeout=180000)
    assert page.get_by_test_id('stDataFrame').count() == 1
    record('document_inventory_preserved')
    mobile = browser.new_page(viewport={'width': 390, 'height': 844})
    mobile.goto(first_conversation_url, wait_until='domcontentloaded')
    mobile.get_by_test_id('stChatInputTextArea').wait_for(timeout=60000)
    mobile.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'", timeout=60000)
    assert mobile.get_by_test_id('stChatInputTextArea').is_visible()
    mobile.screenshot(path='/tmp/assistant-poc-browser-mobile.png', full_page=False)
    assert mobile.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    record('mobile_input_visible_no_horizontal_overflow')
    assert not report['console_errors'], report['console_errors']
    report['status'] = 'passed'
    record('console_health', errors=0, warnings=len(report['console_warnings']))
    browser.close()
print(json.dumps({'status': report['status'], 'checks': len(report['checks']), 'provider_calls': 0}))
