"""이메일 뉴스레터 링크 정체성 계약 — 네트워크 0.

실사고(2026-09-08): Outlook safelinks 가 리다이렉트 없는 200 인터스티셜이라
네트워크 unwrap 이 못 풀었고, 껍데기째 수집된 링크가 가변 파라미터 탓에
발송마다 다른 hash 를 받아 sent 14일 만료 후 재수집 → duplicate 게이트가
빌드를 차단했다. safelinks 는 로컬 파싱으로 벗겨야 한다.
"""
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).parent.parent))

import email_ingest


def _no_network(*a, **kw):
    raise urllib.error.URLError("네트워크 금지 — 테스트")


class SafelinksUnwrapTests(unittest.TestCase):
    def test_safelinks_is_unwrapped_locally_without_network(self):
        target = "https://email.news.ans.org/c/abc123"
        wrapped = ("https://nam12.safelinks.protection.outlook.com/?"
                   f"url={quote(target, safe='')}&data=05%7Cvaries%7C&sdata=x&reserved=0")
        with patch.object(email_ingest.urllib.request, "urlopen", _no_network):
            # 네트워크가 다 막혀도 safelinks 로컬 파싱만으로 진짜 타깃이 나온다 —
            # 가변 data/sdata 파라미터가 hash 정체성에서 사라진다.
            self.assertEqual(email_ingest._unwrap(wrapped), target)

    def test_safelinks_without_url_param_keeps_the_original(self):
        wrapped = "https://nam12.safelinks.protection.outlook.com/?data=only"
        with patch.object(email_ingest.urllib.request, "urlopen", _no_network):
            self.assertEqual(email_ingest._unwrap(wrapped), wrapped)

    def test_ordinary_urls_are_untouched_when_network_fails(self):
        with patch.object(email_ingest.urllib.request, "urlopen", _no_network):
            self.assertEqual(email_ingest._unwrap("https://example.com/a"),
                             "https://example.com/a")


if __name__ == "__main__":
    unittest.main()
