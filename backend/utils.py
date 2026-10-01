import re
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

BASE_PARTNER_MEDIA = [
    "KK KYODO NEWS SITE",
    "OVO",
    "エンタメOVO",
    "ビードット",
    "47NEWS",
    "Twitter",
    "Facebook",
    "Yahooニュース",
    "MSNニュース",
    "gooニュース",
    "ドコモｄメニューニュース",
    "ドコモエンタメウィーク",
    "ビッグローブニュース",
    "楽天インフォシークニュース",
    "mixiニュース",
    "ニコニコニュース",
    "アメーバニュース",
    "GIGAパーク",
    "めるもニュース",
    "エキサイトニュース",
    "ソネットニュース",
    "Googleニュース",
    "LIMIA",
    "ニフティニュース",
    "ライブドアニュース",
    "ビルボードジャパン",
    "ウレぴあ総研",
    "OKMusic",
    "LINE News",
    "SmartNews",
    "Gunosy",
    "auニュースパス",
    "ルクラ",
    "auサービスToday",
    "Socialife",
    "NewsSuite",
    "TheNewsLens",
    "らくらくコミュニティ",
    "Viber",
    "NewsPicks",
    "Gate",
    "NewsDigest",
    "モデルプレス",
    "Entinsight",
    "Domingo",
    "エンタメポスト",
    "NEWS速報",
    "チバテレ",
    "GIGAパーク",
    "antenna",
    "NEWSjp",
    "PRタイムス",
    "アットプレス",
    "ドリームニュース",
    "Yahooファイナンス",
    "その他",
]

PARTNER_MEDIA_ALL = []
for media_name in BASE_PARTNER_MEDIA:
    if media_name not in PARTNER_MEDIA_ALL:
        PARTNER_MEDIA_ALL.append(media_name)

ORIGINAL_MEDIA = {
    "OVO",
    "ビードット",
}

SOCIAL_MEDIA = {
    "Twitter",
    "Facebook",
}

BAD_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "ref",
    "ref_src",
    "from",
    "fbclid",
    "gclid",
    "yclid",
    "guccounter",
    "guce_referrer",
    "guce_referrer_sig",
    "output",
    "amp",
    "spm",
    "news_ref",
    "news_ref_id",
    # GIGAパーク空パラメータ
    "aid",
    # MSN追跡パラメータ
    "ocid",
    "cvid",
    "cvpid",
    "ei",
    # 一般的な追跡・ページパラメータ
    "ntv_source",
    "ntv_medium",
}

NOISE_PATH_PATTERNS = [
    r"/source(?:/|$)",
    r"/sources(?:/|$)",
    r"/tag(?:/|$)",
    r"/tags(?:/|$)",
    r"/search(?:/|$)",
    r"/list(?:/|$)",
    r"/ranking(?:/|$)",
    r"/archive(?:/|$)",
    r"/archives(?:/|$)",
    r"/category(?:/|$)",
    r"/categories(?:/|$)",
    r"/brands(?:/|$)",
    r"/topics/?$",
    r"/images(?:/|$)",
    r"/photo(?:/|$)",
    r"/photos(?:/|$)",
]

