"""출처 등급(SOURCE_TIER). 통신사·종합지·경제지 1, 블로그·카페·보도자료 배포 0, 표에 없는 출처 0.5."""
from __future__ import annotations

from urllib.parse import urlparse

TIER1 = (
    # 통신사
    "yna.co.kr", "newsis.com", "news1.kr",
    # 종합지
    "chosun.com", "joongang.co.kr", "donga.com", "hani.co.kr", "khan.co.kr", "hankookilbo.com",
    "seoul.co.kr", "kmib.co.kr", "segye.com", "munhwa.com",
    # 경제지·경제 전문
    "hankyung.com", "mk.co.kr", "sedaily.com", "mt.co.kr", "edaily.co.kr", "fnnews.com", "asiae.co.kr",
    "heraldcorp.com", "ajunews.com", "etnews.com", "thebell.co.kr", "newspim.com", "bizwatch.co.kr",
    "dnews.co.kr", "inews24.com", "zdnet.co.kr", "ddaily.co.kr",
)
TIER0 = (
    "blog.naver.com", "cafe.naver.com", "tistory.com", "brunch.co.kr", "post.naver.com",
    "contents.premium.naver.com", "newswire.co.kr", "prnewswire.com", "businesswire.com", "youtube.com",
)


def domain_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _match(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def source_tier(url: str) -> float:
    host = domain_of(url)
    if _match(host, TIER0):
        return 0.0
    if _match(host, TIER1):
        return 1.0
    return 0.5
