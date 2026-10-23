"""Page moves must retain valid control hooks and preserve original task APIs."""
from html.parser import HTMLParser
import io
from pathlib import Path
import re
import unittest
from fastapi.testclient import TestClient
from PIL import Image
from test_optimization import worker
import webui

ROOT=Path(__file__).resolve().parents[1]
class Page(HTMLParser):
    def __init__(self,path):
        super().__init__();self.ids=[];self.hrefs=[];self.scripts=[];self.feed(path.read_text('utf-8'))
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if 'id' in attrs:self.ids.append(attrs['id'])
        if 'href' in attrs:self.hrefs.append(attrs['href'])
        if tag=='script':self.scripts.append(attrs.get('src',''))

class SettingsNavigationTests(unittest.TestCase):
    def test_console_retains_execution_and_record_controls_not_persistent_forms(self):
        page=Page(ROOT/'page/index.html')
        for name in ('start','save','saveBase','pause','resume','stop','keepOpen','pointsDays','logPanel','scheduleCountdown','providerNotice'):
            self.assertIn(name,page.ids)
        for name in ('autoClose','diagnose','fixedTime','windowStart','windowEnd','settingsAutoClose','version'):
            self.assertNotIn(name,page.ids)
        self.assertIn('/local/settings#executionHeading',page.hrefs)

    def test_settings_owns_schedule_service_finish_history_and_diagnostics(self):
        page=Page(ROOT/'page/settings.html')
        for name in ('fixedTime','windowStart','windowEnd','scheduleSave','settingsCloseEmulator','settingsCloseConsole','diagnose','settingsVersion','historyImportDetails','settingsProviderSetup'):
            self.assertIn(name,page.ids)
        for href in page.hrefs:
            if href.startswith('#'):self.assertIn(href[1:],page.ids)
        self.assertIn('/assets/local-schedule.js',page.scripts)
        self.assertIn('/assets/schedule-view.js',page.scripts)

    def test_moved_scripts_have_all_literal_dom_hooks_on_their_page(self):
        for html,scripts in [('index.html',['local-provider.js','points-history.js','schedule-summary.js']),
                             ('settings.html',['local-settings.js','local-schedule.js'])]:
            page=Page(ROOT/'page'/html)
            self.assertEqual(len(page.ids),len(set(page.ids)),html)
            for script in scripts:
                ids=set(re.findall(r"\$\('([^']+)'\)",(ROOT/'page/assets'/script).read_text('utf-8')))
                self.assertFalse(ids-set(page.ids),(html,script,ids-set(page.ids)))

    def test_provider_configuration_returns_to_the_opened_context(self):
        client=TestClient(webui.app)
        self.assertIn('href="/local/settings#serviceHeading">← 返回设置',client.get('/local/api-key?source=settings').text)
        self.assertIn('href="/">← 返回控制台',client.get('/local/api-key').text)

    def test_server_loads_summary_for_console_and_editor_for_settings(self):
        client=TestClient(webui.app);console=client.get('/').text;settings=client.get('/local/settings').text
        self.assertIn('/assets/schedule-summary.js?v=',console)
        self.assertNotIn('/assets/local-schedule.js?v=',console)
        self.assertIn('/assets/local-schedule.js?v=',settings)
        self.assertLess(console.index('/assets/schedule-view.js'),console.index('/assets/schedule-summary.js'))

    def test_browser_icons_are_served_and_windows_icon_has_multiple_sizes(self):
        client=TestClient(webui.app)
        response=client.get('/favicon.ico')
        self.assertEqual(response.status_code,200)
        with Image.open(io.BytesIO(response.content)) as icon:
            self.assertTrue({(16,16),(32,32),(48,48),(256,256)}.issubset(icon.ico.sizes()))
        for url in ('/','/local/settings','/local/api-key'):
            page=client.get(url).text
            self.assertIn('href="/favicon.ico?v=',page)
            self.assertIn('href="/assets/logo.svg?v=',page)
        self.assertEqual(client.get('/assets/logo.svg').status_code,200)

if __name__=='__main__':unittest.main()