MEDIA_REGISTRY = {
    "KK KYODO NEWS SITE": {
        "domains": ["www.kyodo.co.jp", "kyodo.co.jp"],
        "patterns": [
            r"/(sport|life|culture|business|area|politics-international|news)/\d{4}-\d{2}-\d{2}_\d+/?$",
            r"/\d{4}/\d{2}/\d{2}/",
            r"/press-releases/",
            r"/article/",
            r"/news/",
        ],
        "aliases": ["共同通信", "KYODO", "KK KYODO NEWS SITE"],
        "searchable": True,
        "trusted": True,
    },
    "OVO": {
        "domains": ["ovo.kyodo.co.jp"],
        "patterns": [r"/news/.+/a-\d+/?$"],
        "aliases": ["OVO", "オーヴォ"],
        "searchable": True,
        "trusted": False,
    },
    "エンタメOVO": {
        "domains": ["entame.ovo.kyodo.co.jp"],
        "patterns": [r"/"],
        "aliases": ["エンタメOVO"],
        "searchable": True,
        "trusted": False,
    },
    "ビードット": {
        "domains": ["b-dot.jp", "b.kyodo.co.jp"],
        "patterns": [r"/.+/\d{4}-\d{2}-\d{2}[_\-]\d+/?$", r"/"],
        "aliases": ["b-dot", "ビードット", "b."],
        "searchable": True,
        "trusted": False,
    },
    "47NEWS": {
        "domains": ["www.47news.jp", "47news.jp"],
        "patterns": [r"/\d+\.html$"],
        "aliases": ["47NEWS"],
        "searchable": True,
        "trusted": True,
    },
    "Twitter": {
        "domains": ["x.com", "twitter.com"],
        "patterns": [r"/status/\d+"],
        "aliases": ["X", "Twitter"],
        "searchable": True,
        "trusted": False,
    },
    "Facebook": {
        "domains": ["facebook.com", "www.facebook.com"],
        "patterns": [r"/"],
        "aliases": ["Facebook"],
        "searchable": True,
        "trusted": False,
    },
    "Yahooニュース": {
        "domains": ["news.yahoo.co.jp"],
        "patterns": [r"/articles/[A-Za-z0-9]+/?$"],
        "aliases": ["Yahooニュース", "Yahoo!ニュース"],
        "searchable": True,
        "trusted": True,
    },
    "MSNニュース": {
        "domains": ["www.msn.com", "msn.com"],
        "patterns": [r"/ja-jp/news/", r"/ja-jp/sports/", r"/news/", r"/ar-[A-Za-z0-9]+/?$"],
        "aliases": ["MSNニュース", "MSN"],
        "searchable": True,
        "trusted": True,
    },
    "gooニュース": {
        "domains": ["news.goo.ne.jp"],
        "patterns": [r"/article/"],
        "aliases": ["gooニュース", "goo"],
        "searchable": True,
        "trusted": True,
    },
    "ドコモｄメニューニュース": {
        "domains": ["topics.smt.docomo.ne.jp"],
        "patterns": [r"/article/ovo/.+/ovo-O\d+/?$"],
        "aliases": ["dメニュー", "dメニューニュース", "docomo"],
        "searchable": True,
        "trusted": True,
    },
    "ドコモエンタメウィーク": {
        "domains": ["entameweek.com"],
        "patterns": [r"/article/", r"/news/"],
        "aliases": ["エンタメウィーク", "ドコモエンタメウィーク"],
        "searchable": True,
        "trusted": False,
    },
    "ビッグローブニュース": {
        "domains": ["news.biglobe.ne.jp"],
        "patterns": [r"/\w+/ovo/\d+\.html$", r"/news/", r"/\d+\.html$"],
        "aliases": ["BIGLOBEニュース", "ビッグローブニュース", "BIGLOBE"],
        "searchable": True,
        "trusted": True,
    },
    "楽天インフォシークニュース": {
        "domains": ["news.infoseek.co.jp"],
        "patterns": [r"/article/ovo_O\d+/?$", r"/article/"],
        "aliases": ["Infoseek", "インフォシーク", "楽天インフォシークニュース"],
        "searchable": True,
        "trusted": True,
    },
    "mixiニュース": {
        "domains": ["news.mixi.jp"],
        "patterns": [r"/view_news\.pl"],
        "aliases": ["mixiニュース", "mixi"],
        "searchable": True,
        "trusted": True,
    },
    "ニコニコニュース": {
        "domains": ["news.nicovideo.jp"],
        "patterns": [r"/watch/nw\d+(?:\?|/?$)", r"/article/"],
        "aliases": ["ニコニコニュース", "niconico", "ニコニコ"],
        "searchable": True,
        "trusted": True,
    },
    "アメーバニュース": {
        "domains": ["news.ameba.jp"],
        "patterns": [r"/entry/", r"/article/"],
        "aliases": ["Amebaニュース", "アメーバニュース"],
        "searchable": True,
        "trusted": True,
    },
    "めるもニュース": {
        "domains": ["news.merumo.ne.jp"],
        "patterns": [r"/article/", r"/article/gen/"],
        "aliases": ["めるもニュース", "merumo"],
        "searchable": True,
        "trusted": True,
    },
    "エキサイトニュース": {
        "domains": ["www.excite.co.jp", "excite.co.jp"],
        "patterns": [r"/news/article/Ovo_\d+/?$", r"/news/article/"],
        "aliases": ["Excite", "エキサイトニュース"],
        "searchable": True,
        "trusted": True,
    },
    "ソネットニュース": {
        "domains": ["news.so-net.ne.jp"],
        "patterns": [r"/article/", r"/detail/"],
        "aliases": ["So-netニュース", "ソネットニュース"],
        "searchable": False,
        "trusted": False,
    },
    "Googleニュース": {
        "domains": ["news.google.com"],
        "patterns": [r"/articles/"],
        "aliases": ["Googleニュース"],
        "searchable": True,
        "trusted": False,
    },
    "LIMIA": {
        "domains": ["limia.jp"],
        "patterns": [r"/article/"],
        "aliases": ["LIMIA"],
        "searchable": True,
        "trusted": True,
    },
    "ニフティニュース": {
        "domains": ["news.nifty.com"],
        "patterns": [r"/article/", r"/\d+/?$", r"/12176-\d+/?$"],
        "aliases": ["ニフティニュース", "@niftyニュース", "nifty"],
        "searchable": True,
        "trusted": True,
    },
    "ライブドアニュース": {
        "domains": ["news.livedoor.com"],
        "patterns": [r"/article/detail/"],
        "aliases": ["ライブドアニュース", "livedoor"],
        "searchable": True,
        "trusted": True,
    },
    "ビルボードジャパン": {
        "domains": ["billboard-japan.com"],
        "patterns": [r"/d_news/detail/", r"/news/detail/"],
        "aliases": ["Billboard JAPAN", "ビルボードジャパン"],
        "searchable": True,
        "trusted": True,
    },
    "ウレぴあ総研": {
        "domains": ["ure.pia.co.jp"],
        "patterns": [r"/articles?/"],
        "aliases": ["ウレぴあ総研"],
        "searchable": True,
        "trusted": True,
    },
    "OKMusic": {
        "domains": ["okmusic.jp"],
        "patterns": [r"/news/"],
        "aliases": ["OKMusic"],
        "searchable": True,
        "trusted": True,
    },
    "LINE News": {
        "domains": ["news.line.me", "line.me"],
        "patterns": [r"/issue/", r"/detail/"],
        "aliases": ["LINE News"],
        "searchable": True,
        "trusted": False,
    },
    "SmartNews": {
        "domains": ["smartnews.com", "www.smartnews.com", "l.smartnews.com"],
        "patterns": [
            r"/ja/", r"/en/",
            r"/m-[A-Za-z0-9]+(?:/[A-Za-z0-9]+)?(?:\?|/?$)",
            r"/articles/[A-Za-z0-9]+(?:\?|/?$)",
            r"/",
        ],
        "aliases": ["SmartNews", "スマートニュース"],
        "searchable": True,
        "trusted": True,
    },
    "Gunosy": {
        "domains": ["gunosy.com"],
        "patterns": [r"/articles/"],
        "aliases": ["Gunosy", "グノシー"],
        "searchable": True,
        "trusted": True,
    },
    "auニュースパス": {
        "domains": ["newspass.jp"],
        "patterns": [r"/a/"],
        "aliases": ["auニュースパス", "ニュースパス"],
        "searchable": True,
        "trusted": False,
    },
    "ルクラ": {
        "domains": ["letronc-m.com", "letronc.com"],
        "patterns": [r"/\d+"],
        "aliases": ["ルクラ"],
        "searchable": True,
        "trusted": False,
    },
    "auサービスToday": {
        "domains": ["service.auone.jp"],
        "patterns": [r"/article/"],
        "aliases": ["auサービスToday", "auone"],
        "searchable": True,
        "trusted": False,
    },
    "Socialife": {
        "domains": ["socialife.goo.ne.jp"],
        "patterns": [r"/article/"],
        "aliases": ["Socialife"],
        "searchable": True,
        "trusted": False,
    },
    "NewsSuite": {
        "domains": ["news-suite.jp"],
        "patterns": [r"/article/"],
        "aliases": ["NewsSuite"],
        "searchable": True,
        "trusted": False,
    },
    "TheNewsLens": {
        "domains": ["thenewslens.com", "japan.thenewslens.com"],
        "patterns": [r"/article/"],
        "aliases": ["TheNewsLens"],
        "searchable": True,
        "trusted": False,
    },
    "らくらくコミュニティ": {
        "domains": [],
        "patterns": [],
        "aliases": ["らくらくコミュニティ"],
        "searchable": False,
        "trusted": False,
    },
    "Viber": {
        "domains": ["viber.com"],
        "patterns": [r"/en/news/", r"/"],
        "aliases": ["Viber"],
        "searchable": True,
        "trusted": False,
    },
    "NewsPicks": {
        "domains": ["newspicks.com"],
        "patterns": [r"/news/\d+/?$"],
        "aliases": ["NewsPicks", "ニューズピックス"],
        "searchable": True,
        "trusted": True,
    },
    "Gate": {
        "domains": [],
        "patterns": [],
        "aliases": ["Gate"],
        "searchable": False,
        "trusted": False,
    },
    "NewsDigest": {
        "domains": ["newsdigest.jp"],
        "patterns": [r"/news/"],
        "aliases": ["NewsDigest"],
        "searchable": True,
        "trusted": False,
    },
    "モデルプレス": {
        "domains": ["mdpr.jp"],
        "patterns": [r"/news/detail/"],
        "aliases": ["モデルプレス"],
        "searchable": True,
        "trusted": True,
    },
    "Entinsight": {
        "domains": [],
        "patterns": [],
        "aliases": ["Entinsight"],
        "searchable": False,
        "trusted": False,
    },
    "Domingo": {
        "domains": ["domingo.ne.jp"],
        "patterns": [r"/article/"],
        "aliases": ["Domingo"],
        "searchable": True,
        "trusted": False,
    },
    "エンタメポスト": {
        "domains": ["entame-post.jp"],
        "patterns": [r"/post/"],
        "aliases": ["エンタメポスト"],
        "searchable": True,
        "trusted": False,
    },
    "NEWS速報": {
        "domains": [],
        "patterns": [],
        "aliases": ["NEWS速報"],
        "searchable": False,
        "trusted": False,
    },
    "チバテレ": {
        "domains": ["www.chiba-tv.com", "chiba-tv.com"],
        "patterns": [r"/plus/detail/(amp/)?\d+(?:\?|/?$)"],
        "aliases": ["チバテレ", "千葉テレビ"],
        "searchable": True,
        "trusted": True,
    },
    "GIGAパーク": {
        "domains": ["www.giga.co.jp", "giga.co.jp"],
        "patterns": [r"/jsp/artist/news_gp\.jsp", r"[?&]index=ovo_news(?:&|$)", r"[?&]key=O\d+(?:&|$)"],
        "aliases": ["GIGAパーク", "GIGAPARK", "giga.co.jp"],
        "searchable": True,
        "trusted": True,
    },
    "antenna": {
        "domains": ["antenna.jp"],
        "patterns": [r"/articles/\d+(?:\?|/?$)", r"/brands/\d+(?:\?|/?$)"],
        "aliases": ["antenna"],
        "searchable": True,
        "trusted": False,
    },
    "NEWSjp": {
        "domains": ["news.jp"],
        "patterns": [r"/i/\d+(?:\?|/?$)"],
        "aliases": ["NEWSjp", "NEWS.jp", "news.jp"],
        "searchable": True,
        "trusted": True,
    },
    "PRタイムス": {
        "domains": ["prtimes.jp"],
        "patterns": [r"/main/html/rd/p/", r"/main/html/"],
        "aliases": ["PR TIMES", "PRタイムス", "prtimes"],
        "searchable": True,
        "trusted": True,
    },
    "アットプレス": {
        "domains": ["at-press.net", "www.atpress.ne.jp", "atpress.ne.jp"],
        "patterns": [r"/news/\d+", r"/release/"],
        "aliases": ["@Press", "アットプレス", "atpress"],
        "searchable": True,
        "trusted": True,
    },
    "ドリームニュース": {
        "domains": ["dreamnews.jp", "www.dreamnews.jp"],
        "patterns": [r"/press/0*\d+", r"/r/\d+"],
        "aliases": ["Dream News", "ドリームニュース", "dreamnews"],
        "searchable": True,
        "trusted": True,
    },
    "Yahooファイナンス": {
        "domains": ["finance.yahoo.co.jp"],
        "patterns": [r"/news/detail/", r"/news/"],
        "aliases": ["Yahoo!ファイナンス", "Yahooファイナンス", "Yahoo Finance"],
        "searchable": True,
        "trusted": True,
    },
    "その他": {
        "domains": [],
        "patterns": [],
        "aliases": [],
        "searchable": False,
        "trusted": False,
    },
}

