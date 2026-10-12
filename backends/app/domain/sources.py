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


# 리포트 뉴스 목록에 보이는 매체 이름. 긴 도메인을 먼저 맞춘다(biz.chosun.com → 조선비즈).
OUTLETS = {
    "biz.chosun.com": "조선비즈", "magazine.hankyung.com": "한경BUSINESS", "news.einfomax.co.kr": "연합인포맥스",
    "yna.co.kr": "연합뉴스", "newsis.com": "뉴시스", "news1.kr": "뉴스1", "chosun.com": "조선일보",
    "joongang.co.kr": "중앙일보", "donga.com": "동아일보", "hani.co.kr": "한겨레", "khan.co.kr": "경향신문",
    "hankookilbo.com": "한국일보", "seoul.co.kr": "서울신문", "kmib.co.kr": "국민일보", "segye.com": "세계일보",
    "munhwa.com": "문화일보", "hankyung.com": "한국경제", "mk.co.kr": "매일경제", "sedaily.com": "서울경제",
    "mt.co.kr": "머니투데이", "edaily.co.kr": "이데일리", "fnnews.com": "파이낸셜뉴스", "asiae.co.kr": "아시아경제",
    "heraldcorp.com": "헤럴드경제", "ajunews.com": "아주경제", "etnews.com": "전자신문", "thebell.co.kr": "더벨",
    "newspim.com": "뉴스핌", "bizwatch.co.kr": "비즈워치", "dnews.co.kr": "대한경제", "inews24.com": "아이뉴스24",
    "zdnet.co.kr": "지디넷코리아", "ddaily.co.kr": "디지털데일리", "wowtv.co.kr": "한국경제TV", "sbs.co.kr": "SBS",
    "kbs.co.kr": "KBS", "imbc.com": "MBC", "jtbc.co.kr": "JTBC", "ytn.co.kr": "YTN", "mbn.co.kr": "MBN",
    "businesspost.co.kr": "비즈니스포스트", "thelec.kr": "디일렉", "bloter.net": "블로터", "newstomato.com": "뉴스토마토",
    "sisajournal-e.com": "시사저널e", "businesskorea.co.kr": "비즈니스코리아", "koreaherald.com": "코리아헤럴드",
    "koreatimes.co.kr": "코리아타임스", "nocutnews.co.kr": "노컷뉴스", "dailian.co.kr": "데일리안", "ebn.co.kr": "EBN",
    "viva100.com": "브릿지경제", "metroseoul.co.kr": "메트로신문", "fntimes.com": "한국금융신문",
    "ceoscoredaily.com": "CEO스코어데일리", "kukinews.com": "쿠키뉴스", "pinpointnews.co.kr": "핀포인트뉴스",
    "theguru.co.kr": "더구루", "g-enews.com": "글로벌이코노믹", "enewstoday.co.kr": "이뉴스투데이",
}


def outlet_name(url: str) -> str:
    host = domain_of(url)
    for d in sorted(OUTLETS, key=len, reverse=True):
        if host == d or host.endswith("." + d):
            return OUTLETS[d]
    return host
