"core.parser.platform.xiaoheihe 模块。"
import asyncio
import base64
import gzip
import hashlib
import html as html_lib
import json
import random
import re
import time
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple, Iterable
from urllib.parse import urlparse, parse_qs, unquote

import aiohttp

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.algorithms import AES
    from cryptography.hazmat.primitives.ciphers.base import Cipher
    from cryptography.hazmat.primitives.ciphers.modes import CBC, ECB
    try:
        from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
    except Exception:
        from cryptography.hazmat.primitives.ciphers.algorithms import TripleDES
    _CRYPTO_IMPORT_ERROR = None
except Exception as e:
    serialization = None
    padding = None
    AES = None
    Cipher = None
    CBC = None
    ECB = None
    TripleDES = None
    _CRYPTO_IMPORT_ERROR = e

from ...logger import logger

from .base import BaseVideoParser
from ..utils import build_request_headers
from ...constants import Config


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class XiaoheiheSign:
    """小黑盒 Web API 签名生成器。"""

    CHAR_TABLE = "AB45STUVWZEFGJ6CH01D237IXYPQRKLMN89"
    _OFFSET_MAP = {
        "a": -1,
        "b": -2,
        "c": -3,
        "d": -4,
        "e": -5,
        "f": 0,
        "g": +1,
        "h": +2,
        "i": +3,
        "j": +4,
        "k": +5,
    }

    def __init__(self, method_key: str = "g"):
        self._offset = self._OFFSET_MAP[method_key]

    def sign(self, path: str) -> Dict[str, Any]:
        """为指定 API 路径生成 `hkey/_time/nonce`。"""
        now = int(time.time())
        nonce = hashlib.md5(
            (str(now) + str(random.random())).encode()
        ).hexdigest().upper()
        hkey = self._ov(path, now + self._offset, nonce)
        return {"hkey": hkey, "_time": now, "nonce": nonce}

    def _ov(self, path: str, timestamp: int, nonce: str) -> str:
        path = "/" + "/".join(p for p in path.split("/") if p) + "/"
        mapped = [
            self._av(str(timestamp), self.CHAR_TABLE, -2),
            self._sv(path, self.CHAR_TABLE),
            self._sv(nonce, self.CHAR_TABLE),
        ]
        interleaved = self._interleave(mapped)[:20]
        md5_hex = hashlib.md5(interleaved.encode()).hexdigest()
        suffix = str(
            sum(self._mix_columns([ord(c) for c in md5_hex[-6:]])) % 100
        ).zfill(2)
        prefix = self._av(md5_hex[:5], self.CHAR_TABLE, -4)
        return prefix + suffix

    @staticmethod
    def _av(text: str, table: str, cut: int) -> str:
        sub_table = table[:cut]
        return "".join(sub_table[ord(c) % len(sub_table)] for c in text)

    @staticmethod
    def _sv(text: str, table: str) -> str:
        return "".join(table[ord(c) % len(table)] for c in text)

    @staticmethod
    def _interleave(arrays: List[str]) -> str:
        result = []
        max_len = max(len(a) for a in arrays)
        for i in range(max_len):
            for a in arrays:
                if i < len(a):
                    result.append(a[i])
        return "".join(result)

    @staticmethod
    def _xtime(e: int) -> int:
        return (e << 1 ^ 27) & 0xFF if e & 128 else e << 1

    @classmethod
    def _mul3(cls, e: int) -> int:
        return cls._xtime(e) ^ e

    @classmethod
    def _mul6(cls, e: int) -> int:
        return cls._mul3(cls._xtime(e))

    @classmethod
    def _mul12(cls, e: int) -> int:
        return cls._mul6(cls._mul3(cls._xtime(e)))

    @classmethod
    def _mul14(cls, e: int) -> int:
        return cls._mul12(e) ^ cls._mul6(e) ^ cls._mul3(e)

    @classmethod
    def _mix_columns(cls, col: List[int]) -> List[int]:
        while len(col) < 4:
            col.append(0)
        e = col
        result = [
            cls._mul14(e[0]) ^ cls._mul12(e[1]) ^ cls._mul6(e[2]) ^ cls._mul3(e[3]),
            cls._mul3(e[0]) ^ cls._mul14(e[1]) ^ cls._mul12(e[2]) ^ cls._mul6(e[3]),
            cls._mul6(e[0]) ^ cls._mul3(e[1]) ^ cls._mul14(e[2]) ^ cls._mul12(e[3]),
            cls._mul12(e[0]) ^ cls._mul6(e[1]) ^ cls._mul3(e[2]) ^ cls._mul14(e[3]),
        ]
        if len(e) > 4:
            result.extend(e[4:])
        return result