FAST_LANE_MEDIA = [
    "KK KYODO NEWS SITE",
    "GIGAパーク",
    "チバテレ",
    "ニコニコニュース",
    "エキサイトニュース",
    "Yahooニュース",
    "47NEWS",
    "ドコモｄメニューニュース",
    "楽天インフォシークニュース",
    "ニフティニュース",
    "NewsPicks",
    "NEWSjp",
    "SmartNews",
]

SECOND_LANE_MEDIA = [
    "gooニュース",
    "ビッグローブニュース",
    "ライブドアニュース",
    "アメーバニュース",
    "mixiニュース",
    "めるもニュース",
    "Gunosy",
    "OKMusic",
    "ビルボードジャパン",
    "ウレぴあ総研",
    "LIMIA",
    "モデルプレス",
    "MSNニュース",
    "PRタイムス",
    "アットプレス",
    "ドリームニュース",
    "Yahooファイナンス",
]

THIRD_LANE_MEDIA = [
    "Googleニュース",
    "LINE News",
    "SmartNews",
    "auニュースパス",
    "ドコモエンタメウィーク",
    "ルクラ",
    "auサービスToday",
    "Socialife",
    "NewsSuite",
    "TheNewsLens",
    "Viber",
    "NewsPicks",
    "NewsDigest",
    "Domingo",
    "エンタメポスト",
    "antenna",
]

ID_FRIENDLY_MEDIA = {
    "KK KYODO NEWS SITE",
    "GIGAパーク",
    "エキサイトニュース",
    "ドコモｄメニューニュース",
    "楽天インフォシークニュース",
    "gooニュース",
    "ビッグローブニュース",
    "ニフティニュース",
    "ライブドアニュース",
    "アメーバニュース",
    "めるもニュース",
    "mixiニュース",
    "antenna",
}

TRUSTED_MEDIA = {name for name, cfg in MEDIA_REGISTRY.items() if cfg.get("trusted")}


def safe_str(v):
    return "" if v is None else str(v).strip()


def normalize_text(text):
    text = safe_str(text).replace("\u3000", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def strip_brackets(text):
    text = safe_str(text)
    text = re.sub(r"【.*?】", " ", text)
    text = re.sub(r"\[.*?\]", " ", text)
    text = re.sub(r"（.*?）", " ", text)
    text = re.sub(r"\(.*?\)", " ", text)
    return normalize_text(text)


def title_core(text):
    return normalize_text(re.sub(r"\s*[|｜\-—－:：]\s*.*$", "", strip_brackets(text)))


def sim(a, b):
    a = normalize_text(a)
    b = normalize_text(b)
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def extract_keywords(text):
    """汎用キーワード抽出。長いトークン(12文字超)はトリムして検索クエリに使いやすくする。"""
    text = title_core(text)
    # 漢字とカタカナを個別に抽出。ひらがなを含めると助詞で単語が連結して無意味な長トークンになる
    tokens = re.findall(r"[A-Za-z0-9\-\+]{2,}|[一-龥]{2,}|[ァ-ヶー]{2,}", text)
    stop = {
        "ニュース", "記事", "提供", "発表", "導入", "開始", "決定", "推進",
        "本格導入", "さらなる", "もの", "こと", "ため", "東京",
        "オーヴォ", "共同通信", "共同通信社", "サイト",
    }
    seen = set()
    out = []
    for token in tokens:
        if token in stop:
            continue
        # 純数字トークン（日付・数値）は検索キーワードとして不適切なため除外
        if token.isdigit():
            continue
        # 12文字超の日本語トークンは先頭12文字にトリム（検索クエリ用）
        key = token[:12] if len(token) > 12 else token
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out[:24]


def keyword_overlap(a, b):
    text_a = title_core(a)
    text_b = title_core(b)
    # ひらがなを含めると助詞で単語が連結し、同記事でも一致しなくなる
    tokens_a = re.findall(r"[A-Za-z0-9\-\+]{2,}|[一-龥]{2,}|[ァ-ヶー]{2,}", text_a)
    tokens_b = re.findall(r"[A-Za-z0-9\-\+]{2,}|[一-龥]{2,}|[ァ-ヶー]{2,}", text_b)
    stop = {
        "ニュース", "記事", "提供", "発表", "導入", "開始", "決定", "推進",
        "本格導入", "さらなる", "もの", "こと", "ため", "東京",
        "オーヴォ", "共同通信", "共同通信社", "サイト",
    }
    ka = set(t for t in tokens_a if t not in stop)
    kb = set(t for t in tokens_b if t not in stop)
    return (len(ka & kb) / len(ka | kb)) if ka and kb else 0.0


def extract_alpha_keywords(text, max_count=4):
    """
    英数字のみのキーワードを抽出（補助用）。
    AI, MIXI, 2027 等の固有名詞が複数ある記事では検索精度が上がる。
    英数字が少ない記事では空リストを返すので、通常KWのクエリにフォールバックする。
    """
    text = title_core(text)
    tokens = re.findall(r"[A-Za-z0-9\-\+]{2,}", text)
    # 一般的すぎる英語単語はスキップ
    stop_alpha = {"DAY", "THE", "FOR", "AND", "OR", "IN", "OF", "IS", "AT"}
    seen = set()
    out = []
    for tok in tokens:
        if tok.upper() in stop_alpha:
            continue
        # 純数字トークン（18, 2026 等の日付・数値）は固有名詞でないため除外
        if tok.isdigit():
            continue
        if tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out[:max_count]


def domain_of(url):
    try:
        return urlparse(safe_str(url)).netloc.lower()
    except Exception:
        return ""


def canonicalize_url(url):
    url = safe_str(url)
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        scheme = (parsed.scheme or "https").lower()
        netloc = parsed.netloc.lower().replace(":80", "").replace(":443", "")
        path = re.sub(r"/+", "/", parsed.path or "/")

        if netloc in {"www.chiba-tv.com", "chiba-tv.com"}:
            path = re.sub(r"^/plus/detail/amp/(\d+)/?$", r"/plus/detail/\1", path)

        if path != "/" and path.endswith("/"):
            path = path[:-1]

        query_items = []
        for k, v in parse_qsl(parsed.query, keep_blank_values=True):
            if k in BAD_QUERY_KEYS:
                continue
            query_items.append((k, v))
        query_items.sort()
        query = urlencode(query_items)

        return urlunparse((scheme, netloc, path, "", query, ""))
    except Exception:
        return url.strip()


def detect_source_mode(url):
    d = domain_of(url)
    if "ovo.kyodo.co.jp" in d:
        return "OVO"
    if "b-dot.jp" in d or "b.kyodo.co.jp" in d:
        return "b-dot"
    return "unknown"


def classify_media(url, title=""):
    d = domain_of(url)
    t = safe_str(title)

    for media, cfg in MEDIA_REGISTRY.items():
        for dm in cfg.get("domains", []):
            if d == dm.lower():
                return media

    title_lc = t.lower()
    for media, cfg in MEDIA_REGISTRY.items():
        for alias in cfg.get("aliases", []):
            if alias and alias.lower() in title_lc:
                return media

    return "その他"


def extract_source_id(url, title="", text=""):
    hay = f"{safe_str(url)} {safe_str(title)} {safe_str(text)}"
    patterns = [
        r"Ovo[_\-]?(\d{6,})",
        r"ovo[_\-]?O?(\d{6,})",
        r"\ba[-_](\d{6,})\b",
        r"[?&]key=O(\d{6,})(?:&|$)",
        r"\bO(\d{6,})\b",
        # b-dot URL形式: /YYYY-MM-DD_ID（_の直前がword文字のため\bが機能しない問題を回避）
        r"/\d{4}-\d{2}-\d{2}[_\-](\d{6,})(?:[/?#]|$)",
        r"\b(\d{6,7})\b",
    ]
    for pat in patterns:
        m = re.search(pat, hay, flags=re.IGNORECASE)
        if m:
            return m.group(1)
    return ""


def is_noise_url(url):
    u = safe_str(url).lower()
    if not u.startswith("http"):
        return True
    if "www.giga.co.jp/jsp/artist/news_gp.jsp" in u:
        return False
    if re.search(r"/watch/nw\d+(?:\?|/?$)", u):
        return False
    if re.search(r"/plus/detail/(amp/)?\d+(?:\?|/?$)", u):
        return False
    if re.search(r"/view_news\.pl", u):
        return False
    # パスのみを見てノイズ判定（クエリパラメータは除外）
    from urllib.parse import urlparse
    path = urlparse(u).path
    return any(re.search(pat, path) for pat in NOISE_PATH_PATTERNS)


def is_probable_article_url(url, media=""):
    u = safe_str(url).lower()
    if not u.startswith("http"):
        return False
    if media in SOCIAL_MEDIA:
        return False

    if media == "Yahooニュース" and re.search(r"/articles/[A-Za-z0-9]+(?:\?|/?$)", u):
        return True
    if media == "47NEWS" and re.search(r"/\d+\.html(?:\?|$)", u):
        return True
    if media == "ドコモｄメニューニュース" and re.search(r"/article/ovo/.+/ovo-O\d+(?:\?|/?$)", u):
        return True
    if media == "楽天インフォシークニュース" and re.search(r"/article/ovo_O\d+(?:\?|/?$)", u):
        return True
    if media == "エキサイトニュース" and re.search(r"/news/article/ovo_\d+(?:\?|/?$)", u, flags=re.IGNORECASE):
        return True
    if media == "チバテレ" and re.search(r"/plus/detail/(amp/)?\d+(?:\?|/?$)", u):
        return True
    if media == "GIGAパーク" and "news_gp.jsp" in u and re.search(r"[?&]key=o\d+(?:&|$)", u):
        return True
    if media == "ニコニコニュース" and re.search(r"/watch/nw\d+(?:\?|/?$)", u):
        return True
    if media == "mixiニュース" and re.search(r"/view_news\.pl", u):
        return True
    if media == "KK KYODO NEWS SITE" and (
        any(x in u for x in ["/article/", "/news/", "/press-releases/"])
        or re.search(r"/(sport|life|culture|business|area|politics-international|news)/\d{4}-\d{2}-\d{2}_\d+(?:\?|/?$)", u)
    ):
        return True
    if media == "MSNニュース" and re.search(r"/ar-[A-Za-z0-9]+(?:\?|/?$)", u):
        return True
    if media == "antenna" and re.search(r"/articles/\d+(?:\?|/?$)", u):
        return True
    if media == "NewsPicks" and re.search(r"/news/\d+(?:\?|/?$)", u):
        return True
    if media == "NEWSjp" and re.search(r"/i/\d+(?:\?|/?$)", u):
        return True
    if media == "SmartNews" and (
        re.search(r"/m-[A-Za-z0-9]+(?:/[A-Za-z0-9]+)?(?:\?|/?$)", u)
        or re.search(r"/articles/[A-Za-z0-9]+(?:\?|/?$)", u)
    ):
        return True
    if media == "PRタイムス" and re.search(r"/main/html/", u):
        return True
    if media == "アットプレス" and re.search(r"/news/\d+", u):
        return True
    if media == "ドリームニュース" and re.search(r"/press/0*\d+", u):
        return True
    if media == "Yahooファイナンス" and re.search(r"/news/", u):
        return True

    positive = [
        r"/article/",
        r"/articles/",
        r"/detail/",
        r"/entry/",
        r"/story/",
        r"/news/",
        r"/watch/nw\d+(?:\?|/?$)",
        r"/post/",
        r"/jsp/artist/news_gp\.jsp",
        r"/ar-[A-Za-z0-9]+(?:\?|/?$)",
        r"/\d{6,}\.html(?:\?|$)",
        r"/\d{4}/\d{2}/\d{2}/",
        r"/(sport|life|culture|business|area|politics-international|news)/\d{4}-\d{2}-\d{2}_\d+(?:\?|/?$)",
        r"/plus/detail/(amp/)?\d+(?:\?|/?$)",
        r"/view_news\.pl",
        r"/m-[A-Za-z0-9]+/",
        r"/i/\d+(?:\?|/?$)",
    ]
    return any(re.search(p, u) for p in positive) and not is_noise_url(u)


def media_url_pattern_score(url, media, source_id=""):
    u = safe_str(url).lower()
    score = 0.0
    cfg = MEDIA_REGISTRY.get(media, {})

    for pat in cfg.get("patterns", []):
        if re.search(pat, u, flags=re.IGNORECASE):
            score += 0.08

    if media == "KK KYODO NEWS SITE" and (
        any(x in u for x in ["/article/", "/news/", "/press-releases/"])
        or re.search(r"/(sport|life|culture|business|area|politics-international|news)/\d{4}-\d{2}-\d{2}_\d+/?$", u)
    ):
        score += 0.22
    if media == "GIGAパーク" and "news_gp.jsp" in u and re.search(r"[?&]key=o\d+(?:&|$)", u):
        score += 0.24
    if media == "ニコニコニュース" and re.search(r"/watch/nw\d+(?:\?|/?$)", u):
        score += 0.18
    if media == "チバテレ" and re.search(r"/plus/detail/(amp/)?\d+(?:\?|/?$)", u):
        score += 0.18
    if media == "エキサイトニュース" and re.search(r"/news/article/ovo_\d+(?:\?|/?$)", u):
        score += 0.18
    if media == "ドコモｄメニューニュース" and re.search(r"/article/ovo/.+/ovo-O\d+(?:\?|/?$)", u):
        score += 0.18
    if media == "楽天インフォシークニュース" and re.search(r"/article/ovo_O\d+(?:\?|/?$)", u):
        score += 0.18
    if media == "47NEWS" and re.search(r"/\d+\.html(?:\?|$)", u):
        score += 0.14
    if media == "Yahooニュース" and re.search(r"/articles/[A-Za-z0-9]+(?:\?|/?$)", u):
        score += 0.12
    if media == "MSNニュース" and re.search(r"/ar-[A-Za-z0-9]+(?:\?|/?$)", u):
        score += 0.16
    if media == "antenna" and re.search(r"/articles/\d+(?:\?|/?$)", u):
        score += 0.10
    if media == "NewsPicks" and re.search(r"/news/\d+(?:\?|/?$)", u):
        score += 0.14
    if media == "NEWSjp" and re.search(r"/i/\d+(?:\?|/?$)", u):
        score += 0.14
    if media == "SmartNews" and (
        re.search(r"/m-[A-Za-z0-9]+(?:/[A-Za-z0-9]+)?(?:\?|/?$)", u)
        or re.search(r"/articles/[A-Za-z0-9]+(?:\?|/?$)", u)
    ):
        score += 0.12
    if media == "mixiニュース" and re.search(r"/view_news\.pl", u):
        score += 0.16
    if media == "PRタイムス" and re.search(r"/main/html/", u):
        score += 0.14
    if media == "アットプレス" and re.search(r"/news/\d+", u):
        score += 0.14
    if media == "ドリームニュース" and re.search(r"/press/0*\d+", u):
        score += 0.14
    if media == "Yahooファイナンス" and re.search(r"/news/", u):
        score += 0.12

    if source_id:
        if source_id in u:
            score += 0.22
        if f"o{source_id}" in u:
            score += 0.22

    return min(score, 0.40)


def is_trusted_media(media):
    return media in TRUSTED_MEDIA


def dedupe_strings(items):
    seen = set()
    out = []
    for item in items:
        item = safe_str(item)
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def build_direct_candidates(source_id):
    if not source_id:
        return []
    return dedupe_strings([
        f"https://www.excite.co.jp/news/article/Ovo_{source_id}/",
        f"https://topics.smt.docomo.ne.jp/article/ovo/life/ovo-O{source_id}",
        f"https://news.infoseek.co.jp/article/ovo_O{source_id}/",
        f"https://www.giga.co.jp/jsp/artist/news_gp.jsp?index=ovo_news&key=O{source_id}",
    ])


def build_general_queries(source_title, source_keywords, source_id=""):
    """
    汎用クエリ生成。
    - 通常KW（必ず生成）: 全記事で機能
    - 英数字KW（英数字が2個以上の場合のみ追加）: MIXI/AI等の固有名詞記事で精度向上
    """
    core = title_core(source_title)
    short_core = core if len(core) <= 25 else ""
    alpha_kws = extract_alpha_keywords(source_title, max_count=4)
    queries = []

    if short_core:
        queries.append(f'"{short_core}"')
        queries.append(f'"{short_core}" ニコニコニュース')
        queries.append(f'"{short_core}" チバテレ')
        queries.append(f'"{short_core}" GIGAパーク')

    # 通常KW（全記事共通）
    if len(source_keywords) >= 2:
        kw2q = f'"{source_keywords[0]}" "{source_keywords[1]}"'
        queries.append(kw2q)
        queries.append(f"{kw2q} MSN")
        queries.append(f"{kw2q} antenna")
        queries.append(f"{kw2q} 共同通信社")
        queries.append(f"{kw2q} GIGAパーク")
        queries.append(f"{kw2q} ニコニコニュース")
        queries.append(f"{kw2q} チバテレ")
        queries.append(f"{kw2q} NewsPicks")
        queries.append(f"{kw2q} SmartNews")
        queries.append(f"{kw2q} mixi")
        queries.append(f"{kw2q} NEWSjp")

    if len(source_keywords) >= 3:
        kw3q = f'"{source_keywords[0]}" "{source_keywords[1]}" "{source_keywords[2]}"'
        queries.append(kw3q)
        queries.append(f"{kw3q} GIGAパーク")
        queries.append(f"{kw3q} NewsPicks")
        queries.append(f"{kw3q} SmartNews")

    # 中位KW（位置2-3）: タイトル前半が汎用語の場合に特定性の高いペアで補完
    if len(source_keywords) >= 4:
        kw2b_a = source_keywords[2]
        kw2b_b = source_keywords[3]
        kw2bq = f'"{kw2b_a}" "{kw2b_b}"'
        kw2b = f"{kw2b_a} {kw2b_b}"
        queries.append(kw2bq)
        queries.append(f"{kw2bq} 共同通信")
        queries.append(f"{kw2bq} GIGAパーク")
        queries.append(f"{kw2bq} ニコニコニュース")
        queries.append(f"{kw2bq} チバテレ")
        queries.append(f"{kw2bq} NewsPicks")
        queries.append(f"{kw2bq} SmartNews")
        queries.append(f"{kw2bq} mixi")
        queries.append(f"{kw2bq} NEWSjp")
        queries.append(f"{kw2bq} PR TIMES")
        queries.append(f"site:prtimes.jp {kw2b}")
        queries.append(f"site:at-press.net {kw2b}")

    # 英数字KW（英数字が2個以上の記事のみ追加クエリ）
    if len(alpha_kws) >= 2:
        ak2q = f'"{alpha_kws[0]}" "{alpha_kws[1]}"'
        queries.append(ak2q)
        queries.append(f"{ak2q} NewsPicks")
        queries.append(f"{ak2q} SmartNews")
        queries.append(f"{ak2q} mixi")
        queries.append(f"{ak2q} NEWSjp")
        queries.append(f"{ak2q} ニコニコニュース")
        queries.append(f"{ak2q} チバテレ")
    if len(alpha_kws) >= 3:
        ak3q = f'"{alpha_kws[0]}" "{alpha_kws[1]}" "{alpha_kws[2]}"'
        queries.append(ak3q)
        queries.append(f"{ak3q} NewsPicks")
        queries.append(f"{ak3q} SmartNews")

    if source_id:
        queries.append(f'"Ovo_{source_id}"')
        queries.append(f'"ovo_O{source_id}"')
        queries.append(f'"O{source_id}"')
        queries.append(f'"key=O{source_id}"')
    return dedupe_strings(queries)


def build_site_queries_for_media(source_title, source_keywords, source_id, media_names):
    """
    site:クエリ生成。
    - 通常KW2〜3語のsite:クエリ（全記事）
    - 英数字KW2〜3語のsite:クエリ（英数字が多い記事のみ追加）
    """
    core = title_core(source_title)
    short_core = core if len(core) <= 25 else ""
    alpha_kws = extract_alpha_keywords(source_title, max_count=4)
    exact_title = f'"{short_core}"' if short_core else ""
    kw2 = " ".join(source_keywords[:2])
    kw3 = " ".join(source_keywords[:3])
    ak2 = " ".join(alpha_kws[:2]) if len(alpha_kws) >= 2 else ""
    ak3 = " ".join(alpha_kws[:3]) if len(alpha_kws) >= 3 else ""
    queries = []

    for media in media_names:
        cfg = MEDIA_REGISTRY.get(media, {})
        for dm in cfg.get("domains", []):
            if dm == "l.smartnews.com":
                continue
            # 通常KW（全記事共通）
            if kw2:
                queries.append(f"site:{dm} {kw2}")
            if kw3 and kw3 != kw2:
                queries.append(f"site:{dm} {kw3}")
            if exact_title:
                queries.append(f"site:{dm} {exact_title}")
            # 英数字KW（補助）
            if ak2:
                queries.append(f"site:{dm} {ak2}")
            if ak3 and ak3 != ak2:
                queries.append(f"site:{dm} {ak3}")
            if source_id and media in ID_FRIENDLY_MEDIA:
                queries.append(f'site:{dm} "O{source_id}"')
                if kw2:
                    queries.append(f'site:{dm} {kw2} "O{source_id}"')
    return dedupe_strings(queries)


def media_sort_key(media):
    try:
        return (PARTNER_MEDIA_ALL.index(media), media)
    except ValueError:
        return (999, media)


def to_level(score):
    if score >= 0.80:
        return "高"
    if score >= 0.54:
        return "中"
    if score >= 0.34:
        return "低"
    return "対象外"