class XiaoheiheDevice:
    """生成小黑盒 Web 风控设备 token。"""

    DEVICES_INFO_URL = "https://fp-it.portal101.cn/deviceprofile/v4"
    SM_CONFIG = {
        "organization": "0yD85BjYvGFAvHaSQ1mc",
        "appId": "heybox_website",
        "publicKey": (
            "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCXj9exmI4nQjmT52iwr+yf7hAQ06bfSZHTAH"
            "UfRBYiagCf/whhd8es0R79wBigpiHLd28TKA8b8mGR8OiiI1hV+qfynCWihvp3mdj8MiiH6SU3"
            "lhro2hkfYzImZB0RmWr2zE4Xt1+A6Oyp6bf+W7JSxYUXHw3nNv7Td4jw4jEFKQIDAQAB"
        ),
    }

    DES_RULE = {
        "appId": {"cipher": "DES", "is_encrypt": 1, "key": "uy7mzc4h", "obfuscated_name": "xx"},
        "box": {"is_encrypt": 0, "obfuscated_name": "jf"},
        "canvas": {"cipher": "DES", "is_encrypt": 1, "key": "snrn887t", "obfuscated_name": "yk"},
        "clientSize": {"cipher": "DES", "is_encrypt": 1, "key": "cpmjjgsu", "obfuscated_name": "zx"},
        "organization": {"cipher": "DES", "is_encrypt": 1, "key": "78moqjfc", "obfuscated_name": "dp"},
        "os": {"cipher": "DES", "is_encrypt": 1, "key": "je6vk6t4", "obfuscated_name": "pj"},
        "platform": {"cipher": "DES", "is_encrypt": 1, "key": "pakxhcd2", "obfuscated_name": "gm"},
        "plugins": {"cipher": "DES", "is_encrypt": 1, "key": "v51m3pzl", "obfuscated_name": "kq"},
        "pmf": {"cipher": "DES", "is_encrypt": 1, "key": "2mdeslu3", "obfuscated_name": "vw"},
        "protocol": {"is_encrypt": 0, "obfuscated_name": "protocol"},
        "referer": {"cipher": "DES", "is_encrypt": 1, "key": "y7bmrjlc", "obfuscated_name": "ab"},
        "res": {"cipher": "DES", "is_encrypt": 1, "key": "whxqm2a7", "obfuscated_name": "hf"},
        "rtype": {"cipher": "DES", "is_encrypt": 1, "key": "x8o2h2bl", "obfuscated_name": "lo"},
        "sdkver": {"cipher": "DES", "is_encrypt": 1, "key": "9q3dcxp2", "obfuscated_name": "sc"},
        "status": {"cipher": "DES", "is_encrypt": 1, "key": "2jbrxxw4", "obfuscated_name": "an"},
        "subVersion": {"cipher": "DES", "is_encrypt": 1, "key": "eo3i2puh", "obfuscated_name": "ns"},
        "svm": {"cipher": "DES", "is_encrypt": 1, "key": "fzj3kaeh", "obfuscated_name": "qr"},
        "time": {"cipher": "DES", "is_encrypt": 1, "key": "q2t3odsk", "obfuscated_name": "nb"},
        "timezone": {"cipher": "DES", "is_encrypt": 1, "key": "1uv05lj5", "obfuscated_name": "as"},
        "tn": {"cipher": "DES", "is_encrypt": 1, "key": "x9nzj1bp", "obfuscated_name": "py"},
        "trees": {"cipher": "DES", "is_encrypt": 1, "key": "acfs0xo4", "obfuscated_name": "pi"},
        "ua": {"cipher": "DES", "is_encrypt": 1, "key": "k92crp1t", "obfuscated_name": "bj"},
        "url": {"cipher": "DES", "is_encrypt": 1, "key": "y95hjkoo", "obfuscated_name": "cf"},
        "version": {"is_encrypt": 0, "obfuscated_name": "version"},
        "vpw": {"cipher": "DES", "is_encrypt": 1, "key": "r9924ab5", "obfuscated_name": "ca"},
    }

    BROWSER_ENV = {
        "plugins": (
            "MicrosoftEdgePDFPluginPortableDocumentFormatinternal-pdf-viewer1,Micros"
            "oftEdgePDFViewermhjfbmdgcfjbbpaeojofohoefgiehjai1"
        ),
        "ua": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0"
        ),
        "canvas": "259ffe69",
        "timezone": -480,
        "platform": "Win32",
        "url": "https://www.skland.com/",
        "referer": "",
        "res": "1920_1080_24_1.25",
        "clientSize": "0_0_1080_1920_1920_1080_1920_1080",
        "status": "0011",
    }

    @classmethod
    def _ensure_crypto(cls):
        if _CRYPTO_IMPORT_ERROR is not None:
            raise RuntimeError(
                "小黑盒 BBS/API 解析需要 cryptography 依赖"
            ) from _CRYPTO_IMPORT_ERROR

    @classmethod
    def _des(cls, payload: Dict[str, Any]) -> Dict[str, Any]:
        cls._ensure_crypto()
        result = {}
        for key in payload.keys():
            if key not in cls.DES_RULE:
                result[key] = payload[key]
                continue
            rule = cls.DES_RULE[key]
            value = payload[key]
            if rule["is_encrypt"] == 1:
                cipher = Cipher(TripleDES(rule["key"].encode("utf-8")), ECB())
                data = str(value).encode("utf-8") + b"\x00" * 8
                value = base64.b64encode(cipher.encryptor().update(data)).decode("utf-8")
            result[rule["obfuscated_name"]] = value
        return result

    @classmethod
    def _aes(cls, value: bytes, key: bytes) -> str:
        cls._ensure_crypto()
        iv = "0102030405060708"
        cipher = Cipher(AES(key), CBC(iv.encode("utf-8")))
        value += b"\x00"
        while len(value) % 16 != 0:
            value += b"\x00"
        return cipher.encryptor().update(value).hex()

    @staticmethod
    def _gzip(payload: Dict[str, Any]) -> bytes:
        json_str = json.dumps(payload, ensure_ascii=False)
        stream = gzip.compress(json_str.encode("utf-8"), 2, mtime=0)
        return base64.b64encode(stream)

    @staticmethod
    def _get_tn(payload: Dict[str, Any]) -> str:
        parts = []
        for key in sorted(payload.keys()):
            value = payload[key]
            if isinstance(value, (int, float)):
                value = str(value * 10000)
            elif isinstance(value, dict):
                value = XiaoheiheDevice._get_tn(value)
            parts.append(value)
        return "".join(parts)

    @staticmethod
    def _get_smid() -> str:
        current = time.localtime()
        time_text = (
            f"{current.tm_year}{current.tm_mon:0>2d}{current.tm_mday:0>2d}"
            f"{current.tm_hour:0>2d}{current.tm_min:0>2d}{current.tm_sec:0>2d}"
        )
        uid = str(uuid.uuid4())
        value = time_text + hashlib.md5(uid.encode("utf-8")).hexdigest() + "00"
        smsk_web = hashlib.md5(("smsk_web_" + value).encode("utf-8")).hexdigest()[0:14]
        return value + smsk_web + "0"

    @classmethod
    async def get_d_id(cls, session: aiohttp.ClientSession) -> str:
        """向数美设备接口换取小黑盒 `x_xhh_tokenid`。"""
        cls._ensure_crypto()
        uid = str(uuid.uuid4()).encode("utf-8")
        pri_id = hashlib.md5(uid).hexdigest()[0:16]
        public_key = serialization.load_der_public_key(
            base64.b64decode(cls.SM_CONFIG["publicKey"])
        )
        ep = base64.b64encode(public_key.encrypt(uid, padding.PKCS1v15())).decode("utf-8")

        browser = cls.BROWSER_ENV.copy()
        current_time = int(time.time() * 1000)
        browser.update({
            "vpw": str(uuid.uuid4()),
            "svm": current_time,
            "trees": str(uuid.uuid4()),
            "pmf": current_time,
        })

        target = {
            **browser,
            "protocol": 102,
            "organization": cls.SM_CONFIG["organization"],
            "appId": cls.SM_CONFIG["appId"],
            "os": "web",
            "version": "3.0.0",
            "sdkver": "3.0.0",
            "box": "",
            "rtype": "all",
            "smid": cls._get_smid(),
            "subVersion": "1.0.0",
            "time": 0,
        }
        target["tn"] = hashlib.md5(cls._get_tn(target).encode()).hexdigest()
        encrypted = cls._aes(cls._gzip(cls._des(target)), pri_id.encode("utf-8"))

        async with session.post(
            cls.DEVICES_INFO_URL,
            json={
                "appId": cls.SM_CONFIG["appId"],
                "compress": 2,
                "data": encrypted,
                "encode": 5,
                "ep": ep,
                "organization": cls.SM_CONFIG["organization"],
                "os": "web",
            },
            timeout=aiohttp.ClientTimeout(total=20),
        ) as response:
            data = await response.json(content_type=None)
        if data.get("code") != 1100:
            raise RuntimeError("小黑盒设备 token 生成失败")
        return "B" + data["detail"]["deviceId"]


class XiaoheiheParser(BaseVideoParser):

    "XiaoheiheParser 类。"
    def __init__(
        self,
        use_video_proxy: bool = False,
        proxy_url: str = None
    ):
        """初始化解析器并设置并发限制与默认请求头。

        Args:
            use_video_proxy: 视频下载是否使用代理
            proxy_url: 代理地址（格式：http://host:port 或 socks5://host:port）
        """
        super().__init__("xiaoheihe")
        self.use_video_proxy = use_video_proxy
        self.proxy_url = proxy_url
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)
        self._device_id: Optional[str] = None
        self._device_id_lock = asyncio.Lock()
        self._default_headers = {
            "User-Agent": UA,
            "Referer": "https://www.xiaoheihe.cn/",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate",
        }
    
    def _add_m3u8_prefix_to_urls(self, urls: List[str]) -> List[str]:
        """为 m3u8 URL 列表添加 m3u8: 前缀
        
        Args:
            urls: URL 列表
            
        Returns:
            添加了 m3u8: 前缀的 URL 列表（仅对 m3u8 URL 添加）
        """
        if not urls:
            return urls
        
        result = []
        for url in urls:
            if url and isinstance(url, str):
                url_lower = url.lower()
                if '.m3u8' in url_lower and not url.startswith('m3u8:'):
                    result.append(f'm3u8:{url}')
                else:
                    result.append(url)
            else:
                result.append(url)
        
        return result

    def can_parse(self, url: str) -> bool:
        """判断是否可以解析该 URL。

        Args:
            url: 待判断的链接。

        Returns:
            若该链接能解析出 appid 与 game_type 则返回 True，否则 False。
        """
        if not url:
            logger.debug(f"[{self.name}] can_parse: URL为空")
            return False
        url_type = self._detect_url_type(url)
        ok = url_type is not None
        logger.debug(
            f"[{self.name}] can_parse: {'可解析' if ok else '不可解析'} "
            f"type={url_type}, url={url}"
        )
        return ok

    def extract_links(self, text: str) -> List[str]:
        """从文本中提取该解析器可处理的链接。

        Args:
            text: 输入文本（可能包含多个链接）。

        Returns:
            可解析的小黑盒链接列表（已过滤掉无法提取 appid/game_type 的候选）。
        """
        candidates = set()

        patterns = [
            r"https?://api\.xiaoheihe\.cn/game/share_game_detail[^\s<>\"'()]+",
            r"https?://api\.xiaoheihe\.cn/v3/bbs/app/api/web/share[^\s<>\"'()]+",
            r"https?://(?:www\.)?xiaoheihe\.cn/[^\s<>\"'()]+",
            r"https?://api\.xiaoheihe\.cn/[^\s<>\"'()]+",
        ]
        for pattern in patterns:
            candidates.update(re.findall(pattern, text, re.IGNORECASE))

        result: List[str] = []
        for u in candidates:
            if self._detect_url_type(u):
                result.append(u)

        if result:
            logger.debug(
                f"[{self.name}] extract_links: 提取到 {len(result)} 个链接: "
                f"{result[:3]}{'...' if len(result) > 3 else ''}"
            )
        else:
            logger.debug(f"[{self.name}] extract_links: 未提取到链接")
        return result

    def _detect_url_type(self, url: str) -> Optional[str]:
        """识别 URL 属于游戏详情页还是 BBS 帖子页。"""
        appid, game_type = self._extract_appid_game_type(url)
        if appid and game_type:
            return "game"
        if self._extract_bbs_link_id(url):
            return "bbs"
        return None

    @staticmethod
    def _normalize_appid(value: Any) -> Optional[str]:
        """将小黑盒 appid 规范化为字符串，兼容纯数字和新版字母数字混合 id。"""
        if value is None:
            return None
        appid = str(value).strip()
        if not appid:
            return None
        if not re.match(r"^[A-Za-z0-9_-]+$", appid):
            return None
        return appid

    def _extract_appid_game_type(self, url: str) -> Tuple[Optional[str], Optional[str]]:
        """从 URL 中提取 appid 与 game_type。

        Args:
            url: 小黑盒分享链接或网页链接。

        Returns:
            二元组 (appid, game_type)：
            - appid: 成功时为字符串，否则为 None
            - game_type: 成功时为字符串（例如 pc），否则为 None
        """
        if not url:
            return None, None
        try:
            u = urlparse(url)
        except Exception:
            return None, None

        host = (u.netloc or "").lower()
        path = u.path or ""

        if "xiaoheihe.cn" in host and "/game/share_game_detail" in path:
            qs = parse_qs(u.query or "")
            raw_appid = (qs.get("appid") or [None])[0]
            raw_game_type = (qs.get("game_type") or ["pc"])[0] or "pc"
            return self._normalize_appid(raw_appid), raw_game_type

        if "xiaoheihe.cn" in host:
            m = re.search(r"/app/topic/game/(?P<gt>[^/]+)/(?P<appid>[^/?#]+)", path, re.I)
            if m:
                return self._normalize_appid(unquote(m.group("appid"))), m.group("gt")

        return None, None

    def _extract_bbs_link_id(self, url: str) -> Optional[str]:
        """从小黑盒 BBS 帖子链接中提取 `link_id`。"""
        if not url:
            return None
        try:
            u = urlparse(url)
        except Exception:
            return None

        host = (u.netloc or "").lower()
        if "xiaoheihe.cn" not in host:
            return None

        qs = parse_qs(u.query or "")
        raw_link_id = (qs.get("link_id") or [None])[0]
        if raw_link_id:
            return raw_link_id

        path = unquote(u.path or "")
        patterns = [
            r"/app/bbs/link/(?P<link_id>[^/?#]+)",
            r"/community/.+?/list/(?P<link_id>[^/?#]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, path, re.I)
            if match:
                return match.group("link_id")
        return None

    @staticmethod
    def _format_unix_date(value: Any) -> str:
        """将 Unix 时间戳格式化为 `YYYY-MM-DD`。"""
        try:
            ts = int(value)
        except Exception:
            return ""
        if ts <= 0:
            return ""
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")

    @staticmethod
    def _format_user_name(user: Any) -> str:
        """将小黑盒用户对象格式化为作者展示字符串。"""
        if not isinstance(user, dict):
            return ""
        username = str(user.get("username") or "").strip()
        userid = user.get("userid")
        if username and userid:
            return f"{username}(uid:{userid})"
        return username

    def _canonical_web_url(self, appid: str, game_type: str) -> str:
        """构造规范的小黑盒 Web 详情页链接。

        Args:
            appid: 游戏 appid。
            game_type: 游戏类型（例如 pc）。

        Returns:
            标准化后的网页链接。
        """
        gt = (game_type or "pc").strip().lower()
        return f"https://www.xiaoheihe.cn/app/topic/game/{gt}/{appid}"

    @staticmethod
    def _unique_keep_order(urls: Iterable[str]) -> List[str]:
        """去重并保持原有顺序。

        Args:
            urls: URL 可迭代对象。

        Returns:
            去重后的 URL 列表（保持首次出现顺序）。
        """
        seen = set()
        out: List[str] = []
        for u in urls:
            if not u or not isinstance(u, str):
                continue
            if u in seen:
                continue
            seen.add(u)
            out.append(u)
        return out

    @staticmethod
    def _strip_tags(text: str) -> str:
        """粗略清理 HTML 标签并做一定的换行/空白规范化。

        Args:
            text: 原始 HTML 或混合文本。

        Returns:
            清理后的纯文本。
        """
        if not text:
            return ""
        t = re.sub(r"(?is)<script[^>]*>.*?</script>", "", text)
        t = re.sub(r"(?is)<style[^>]*>.*?</style>", "", t)
        t = re.sub(r"(?is)<video[^>]*>.*?</video>", "", t)
        t = re.sub(r"(?is)<img[^>]*>", "", t)

        t = re.sub(r"(?i)</p\s*>", "\n\n", t)
        t = re.sub(r"(?i)<p[^>]*>", "", t)
        t = re.sub(r"(?i)</div\s*>", "\n", t)
        t = re.sub(r"(?i)<div[^>]*>", "", t)
        t = re.sub(r"(?i)<li[^>]*>", "\n・", t)
        t = re.sub(r"(?i)</li\s*>", "\n", t)
        t = re.sub(r"(?i)</(ul|ol)\s*>", "\n", t)
        t = re.sub(r"(?i)</h[1-6]\s*>", "\n", t)
        t = re.sub(r"(?i)<h[1-6][^>]*>", "\n", t)

        t = re.sub(r"(?i)<br\s*/?>", "\n", t)
        t = re.sub(r"<[^>]+>", "", t)
        t = html_lib.unescape(t)
        t = t.replace("\r\n", "\n").replace("\r", "\n")
        t = t.replace("\u2028", "\n").replace("\u2029", "\n")
        t = t.replace("・・", "・")
        t = re.sub(r"\n{3,}", "\n\n", t).strip()
        return t

    async def _fetch_game_introduction_api(
        self,
        steam_appid: int,
        session: aiohttp.ClientSession,
    ) -> Optional[Dict[str, Any]]:
        """调用小黑盒 `game_introduction` 接口获取简介与发行信息。

        Args:
            steam_appid: Steam appid。
            session: aiohttp 会话。

        Returns:
            成功时返回接口 `result` 字段（dict），失败返回 None。
        """
        if not steam_appid:
            return None
        api_url = (
            "https://api.xiaoheihe.cn/game/game_introduction/"
            f"?steam_appid={steam_appid}&return_json=1"
        )
        async with session.get(
            api_url,
            headers={**self._default_headers, "Accept": "application/json"},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
        if not isinstance(data, dict):
            return None
        if data.get("status") != "ok":
            return None
        result = data.get("result")
        return result if isinstance(result, dict) else None

    @staticmethod
    def _format_cn_ymd_to_dotted(text: str) -> str:
        """将中文日期（YYYY年M月D日）或常见分隔日期格式化为 `YYYY.M.D`。

        Args:
            text: 日期文本。

        Returns:
            格式化后的日期字符串；若无法识别则返回原始去空白结果。
        """
        if not text:
            return ""
        s = html_lib.unescape(text).strip()
        s = re.sub(r"\s+", "", s)
        m = re.match(r"^(\d{4})年(\d{1,2})月(\d{1,2})日?$", s)
        if m:
            y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
            return f"{y}.{mo}.{d}"
        m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$", s)
        if m:
            y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
            return f"{y}.{mo}.{d}"
        return text.strip()

    async def _fetch_html(self, url: str, session: aiohttp.ClientSession) -> str:
        """拉取页面 HTML。

        Args:
            url: 页面链接。
            session: aiohttp 会话。

        Returns:
            HTML 文本。

        Raises:
            RuntimeError: 当请求失败（非 200）时。
        """
        async with session.get(
            url,
            headers=self._default_headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"无法获取页面内容，状态码: {response.status}")
            return await response.text()

    def _extract_nuxt_data_payload(self, html: str) -> Optional[list]:
        """从 HTML 中提取 Nuxt 注入的 `__NUXT_DATA__` JSON payload。

        Args:
            html: 页面 HTML。

        Returns:
            解析成功时返回 list payload，否则 None。
        """
        if not html:
            return None
        m = re.search(
            r'<script[^>]+id="__NUXT_DATA__"[^>]*>(.*?)</script>',
            html,
            re.S | re.I,
        )
        if not m:
            return None
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
        except Exception:
            return None
        return data if isinstance(data, list) else None

    def _devalue_resolve_root(self, payload: list) -> Any:
        """将 Nuxt 的 devalue/索引引用结构还原为普通 Python 对象树。

        Nuxt `__NUXT_DATA__` 中经常用“索引引用”来压缩结构，本函数会：
        - 将 `int` 索引引用解析为对应条目
        - 处理部分包装结构（Reactive/Ref/Readonly 等）
        - 尝试规避循环引用导致的递归死循环

        Args:
            payload: `__NUXT_DATA__` 解析得到的 list。

        Returns:
            还原后的根对象（通常为 dict/list）。
        """
        n = len(payload)
        memo: Dict[int, Any] = {}
        resolving: set[int] = set()

        def resolve(v: Any) -> Any:
            "处理resolve逻辑。"
            if isinstance(v, int) and 0 <= v < n:
                return resolve_idx(v)
            if isinstance(v, list):
                if (
                    len(v) == 2
                    and isinstance(v[0], str)
                    and v[0] in {
                        "ShallowReactive",
                        "Reactive",
                        "Ref",
                        "ShallowRef",
                        "Readonly",
                        "ShallowReadonly",
                    }
                ):
                    return resolve(v[1])
                return [resolve(x) for x in v]
            if isinstance(v, dict):
                return {k: resolve(val) for k, val in v.items()}
            return v

        def resolve_idx(idx: int) -> Any:
            "处理resolve idx逻辑。"
            if idx in memo:
                return memo[idx]
            if idx in resolving:
                return None
            resolving.add(idx)
            memo[idx] = None
            memo[idx] = resolve(payload[idx])
            resolving.remove(idx)
            return memo[idx]

        return resolve(0)

    @staticmethod
    def _find_best_game_dict(root: Any, appid: str) -> Optional[Dict[str, Any]]:
        """在还原后的对象树中寻找最“像游戏详情”的 dict。

        Args:
            root: `_devalue_resolve_root` 的返回值。
            appid: 目标 appid（steam_appid/appid 匹配）。

        Returns:
            匹配到的游戏详情 dict；若未找到返回 None。
        """
        normalized_appid = XiaoheiheParser._normalize_appid(appid)
        best: Optional[Dict[str, Any]] = None
        best_score = -1
        fallback_best: Optional[Dict[str, Any]] = None
        fallback_score = -1
        stack = [root]
        while stack:
            cur = stack.pop()
            if isinstance(cur, dict):
                score = 0
                for k in (
                    "about_the_game",
                    "name",
                    "name_en",
                    "price",
                    "heybox_price",
                    "user_num",
                    "game_award",
                ):
                    if k in cur:
                        score += 3
                if "comment_stats" in cur:
                    score += 2

                current_appid = XiaoheiheParser._normalize_appid(cur.get("appid"))
                current_steam_appid = XiaoheiheParser._normalize_appid(cur.get("steam_appid"))
                if normalized_appid and (
                    current_appid == normalized_appid or current_steam_appid == normalized_appid
                ):
                    if current_steam_appid == normalized_appid:
                        score += 2
                    if score > best_score:
                        best = cur
                        best_score = score

                if score > fallback_score and (
                    "name" in cur or "name_en" in cur
                ) and (
                    "about_the_game" in cur or "comment_stats" in cur or "user_num" in cur
                ):
                    fallback_best = cur
                    fallback_score = score
                for v in cur.values():
                    if isinstance(v, (dict, list)):
                        stack.append(v)
            elif isinstance(cur, list):
                for v in cur:
                    if isinstance(v, (dict, list)):
                        stack.append(v)
        return best or fallback_best

    @staticmethod
    def _format_people_count(count: Optional[int]) -> str:
        """将评价人数格式化为更易读的中文文本。"""
        if not isinstance(count, int) or count <= 0:
            return ""
        if count >= 10000:
            return f"{count / 10000:.1f} 万人评价"
        return f"{count} 人评价"

    @staticmethod
    def _format_yuan_from_coin(coin: Any) -> str:
        """将小黑盒 coin（千分之一元）转换为人民币字符串。"""
        try:
            c = int(coin)
        except Exception:
            return ""
        value = c / 1000.0
        if abs(value - round(value)) < 1e-9:
            return str(int(round(value)))
        return f"{value:.2f}"

    @staticmethod
    def _normalize_value_text(text: str) -> str:
        """规范化展示文本（百分号、小时、货币符号与空白）。"""
        if not text:
            return ""
        v = str(text).strip()
        v = re.sub(r"(\d)\%", r"\1 %", v)
        v = re.sub(r"(\d)h\b", r"\1 h", v, flags=re.I)
        v = re.sub(r"#(\d)", r"# \1", v)
        v = v.replace("￥", "¥ ")
        v = re.sub(r"\s{2,}", " ", v).strip()
        return v

    @staticmethod
    def _extract_rich_text(it: Any) -> str:
        """从 `hb_rich_text.attrs[].text` 中提取拼接后的纯文本。"""
        if not isinstance(it, dict):
            return ""
        rt = it.get("hb_rich_text")
        if not isinstance(rt, dict):
            return ""
        attrs = rt.get("attrs")
        if not isinstance(attrs, list):
            return ""
        parts: List[str] = []
        for a in attrs:
            if isinstance(a, dict) and isinstance(a.get("text"), str):
                parts.append(a["text"])
        return "".join(parts).strip()

    @staticmethod
    def _clean_award_text(text: str) -> str:
        """清理奖项文本中的括号补充说明与多余空白。"""
        if not text:
            return ""
        t = str(text).strip()
        t = re.sub(r"（[^）]*）", "", t)
        t = re.sub(r"\([^)]*\)", "", t)
        return re.sub(r"\s{2,}", " ", t).strip()

    def _format_intro_text(self, text: str) -> str:
        """将简介 HTML/文本清理为更适合消息展示的段落文本。

        Args:
            text: 简介内容（可能包含 HTML）。

        Returns:
            清理后的简介文本。
        """
        if not text:
            return ""
        t = self._strip_tags(text)
        t = t.replace("\u3000", " ").replace("\xa0", " ")
        if "\n" in t:
            t = re.sub(r"[ \t]+\n", "\n", t)
            t = re.sub(r"\n[ \t]+", "\n", t)
            t = re.sub(r"\n{3,}", "\n\n", t).strip()
            return t
        t = re.sub(r"([。！？])\s+(?=[\u4e00-\u9fffA-Za-z0-9])", r"\1\n\n", t)
        t = re.sub(r"。(?=(探索|复仇雪耻))", "。\n\n", t)
        t = re.sub(r"\n{3,}", "\n\n", t).strip()
        return t

    def _parse_types_from_html(self, html: str) -> str:
        """从页面 HTML 中解析“类型/标签”文本。

        Args:
            html: 页面 HTML。

        Returns:
            拼接后的类型文本（可能为空字符串）。
        """
        group1 = ""
        group2_tags: List[str] = []

        m = re.search(r'<div class="row-2">.*?<div class="tags">(.*?)</div></div>', html, re.S | re.I)
        tags_html = m.group(1) if m else ""
        if tags_html:
            m2 = re.search(r'<div class="tag common"[^>]*>(.*?)</div>', tags_html, re.S | re.I)
            if m2:
                spans = re.findall(r"<span[^>]*>(.*?)</span>", m2.group(1), re.S | re.I)
                toks = [self._strip_tags(x) for x in spans]
                toks = [re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", t) for t in toks]
                toks = [t for t in toks if t]
                if toks:
                    group1 = " ".join(toks)

            raw_tags = re.findall(r'<p class="tag"[^>]*>(.*?)</p>', tags_html, re.S | re.I)
            group2_tags = [self._strip_tags(t) for t in raw_tags]
            group2_tags = [t for t in group2_tags if t]

        parts: List[str] = []
        if group1:
            parts.append(f"[ {group1} ]")
        if group2_tags:
            parts.append(f"[ {' '.join(group2_tags)} ]")
        return " ".join(parts).strip()

    async def _get_device_id(self, session: aiohttp.ClientSession) -> str:
        """获取并缓存小黑盒 Web API 所需的设备 token。"""
        if self._device_id:
            return self._device_id
        async with self._device_id_lock:
            if self._device_id:
                return self._device_id
            self._device_id = await XiaoheiheDevice.get_d_id(session)
            return self._device_id

    @staticmethod
    def _xhh_common_params() -> Dict[str, Any]:
        """小黑盒 Web API 通用参数。"""
        return {
            "os_type": "web",
            "app": "heybox",
            "client_type": "web",
            "version": "999.0.4",
            "web_version": "2.5",
            "x_client_type": "web",
            "x_app": "heybox_website",
            "heybox_id": "",
            "x_os_type": "Windows",
            "device_info": "Chrome",
        }

    async def _fetch_signed_api(
        self,
        session: aiohttp.ClientSession,
        path: str,
        params: Dict[str, Any],
        retry: bool = True,
    ) -> Dict[str, Any]:
        """请求小黑盒需要签名和设备 token 的 Web API。"""
        query = {
            **self._xhh_common_params(),
            **params,
            **XiaoheiheSign().sign(path),
        }
        token = await self._get_device_id(session)
        headers = {
            **self._default_headers,
            "Accept": "application/json",
        }
        async with session.get(
            f"https://api.xiaoheihe.cn{path}",
            params=query,
            headers=headers,
            cookies={"x_xhh_tokenid": token},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"小黑盒 API 请求失败，状态码: {response.status}")
            data = await response.json(content_type=None)

        if not isinstance(data, dict):
            raise RuntimeError("小黑盒 API 返回格式异常")

        status = data.get("status")
        if status == "ok":
            result = data.get("result")
            return result if isinstance(result, dict) else {}

        if retry and status in {"lack_token", "show_captcha"}:
            logger.debug(f"[{self.name}] API token 失效或需重试: {status}")
            self._device_id = None
            return await self._fetch_signed_api(session, path, params, retry=False)

        msg = data.get("msg") or status or "未知错误"
        raise RuntimeError(f"小黑盒 API 请求失败: {msg}")

    async def _fetch_game_detail_api(
        self,
        appid: str,
        game_type: str,
        session: aiohttp.ClientSession,
    ) -> Optional[Dict[str, Any]]:
        """调用新版游戏详情接口。"""
        normalized_appid = self._normalize_appid(appid)
        if not normalized_appid:
            return None
        return await self._fetch_signed_api(
            session,
            "/game/get_game_detail/",
            {"appid": normalized_appid, "game_type": game_type or "pc"},
        )

    async def _fetch_bbs_link_tree(
        self,
        link_id: str,
        session: aiohttp.ClientSession,
    ) -> Dict[str, Any]:
        """调用 BBS 帖子详情接口。"""
        return await self._fetch_signed_api(
            session,
            "/bbs/app/link/tree",
            {
                "link_id": str(link_id),
                "is_first": "1",
                "page": "1",
                "index": "1",
                "limit": "20",
                "owner_only": "1",
            },
        )

    @staticmethod
    def _iter_rich_text_attrs(node: Any) -> Iterable[str]:
        """遍历小黑盒 rich_text 结构中的文本片段。"""
        if isinstance(node, dict):
            attrs = node.get("attrs")
            if isinstance(attrs, list):
                for attr in attrs:
                    if isinstance(attr, dict) and isinstance(attr.get("text"), str):
                        yield attr["text"]
            for value in node.values():
                if isinstance(value, (dict, list)):
                    yield from XiaoheiheParser._iter_rich_text_attrs(value)
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, (dict, list)):
                    yield from XiaoheiheParser._iter_rich_text_attrs(item)

    def _format_game_tags_from_api(self, game: Dict[str, Any]) -> str:
        """从游戏详情 API 中整理类型/标签文本。"""
        group1: List[str] = []
        group2: List[str] = []

        common_tags = game.get("common_tags")
        if isinstance(common_tags, list):
            for item in common_tags:
                if not isinstance(item, dict):
                    continue
                desc_list = item.get("desc_list")
                if isinstance(desc_list, list):
                    for value in desc_list:
                        text = self._normalize_value_text(str(value).replace("", ""))
                        if text and len(text) <= 20:
                            group1.append(text)
                rich_text = item.get("rich_text")
                for text in self._iter_rich_text_attrs(rich_text):
                    text = self._normalize_value_text(text.replace("", ""))
                    if text and len(text) <= 20 and not re.search(r"NO\.\d+", text, re.I):
                        group2.append(text)

        group1 = self._unique_keep_order(group1)[:6]
        group2 = self._unique_keep_order(group2)[:12]
        parts: List[str] = []
        if group1:
            parts.append(f"[ {' '.join(group1)} ]")
        if group2:
            parts.append(f"[ {' '.join(group2)} ]")
        return " ".join(parts).strip()

    def _extract_game_media_from_api(
        self,
        game: Dict[str, Any],
    ) -> Tuple[List[str], List[str]]:
        """从游戏详情 API 提取视频和图片媒体。"""
        videos: List[str] = []
        images: List[str] = []

        image = game.get("image")
        if isinstance(image, str) and image:
            images.append(image)

        screenshots = game.get("screenshots")
        if isinstance(screenshots, list):
            for item in screenshots:
                if not isinstance(item, dict):
                    continue
                media_url = item.get("url")
                thumb_url = item.get("thumbnail")
                item_type = str(item.get("type") or "").lower()
                if not isinstance(media_url, str) or not media_url:
                    if isinstance(thumb_url, str) and thumb_url:
                        images.append(thumb_url)
                    continue
                media_lower = media_url.lower()
                if item_type == "movie" or ".m3u8" in media_lower or re.search(
                    r"\.(mp4|mov|webm|m4v)(?:\?|$)", media_lower
                ):
                    videos.append(media_url)
                else:
                    images.append(media_url)

        return self._unique_keep_order(videos), self._unique_keep_order(images)

    async def _build_game_result_from_api(
        self,
        session: aiohttp.ClientSession,
        game: Dict[str, Any],
        source_url: str,
        web_url: str,
        appid: str,
    ) -> Dict[str, Any]:
        """用新版游戏详情 API 结果构建插件统一结果。"""
        name = game.get("name") if isinstance(game.get("name"), str) else ""
        name_en = game.get("name_en") if isinstance(game.get("name_en"), str) else ""
        title = f"{name}（{name_en}）" if (name and name_en) else (name or name_en)
        if not title:
            raise RuntimeError("未解析到游戏标题")

        steam_appid = game.get("steam_appid")
        if isinstance(steam_appid, str) and steam_appid.isdigit():
            steam_appid = int(steam_appid)
        if not isinstance(steam_appid, int) or steam_appid <= 0:
            normalized_appid = self._normalize_appid(appid)
            steam_appid = int(normalized_appid) if normalized_appid and normalized_appid.isdigit() else None

        intro_api = None
        if steam_appid:
            try:
                intro_api = await self._fetch_game_introduction_api(steam_appid, session)
            except Exception as e:
                logger.debug(f"[{self.name}] game_introduction 补充接口失败: {e}")

        intro_source = ""
        if isinstance(intro_api, dict) and isinstance(intro_api.get("about_the_game"), str):
            intro_source = intro_api.get("about_the_game") or ""
        if not intro_source and isinstance(game.get("about_the_game"), str):
            intro_source = game.get("about_the_game") or ""
        intro = self._format_intro_text(intro_source)

        release_date = ""
        developer = ""
        publisher = ""
        if isinstance(intro_api, dict):
            release_date = self._format_cn_ymd_to_dotted(
                str(intro_api.get("release_date") or "").strip()
            )
            developers = intro_api.get("developers")
            publishers = intro_api.get("publishers")
            if isinstance(developers, list):
                developer = ",".join(
                    d.get("value")
                    for d in developers
                    if isinstance(d, dict) and isinstance(d.get("value"), str) and d.get("value")
                )
            if isinstance(publishers, list):
                publisher = ",".join(
                    p.get("value")
                    for p in publishers
                    if isinstance(p, dict) and isinstance(p.get("value"), str) and p.get("value")
                )

        types = self._format_game_tags_from_api(game)

        score = str(game.get("score")).strip() if game.get("score") is not None else ""
        score_count = ""
        comment_stats = game.get("comment_stats") if isinstance(game.get("comment_stats"), dict) else {}
        score_comment = comment_stats.get("score_comment")
        if isinstance(score_comment, int):
            score_count = self._format_people_count(score_comment)
        rating_line = ""
        if score:
            rating_line = f"小黑盒评分：{score}"
            if score_count:
                rating_line = f"小黑盒评分：{score}（{score_count}）"

        stats_map: Dict[str, Dict[str, Any]] = {}
        if isinstance(game.get("user_num"), dict):
            gd = game["user_num"].get("game_data")
            if isinstance(gd, list):
                for item in gd:
                    if isinstance(item, dict) and isinstance(item.get("desc"), str):
                        stats_map[item["desc"]] = item

        def stat_line(desc_key: str, out_label: str, include_rank: bool = False) -> str:
            item = stats_map.get(desc_key)
            if not item:
                return ""
            raw = self._extract_rich_text(item) or item.get("value")
            value = self._normalize_value_text(raw)
            if not value:
                return ""
            if include_rank:
                rank = item.get("rank")
                if isinstance(rank, str) and rank.strip():
                    rank_text = self._normalize_value_text(rank)
                    if rank_text.startswith("#"):
                        value = f"{value}（{rank_text}）"
            return f"{out_label}：{value}"

        good_rate_line = stat_line("全语言好评率", "全语言好评率")
        avg_time_line = stat_line("平均游戏时间", "平均游戏时间", include_rank=True)
        online_now_line = stat_line("当前在线", "当前在线")
        yesterday_peak_line = stat_line("昨日峰值在线", "昨日峰值在线", include_rank=True)
        sale_rank_line = stat_line("全球销量排行", "全球销量排行")
        month_avg_line = stat_line("本月平均在线", "本月平均在线", include_rank=True)

        price_line = ""
        current_price_line = ""
        lowest_price_line = ""
        if isinstance(game.get("price"), dict):
            price = game["price"]
            initial = price.get("initial") or price.get("current")
            if initial:
                price_line = (
                    f"价格：¥ {self._normalize_value_text(initial).replace('¥ ', '').replace('¥', '').strip()}"
                )
            lowest_price = price.get("lowest_price")
            if lowest_price:
                lowest_price_line = (
                    "史低价格：¥ "
                    f"{self._normalize_value_text(lowest_price).replace('¥ ', '').replace('¥', '').strip()}"
                )
        if isinstance(game.get("heybox_price"), dict):
            cost_coin = game["heybox_price"].get("cost_coin")
            if cost_coin is not None:
                yuan = self._format_yuan_from_coin(cost_coin)
                if yuan:
                    current_price_line = f"当前价格：¥ {yuan}"

        lowest_item = stats_map.get("史低价格")
        if lowest_item:
            value = self._normalize_value_text(lowest_item.get("value"))
            if value:
                lowest_price_line = f"史低价格：¥ {value.replace('¥', '').strip()}"

        awards: List[str] = []
        if isinstance(game.get("game_award"), list):
            for item in game["game_award"]:
                if not isinstance(item, dict):
                    continue
                desc = self._clean_award_text(item.get("desc"))
                detail = self._clean_award_text(item.get("detail_name"))
                if desc and detail:
                    awards.append(f"{desc}：{detail}")
        awards = self._unique_keep_order(awards)

        desc_lines: List[str] = ["", "", "============="]
        if intro:
            desc_lines.append(intro)
        desc_lines.extend(["=============", ""])
        if types:
            desc_lines.append(f"类型：{types}")
        if release_date:
            desc_lines.append(f"发布时间：{release_date}")
        if developer:
            desc_lines.append(f"开发商：{developer}")
        if publisher:
            desc_lines.append(f"发行商：{publisher}")
        for line in (
            rating_line,
            good_rate_line,
            avg_time_line,
            online_now_line,
            yesterday_peak_line,
        ):
            if line:
                desc_lines.append(line)
        if sale_rank_line:
            if month_avg_line:
                desc_lines.append(f"{sale_rank_line}（注意：部分游戏在这里是：{month_avg_line}）")
            else:
                desc_lines.append(sale_rank_line)
        elif month_avg_line:
            desc_lines.append(month_avg_line)
        for line in (price_line, current_price_line, lowest_price_line):
            if line:
                desc_lines.append(line)
        if awards:
            desc_lines.append("奖项：")
            for award in awards:
                desc_lines.append(f"   {award}")

        videos, images = self._extract_game_media_from_api(game)
        prefixed_videos = self._add_m3u8_prefix_to_urls(videos) if videos else []
        video_urls = [[v] for v in prefixed_videos] if prefixed_videos else []
        image_urls = [[img] for img in images] if images else []
        if not video_urls and not image_urls:
            raise RuntimeError("未找到任何内容")

        referer = "https://store.steampowered.com/"
        result_dict = {
            "url": web_url,
            "source_url": source_url,
            "title": title,
            "author": "",
            "desc": "\n".join(desc_lines).rstrip(),
            "timestamp": release_date or "",
            "video_urls": video_urls,
            "image_urls": image_urls,
            "image_headers": build_request_headers(is_video=False, referer=referer),
            "video_headers": build_request_headers(is_video=True, referer=referer),
            "use_video_proxy": self.use_video_proxy,
            "proxy_url": self.proxy_url if self.use_video_proxy else None,
        }
        if video_urls:
            result_dict["video_force_download"] = True
        return result_dict

    def _parse_bbs_text_list(self, raw_text: str) -> Tuple[str, List[str]]:
        """解析 BBS 图文/文章的 text JSON。"""
        content = ""
        images: List[str] = []
        if not raw_text:
            return content, images

        try:
            text_list = json.loads(raw_text)
        except Exception:
            return self._strip_tags(raw_text), images

        if not isinstance(text_list, list):
            return self._strip_tags(raw_text), images

        content_parts: List[str] = []
        for item in text_list:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type in {"text", "html"} and isinstance(item.get("text"), str):
                text = item.get("text") or ""
                content_parts.append(self._strip_tags(text) if item_type == "html" else text)
                if item_type == "html":
                    html_images = re.findall(
                        r'<img[^>]+(?:src|data-original|data-src)=["\']([^"\']+)["\']',
                        text,
                        re.I,
                    )
                    images.extend(html_images)
            elif item_type == "img" and isinstance(item.get("url"), str):
                images.append(item["url"])

        content = "\n\n".join(part.strip() for part in content_parts if part and part.strip())
        return content, self._unique_keep_order(images)

    async def _parse_bbs_link(
        self,
        session: aiohttp.ClientSession,
        url: str,
        link_id: str,
    ) -> Dict[str, Any]:
        """解析小黑盒 BBS 帖子页。"""
        data = await self._fetch_bbs_link_tree(link_id, session)
        link = data.get("link")
        if not isinstance(link, dict):
            raise RuntimeError("未获取到小黑盒帖子详情")

        title = str(link.get("title") or "").strip() or "小黑盒帖子"
        content = str(link.get("description") or "").strip()
        videos: List[str] = []
        images: List[str] = []

        has_video = bool(link.get("has_video"))
        if has_video:
            video_url = link.get("video_url")
            video_thumb = link.get("video_thumb")
            if isinstance(video_url, str) and video_url:
                videos.append(video_url)
            if isinstance(video_thumb, str) and video_thumb:
                images.append(video_thumb)
            if isinstance(link.get("text"), str) and link.get("text"):
                content = link["text"]
        else:
            parsed_content, parsed_images = self._parse_bbs_text_list(str(link.get("text") or ""))
            if parsed_content:
                content = parsed_content
            images.extend(parsed_images)

        if not videos and not images:
            raise RuntimeError("帖子不包含图片或视频")

        tags: List[str] = []
        content_tags = link.get("content_tags")
        if isinstance(content_tags, list):
            for item in content_tags:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    tags.append(item["text"].strip())
        tags = self._unique_keep_order([t for t in tags if t])

        desc_lines = [content] if content else []
        if tags:
            desc_lines.append("标签：" + " / ".join(tags))
        comment_num = link.get("comment_num")
        award_num = link.get("link_award_num")
        meta_parts = []
        if isinstance(comment_num, int):
            meta_parts.append(f"评论 {comment_num}")
        if isinstance(award_num, int):
            meta_parts.append(f"获赞/收藏 {award_num}")
        if meta_parts:
            desc_lines.append("互动：" + "，".join(meta_parts))

        web_url = f"https://www.xiaoheihe.cn/app/bbs/link/{link_id}"
        image_headers = build_request_headers(is_video=False, referer="https://www.xiaoheihe.cn/")
        video_headers = build_request_headers(is_video=True, referer="https://www.xiaoheihe.cn/")

        video_urls = self._add_range_prefix_to_video_urls([[v] for v in self._unique_keep_order(videos)])
        result_dict = {
            "url": web_url,
            "source_url": url,
            "title": title,
            "author": self._format_user_name(link.get("user")),
            "desc": "\n\n".join(line for line in desc_lines if line).strip(),
            "timestamp": self._format_unix_date(link.get("create_at")),
            "video_urls": video_urls,
            "image_urls": [[img] for img in self._unique_keep_order(images)],
            "image_headers": image_headers,
            "video_headers": video_headers,
            "use_video_proxy": self.use_video_proxy,
            "proxy_url": self.proxy_url if self.use_video_proxy else None,
        }
        return result_dict

    async def parse(
        self,
        session: aiohttp.ClientSession,
        url: str
    ) -> Optional[Dict[str, Any]]:
        """解析小黑盒链接并返回统一结构的结果字典。

        解析流程概览：
        - 优先识别为游戏详情页或 BBS 帖子页
        - 游戏详情页优先走结构化 API，失败时回退到旧 HTML/Nuxt 解析
        - BBS 帖子页走 ParseHub 同类签名 API 方案

        Args:
            session: aiohttp 会话。
            url: 小黑盒分享链接或 Web 链接。

        Returns:
            解析成功时返回结果字典；解析失败会抛出异常（通常不返回 None）。

        Raises:
            RuntimeError: 当无法提取必要字段或未解析到有效媒体内容时。
        """
        logger.debug(f"[{self.name}] parse: 开始解析 {url}")
        async with self.semaphore:
            url_type = self._detect_url_type(url)
            if url_type == "bbs":
                link_id = self._extract_bbs_link_id(url)
                if not link_id:
                    raise RuntimeError(f"无法从URL提取 link_id: {url}")
                result_dict = await self._parse_bbs_link(session, url, link_id)
                logger.debug(
                    f"[{self.name}] parse: BBS 解析完成 {url}, "
                    f"title_len={len(result_dict.get('title') or '')}, "
                    f"desc_len={len(result_dict.get('desc') or '')}, "
                    f"video_count={len(result_dict.get('video_urls') or [])}, "
                    f"image_count={len(result_dict.get('image_urls') or [])}"
                )
                return result_dict

            appid, game_type = self._extract_appid_game_type(url)
            if not appid or not game_type:
                raise RuntimeError(f"无法识别的小黑盒链接: {url}")

            web_url = self._canonical_web_url(appid, game_type)
            logger.debug(f"[{self.name}] parse: 使用 Web 链接 {web_url}")

            api_error: Optional[Exception] = None
            try:
                game = await self._fetch_game_detail_api(appid, game_type, session)
                if game:
                    result_dict = await self._build_game_result_from_api(
                        session=session,
                        game=game,
                        source_url=url,
                        web_url=web_url,
                        appid=appid,
                    )
                    logger.debug(
                        f"[{self.name}] parse: 游戏 API 解析完成 {url}, "
                        f"title_len={len(result_dict.get('title') or '')}, "
                        f"desc_len={len(result_dict.get('desc') or '')}, "
                        f"video_count={len(result_dict.get('video_urls') or [])}, "
                        f"image_count={len(result_dict.get('image_urls') or [])}"
                    )
                    return result_dict
            except Exception as e:
                api_error = e
                logger.debug(f"[{self.name}] game_detail API 失败，回退 HTML 解析: {e}")

            html = await self._fetch_html(web_url, session)

            videos = self._unique_keep_order(re.findall(
                r"https?://[^\"'\s<>]+\.m3u8(?:\?[^\"'\s<>]*)?",
                html, re.I
            ))
            all_images = re.findall(
                r"https?://[^\"'\s<>]+\.(?:jpg|jpeg|png|webp)(?:\?[^\"'\s<>]*)?",
                html, re.I
            )
            images: List[str] = []
            for img in self._unique_keep_order(all_images):
                img_lower = img.lower()
                if "/thumbnail/" in img_lower:
                    continue
                if any(kw in img_lower for kw in ["gameimg", "steam_item_assets", "screenshot", "game"]):
                    images.append(img)

            types = self._parse_types_from_html(html)

            payload = self._extract_nuxt_data_payload(html)
            if not payload:
                raise RuntimeError(
                    "未找到 __NUXT_DATA__，且游戏详情 API 解析失败"
                    + (f"：{api_error}" if api_error else "")
                )
            root = self._devalue_resolve_root(payload)
            game = self._find_best_game_dict(root, appid)
            if not game:
                raise RuntimeError(
                    "未找到游戏详情数据（Nuxt 解析失败）"
                    + (f"；API 错误：{api_error}" if api_error else "")
                )

            name = game.get("name") if isinstance(game.get("name"), str) else ""
            name_en = game.get("name_en") if isinstance(game.get("name_en"), str) else ""
            title = f"{name}（{name_en}）" if (name and name_en) else (name or name_en)
            if not title:
                raise RuntimeError("未解析到游戏标题")

            score = str(game.get("score")).strip() if game.get("score") is not None else ""
            score_count = ""
            comment_stats = game.get("comment_stats") if isinstance(game.get("comment_stats"), dict) else {}
            score_comment = comment_stats.get("score_comment")
            if isinstance(score_comment, int):
                score_count = self._format_people_count(score_comment)
            rating_line = ""
            if score:
                rating_line = f"小黑盒评分：{score}"
                if score_count:
                    rating_line = f"小黑盒评分：{score}（{score_count}）"

            steam_appid = game.get("steam_appid")
            if isinstance(steam_appid, str) and steam_appid.isdigit():
                steam_appid = int(steam_appid)
            if not isinstance(steam_appid, int) or steam_appid <= 0:
                normalized_appid = self._normalize_appid(appid)
                steam_appid = int(normalized_appid) if normalized_appid and normalized_appid.isdigit() else None

            intro_api = None
            if steam_appid:
                try:
                    intro_api = await self._fetch_game_introduction_api(
                        steam_appid,
                        session
                    )
                except Exception as e:
                    logger.debug(
                        f"[{self.name}] game_introduction 琛ュ厖鎺ュ彛澶辫触: {e}"
                    )

            intro_source = ""
            if isinstance(intro_api, dict) and isinstance(intro_api.get("about_the_game"), str):
                intro_source = intro_api.get("about_the_game") or ""
            if not intro_source and isinstance(game.get("about_the_game"), str):
                intro_source = game.get("about_the_game") or ""
            intro_source = intro_source or " "
            if not intro_source:
                raise RuntimeError("未获取到简介（game_introduction 接口失败）")

            intro = self._format_intro_text(intro_source)
            release_date = ""
            developers = None
            publishers = None
            if isinstance(intro_api, dict):
                release_date = self._format_cn_ymd_to_dotted(str(intro_api.get("release_date") or "").strip())
                developers = intro_api.get("developers")
                publishers = intro_api.get("publishers")
            developer = ""
            publisher = ""
            if isinstance(developers, list):
                developer = ",".join(
                    d.get("value")
                    for d in developers
                    if isinstance(d, dict) and isinstance(d.get("value"), str) and d.get("value")
                )
            if isinstance(publishers, list):
                publisher = ",".join(
                    p.get("value")
                    for p in publishers
                    if isinstance(p, dict) and isinstance(p.get("value"), str) and p.get("value")
                )

            stats_map: Dict[str, Dict[str, Any]] = {}
            if isinstance(game.get("user_num"), dict):
                gd = game["user_num"].get("game_data")
                if isinstance(gd, list):
                    for it in gd:
                        if isinstance(it, dict) and isinstance(it.get("desc"), str):
                            stats_map[it["desc"]] = it

            def stat_line(desc_key: str, out_label: str, include_rank: bool = False) -> str:
                it = stats_map.get(desc_key)
                if not it:
                    return ""
                raw = self._extract_rich_text(it) or it.get("value")
                value = self._normalize_value_text(raw)
                if not value:
                    return ""
                if include_rank:
                    rk = it.get("rank")
                    if isinstance(rk, str) and rk.strip():
                        rank_text = self._normalize_value_text(rk)
                        if rank_text.startswith("#"):
                            value = f"{value}（{rank_text}）"
                return f"{out_label}：{value}"

            good_rate_line = stat_line("全语言好评率", "全语言好评率")
            avg_time_line = stat_line("平均游戏时间", "平均游戏时间", include_rank=True)
            online_now_line = stat_line("当前在线", "当前在线")
            yesterday_peak_line = stat_line("昨日峰值在线", "昨日峰值在线", include_rank=True)
            sale_rank_line = stat_line("全球销量排行", "全球销量排行")
            month_avg_line = stat_line("本月平均在线", "本月平均在线", include_rank=True)

            price_line = ""
            current_price_line = ""
            lowest_price_line = ""
            if isinstance(game.get("price"), dict):
                price = game["price"]
                initial = price.get("initial") or price.get("current")
                if initial:
                    price_line = (
                        f"价格：¥ {self._normalize_value_text(initial).replace('¥ ', '').replace('¥', '').strip()}"
                    )
                lowest_price = price.get("lowest_price")
                if lowest_price:
                    lowest_price_line = (
                        "史低价格：¥ "
                        f"{self._normalize_value_text(lowest_price).replace('¥ ', '').replace('¥', '').strip()}"
                    )
            if isinstance(game.get("heybox_price"), dict):
                cost_coin = game["heybox_price"].get("cost_coin")
                if cost_coin is not None:
                    yuan = self._format_yuan_from_coin(cost_coin)
                    if yuan:
                        current_price_line = f"当前价格：¥ {yuan}"

            lowest_item = stats_map.get("史低价格")
            if lowest_item:
                value = self._normalize_value_text(lowest_item.get("value"))
                if value:
                    lowest_price_line = f"史低价格：¥ {value.replace('¥', '').strip()}"

            awards: List[str] = []
            if isinstance(game.get("game_award"), list):
                for it in game["game_award"]:
                    if isinstance(it, dict):
                        desc = self._clean_award_text(it.get("desc"))
                        detail = self._clean_award_text(it.get("detail_name"))
                        if desc and detail:
                            awards.append(f"{desc}：{detail}")
            awards = self._unique_keep_order(awards)

            desc_lines: List[str] = ["", "", "============="]
            if intro:
                desc_lines.append(intro)
            desc_lines.extend(["=============", ""])

            if types:
                desc_lines.append(f"类型：{types}")
            if release_date:
                desc_lines.append(f"发布时间：{release_date}")
            if developer:
                desc_lines.append(f"开发商：{developer}")
            if publisher:
                desc_lines.append(f"发行商：{publisher}")
            for line in (
                rating_line,
                good_rate_line,
                avg_time_line,
                online_now_line,
                yesterday_peak_line,
            ):
                if line:
                    desc_lines.append(line)

            if sale_rank_line:
                if month_avg_line:
                    desc_lines.append(f"{sale_rank_line}（注意：部分游戏在这里是：{month_avg_line}）")
                else:
                    desc_lines.append(sale_rank_line)
            elif month_avg_line:
                desc_lines.append(month_avg_line)

            for line in (price_line, current_price_line, lowest_price_line):
                if line:
                    desc_lines.append(line)

            if awards:
                desc_lines.append("奖项：")
                for award in awards:
                    desc_lines.append(f"   {award}")

            desc = "\n".join(desc_lines).rstrip()

            prefixed_videos = self._add_m3u8_prefix_to_urls(videos) if videos else []
            video_urls = [[v] for v in prefixed_videos] if prefixed_videos else []
            image_urls = [[img] for img in images] if images else []

            if not video_urls and not image_urls:
                logger.debug(f"[{self.name}] parse: 未找到任何内容 {url}")
                raise RuntimeError(f"未找到任何内容: {url}")

            referer = "https://store.steampowered.com/"
            image_headers = build_request_headers(is_video=False, referer=referer)
            video_headers = build_request_headers(is_video=True, referer=referer)

            result_dict = {
                "url": web_url,
                "source_url": url,
                "title": title or "",
                "author": "",
                "desc": desc,
                "timestamp": release_date or "",
                "video_urls": video_urls,
                "image_urls": image_urls,
                "image_headers": image_headers,
                "video_headers": video_headers,
                "use_video_proxy": self.use_video_proxy,
                "proxy_url": self.proxy_url if self.use_video_proxy else None,
            }
            if video_urls:
                result_dict["video_force_download"] = True
            logger.debug(
                f"[{self.name}] parse: HTML 回退解析完成 {url}, "
                f"title_len={len(result_dict.get('title') or '')}, "
                f"desc_len={len(result_dict.get('desc') or '')}, "
                f"video_count={len(video_urls)}, image_count={len(image_urls)}"
            )
            return result_dict
